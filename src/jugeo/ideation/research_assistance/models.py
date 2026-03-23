r"""Core domain models for JuGeo research assistance — Chapter 51.

This module defines the immutable and mutable data structures that flow
through every stage of the research assistance pipeline: proof suggestion,
lemma mining, conjecture generation, and oracle interaction.

Mathematical framing
--------------------

A *research session* :math:`S` is a tuple
:math:`(ctx, H, Q, \sigma)` where:

- :math:`ctx` — the :class:`ResearchContext` describing the current proof state.
- :math:`H` — the ordered sequence of :class:`ProofSuggestion` instances applied
  so far (the proof *history*).
- :math:`Q` — the set of open :class:`ConjectureRecord` instances.
- :math:`\sigma \in \{\text{ACTIVE}, \text{PAUSED}, \text{COMPLETED},
  \text{ABANDONED}\}` — the session lifecycle status.

All state transitions are monotone: ACTIVE → COMPLETED (or ABANDONED) are
terminal, and VERIFIED lemmas never revert to PENDING (Thm 51.6, 51.8).
"""

from __future__ import annotations

import logging
import math
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float to a closed interval."""
    return max(lo, min(hi, float(value)))


def _tokenize(text: str) -> set[str]:
    """Return the set of lowercase alphanumeric tokens of length >= 2."""
    return {t.lower() for t in re.split(r"[^a-zA-Z0-9]+", text) if len(t) >= 2}


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class VerificationStatus(str, Enum):
    """Lifecycle status of a verification attempt on a research artifact.

    PENDING means the artifact has not yet been submitted to the formal
    verifier.  VERIFIED means the verifier accepted it.  FAILED means it was
    rejected.  SKIPPED means verification was explicitly waived.
    """

    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"


class LemmaSource(str, Enum):
    """Records how a :class:`LemmaCandidate` was discovered.

    MINED means it was extracted from an existing theorem archive.
    ORACLE means a copilot oracle proposed it.
    MANUAL means a human researcher supplied it directly.
    """

    MINED = "mined"
    ORACLE = "oracle"
    MANUAL = "manual"


class ConjectureStatus(str, Enum):
    """Lifecycle status of a :class:`ConjectureRecord`.

    OPEN is the initial state.  VERIFIED means a formal proof was found.
    FALSIFIED means a counterexample was found; this is irreversible.
    ARCHIVED means the conjecture was deferred without resolution.
    """

    OPEN = "open"
    VERIFIED = "verified"
    FALSIFIED = "falsified"
    ARCHIVED = "archived"


class SessionStatus(str, Enum):
    """Lifecycle status of a :class:`ResearchSession`.

    ACTIVE is the initial operating state.  PAUSED means work is suspended.
    COMPLETED means the session ended successfully.  ABANDONED means it was
    terminated without completion.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


# ---------------------------------------------------------------------------
# LemmaCandidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LemmaCandidate:
    """An immutable record representing a candidate auxiliary lemma.

    Lemma candidates flow from the mining or oracle subsystem into the proof
    engine.  They carry a relevance score and a verification status that
    progresses monotonically toward VERIFIED or FAILED.

    Attributes:
        candidate_id: Stable unique identifier.
        statement: Formal statement of the lemma (mathematical text).
        proof_sketch: Informal or partial proof of the lemma.
        relevance_score: Score in [0, 1] expressing relevance to the current
            research context.
        source: How this candidate was discovered.
        verification_status: Current verification lifecycle status.
        dependencies: Identifiers of other lemmas this one depends on.
        tags: Descriptive tags for indexing and retrieval.
    """

    candidate_id: str
    statement: str
    proof_sketch: str
    relevance_score: float
    source: LemmaSource
    verification_status: VerificationStatus = VerificationStatus.PENDING
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)

    def is_verified(self) -> bool:
        """Return True if this lemma has been formally verified."""
        return self.verification_status == VerificationStatus.VERIFIED

    def to_summary(self) -> str:
        """Return a compact one-line description."""
        return (
            f"Lemma[{self.candidate_id[:8]}] score={self.relevance_score:.2f} "
            f"status={self.verification_status.value} source={self.source.value} "
            f"stmt={self.statement[:50]!r}"
        )

    def with_verification(self, status: VerificationStatus) -> LemmaCandidate:
        """Return a new LemmaCandidate with the verification status updated."""
        return replace(self, verification_status=status)


# ---------------------------------------------------------------------------
# ConjectureRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ConjectureRecord:
    """A mutable record representing a mathematical conjecture under investigation.

    Conjectures accumulate supporting evidence and falsification attempts over
    time.  The confidence score evolves monotonically (upward via evidence,
    downward to zero via falsification).

    Attributes:
        conjecture_id: Stable unique identifier.
        statement: Informal or formal conjecture statement.
        supporting_evidence: List of evidence strings supporting the conjecture.
        falsification_attempts: List of failed falsification attempt descriptions.
        confidence: Current confidence score in [0, 1].
        status: Current lifecycle status.
    """

    conjecture_id: str
    statement: str
    supporting_evidence: list[str] = field(default_factory=list)
    falsification_attempts: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: ConjectureStatus = ConjectureStatus.OPEN

    def add_evidence(self, evidence: str) -> None:
        """Append a piece of supporting evidence and nudge confidence upward."""
        self.supporting_evidence.append(evidence)
        self.confidence = _clamp(self.confidence + 0.05)
        _log.debug("ConjectureRecord %s: added evidence, confidence=%.3f", self.conjecture_id, self.confidence)

    def falsify(self, reason: str) -> None:
        """Record a falsification; sets status to FALSIFIED and confidence to 0."""
        self.falsification_attempts.append(reason)
        self.status = ConjectureStatus.FALSIFIED
        self.confidence = 0.0
        _log.info("ConjectureRecord %s: falsified with reason=%r", self.conjecture_id, reason[:60])

    def strengthen(self, delta: float) -> None:
        """Increase confidence by delta, clamped to [0, 1]."""
        self.confidence = _clamp(self.confidence + delta)

    def to_summary(self) -> str:
        """Return a compact one-line description."""
        return (
            f"Conjecture[{self.conjecture_id[:8]}] status={self.status.value} "
            f"conf={self.confidence:.2f} evidence={len(self.supporting_evidence)} "
            f"stmt={self.statement[:50]!r}"
        )


# ---------------------------------------------------------------------------
# ProofSuggestion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProofSuggestion:
    """An immutable suggested next step in an ongoing proof.

    Suggestions are produced by the proof suggestion engine and optionally
    filtered by the formal verifier before being presented to the researcher.

    Attributes:
        suggestion_id: Stable unique identifier.
        tactic_description: Human-readable description of the suggested tactic.
        target_goal: The proof goal this suggestion targets.
        confidence: Confidence score in [0, 1].
        justification: Explanation of why this tactic was chosen.
        oracle_source: Identifier of the oracle that generated this suggestion.
        verification_status: Whether the suggestion was formally verified.
    """

    suggestion_id: str
    tactic_description: str
    target_goal: str
    confidence: float
    justification: str
    oracle_source: str
    verification_status: VerificationStatus = VerificationStatus.PENDING

    def apply(self) -> ProofSuggestion:
        """Return a new ProofSuggestion with status set to VERIFIED."""
        return replace(self, verification_status=VerificationStatus.VERIFIED)

    def reject(self) -> ProofSuggestion:
        """Return a new ProofSuggestion with status set to FAILED."""
        return replace(self, verification_status=VerificationStatus.FAILED)

    def to_summary(self) -> str:
        """Return a compact one-line description."""
        return (
            f"Suggestion[{self.suggestion_id[:8]}] tactic={self.tactic_description!r} "
            f"conf={self.confidence:.2f} status={self.verification_status.value}"
        )


# ---------------------------------------------------------------------------
# ResearchContext
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResearchContext:
    """Mutable record holding the current state of a research effort.

    A context is threaded through the proof suggestion, lemma mining, and
    conjecture generation sub-systems.  It grows monotonically: lemmas are
    only added, never removed (Thm 51.12).

    Attributes:
        context_id: Stable unique identifier.
        current_theorem: The theorem currently being proved or explored.
        partial_proof: The proof developed so far (may be empty).
        available_lemmas: Lemmas that have been surfaced for this context.
        purpose: High-level research purpose statement.
        constraints: Additional constraints on the proof search.
    """

    context_id: str
    current_theorem: str
    partial_proof: str = ""
    available_lemmas: list[LemmaCandidate] = field(default_factory=list)
    purpose: str = ""
    constraints: tuple[str, ...] = field(default_factory=tuple)

    def add_lemma(self, lemma: LemmaCandidate) -> None:
        """Add a lemma candidate if not already present (keyed by candidate_id)."""
        existing_ids = {c.candidate_id for c in self.available_lemmas}
        if lemma.candidate_id not in existing_ids:
            self.available_lemmas.append(lemma)
            _log.debug(
                "ResearchContext %s: added lemma %s",
                self.context_id,
                lemma.candidate_id,
            )

    def update_proof_state(self, new_state: str) -> None:
        """Replace the current partial proof with a new state string."""
        self.partial_proof = new_state

    def relevant_lemmas(self, query: str) -> list[LemmaCandidate]:
        """Return lemmas whose statement shares at least one token with query."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return list(self.available_lemmas)
        return [
            lemma
            for lemma in self.available_lemmas
            if query_tokens & _tokenize(lemma.statement)
        ]

    def to_summary(self) -> str:
        """Return a compact one-line description."""
        return (
            f"Context[{self.context_id[:8]}] theorem={self.current_theorem[:40]!r} "
            f"lemmas={len(self.available_lemmas)} purpose={self.purpose[:30]!r}"
        )


# ---------------------------------------------------------------------------
# Oracle I/O
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OracleQuery:
    """An immutable structured query sent to a research oracle.

    Attributes:
        query_id: Stable unique identifier.
        query_type: Logical type of the query (e.g. ``"proof_step"``).
        content: The query payload text.
        context_id: Identifier of the :class:`ResearchContext` for this query.
        parameters: Additional key-value parameters.
        timestamp: Unix timestamp when the query was created.
    """

    query_id: str
    query_type: str
    content: str
    context_id: str
    parameters: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class OracleResponse:
    """An immutable response returned by a research oracle.

    Attributes:
        response_id: Stable unique identifier.
        query_id: Identifier of the originating :class:`OracleQuery`.
        content: The response payload text.
        confidence: Confidence score in [0, 1].
        oracle_id: Identifier of the oracle that produced this response.
        timestamp: Unix timestamp when the response was created.
        raw_output: Unprocessed oracle output, if available.
    """

    response_id: str
    query_id: str
    content: str
    confidence: float
    oracle_id: str
    timestamp: float
    raw_output: str = ""

    def is_high_confidence(self, threshold: float = 0.7) -> bool:
        """Return True if confidence is at or above the given threshold."""
        return self.confidence >= threshold


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    """An immutable record of a formal verification attempt.

    Attributes:
        record_id: Stable unique identifier.
        subject_id: Identifier of the artifact being verified.
        verdict: True if the artifact passed verification.
        evidence: Human-readable description of the verification outcome.
        timestamp: Unix timestamp of the verification attempt.
        verifier_id: Identifier of the verifier system used.
    """

    record_id: str
    subject_id: str
    verdict: bool
    evidence: str
    timestamp: float
    verifier_id: str = "formal"

    def is_passing(self) -> bool:
        """Return True if the verification verdict is positive."""
        return self.verdict


# ---------------------------------------------------------------------------
# ResearchSession
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResearchSession:
    """Mutable top-level container for an active research assistance session.

    A session holds the research context, the history of applied proof
    suggestions, the set of active conjectures, and lifecycle metadata.

    Attributes:
        session_id: Stable unique identifier.
        context: The current :class:`ResearchContext`.
        history: Ordered list of :class:`ProofSuggestion` instances applied.
        active_conjectures: Open :class:`ConjectureRecord` instances.
        status: Current :class:`SessionStatus`.
        created_at: Unix timestamp of session creation.
        updated_at: Unix timestamp of last modification.
    """

    session_id: str
    context: ResearchContext
    history: list[ProofSuggestion] = field(default_factory=list)
    active_conjectures: list[ConjectureRecord] = field(default_factory=list)
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_suggestion(self, suggestion: ProofSuggestion) -> None:
        """Append a proof suggestion to the session history."""
        self.history.append(suggestion)
        self.updated_at = time.time()
        _log.debug(
            "ResearchSession %s: added suggestion %s",
            self.session_id,
            suggestion.suggestion_id,
        )

    def record_verification(self, record: VerificationRecord) -> None:
        """Update the session timestamp after recording a verification result."""
        self.updated_at = time.time()
        _log.info(
            "ResearchSession %s: verification record %s verdict=%s",
            self.session_id,
            record.record_id,
            record.verdict,
        )

    def close(self) -> None:
        """Transition the session to COMPLETED status."""
        self.status = SessionStatus.COMPLETED
        self.updated_at = time.time()
        _log.info("ResearchSession %s: closed", self.session_id)

    def to_summary(self) -> str:
        """Return a compact one-line description."""
        return (
            f"Session[{self.session_id[:8]}] status={self.status.value} "
            f"suggestions={len(self.history)} "
            f"conjectures={len(self.active_conjectures)}"
        )


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_context(
    current_theorem: str,
    *,
    purpose: str = "",
    partial_proof: str = "",
) -> ResearchContext:
    """Create a fresh :class:`ResearchContext` with a generated id."""
    return ResearchContext(
        context_id=str(uuid.uuid4()),
        current_theorem=current_theorem,
        partial_proof=partial_proof,
        purpose=purpose,
    )


def make_session(context: ResearchContext) -> ResearchSession:
    """Create a fresh ACTIVE :class:`ResearchSession` for the given context."""
    return ResearchSession(
        session_id=str(uuid.uuid4()),
        context=context,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "ConjectureStatus",
    "LemmaSource",
    "SessionStatus",
    "VerificationStatus",
    # Dataclasses
    "ConjectureRecord",
    "LemmaCandidate",
    "OracleQuery",
    "OracleResponse",
    "ProofSuggestion",
    "ResearchContext",
    "ResearchSession",
    "VerificationRecord",
    # Factories
    "make_context",
    "make_session",
]
