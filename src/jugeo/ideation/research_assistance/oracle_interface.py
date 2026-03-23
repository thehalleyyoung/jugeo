r"""Controlled oracle interface for JuGeo research assistance — Chapter 51, §4.

This module implements the *controlled oracle* discipline: an oracle (typically
a language model) proposes research candidates, but **every** accepted
response must be associated with a passing :class:`VerificationRecord` before
it is integrated into the proof state (Thm 51.1, 51.13).

Oracle discipline
-----------------

Let :math:`O` be a :class:`CopilotOracle` with policy :math:`\pi`.  The
acceptance predicate is:

.. math::

    \text{accept}(r) = \pi.\text{enforce\_verification}(r, \text{verify}(r))

With ``verification_required = True``:

.. math::

    \text{accept}(r) = \text{True}
        \iff \exists w : w.\text{subject\_id} = r.\text{response\_id}
                      \wedge w.\text{verdict} = \text{True}

The :class:`OracleAuditLog` records every ``(query, response, accepted)``
triple when ``audit_all = True`` (Thm 51.13).

The :class:`MockOracle` is a **fully deterministic** oracle suitable for
testing and offline reasoning — it never calls external services and always
returns the same response for the same input.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass, field

from jugeo.ideation.research_assistance.models import (
    OracleQuery,
    OracleResponse,
    ResearchContext,
    VerificationRecord,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float to [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _hash_content(content: str) -> str:
    """Return the first 16 hex characters of the SHA-256 digest of content."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# OraclePolicy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OraclePolicy:
    """Immutable policy governing oracle query behaviour and acceptance criteria.

    Attributes:
        max_queries: Maximum total queries allowed across all sessions.
        confidence_threshold: Minimum confidence for a response to be acceptable.
        verification_required: If True, acceptance requires a passing verification
            record from the formal verifier.
        allow_unverified_on_timeout: If True, unverified responses may be
            tentatively accepted when the verifier is unavailable.
        audit_all: If True, every query-response pair is logged to the audit.
    """

    max_queries: int = 100
    confidence_threshold: float = 0.7
    verification_required: bool = True
    allow_unverified_on_timeout: bool = False
    audit_all: bool = True

    def is_response_acceptable(self, response: OracleResponse) -> bool:
        """Return True if the response meets the confidence threshold."""
        return response.confidence >= self.confidence_threshold


# ---------------------------------------------------------------------------
# OracleAuditLog
# ---------------------------------------------------------------------------


class OracleAuditLog:
    """Append-only audit trail of all oracle query-response interactions.

    Every entry records the query id, response id, acceptance decision, and
    timestamp.  This supports post-hoc analysis and compliance auditing.
    """

    def __init__(self) -> None:
        self._entries: list[dict] = []

    def log(
        self,
        query: OracleQuery,
        response: OracleResponse,
        accepted: bool,
    ) -> None:
        """Append a new audit entry for the given query-response pair."""
        entry = {
            "query_id": query.query_id,
            "response_id": response.response_id,
            "accepted": accepted,
            "timestamp": time.time(),
            "oracle_id": response.oracle_id,
            "confidence": response.confidence,
        }
        self._entries.append(entry)
        _log.debug(
            "OracleAuditLog: query=%s response=%s accepted=%s",
            query.query_id,
            response.response_id,
            accepted,
        )

    def entries(self) -> list[dict]:
        """Return a copy of all audit entries."""
        return list(self._entries)

    def query_count(self) -> int:
        """Return the total number of logged interactions."""
        return len(self._entries)

    def acceptance_rate(self) -> float:
        """Return the fraction of logged interactions that were accepted."""
        if not self._entries:
            return 0.0
        accepted = sum(1 for e in self._entries if e["accepted"])
        return accepted / len(self._entries)

    def entries_for_oracle(self, oracle_id: str) -> list[dict]:
        """Return all audit entries for the specified oracle."""
        return [e for e in self._entries if e.get("oracle_id") == oracle_id]


# ---------------------------------------------------------------------------
# MockOracle
# ---------------------------------------------------------------------------


class MockOracle:
    """A fully deterministic oracle for unit testing and offline development.

    The mock oracle never makes external calls.  Its responses are derived
    deterministically from a ``responses`` dictionary and a SHA-256 hash of
    the query content, ensuring reproducibility across runs.

    Attributes:
        _responses: Optional dictionary mapping query content → response text.
        _default_confidence: Confidence attached to all generated responses.
        _counter: Number of queries processed (monotonically increasing).
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_confidence: float = 0.8,
    ) -> None:
        self._responses: dict[str, str] = responses or {}
        self._default_confidence = _clamp(default_confidence)
        self._counter: int = 0

    def query(self, query: OracleQuery) -> OracleResponse:
        """Return a deterministic response for the given query."""
        self._counter += 1
        if query.content in self._responses:
            content = self._responses[query.content]
        else:
            h = _hash_content(query.content)
            content = (
                f"MockOracle response #{self._counter} for "
                f"[{query.content[:40]}]: hash={h}"
            )

        return OracleResponse(
            response_id=str(uuid.uuid4()),
            query_id=query.query_id,
            content=content,
            confidence=self._default_confidence,
            oracle_id="mock",
            timestamp=time.time(),
            raw_output=content,
        )

    def verify_response(self, response: OracleResponse) -> VerificationRecord:
        """Deterministically verify a response based on its confidence level."""
        verdict = response.confidence >= 0.5
        return VerificationRecord(
            record_id=str(uuid.uuid4()),
            subject_id=response.response_id,
            verdict=verdict,
            evidence=f"MockOracle: confidence {response.confidence:.2f} >= 0.5 = {verdict}",
            timestamp=time.time(),
            verifier_id="mock-verifier",
        )

    def accept(self, response: OracleResponse) -> bool:
        """Return True if the response confidence meets the acceptance bar."""
        return response.confidence >= self._default_confidence * 0.9

    def query_count(self) -> int:
        """Return the number of queries processed since instantiation."""
        return self._counter

    def reset_counter(self) -> None:
        """Reset the internal query counter to zero."""
        self._counter = 0


# ---------------------------------------------------------------------------
# ControlledOracleProtocol
# ---------------------------------------------------------------------------


class ControlledOracleProtocol:
    """Enforces the controlled oracle discipline across all oracle interactions.

    The protocol maintains per-session query budgets and requires that
    verification records pass before accepting oracle responses when the
    ``verification_required`` flag is active.

    Attributes:
        _policy: The immutable :class:`OraclePolicy` governing this protocol.
        _query_counts: Per-session query counts keyed by session_id.
    """

    def __init__(self, policy: OraclePolicy) -> None:
        self._policy = policy
        self._query_counts: dict[str, int] = {}

    def is_query_allowed(self, context: ResearchContext) -> bool:
        """Return True if a query is permitted under the current policy."""
        session_total = sum(self._query_counts.values())
        return session_total < self._policy.max_queries

    def enforce_verification(
        self,
        response: OracleResponse,
        record: VerificationRecord,
    ) -> bool:
        """Return True if the response should be accepted given the record.

        With verification_required, acceptance requires record.verdict = True.
        Without it, falls back to the confidence threshold check.
        """
        if self._policy.verification_required:
            return record.verdict
        return self._policy.is_response_acceptable(response)

    def remaining_budget(self, session_id: str) -> int:
        """Return the number of queries remaining for the given session."""
        used = self._query_counts.get(session_id, 0)
        return max(0, self._policy.max_queries - used)

    def record_query(self, session_id: str) -> None:
        """Increment the query counter for the given session."""
        self._query_counts[session_id] = self._query_counts.get(session_id, 0) + 1
        _log.debug(
            "ControlledOracleProtocol: session=%s count=%d",
            session_id,
            self._query_counts[session_id],
        )

    def total_queries(self) -> int:
        """Return the total number of queries across all sessions."""
        return sum(self._query_counts.values())

    def reset_session(self, session_id: str) -> None:
        """Reset the query counter for a specific session."""
        self._query_counts.pop(session_id, None)


# ---------------------------------------------------------------------------
# CopilotOracle
# ---------------------------------------------------------------------------


class CopilotOracle:
    """Main oracle implementation following the controlled oracle discipline.

    The :class:`CopilotOracle` wraps a language-model interaction surface
    (simulated deterministically by :meth:`_simulate_llm_response`), applies
    the :class:`ControlledOracleProtocol`, optionally verifies responses, and
    logs every interaction to the :class:`OracleAuditLog`.

    Attributes:
        _oracle_id: Stable identifier for this oracle instance.
        _policy: The :class:`OraclePolicy` applied to all interactions.
        _protocol: The :class:`ControlledOracleProtocol` enforcing the policy.
        _audit: The :class:`OracleAuditLog` recording all interactions.
    """

    def __init__(
        self,
        oracle_id: str,
        policy: OraclePolicy | None = None,
        protocol: ControlledOracleProtocol | None = None,
    ) -> None:
        self._oracle_id = oracle_id
        self._policy = policy or OraclePolicy()
        self._protocol = protocol or ControlledOracleProtocol(self._policy)
        self._audit = OracleAuditLog()

    def query(self, query: OracleQuery) -> OracleResponse:
        """Process a query and return a response, logging to the audit trail.

        The response content is generated by :meth:`_simulate_llm_response`.
        If :attr:`OraclePolicy.audit_all` is True the query-response pair
        is unconditionally logged (Thm 51.13).
        """
        response_content = self._simulate_llm_response(query.content)
        response = OracleResponse(
            response_id=str(uuid.uuid4()),
            query_id=query.query_id,
            content=response_content,
            confidence=self._policy.confidence_threshold,
            oracle_id=self._oracle_id,
            timestamp=time.time(),
            raw_output=response_content,
        )

        accepted = self.accept(response)
        if self._policy.audit_all:
            self._audit.log(query, response, accepted)

        _log.debug(
            "CopilotOracle %s: query=%s accepted=%s",
            self._oracle_id,
            query.query_id,
            accepted,
        )
        return response

    def verify_response(self, response: OracleResponse) -> VerificationRecord:
        """Produce a verification record for the given oracle response.

        The verdict is True if the response meets the confidence threshold
        defined in the policy.
        """
        verdict = self._policy.is_response_acceptable(response)
        return VerificationRecord(
            record_id=str(uuid.uuid4()),
            subject_id=response.response_id,
            verdict=verdict,
            evidence=(
                f"CopilotOracle {self._oracle_id!r}: confidence "
                f"{response.confidence:.2f} >= threshold "
                f"{self._policy.confidence_threshold:.2f} = {verdict}"
            ),
            timestamp=time.time(),
            verifier_id=self._oracle_id,
        )

    def accept(self, response: OracleResponse) -> bool:
        """Return True if the response is accepted under the controlled protocol.

        Creates a verification record and delegates to the protocol's
        :meth:`~ControlledOracleProtocol.enforce_verification` method
        (Thm 51.1).
        """
        record = self.verify_response(response)
        return self._protocol.enforce_verification(response, record)

    def audit_log(self) -> OracleAuditLog:
        """Return the audit log for this oracle instance."""
        return self._audit

    def oracle_id(self) -> str:
        """Return this oracle's identifier string."""
        return self._oracle_id

    def policy(self) -> OraclePolicy:
        """Return this oracle's policy."""
        return self._policy

    def _simulate_llm_response(self, content: str) -> str:
        """Produce a deterministic simulated LLM response for the given content.

        The simulation is hash-based so it is reproducible and requires no
        external service calls.  Production deployments replace this method
        with a real LLM call.
        """
        h = _hash_content(content)
        preview = content[:40].replace("\n", " ")
        return (
            f"Proof step for [{preview!r}]: "
            f"apply hypothesis and rewrite using available lemmas. "
            f"Hash: {h}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ControlledOracleProtocol",
    "CopilotOracle",
    "MockOracle",
    "OracleAuditLog",
    "OraclePolicy",
]
