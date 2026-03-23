"""Treaty negotiation, semantic memory, and archival semantics for JuGeo.

Treaties are stabilized overlap laws that make global gluing possible.  This
module provides a dedicated account of how treaties are **discovered**,
**negotiated**, **remembered**, **reused**, **challenged**, and **retired**.

Semantic negotiation memory tracks which friction patterns produced which law
proposals, enabling the system to learn from past overlap resolution attempts
and accelerate future treaty synthesis.

Theory reference
----------------
theory2.tex §3 – "Treaty synthesis, negotiation memory, and archival semantics"

Design notes
------------
* Every negotiation is a first-class session with provenance.
* Friction patterns are mined from failed negotiations to guide future rounds.
* The copilot integration surfaces compromise suggestions and diagnostics to
  human collaborators in real time.
* Deadlock detection uses structural classification so escalation is targeted,
  not a blanket "give up".
* The treaty archive preserves full evolution history so replay conservativity
  can be verified at any time.
"""

from __future__ import annotations

import hashlib
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Lightweight stubs for upstream types so the module stays importable even when
# the full dependency graph is unavailable (e.g. isolated testing).
# ---------------------------------------------------------------------------
try:
    from jugeo.orchestration.frontier import FrontierItem
except Exception:  # pragma: no cover
    FrontierItem = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import CoordinateObject
except Exception:  # pragma: no cover
    CoordinateObject = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.covers import OverlapDatum
except Exception:  # pragma: no cover
    OverlapDatum = Any  # type: ignore[assignment,misc]

try:
    from jugeo.generation.treaties import OverlapTreaty, TreatyClause
except Exception:  # pragma: no cover
    OverlapTreaty = Any  # type: ignore[assignment,misc]
    TreatyClause = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.manifests import EvidenceManifest
except Exception:  # pragma: no cover
    EvidenceManifest = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import DescentEngine
except Exception:  # pragma: no cover
    DescentEngine = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import CertificateAuthority
except Exception:  # pragma: no cover
    CertificateAuthority = None  # type: ignore[assignment,misc]


# ===================================================================== #
#  Enumerations                                                          #
# ===================================================================== #

class SessionState(Enum):
    """Lifecycle state of a negotiation session."""

    OPEN = auto()
    AGREED = auto()
    DEADLOCKED = auto()
    ABANDONED = auto()


class DeadlockKind(Enum):
    """Structural classification of a deadlock.

    Each variant maps to a different resolution strategy (theory2.tex §3.4).
    """

    EVIDENCE_GAP = auto()
    GUARD_CONFLICT = auto()
    OVERLAP_AMBIGUITY = auto()
    TRUST_MISMATCH = auto()
    RESOURCE_EXHAUSTION = auto()


class NegotiationEventKind(Enum):
    """Events emitted during a negotiation session."""

    PROPOSAL_MADE = auto()
    COUNTER_PROPOSED = auto()
    ACCEPTED = auto()
    REJECTED = auto()
    DEADLOCKED = auto()
    RESOLVED = auto()
    ESCALATED = auto()
    ARCHIVED = auto()


# ===================================================================== #
#  Core data-classes                                                     #
# ===================================================================== #

@dataclass(slots=True)
class TreatyProposal:
    """A single proposal for an overlap law within a negotiation.

    Parameters
    ----------
    proposal_id : str
        Unique identifier for this proposal.
    proposer : str
        Identity of the party that originated the proposal.
    overlap_law : str
        Formal statement of the proposed compatibility law.
    support_evidence : list[str]
        Evidence identifiers backing the proposal.
    guard_conditions : list[str]
        Conditions that must hold for the law to be valid.
    invalidation_triggers : list[str]
        Events or changes that would invalidate this proposal.
    confidence : float
        Proposer's confidence in the law (0.0–1.0).
    revision_of : str | None
        If this revises a prior proposal, the original proposal_id.

    Notes
    -----
    Proposals are first-class semantic objects with provenance so that the
    negotiation archive can later verify replay conservativity (theory2.tex §3.6).
    """

    proposal_id: str = field(default_factory=lambda: f"prop-{uuid.uuid4().hex[:12]}")
    proposer: str = ""
    overlap_law: str = ""
    support_evidence: list[str] = field(default_factory=list)
    guard_conditions: list[str] = field(default_factory=list)
    invalidation_triggers: list[str] = field(default_factory=list)
    confidence: float = 0.5
    revision_of: str | None = None

    # -- helpers ----------------------------------------------------------

    def fingerprint(self) -> str:
        """Content-addressable fingerprint of the law + guards."""
        blob = f"{self.overlap_law}|{'|'.join(sorted(self.guard_conditions))}"
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def strengthen(self, extra_evidence: Sequence[str]) -> TreatyProposal:
        """Return a copy with additional support evidence and raised confidence."""
        new_evidence = list(self.support_evidence) + list(extra_evidence)
        bump = min(1.0, self.confidence + 0.05 * len(extra_evidence))
        return TreatyProposal(
            proposer=self.proposer,
            overlap_law=self.overlap_law,
            support_evidence=new_evidence,
            guard_conditions=list(self.guard_conditions),
            invalidation_triggers=list(self.invalidation_triggers),
            confidence=bump,
            revision_of=self.proposal_id,
        )

    def weaken(self, dropped_guards: Sequence[str]) -> TreatyProposal:
        """Return a copy with fewer guard conditions (less restrictive)."""
        remaining = [g for g in self.guard_conditions if g not in dropped_guards]
        return TreatyProposal(
            proposer=self.proposer,
            overlap_law=self.overlap_law,
            support_evidence=list(self.support_evidence),
            guard_conditions=remaining,
            invalidation_triggers=list(self.invalidation_triggers),
            confidence=max(0.0, self.confidence - 0.05 * len(dropped_guards)),
            revision_of=self.proposal_id,
        )

    def is_compatible_with(self, other: TreatyProposal) -> bool:
        """Check whether two proposals can coexist without guard conflict."""
        my_guards = set(self.guard_conditions)
        their_triggers = set(other.invalidation_triggers)
        return len(my_guards & their_triggers) == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for archival."""
        return {
            "proposal_id": self.proposal_id,
            "proposer": self.proposer,
            "overlap_law": self.overlap_law,
            "support_evidence": self.support_evidence,
            "guard_conditions": self.guard_conditions,
            "invalidation_triggers": self.invalidation_triggers,
            "confidence": self.confidence,
            "revision_of": self.revision_of,
        }


@dataclass(slots=True)
class NegotiationSession:
    """Stateful container for one treaty-negotiation episode.

    Parameters
    ----------
    session_id : str
        Unique session identifier.
    parties : list[str]
        Identities of the negotiating parties (fleet members, oracles, …).
    overlap_coordinate : str
        The coordinate (or coordinate path) of the overlap region under
        negotiation.
    proposed_laws : list[TreatyProposal]
        Proposals tabled so far.
    counter_proposals : list[TreatyProposal]
        Counter-proposals tabled in response to earlier proposals.
    current_state : SessionState
        Lifecycle state of the session.
    rounds_completed : int
        How many proposal / counter-proposal rounds have been executed.
    evidence_exchanged : list[str]
        Evidence record ids exchanged during this session.

    Notes
    -----
    A session progresses through OPEN → AGREED | DEADLOCKED | ABANDONED.
    The negotiation memory (see :class:`NegotiationMemory`) records the
    complete session for future pattern mining.
    """

    session_id: str = field(default_factory=lambda: f"sess-{uuid.uuid4().hex[:12]}")
    parties: list[str] = field(default_factory=list)
    overlap_coordinate: str = ""
    proposed_laws: list[TreatyProposal] = field(default_factory=list)
    counter_proposals: list[TreatyProposal] = field(default_factory=list)
    current_state: SessionState = SessionState.OPEN
    rounds_completed: int = 0
    evidence_exchanged: list[str] = field(default_factory=list)
    _started_at: float = field(default_factory=time.monotonic)
    _provenance: list[str] = field(default_factory=list)

    # -- lifecycle --------------------------------------------------------

    def is_active(self) -> bool:
        """Return True if the session is still accepting proposals."""
        return self.current_state == SessionState.OPEN

    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since the session was created."""
        return time.monotonic() - self._started_at

    def add_proposal(self, proposal: TreatyProposal) -> None:
        """Table a new proposal, recording provenance."""
        self.proposed_laws.append(proposal)
        self._provenance.append(f"proposal:{proposal.proposal_id}")

    def add_counter(self, counter: TreatyProposal) -> None:
        """Table a counter-proposal."""
        self.counter_proposals.append(counter)
        self._provenance.append(f"counter:{counter.proposal_id}")

    def advance_round(self) -> None:
        """Mark one negotiation round as completed."""
        self.rounds_completed += 1
        self._provenance.append(f"round:{self.rounds_completed}")

    def close(self, outcome: SessionState) -> None:
        """Close the session with the given outcome."""
        if outcome == SessionState.OPEN:
            raise ValueError("Cannot close a session with OPEN state.")
        self.current_state = outcome
        self._provenance.append(f"closed:{outcome.name}")

    def best_proposal(self) -> TreatyProposal | None:
        """Return the highest-confidence proposal tabled so far."""
        all_proposals = self.proposed_laws + self.counter_proposals
        if not all_proposals:
            return None
        return max(all_proposals, key=lambda p: p.confidence)

    def provenance_chain(self) -> tuple[str, ...]:
        """Immutable snapshot of the session provenance."""
        return tuple(self._provenance)

    # ── cross-subsystem integration ─────────────────────────────────────

    def treaty_from_descent(
        self, sections: Sequence[Any] | None = None
    ) -> TreatyProposal | None:
        """Derive a treaty proposal from descent conditions.

        Uses :class:`jugeo.geometry.descent.DescentEngine` to compute the
        cocycle / overlap conditions that must hold between the local
        sections involved in this negotiation, then translates those
        conditions into guard clauses for a :class:`TreatyProposal`.

        Parameters
        ----------
        sections
            Local sections participating in the overlap.  If ``None``,
            uses the support evidence references already in the session.

        Returns a :class:`TreatyProposal` whose guard conditions are
        derived from geometric descent, or ``None`` if the engine is
        unavailable.

        Theory ref: theory2.tex §3 — Descent and Gluing.
        """
        if DescentEngine is None:
            return None

        engine = DescentEngine()
        evidence_ids = []
        for prop in self.proposed_laws:
            evidence_ids.extend(prop.support_evidence)

        conditions = engine.overlap_conditions(
            sections=sections or evidence_ids
        )
        guard_conditions = [
            str(getattr(c, "expression", c))
            for c in (conditions if conditions else [])
        ]
        proposal = TreatyProposal(
            proposer="descent_engine",
            overlap_law="gluing_from_descent",
            support_evidence=evidence_ids,
            guard_conditions=guard_conditions,
            confidence=0.8,
        )
        self.add_proposal(proposal)
        return proposal

    def certificate_negotiation(
        self, required_tier: str = "VERIFIED"
    ) -> dict[str, Any]:
        """Negotiate certificate requirements for treaty acceptance.

        Uses :class:`jugeo.evidence.certificates.CertificateAuthority` to
        determine which evidence certificates are needed before the
        current best proposal can be promoted to an accepted treaty.

        Parameters
        ----------
        required_tier
            The minimum trust tier that certificates must attest to.

        Returns a dict summarising the certificate requirements and any
        gaps that must be filled.

        Theory ref: theory2.tex §252 — Evidence Algebra, Certificates.
        """
        if CertificateAuthority is None:
            return {"status": "unavailable", "requirements": []}

        ca = CertificateAuthority()
        best = self.best_proposal()
        if best is None:
            return {"status": "no_proposal", "requirements": []}

        requirements = ca.requirements_for(
            evidence_ids=best.support_evidence,
            tier=required_tier,
        )
        gaps = [
            {
                "evidence_id": getattr(r, "evidence_id", str(r)),
                "needed": getattr(r, "needed_tier", required_tier),
                "current": getattr(r, "current_tier", "UNVERIFIED"),
            }
            for r in (requirements if requirements else [])
            if getattr(r, "is_gap", True)
        ]
        return {
            "status": "ok",
            "requirements": [str(r) for r in (requirements or [])],
            "gaps": gaps,
            "proposal_id": best.proposal_id,
        }


# ===================================================================== #
#  FrictionPattern                                                       #
# ===================================================================== #

@dataclass(slots=True)
class FrictionPattern:
    """Recurring negotiation difficulty mined from session history.

    Friction patterns are the semantic feedback loop that makes the negotiation
    memory useful: they encode *why* certain overlaps are hard to stabilise and
    *what typically works* to resolve them.

    Parameters
    ----------
    pattern_id : str
        Unique identifier.
    description : str
        Human-readable description of the friction.
    frequency : int
        How many times this pattern has been observed.
    typical_resolution : str
        Summary of the strategy that usually resolves this friction.
    typical_evidence_needed : list[str]
        Evidence kinds that help unlock the resolution.
    coordinates_affected : list[str]
        Coordinate paths where this friction most often occurs.
    """

    pattern_id: str = field(default_factory=lambda: f"fp-{uuid.uuid4().hex[:8]}")
    description: str = ""
    frequency: int = 0
    typical_resolution: str = ""
    typical_evidence_needed: list[str] = field(default_factory=list)
    coordinates_affected: list[str] = field(default_factory=list)

    def bump(self) -> None:
        """Increment the frequency counter."""
        self.frequency += 1

    def matches_coordinate(self, coordinate: str) -> bool:
        """Check whether *coordinate* is in the affected set."""
        return any(coordinate.startswith(c) for c in self.coordinates_affected)

    def resolution_confidence(self) -> float:
        """Heuristic confidence that *typical_resolution* will work again.

        Confidence grows logarithmically with observed frequency.
        """
        if self.frequency <= 0:
            return 0.0
        return min(1.0, 0.3 + 0.15 * math.log1p(self.frequency))

    def merge(self, other: FrictionPattern) -> FrictionPattern:
        """Merge two observations of the same underlying friction."""
        coords = list(set(self.coordinates_affected + other.coordinates_affected))
        evidence = list(set(self.typical_evidence_needed + other.typical_evidence_needed))
        return FrictionPattern(
            pattern_id=self.pattern_id,
            description=self.description or other.description,
            frequency=self.frequency + other.frequency,
            typical_resolution=self.typical_resolution or other.typical_resolution,
            typical_evidence_needed=evidence,
            coordinates_affected=coords,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for archival or copilot diagnostics display."""
        return {
            "pattern_id": self.pattern_id,
            "description": self.description,
            "frequency": self.frequency,
            "typical_resolution": self.typical_resolution,
            "typical_evidence_needed": self.typical_evidence_needed,
            "coordinates_affected": self.coordinates_affected,
            "resolution_confidence": round(self.resolution_confidence(), 4),
        }


# ===================================================================== #
#  CompromiseStrategy                                                    #
# ===================================================================== #

class CompromiseStrategy:
    """Strategies for finding agreement between conflicting proposals.

    Each strategy takes one or two proposals and returns a weakened or refined
    version that is more likely to be accepted by both parties.

    The copilot integration (``copilot_suggest_compromise``) combines multiple
    strategies with heuristic scoring to surface the best option to a human
    collaborator.
    """

    @staticmethod
    def weaken_both(
        left: TreatyProposal,
        right: TreatyProposal,
    ) -> tuple[TreatyProposal, TreatyProposal]:
        """Drop the guards from each side that trigger the other's invalidation.

        This is the gentlest compromise: each party gives up the conditions
        that the other party finds unacceptable.
        """
        left_bad = set(left.guard_conditions) & set(right.invalidation_triggers)
        right_bad = set(right.guard_conditions) & set(left.invalidation_triggers)
        return left.weaken(list(left_bad)), right.weaken(list(right_bad))

    @staticmethod
    def strengthen_evidence(
        proposal: TreatyProposal,
        available_evidence: Sequence[str],
    ) -> TreatyProposal:
        """Instead of weakening the law, add more evidence to justify it.

        Stronger evidence can raise confidence above the acceptance threshold
        without changing the law itself.
        """
        new_evidence = [e for e in available_evidence if e not in proposal.support_evidence]
        if not new_evidence:
            return proposal
        return proposal.strengthen(new_evidence)

    @staticmethod
    def split_overlap(
        proposal: TreatyProposal,
        sub_coordinates: Sequence[str],
    ) -> list[TreatyProposal]:
        """Split a single proposal into finer-grained per-sub-coordinate laws.

        When the overlap region is too large for a single law, splitting into
        smaller regions often makes each sub-law easier to negotiate
        (theory2.tex §3.3 – "refine the cover").
        """
        results: list[TreatyProposal] = []
        for coord in sub_coordinates:
            child = TreatyProposal(
                proposer=proposal.proposer,
                overlap_law=f"{proposal.overlap_law} [restricted to {coord}]",
                support_evidence=list(proposal.support_evidence),
                guard_conditions=list(proposal.guard_conditions),
                invalidation_triggers=list(proposal.invalidation_triggers),
                confidence=proposal.confidence * 0.9,
                revision_of=proposal.proposal_id,
            )
            results.append(child)
        return results

    @staticmethod
    def defer_to_finer_cover(
        session: NegotiationSession,
        finer_coordinates: Sequence[str],
    ) -> list[NegotiationSession]:
        """Abandon the current session in favour of finer-grained sessions.

        Returns one new session per sub-coordinate, inheriting the parties and
        evidence of the parent session.
        """
        session.close(SessionState.ABANDONED)
        children: list[NegotiationSession] = []
        for coord in finer_coordinates:
            child = NegotiationSession(
                parties=list(session.parties),
                overlap_coordinate=coord,
                evidence_exchanged=list(session.evidence_exchanged),
            )
            child._provenance.append(f"deferred_from:{session.session_id}")
            children.append(child)
        return children

    @staticmethod
    def copilot_suggest_compromise(
        left: TreatyProposal,
        right: TreatyProposal,
        friction_history: Sequence[FrictionPattern],
    ) -> dict[str, Any]:
        """Copilot-driven compromise suggestion.

        Combines structural analysis of the two proposals with friction-pattern
        history to produce a ranked set of compromise options a human reviewer
        can choose from.

        Returns a diagnostics dictionary suitable for rendering in a copilot
        UI panel.
        """
        options: list[dict[str, Any]] = []

        # Option 1 – mutual weakening
        wl, wr = CompromiseStrategy.weaken_both(left, right)
        options.append({
            "strategy": "weaken_both",
            "left_confidence": round(wl.confidence, 3),
            "right_confidence": round(wr.confidence, 3),
            "guards_dropped": len(left.guard_conditions) - len(wl.guard_conditions)
                              + len(right.guard_conditions) - len(wr.guard_conditions),
        })

        # Option 2 – strengthen the weaker side
        weaker, stronger = (left, right) if left.confidence < right.confidence else (right, left)
        boosted = weaker.strengthen(stronger.support_evidence)
        options.append({
            "strategy": "strengthen_weaker",
            "original_confidence": round(weaker.confidence, 3),
            "boosted_confidence": round(boosted.confidence, 3),
        })

        # Option 3 – friction-informed suggestion
        relevant_frictions = [
            fp for fp in friction_history
            if fp.resolution_confidence() > 0.4
        ]
        if relevant_frictions:
            best_fp = max(relevant_frictions, key=lambda fp: fp.resolution_confidence())
            options.append({
                "strategy": "friction_guided",
                "pattern": best_fp.description,
                "resolution_hint": best_fp.typical_resolution,
                "confidence": round(best_fp.resolution_confidence(), 3),
            })

        return {
            "copilot_compromise_options": options,
            "left_proposal": left.proposal_id,
            "right_proposal": right.proposal_id,
            "recommendation": options[0]["strategy"] if options else "manual_review",
        }


# ===================================================================== #
#  NegotiationMemory                                                     #
# ===================================================================== #

class NegotiationMemory:
    """Persistent memory of all negotiation sessions.

    The memory enables pattern mining: which friction patterns produce which
    law proposals, and which strategies tend to resolve which deadlocks.

    This is the "semantic negotiation memory" described in theory2.tex §3.2.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, NegotiationSession] = {}
        self._friction_index: dict[str, list[FrictionPattern]] = defaultdict(list)
        self._strategy_outcomes: list[dict[str, Any]] = []

    # -- recording --------------------------------------------------------

    def record_session(self, session: NegotiationSession) -> None:
        """Persist a completed (or in-progress) session snapshot."""
        self._sessions[session.session_id] = session

    def record_strategy_outcome(
        self,
        session_id: str,
        strategy_name: str,
        succeeded: bool,
        notes: str = "",
    ) -> None:
        """Record whether a compromise strategy succeeded in a session."""
        self._strategy_outcomes.append({
            "session_id": session_id,
            "strategy": strategy_name,
            "succeeded": succeeded,
            "notes": notes,
            "timestamp": time.time(),
        })

    def record_friction(
        self,
        coordinate: str,
        pattern: FrictionPattern,
    ) -> None:
        """Index a friction pattern under its coordinate."""
        self._friction_index[coordinate].append(pattern)

    # -- recall -----------------------------------------------------------

    def recall_similar(
        self,
        overlap_coordinate: str,
        *,
        max_results: int = 10,
    ) -> list[NegotiationSession]:
        """Find past sessions whose overlap coordinate is a prefix match.

        Prefix matching captures hierarchical coordinate relationships –
        e.g. a session on ``src/auth`` is relevant to ``src/auth/jwt``.
        """
        hits: list[NegotiationSession] = []
        for session in self._sessions.values():
            if (
                session.overlap_coordinate.startswith(overlap_coordinate)
                or overlap_coordinate.startswith(session.overlap_coordinate)
            ):
                hits.append(session)
        hits.sort(key=lambda s: s.rounds_completed, reverse=True)
        return hits[:max_results]

    def friction_patterns(
        self,
        coordinate: str | None = None,
    ) -> list[FrictionPattern]:
        """Retrieve friction patterns, optionally filtered by coordinate."""
        if coordinate is None:
            return [fp for fps in self._friction_index.values() for fp in fps]
        return list(self._friction_index.get(coordinate, []))

    def successful_strategies(
        self,
        coordinate: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return strategy outcomes that succeeded, optionally filtered."""
        results = [so for so in self._strategy_outcomes if so["succeeded"]]
        if coordinate is not None:
            session_ids = {
                s.session_id
                for s in self._sessions.values()
                if s.overlap_coordinate.startswith(coordinate)
            }
            results = [r for r in results if r["session_id"] in session_ids]
        return results

    def failed_approaches(
        self,
        coordinate: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return strategy outcomes that failed."""
        results = [so for so in self._strategy_outcomes if not so["succeeded"]]
        if coordinate is not None:
            session_ids = {
                s.session_id
                for s in self._sessions.values()
                if s.overlap_coordinate.startswith(coordinate)
            }
            results = [r for r in results if r["session_id"] in session_ids]
        return results

    def pattern_match(
        self,
        session: NegotiationSession,
    ) -> list[FrictionPattern]:
        """Match a live session against known friction patterns.

        The copilot uses this to proactively warn a human that the current
        negotiation resembles a historically difficult pattern.
        """
        coord = session.overlap_coordinate
        candidates = self.friction_patterns(coord)
        if not candidates:
            # Broaden search to parent coordinate segments
            parts = coord.split("/")
            while parts and not candidates:
                parts.pop()
                candidates = self.friction_patterns("/".join(parts))
        return sorted(candidates, key=lambda fp: fp.frequency, reverse=True)

    def session_count(self) -> int:
        """Total number of sessions recorded."""
        return len(self._sessions)


# ===================================================================== #
#  DeadlockDetector                                                      #
# ===================================================================== #

class DeadlockDetector:
    """Detects and classifies deadlocks in negotiation sessions.

    When proposals and counter-proposals cycle without convergence the session
    is deadlocked.  This class classifies the deadlock structurally (see
    :class:`DeadlockKind`) and recommends a resolution path.
    """

    def __init__(self, *, max_rounds_before_deadlock: int = 5) -> None:
        self._max_rounds = max_rounds_before_deadlock

    def detect(self, session: NegotiationSession) -> bool:
        """Return True if the session appears deadlocked."""
        if session.current_state != SessionState.OPEN:
            return False
        if session.rounds_completed >= self._max_rounds:
            return True
        # Cycle detection: same fingerprint appears in both proposals and
        # counter-proposals (proposals going in circles).
        proposal_fps = {p.fingerprint() for p in session.proposed_laws}
        counter_fps = {c.fingerprint() for c in session.counter_proposals}
        return len(proposal_fps & counter_fps) > 0

    def classify_deadlock(self, session: NegotiationSession) -> DeadlockKind:
        """Determine the structural kind of deadlock.

        Heuristic rules:
        * If no evidence was exchanged → EVIDENCE_GAP
        * If proposals and counters share guards → GUARD_CONFLICT
        * If more than three distinct laws proposed → OVERLAP_AMBIGUITY
        * If confidence spread is wide → TRUST_MISMATCH
        * Otherwise → RESOURCE_EXHAUSTION (too many rounds, no pattern found)
        """
        if not session.evidence_exchanged:
            return DeadlockKind.EVIDENCE_GAP

        all_proposals = session.proposed_laws + session.counter_proposals
        all_guards: list[str] = []
        for p in all_proposals:
            all_guards.extend(p.guard_conditions)
        guard_counts = Counter(all_guards)
        if guard_counts and guard_counts.most_common(1)[0][1] > 2:
            return DeadlockKind.GUARD_CONFLICT

        distinct_laws = {p.overlap_law for p in all_proposals}
        if len(distinct_laws) > 3:
            return DeadlockKind.OVERLAP_AMBIGUITY

        confidences = [p.confidence for p in all_proposals]
        if confidences and (max(confidences) - min(confidences)) > 0.5:
            return DeadlockKind.TRUST_MISMATCH

        return DeadlockKind.RESOURCE_EXHAUSTION

    def suggest_resolution(
        self,
        session: NegotiationSession,
        kind: DeadlockKind | None = None,
    ) -> str:
        """Suggest a human-readable resolution strategy for the deadlock."""
        if kind is None:
            kind = self.classify_deadlock(session)
        suggestions: dict[DeadlockKind, str] = {
            DeadlockKind.EVIDENCE_GAP: (
                "Exchange evidence before further rounds.  Consider requesting "
                "an evidence manifest from each party."
            ),
            DeadlockKind.GUARD_CONFLICT: (
                "The same guard conditions recur in opposing proposals.  Try "
                "CompromiseStrategy.weaken_both() to relax conflicting guards."
            ),
            DeadlockKind.OVERLAP_AMBIGUITY: (
                "Too many distinct overlap laws proposed.  Refine the cover: "
                "split the overlap into finer sub-coordinates and negotiate "
                "each independently."
            ),
            DeadlockKind.TRUST_MISMATCH: (
                "Large confidence disparity across proposals.  Align trust by "
                "exchanging provenance chains and validating evidence sources."
            ),
            DeadlockKind.RESOURCE_EXHAUSTION: (
                "The session has used too many rounds without convergence.  "
                "Escalate to a human mediator or invoke copilot_break_deadlock."
            ),
        }
        return suggestions.get(kind, "No specific suggestion available.")

    def escalation_path(self, session: NegotiationSession) -> list[str]:
        """Return an ordered sequence of escalation steps.

        The copilot renders this path so a human collaborator can decide how
        far to escalate.
        """
        kind = self.classify_deadlock(session)
        base: list[str] = [
            f"1. Re-attempt with strategy suited to {kind.name}",
            "2. Invoke copilot_break_deadlock for automated mediation",
            "3. Escalate to human mediator",
        ]
        if kind == DeadlockKind.OVERLAP_AMBIGUITY:
            base.insert(1, "1b. Refine cover to create finer overlap regions")
        if kind == DeadlockKind.EVIDENCE_GAP:
            base.insert(0, "0. Request additional evidence from all parties")
        return base

    def copilot_break_deadlock(
        self,
        session: NegotiationSession,
        memory: NegotiationMemory,
    ) -> dict[str, Any]:
        """Copilot-driven deadlock resolution.

        Combines the structural classification with negotiation memory to
        produce a concrete action plan.
        """
        kind = self.classify_deadlock(session)
        suggestion = self.suggest_resolution(session, kind)
        past_frictions = memory.pattern_match(session)
        successful = memory.successful_strategies(session.overlap_coordinate)

        recommended_strategy: str = "manual_review"
        if successful:
            recommended_strategy = successful[0].get("strategy", "manual_review")
        elif past_frictions:
            recommended_strategy = past_frictions[0].typical_resolution or "manual_review"

        return {
            "deadlock_kind": kind.name,
            "suggestion": suggestion,
            "recommended_strategy": recommended_strategy,
            "related_friction_patterns": [fp.to_dict() for fp in past_frictions[:3]],
            "escalation_path": self.escalation_path(session),
            "copilot_confidence": round(
                0.6 if successful else 0.3,
                2,
            ),
        }


# ===================================================================== #
#  Negotiator (main orchestrator)                                        #
# ===================================================================== #

class Negotiator:
    """Main orchestrator for treaty negotiations.

    The Negotiator owns the lifecycle of :class:`NegotiationSession` objects
    and coordinates proposals, counter-proposals, acceptance, rejection, and
    mediation.

    Parameters
    ----------
    memory : NegotiationMemory
        Persistent negotiation memory for pattern mining.
    deadlock_detector : DeadlockDetector | None
        Optional custom deadlock detector; a default is created if omitted.
    event_bus : NegotiationEventBus | None
        Optional event bus for publishing negotiation events.
    """

    def __init__(
        self,
        memory: NegotiationMemory | None = None,
        deadlock_detector: DeadlockDetector | None = None,
        event_bus: NegotiationEventBus | None = None,
    ) -> None:
        self._memory = memory or NegotiationMemory()
        self._detector = deadlock_detector or DeadlockDetector()
        self._bus = event_bus or NegotiationEventBus()
        self._active_sessions: dict[str, NegotiationSession] = {}

    # -- session lifecycle ------------------------------------------------

    def open_session(
        self,
        parties: Sequence[str],
        overlap_coordinate: str,
    ) -> NegotiationSession:
        """Create and register a new negotiation session."""
        session = NegotiationSession(
            parties=list(parties),
            overlap_coordinate=overlap_coordinate,
        )
        self._active_sessions[session.session_id] = session
        self._memory.record_session(session)
        return session

    def propose(
        self,
        session_id: str,
        proposal: TreatyProposal,
    ) -> bool:
        """Table a proposal in the given session.

        Returns True if the proposal was accepted for consideration.
        """
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_active():
            return False
        session.add_proposal(proposal)
        self._bus.emit(NegotiationEventKind.PROPOSAL_MADE, {
            "session_id": session_id,
            "proposal_id": proposal.proposal_id,
        })
        return True

    def counter_propose(
        self,
        session_id: str,
        counter: TreatyProposal,
    ) -> bool:
        """Table a counter-proposal in response to an existing proposal."""
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_active():
            return False
        session.add_counter(counter)
        self._bus.emit(NegotiationEventKind.COUNTER_PROPOSED, {
            "session_id": session_id,
            "proposal_id": counter.proposal_id,
        })
        return True

    def accept(self, session_id: str, proposal_id: str) -> bool:
        """Accept a proposal, closing the session with AGREED.

        All parties implicitly agree when ``accept`` is called on the winning
        proposal.
        """
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_active():
            return False
        all_proposals = session.proposed_laws + session.counter_proposals
        match = [p for p in all_proposals if p.proposal_id == proposal_id]
        if not match:
            return False
        session.close(SessionState.AGREED)
        self._bus.emit(NegotiationEventKind.ACCEPTED, {
            "session_id": session_id,
            "accepted_proposal": proposal_id,
        })
        self._memory.record_session(session)
        return True

    def reject(self, session_id: str, proposal_id: str, reason: str = "") -> bool:
        """Reject a specific proposal (the session stays OPEN)."""
        session = self._active_sessions.get(session_id)
        if session is None or not session.is_active():
            return False
        session.advance_round()
        self._bus.emit(NegotiationEventKind.REJECTED, {
            "session_id": session_id,
            "rejected_proposal": proposal_id,
            "reason": reason,
        })
        # Check for deadlock after rejection
        if self._detector.detect(session):
            session.close(SessionState.DEADLOCKED)
            self._bus.emit(NegotiationEventKind.DEADLOCKED, {
                "session_id": session_id,
            })
        self._memory.record_session(session)
        return True

    def mediate(self, session_id: str) -> dict[str, Any]:
        """Run an automated mediation pass over the session.

        Returns a report with compromise suggestions.
        """
        session = self._active_sessions.get(session_id)
        if session is None:
            return {"error": "session not found"}
        if not session.proposed_laws:
            return {"error": "no proposals to mediate"}

        best = session.best_proposal()
        friction = self._memory.pattern_match(session)

        report: dict[str, Any] = {
            "session_id": session_id,
            "rounds_completed": session.rounds_completed,
            "best_proposal": best.to_dict() if best else None,
            "friction_patterns": [fp.to_dict() for fp in friction[:3]],
        }

        if len(session.proposed_laws) >= 2:
            left, right = session.proposed_laws[-2], session.proposed_laws[-1]
            report["compromise"] = CompromiseStrategy.copilot_suggest_compromise(
                left, right, friction,
            )

        return report

    def find_compromise(
        self,
        session_id: str,
    ) -> TreatyProposal | None:
        """Attempt to automatically find a compromise proposal.

        Uses :class:`CompromiseStrategy` heuristics.  Returns the compromise
        proposal if one was synthesised, or None.
        """
        session = self._active_sessions.get(session_id)
        if session is None or len(session.proposed_laws) < 2:
            return None
        left = session.proposed_laws[-2]
        right = session.proposed_laws[-1]
        wl, wr = CompromiseStrategy.weaken_both(left, right)
        # Build a merged proposal from weakened versions
        merged_guards = list(set(wl.guard_conditions + wr.guard_conditions))
        merged_evidence = list(set(wl.support_evidence + wr.support_evidence))
        compromise = TreatyProposal(
            proposer="negotiator:auto_compromise",
            overlap_law=wl.overlap_law if wl.confidence >= wr.confidence else wr.overlap_law,
            support_evidence=merged_evidence,
            guard_conditions=merged_guards,
            invalidation_triggers=list(set(
                wl.invalidation_triggers + wr.invalidation_triggers
            )),
            confidence=(wl.confidence + wr.confidence) / 2.0,
            revision_of=left.proposal_id,
        )
        session.add_proposal(compromise)
        self._bus.emit(NegotiationEventKind.PROPOSAL_MADE, {
            "session_id": session_id,
            "proposal_id": compromise.proposal_id,
            "auto_compromise": True,
        })
        return compromise

    def escalate_to_human(self, session_id: str, reason: str = "") -> dict[str, Any]:
        """Mark the session for human review and return a summary.

        The copilot can present this summary in the UI so the human has full
        context.
        """
        session = self._active_sessions.get(session_id)
        if session is None:
            return {"error": "session not found"}
        self._bus.emit(NegotiationEventKind.ESCALATED, {
            "session_id": session_id,
            "reason": reason,
        })
        return {
            "session_id": session_id,
            "overlap_coordinate": session.overlap_coordinate,
            "parties": session.parties,
            "rounds_completed": session.rounds_completed,
            "proposals_count": len(session.proposed_laws),
            "counters_count": len(session.counter_proposals),
            "escalation_reason": reason,
            "provenance": session.provenance_chain(),
        }

    def copilot_mediate(self, session_id: str) -> dict[str, Any]:
        """Copilot-driven mediation combining memory, deadlock detection, and
        compromise synthesis into a single actionable recommendation.

        This is the primary entry-point for the copilot integration: it returns
        a structured report that the copilot UI renders as an interactive
        negotiation panel.
        """
        session = self._active_sessions.get(session_id)
        if session is None:
            return {"error": "session not found"}

        report: dict[str, Any] = {"session_id": session_id}

        # 1. Deadlock check
        if self._detector.detect(session):
            report["deadlock"] = self._detector.copilot_break_deadlock(
                session, self._memory,
            )

        # 2. Mediation
        report["mediation"] = self.mediate(session_id)

        # 3. Compromise attempt
        compromise = self.find_compromise(session_id)
        if compromise is not None:
            report["auto_compromise"] = compromise.to_dict()

        # 4. Historical context
        similar = self._memory.recall_similar(session.overlap_coordinate, max_results=3)
        report["similar_past_sessions"] = [
            {
                "session_id": s.session_id,
                "outcome": s.current_state.name,
                "rounds": s.rounds_completed,
            }
            for s in similar
            if s.session_id != session_id
        ]

        report["copilot_recommendation"] = self._synthesise_recommendation(report)
        return report

    def _synthesise_recommendation(self, report: dict[str, Any]) -> str:
        """Synthesise a single human-readable recommendation from the report."""
        if "deadlock" in report:
            kind = report["deadlock"].get("deadlock_kind", "UNKNOWN")
            return f"Session is deadlocked ({kind}).  Consider: {report['deadlock'].get('suggestion', 'escalate')}."
        if "auto_compromise" in report:
            conf = report["auto_compromise"].get("confidence", 0)
            return f"Auto-compromise available (confidence {conf:.2f}).  Review and accept or refine."
        return "Session is progressing.  No immediate action needed."

    # ── cross-subsystem integration ─────────────────────────────────────

    def treaty_from_descent(
        self, session_id: str, sections: Sequence[Any] | None = None
    ) -> TreatyProposal | None:
        """Derive a treaty proposal from descent conditions for a session.

        Delegates to :meth:`NegotiationSession.treaty_from_descent` on
        the named session, using :class:`DescentEngine` to compute
        overlap conditions and translate them into treaty guard clauses.

        Theory ref: theory2.tex §3 — Descent and Gluing.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return session.treaty_from_descent(sections)

    def certificate_negotiation(
        self, session_id: str, required_tier: str = "VERIFIED"
    ) -> dict[str, Any]:
        """Negotiate certificate requirements for a session's best proposal.

        Delegates to :meth:`NegotiationSession.certificate_negotiation`,
        using :class:`CertificateAuthority` to identify evidence gaps.

        Theory ref: theory2.tex §252 — Evidence Algebra, Certificates.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"status": "session_not_found", "requirements": []}
        return session.certificate_negotiation(required_tier)


# ===================================================================== #
#  NegotiationHistory                                                    #
# ===================================================================== #

class NegotiationHistory:
    """Aggregate analytics over all recorded negotiation sessions.

    Whereas :class:`NegotiationMemory` is the raw store, this class provides
    analytical views used by the copilot diagnostics panel.
    """

    def __init__(self, memory: NegotiationMemory) -> None:
        self._memory = memory

    def record(self, session: NegotiationSession) -> None:
        """Convenience pass-through to the underlying memory."""
        self._memory.record_session(session)

    def by_coordinate(self, coordinate: str) -> list[NegotiationSession]:
        """All sessions that involved the given overlap coordinate."""
        return self._memory.recall_similar(coordinate, max_results=100)

    def by_outcome(self, state: SessionState) -> list[NegotiationSession]:
        """Filter all sessions by their outcome state."""
        return [
            s for s in self._memory._sessions.values()
            if s.current_state == state
        ]

    def success_rate(self) -> float:
        """Fraction of sessions that ended in AGREED."""
        total = self._memory.session_count()
        if total == 0:
            return 0.0
        agreed = sum(
            1 for s in self._memory._sessions.values()
            if s.current_state == SessionState.AGREED
        )
        return agreed / total

    def average_rounds(self) -> float:
        """Mean number of rounds across all sessions."""
        rounds = [s.rounds_completed for s in self._memory._sessions.values()]
        if not rounds:
            return 0.0
        return statistics.mean(rounds)

    def common_patterns(self, *, top_n: int = 5) -> list[FrictionPattern]:
        """Most frequently observed friction patterns."""
        all_fps = self._memory.friction_patterns()
        all_fps.sort(key=lambda fp: fp.frequency, reverse=True)
        return all_fps[:top_n]

    def outcome_distribution(self) -> dict[str, int]:
        """Count of sessions by outcome state."""
        counter: dict[str, int] = defaultdict(int)
        for s in self._memory._sessions.values():
            counter[s.current_state.name] += 1
        return dict(counter)


# ===================================================================== #
#  TreatyArchive                                                         #
# ===================================================================== #

class TreatyArchive:
    """Long-term archive of completed treaties.

    The archive preserves the full evolution history of every treaty so that
    replay conservativity can be verified at any point (theory2.tex §3.6).
    Active treaties are those that are currently enforced; retired treaties
    remain in the archive with a retirement timestamp and reason.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def archive(
        self,
        session: NegotiationSession,
        accepted_proposal: TreatyProposal,
    ) -> str:
        """Archive a completed treaty, returning the archive key."""
        key = f"treaty-{accepted_proposal.fingerprint()}"
        self._store[key] = {
            "key": key,
            "session_id": session.session_id,
            "proposal": accepted_proposal.to_dict(),
            "overlap_coordinate": session.overlap_coordinate,
            "parties": list(session.parties),
            "rounds": session.rounds_completed,
            "archived_at": time.time(),
            "retired_at": None,
            "retirement_reason": None,
            "challenges": [],
            "provenance": session.provenance_chain(),
        }
        return key

    def retrieve(self, key: str) -> dict[str, Any] | None:
        """Retrieve a treaty record by archive key."""
        return self._store.get(key)

    def by_overlap(self, coordinate: str) -> list[dict[str, Any]]:
        """Find treaties whose overlap coordinate matches *coordinate*."""
        return [
            t for t in self._store.values()
            if t["overlap_coordinate"] == coordinate
            or t["overlap_coordinate"].startswith(coordinate)
            or coordinate.startswith(t["overlap_coordinate"])
        ]

    def by_parties(self, party: str) -> list[dict[str, Any]]:
        """Find treaties where *party* was one of the negotiating parties."""
        return [
            t for t in self._store.values()
            if party in t["parties"]
        ]

    def active_treaties(self) -> list[dict[str, Any]]:
        """Return treaties that have not been retired."""
        return [t for t in self._store.values() if t["retired_at"] is None]

    def retired_treaties(self) -> list[dict[str, Any]]:
        """Return treaties that have been retired."""
        return [t for t in self._store.values() if t["retired_at"] is not None]

    def challenge_treaty(
        self,
        key: str,
        challenger: str,
        reason: str,
        new_evidence: Sequence[str] | None = None,
    ) -> bool:
        """Challenge an existing treaty.

        Challenges are recorded in the treaty's evolution history.  The
        challenge may lead to treaty retirement or renegotiation; that is
        handled by the :class:`Negotiator`.

        Returns True if the challenge was recorded.
        """
        treaty = self._store.get(key)
        if treaty is None:
            return False
        treaty["challenges"].append({
            "challenger": challenger,
            "reason": reason,
            "evidence": list(new_evidence or []),
            "timestamp": time.time(),
        })
        return True

    def retire_treaty(self, key: str, reason: str) -> bool:
        """Retire a treaty, marking it as no longer enforced."""
        treaty = self._store.get(key)
        if treaty is None or treaty["retired_at"] is not None:
            return False
        treaty["retired_at"] = time.time()
        treaty["retirement_reason"] = reason
        return True

    def treaty_count(self) -> int:
        """Total number of treaties (active + retired)."""
        return len(self._store)

    def evolution_history(self, key: str) -> list[dict[str, Any]]:
        """Return the full challenge / retirement history for a treaty."""
        treaty = self._store.get(key)
        if treaty is None:
            return []
        history: list[dict[str, Any]] = list(treaty["challenges"])
        if treaty["retired_at"] is not None:
            history.append({
                "event": "retired",
                "reason": treaty["retirement_reason"],
                "timestamp": treaty["retired_at"],
            })
        return history


# ===================================================================== #
#  NegotiationEventBus                                                   #
# ===================================================================== #

class NegotiationEventBus:
    """Lightweight in-process event bus for negotiation lifecycle events.

    Subscribers register callbacks keyed by :class:`NegotiationEventKind`.
    Events are dispatched synchronously in registration order.
    """

    def __init__(self) -> None:
        self._subscribers: dict[
            NegotiationEventKind, list[Callable[[dict[str, Any]], None]]
        ] = defaultdict(list)
        self._event_log: list[dict[str, Any]] = []

    def subscribe(
        self,
        kind: NegotiationEventKind,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Register *callback* to be invoked when *kind* is emitted."""
        self._subscribers[kind].append(callback)

    def unsubscribe(
        self,
        kind: NegotiationEventKind,
        callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Remove *callback* from the subscriber list for *kind*."""
        subs = self._subscribers.get(kind, [])
        if callback in subs:
            subs.remove(callback)

    def emit(self, kind: NegotiationEventKind, payload: dict[str, Any]) -> None:
        """Emit an event, dispatching to all registered subscribers."""
        event_record = {
            "kind": kind.name,
            "payload": payload,
            "timestamp": time.time(),
        }
        self._event_log.append(event_record)
        for callback in self._subscribers.get(kind, []):
            callback(payload)

    def event_log(self, *, kind: NegotiationEventKind | None = None) -> list[dict[str, Any]]:
        """Return the event log, optionally filtered by kind."""
        if kind is None:
            return list(self._event_log)
        return [e for e in self._event_log if e["kind"] == kind.name]

    def clear_log(self) -> None:
        """Clear the event log (subscribers are not affected)."""
        self._event_log.clear()

    def subscriber_count(self, kind: NegotiationEventKind) -> int:
        """Number of subscribers registered for *kind*."""
        return len(self._subscribers.get(kind, []))


# ===================================================================== #
#  NegotiationDiagnostics                                                #
# ===================================================================== #

class NegotiationDiagnostics:
    """Diagnostics and reporting for the negotiation subsystem.

    Designed to be consumed by the copilot UI to give human collaborators
    real-time visibility into treaty-negotiation health.
    """

    def __init__(
        self,
        history: NegotiationHistory,
        archive: TreatyArchive,
        event_bus: NegotiationEventBus,
    ) -> None:
        self._history = history
        self._archive = archive
        self._bus = event_bus

    def session_summary(self, session: NegotiationSession) -> dict[str, Any]:
        """Produce a concise summary of a single session."""
        return {
            "session_id": session.session_id,
            "state": session.current_state.name,
            "parties": session.parties,
            "overlap_coordinate": session.overlap_coordinate,
            "rounds_completed": session.rounds_completed,
            "proposals": len(session.proposed_laws),
            "counter_proposals": len(session.counter_proposals),
            "evidence_exchanged": len(session.evidence_exchanged),
            "elapsed_seconds": round(session.elapsed_seconds(), 2),
            "best_confidence": round(
                session.best_proposal().confidence if session.best_proposal() else 0.0,
                3,
            ),
        }

    def friction_report(self, *, top_n: int = 10) -> dict[str, Any]:
        """Aggregate friction report across all recorded negotiations."""
        patterns = self._history.common_patterns(top_n=top_n)
        total_friction = sum(fp.frequency for fp in patterns)
        return {
            "total_patterns": len(patterns),
            "total_friction_events": total_friction,
            "top_patterns": [fp.to_dict() for fp in patterns],
            "avg_resolution_confidence": round(
                statistics.mean(fp.resolution_confidence() for fp in patterns)
                if patterns else 0.0,
                3,
            ),
        }

    def success_analysis(self) -> dict[str, Any]:
        """Analyse overall negotiation success and failure rates."""
        rate = self._history.success_rate()
        avg_rounds = self._history.average_rounds()
        dist = self._history.outcome_distribution()
        active = self._archive.active_treaties()
        retired = self._archive.retired_treaties()
        return {
            "success_rate": round(rate, 3),
            "average_rounds": round(avg_rounds, 2),
            "outcome_distribution": dist,
            "active_treaties": len(active),
            "retired_treaties": len(retired),
            "total_treaty_challenges": sum(
                len(t.get("challenges", [])) for t in active + retired
            ),
        }

    def copilot_negotiation_summary(self) -> dict[str, Any]:
        """Full copilot-ready diagnostic summary.

        Combines session analytics, friction patterns, treaty archive stats,
        and recent event log into a single payload the copilot renders in the
        negotiation health panel.
        """
        recent_events = self._bus.event_log()[-20:]
        success = self.success_analysis()
        friction = self.friction_report(top_n=5)

        event_kind_counts: dict[str, int] = defaultdict(int)
        for ev in self._bus.event_log():
            event_kind_counts[ev["kind"]] += 1

        return {
            "copilot_panel": "negotiation_health",
            "success": success,
            "friction": friction,
            "recent_events": recent_events,
            "event_kind_counts": dict(event_kind_counts),
            "recommendations": self._generate_recommendations(success, friction),
        }

    def _generate_recommendations(
        self,
        success: dict[str, Any],
        friction: dict[str, Any],
    ) -> list[str]:
        """Generate human-readable recommendations based on diagnostics."""
        recs: list[str] = []
        rate = success.get("success_rate", 0.0)
        if rate < 0.5:
            recs.append(
                "Negotiation success rate is below 50%.  Review common friction "
                "patterns and consider refining cover design."
            )
        avg = success.get("average_rounds", 0.0)
        if avg > 4:
            recs.append(
                f"Average round count is {avg:.1f}.  Consider lowering the "
                "deadlock threshold or introducing early mediation."
            )
        challenges = success.get("total_treaty_challenges", 0)
        if challenges > 5:
            recs.append(
                f"{challenges} treaty challenges recorded.  Audit challenge "
                "reasons to identify systemic treaty weakness."
            )
        top_fps = friction.get("top_patterns", [])
        if top_fps:
            worst = top_fps[0]
            recs.append(
                f"Most common friction: \"{worst.get('description', '?')}\" "
                f"(observed {worst.get('frequency', 0)} times).  "
                f"Typical resolution: {worst.get('typical_resolution', 'unknown')}."
            )
        if not recs:
            recs.append("All negotiation metrics are within healthy ranges.")
        return recs

    def coordinate_health(self, coordinate: str) -> dict[str, Any]:
        """Health report for negotiations around a specific coordinate."""
        sessions = self._history.by_coordinate(coordinate)
        agreed = [s for s in sessions if s.current_state == SessionState.AGREED]
        deadlocked = [s for s in sessions if s.current_state == SessionState.DEADLOCKED]
        treaties = self._archive.by_overlap(coordinate)
        return {
            "coordinate": coordinate,
            "total_sessions": len(sessions),
            "agreed": len(agreed),
            "deadlocked": len(deadlocked),
            "active_treaties": len([t for t in treaties if t["retired_at"] is None]),
            "retired_treaties": len([t for t in treaties if t["retired_at"] is not None]),
        }


# ===================================================================== #
#  Legacy compatibility shim                                             #
# ===================================================================== #
# The original negotiation.py exposed NegotiationPosition and
# NegotiationRound.  We preserve them here so existing imports continue
# to work.

@dataclass(frozen=True, slots=True)
class NegotiationPosition:
    """A fleet member's bid on a frontier item (legacy API)."""

    member_name: str
    item: FrontierItem
    offer: int


@dataclass(frozen=True, slots=True)
class NegotiationRound:
    """A single round of bidding on a frontier item (legacy API)."""

    positions: tuple[NegotiationPosition, ...]

    def resolve(self) -> str | None:
        """Return the member name with the highest offer, or None."""
        if not self.positions:
            return None
        best = max(self.positions, key=lambda position: position.offer)
        return best.member_name


# ===================================================================== #
#  Module exports                                                        #
# ===================================================================== #

__all__ = [
    # Enumerations
    "SessionState",
    "DeadlockKind",
    "NegotiationEventKind",
    # Core data-classes
    "TreatyProposal",
    "NegotiationSession",
    "FrictionPattern",
    # Strategy & detection
    "CompromiseStrategy",
    "DeadlockDetector",
    # Orchestration
    "Negotiator",
    "NegotiationMemory",
    "NegotiationHistory",
    # Archive & events
    "TreatyArchive",
    "NegotiationEventBus",
    # Diagnostics
    "NegotiationDiagnostics",
    # Legacy
    "NegotiationPosition",
    "NegotiationRound",
]

# copilot: shared-core marker for future LLM orchestration.
