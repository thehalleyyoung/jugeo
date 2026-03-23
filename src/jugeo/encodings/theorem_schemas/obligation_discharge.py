"""Obligation discharge loop for JuGeo's spec-adherence checking benchmark.

Implements the *obligation discharge loop* described in theory2.tex §10.4
(Chapter 10: Specification Satisfaction).  Given a ``ParsedSpecification`` and
an optional evidence manifest, the loop iterates over every ``ParsedObligation``
and attempts to discharge it via one or more solver back-ends (Z3 SMT, runtime
witness lookup, structural tautology check, or oracle deferral).  The result is
a ``DischargeResult`` carrying a full audit trail of every attempt and a
compliance certificate suitable for manifest insertion.

Theory background (§10.4)
--------------------------
A *proof obligation* o_i derived from a specification S is discharged when
a certificate C_i is produced such that C_i ⊨ o_i under the ambient trust
algebra T.  The discharge loop constructs a query Q_i for each o_i, routes
Q_i to the appropriate solver back-end, collects the certificate (or records a
residual failure), and accumulates the results into a ``DischargeResult``.

Trust is an *ordered algebra*, not a scalar float.  Each discharge attempt
carries a ``trust_tier`` label (e.g. ``"PROPOSAL"``, ``"AUDITED"``,
``"CERTIFIED"``) that allows downstream consumers to filter obligations by
the minimum evidence quality they require.

Failed obligations do **not** raise exceptions — they become *cohomology
obstructions* recorded in the manifest's obstruction section.  This keeps the
loop unconditionally non-raising so that a single hard obligation failure
cannot interrupt the discharge of an entire specification.

Solver back-ends (in priority order)
--------------------------------------
1. **structural** — Trivially satisfied constraints (vacuous quantifiers, type
   annotations that are structurally guaranteed).  Fastest; no external calls.
2. **tautology** — Recognised logical tautologies detected by syntactic pattern
   matching on the predicate string.
3. **z3** — Full SMT discharge via the Z3 Python API.  The predicate string is
   parsed into a Z3 Boolean formula; ``z3.solve()`` is invoked.  Falls back
   gracefully if z3 is not installed.
4. **runtime** — Evidence already present in the manifest (runtime witnesses,
   prior certificates, judgment verdicts) is queried.
5. **oracle** — The obligation is deferred to an external reviewer (LLM,
   human, trusted third party) and marked ``ORACLE_PENDING``.

Cohomology class assignment
---------------------------
Each ``DischargeRecord`` is assigned a cohomology class label from the theory:

* ``H0``  — obligation discharged completely (no residual)
* ``H1``  — partially discharged; residual gap remains
* ``H2``  — failed; obstruction recorded in Čech 2-cocycle register
* ``H∞``  — deferred to oracle; class not yet determined

# copilot: obligation discharge loop for theorem-schema spec-adherence benchmark.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Optional jugeo imports — each block is guarded so the module can be imported
# in isolation (e.g. during unit testing or in a minimal environment).
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.specification_satisfaction.specifications import (
        ParsedSpecification,
        ParsedObligation,
    )
except ImportError:  # pragma: no cover
    ParsedSpecification = Any  # type: ignore[assignment,misc]
    ParsedObligation = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.manifests import Manifest, EvidenceArchive
except ImportError:  # pragma: no cover
    Manifest = None  # type: ignore[assignment,misc]
    EvidenceArchive = None  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        EvidenceBundle,
        EvidenceItem,
        TrustAnnotation,
        Obstruction,
        Provenance,
        JudgmentStatus,
    )
except ImportError:  # pragma: no cover
    Judgment = None  # type: ignore[assignment,misc]
    EvidenceBundle = None  # type: ignore[assignment,misc]
    EvidenceItem = None  # type: ignore[assignment,misc]
    TrustAnnotation = None  # type: ignore[assignment,misc]
    Obstruction = None  # type: ignore[assignment,misc]
    Provenance = None  # type: ignore[assignment,misc]
    JudgmentStatus = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.theorem_schemas.models import (
        ProofAgent,
        ProofStyle,
        SubsystemKind,
    )
except ImportError:  # pragma: no cover
    ProofAgent = None  # type: ignore[assignment,misc]
    ProofStyle = None  # type: ignore[assignment,misc]
    SubsystemKind = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings.theorem_schemas.proof_obligations import (
        ObligationTracker,
        ObligationStatus,
    )
except ImportError:  # pragma: no cover
    ObligationTracker = None  # type: ignore[assignment,misc]
    ObligationStatus = None  # type: ignore[assignment,misc]

try:
    import z3 as _z3  # type: ignore[import-untyped]

    _Z3_AVAILABLE = True
except ImportError:  # pragma: no cover
    _z3 = None  # type: ignore[assignment]
    _Z3_AVAILABLE = False

__all__ = [
    "DischargeStatus",
    "DischargeAttempt",
    "DischargeRecord",
    "DischargeResult",
    "DischargeError",
    "ObligationDischarger",
    "discharge_spec",
    "discharge_obligations",
    "quick_check",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Trust tier ordering — not floats; an ordered string algebra.
# Lower index = weaker evidence.
# ---------------------------------------------------------------------------

_TRUST_TIERS: tuple[str, ...] = (
    "NONE",
    "PROPOSAL",
    "REVIEWED",
    "AUDITED",
    "CERTIFIED",
    "AXIOM",
)

# Mapping from method name → minimum trust tier awarded on success
_METHOD_TRUST: dict[str, str] = {
    "tautology": "CERTIFIED",   # Tautologies are unconditional
    "structural": "AUDITED",    # Type-level guarantees are strong
    "z3": "AUDITED",            # SMT proof is machine-checked
    "runtime": "REVIEWED",      # Runtime witness is empirical evidence
    "oracle": "PROPOSAL",       # Oracle result is pending external review
}

# Mapping from method → trust tier when the attempt *fails*
_METHOD_FAILURE_TRUST: dict[str, str] = {
    "tautology": "NONE",
    "structural": "NONE",
    "z3": "NONE",
    "runtime": "PROPOSAL",
    "oracle": "PROPOSAL",
}

# Recognised tautology patterns (regex applied to the normalised predicate)
_TAUTOLOGY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bTrue\b"),
    re.compile(r"\btrue\b"),
    re.compile(r"\b(\w+)\s*==\s*\1\b"),   # x == x
    re.compile(r"\blen\([^)]+\)\s*>=\s*0\b"),
    re.compile(r"\bisinstance\([^,]+,\s*object\)"),
    re.compile(r"\b(\w+)\s+in\s+\1\b"),   # x in x
]

# Structural patterns: type annotations or trivially satisfied containment
_STRUCTURAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^isinstance\(.+\)$"),
    re.compile(r"^type\(.+\)\s*=="),
    re.compile(r"^\w+\s+is\s+not\s+None$"),
    re.compile(r"^hasattr\("),
    re.compile(r"^callable\("),
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Return a fresh UUID4 string for use as a record or attempt identifier."""
    return str(uuid.uuid4())


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _elapsed(start: float) -> float:
    """Return seconds elapsed since *start* (POSIX timestamp)."""
    return time.monotonic() - start


def _trust_rank(tier: str) -> int:
    """Return the ordinal rank of a trust tier string.

    Higher numbers mean stronger evidence.  Unknown tier strings return -1.

    Args:
        tier: One of the values in ``_TRUST_TIERS``.

    Returns:
        Integer rank, or -1 if the tier is unrecognised.
    """
    try:
        return _TRUST_TIERS.index(tier)
    except ValueError:
        return -1


def _trust_gte(a: str, b: str) -> bool:
    """Return True if trust tier *a* is at least as strong as tier *b*.

    In the ordered trust algebra, ``CERTIFIED >= AUDITED >= REVIEWED >= ...``.

    Args:
        a: Candidate tier.
        b: Minimum required tier.

    Returns:
        True when rank(a) >= rank(b).
    """
    return _trust_rank(a) >= _trust_rank(b)


def _get_attr(obj: Any, *attrs: str) -> Any:
    """Look up the first matching attribute or dict key from *obj*.

    Accepts both regular objects (via ``getattr``) and plain dictionaries
    (via key lookup).  Returns ``None`` if none of the names resolve.

    Args:
        obj: The object or dictionary to inspect.
        *attrs: Attribute / key names to try in order.

    Returns:
        The first non-None value found, or None.
    """
    for name in attrs:
        # Dictionary key lookup
        if isinstance(obj, dict):
            val = obj.get(name)
            if val is not None:
                return val
        # Attribute lookup
        val = getattr(obj, name, None)
        if val is not None:
            return val
    return None


def _obligation_id(obligation: Any) -> str:
    """Extract or synthesise a stable string ID from *obligation*.

    Accepts objects with an ``obligation_id`` or ``id`` attribute, plain
    dictionaries with those keys, or a raw string.  Falls back to hashing
    the string representation when no ID field is found.

    Args:
        obligation: The parsed obligation object or dictionary.

    Returns:
        A non-empty string suitable for use as a dictionary key.
    """
    if isinstance(obligation, str):
        return obligation
    val = _get_attr(obligation, "obligation_id", "id", "oid")
    if val:
        return str(val)
    # Last resort: stable hash of the repr
    return hashlib.md5(repr(obligation).encode()).hexdigest()[:16]


def _obligation_predicate(obligation: Any) -> str:
    """Extract the predicate string from *obligation*.

    Tries common attribute and dictionary-key names in order.  Returns an
    empty string if no predicate can be determined so callers do not need
    to handle None.

    Args:
        obligation: The parsed obligation object or dictionary.

    Returns:
        Predicate string, or empty string if not available.
    """
    val = _get_attr(obligation, "predicate", "statement", "formula", "text", "description")
    if isinstance(val, str):
        return val.strip()
    return ""


def _obligation_kind(obligation: Any) -> str:
    """Extract a kind/category label from *obligation*.

    Args:
        obligation: The parsed obligation object or dictionary.

    Returns:
        Kind string such as ``"safety"``, ``"liveness"``, ``"typing"``, etc.
    """
    val = _get_attr(obligation, "kind", "category", "obligation_kind", "type")
    if val is not None:
        return str(val)
    return "unknown"


# ---------------------------------------------------------------------------
# DischargeStatus
# ---------------------------------------------------------------------------


class DischargeStatus(str, Enum):
    """Lifecycle status of a single obligation discharge attempt or record.

    The obligation discharge loop moves each obligation through a well-defined
    state machine.  The possible final states are::

        DISCHARGED         — fully satisfied; certificate produced.
        PARTIALLY_DISCHARGED — evidence closes part of the obligation gap;
                               a residual remains in the manifest.
        FAILED             — no back-end could satisfy the obligation; the
                               obligation becomes a cohomology obstruction.
        DEFERRED           — the obligation is recognised but processing is
                               postponed (e.g. dependency not yet resolved).
        ORACLE_PENDING     — sent to external oracle (LLM / human reviewer);
                               result not yet available.
        BLOCKED_BY_DEPENDENCY — one or more upstream obligations must be
                               discharged before this one can be attempted.

    Note that ``DISCHARGED`` and ``PARTIALLY_DISCHARGED`` are both considered
    *terminal* for the purposes of the discharge loop — the loop does not
    revisit them.
    """

    DISCHARGED = "discharged"
    PARTIALLY_DISCHARGED = "partially_discharged"
    FAILED = "failed"
    DEFERRED = "deferred"
    ORACLE_PENDING = "oracle_pending"
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        """Return True if this status represents a final outcome.

        Both successful discharge and failure are terminal; ``DEFERRED``
        and ``ORACLE_PENDING`` are not, because a future loop iteration
        may resolve them.

        Returns:
            True for ``DISCHARGED``, ``PARTIALLY_DISCHARGED``, or ``FAILED``.
        """
        return self in (
            DischargeStatus.DISCHARGED,
            DischargeStatus.PARTIALLY_DISCHARGED,
            DischargeStatus.FAILED,
        )

    def is_positive(self) -> bool:
        """Return True if this status represents a (full or partial) success.

        Returns:
            True for ``DISCHARGED`` or ``PARTIALLY_DISCHARGED``.
        """
        return self in (
            DischargeStatus.DISCHARGED,
            DischargeStatus.PARTIALLY_DISCHARGED,
        )

    def cohomology_class(self) -> str:
        """Map this discharge status to a cohomology class label.

        The mapping follows the convention from theory2.tex §10.4.4:

        * H0 — fully discharged (no residual cocycle)
        * H1 — partially discharged (residual 1-cocycle)
        * H2 — failed (obstruction 2-cocycle)
        * H∞ — deferred or oracle pending (class indeterminate)

        Returns:
            One of ``"H0"``, ``"H1"``, ``"H2"``, ``"H∞"``.
        """
        _MAP = {
            DischargeStatus.DISCHARGED: "H0",
            DischargeStatus.PARTIALLY_DISCHARGED: "H1",
            DischargeStatus.FAILED: "H2",
            DischargeStatus.DEFERRED: "H∞",
            DischargeStatus.ORACLE_PENDING: "H∞",
            DischargeStatus.BLOCKED_BY_DEPENDENCY: "H∞",
        }
        return _MAP[self]


# ---------------------------------------------------------------------------
# DischargeAttempt
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DischargeAttempt:
    """An immutable record of a single discharge attempt for one obligation.

    The discharge loop may make several attempts per obligation, using
    different solver back-ends in priority order.  Each attempt is recorded as
    a ``DischargeAttempt`` regardless of outcome so that the full audit trail
    is preserved.

    Attributes:
        attempt_id: Unique UUID4 identifier for this attempt.
        obligation_id: The ID of the obligation that was attempted.
        method: The solver back-end used.  One of: ``"z3"``, ``"runtime"``,
            ``"oracle"``, ``"structural"``, ``"tautology"``.
        status: The ``DischargeStatus`` after this attempt.
        certificate: If status is DISCHARGED or PARTIALLY_DISCHARGED, a
            dictionary containing the discharge certificate artefacts (e.g.
            Z3 model, witness value, tautology label).  None otherwise.
        residual: If status is PARTIALLY_DISCHARGED, a dictionary describing
            the remaining obligation gap (predicate sub-expression,
            unresolved variables, etc.).  None for full discharge or failure.
        trust_tier: The trust algebra tier awarded by this attempt.  One of
            the values in ``_TRUST_TIERS``.  Failure attempts receive
            ``"NONE"``; successful z3 or structural attempts receive
            ``"AUDITED"`` or higher.
        elapsed_s: Wall-clock time in seconds consumed by this attempt.
        metadata: Arbitrary additional data (solver version, error message,
            predicate hash, etc.) for debugging and archival purposes.
    """

    attempt_id: str
    obligation_id: str
    method: str
    status: DischargeStatus
    certificate: dict[str, Any] | None
    residual: dict[str, Any] | None
    trust_tier: str
    elapsed_s: float
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise this attempt to a JSON-compatible dictionary.

        All fields are included; ``status`` is stored as its string value.

        Returns:
            JSON-serialisable dictionary.
        """
        return {
            "attempt_id": self.attempt_id,
            "obligation_id": self.obligation_id,
            "method": self.method,
            "status": self.status.value,
            "certificate": self.certificate,
            "residual": self.residual,
            "trust_tier": self.trust_tier,
            "elapsed_s": round(self.elapsed_s, 6),
            "metadata": self.metadata,
        }

    # ------------------------------------------------------------------
    # Convenience predicates
    # ------------------------------------------------------------------

    def succeeded(self) -> bool:
        """Return True if this attempt produced a (partial or full) discharge.

        Returns:
            True when ``self.status.is_positive()``.
        """
        return self.status.is_positive()

    def meets_trust_floor(self, floor: str) -> bool:
        """Return True if this attempt's trust tier meets *floor*.

        Uses the ordered trust algebra: ``CERTIFIED > AUDITED > REVIEWED …``.

        Args:
            floor: The minimum required trust tier string.

        Returns:
            True when ``_trust_gte(self.trust_tier, floor)``.
        """
        return _trust_gte(self.trust_tier, floor)

    def summary(self) -> str:
        """Return a compact, human-readable summary of this attempt.

        Returns:
            Single-line summary string for logging and console output.
        """
        cert_flag = "✓" if self.certificate else "✗"
        return (
            f"Attempt[{self.attempt_id[:8]}] "
            f"method={self.method} "
            f"status={self.status.value} "
            f"trust={self.trust_tier} "
            f"cert={cert_flag} "
            f"elapsed={self.elapsed_s:.4f}s"
        )


# ---------------------------------------------------------------------------
# DischargeRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DischargeRecord:
    """An immutable aggregate record of all discharge attempts for one obligation.

    After the discharge loop exhausts its strategy list (or reaches a
    conclusive result), it produces a ``DischargeRecord`` summarising all
    ``DischargeAttempt`` objects for that obligation, together with the final
    status, best certificate, and assigned cohomology class.

    Attributes:
        record_id: Unique UUID4 identifier for this record.
        obligation_id: The ID of the obligation this record covers.
        attempts: Tuple of all ``DischargeAttempt`` objects, in execution order.
        final_status: The conclusive ``DischargeStatus`` for this obligation.
            Derived from the best attempt if multiple attempts were made.
        certificate: The best certificate found across all attempts, or None
            if the obligation could not be discharged.
        cohomology_class: One of ``"H0"``, ``"H1"``, ``"H2"``, ``"H∞"``,
            assigned according to the theory2.tex §10.4.4 convention.
        timestamp: ISO-8601 UTC timestamp at which the record was created.
        metadata: Arbitrary additional data (predicate hash, obligation kind,
            dependency IDs, etc.).
    """

    record_id: str
    obligation_id: str
    attempts: tuple[DischargeAttempt, ...]
    final_status: DischargeStatus
    certificate: dict[str, Any] | None
    cohomology_class: str | None
    timestamp: str
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return True if the obligation has been fully discharged.

        An obligation is complete only when ``final_status`` is
        ``DISCHARGED`` (not ``PARTIALLY_DISCHARGED``).

        Returns:
            True when ``self.final_status == DischargeStatus.DISCHARGED``.
        """
        return self.final_status == DischargeStatus.DISCHARGED

    def best_attempt(self) -> DischargeAttempt | None:
        """Return the attempt with the highest-trust successful outcome.

        Among all attempts in ``self.attempts``, finds the one with the
        strongest ``trust_tier`` that also ``succeeded()``.  If no attempt
        succeeded, returns the attempt with the highest trust tier overall
        (the "least bad" failure for diagnostics).

        Returns:
            The best ``DischargeAttempt``, or None if no attempts were made.
        """
        if not self.attempts:
            return None
        successful = [a for a in self.attempts if a.succeeded()]
        if successful:
            return max(successful, key=lambda a: _trust_rank(a.trust_tier))
        # No success — return the attempt with the highest trust tier
        return max(self.attempts, key=lambda a: _trust_rank(a.trust_tier))

    def n_attempts(self) -> int:
        """Return the number of discharge attempts recorded.

        Returns:
            Integer count of attempts in ``self.attempts``.
        """
        return len(self.attempts)

    def trust_tier(self) -> str:
        """Return the trust tier of the best attempt, or ``"NONE"`` if empty.

        Returns:
            Trust tier string from ``best_attempt()``, or ``"NONE"``.
        """
        best = self.best_attempt()
        return best.trust_tier if best else "NONE"

    def methods_tried(self) -> list[str]:
        """Return the list of solver back-end method names tried, in order.

        Returns:
            List of method name strings (may contain duplicates if a method
            was tried more than once, though the loop does not retry by
            default).
        """
        return [a.method for a in self.attempts]

    def to_json(self) -> dict[str, Any]:
        """Serialise this record to a JSON-compatible dictionary.

        Includes all attempts serialised inline.

        Returns:
            JSON-serialisable dictionary.
        """
        return {
            "record_id": self.record_id,
            "obligation_id": self.obligation_id,
            "attempts": [a.to_json() for a in self.attempts],
            "final_status": self.final_status.value,
            "certificate": self.certificate,
            "cohomology_class": self.cohomology_class,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    def obstruction_dict(self) -> dict[str, Any] | None:
        """Return a Čech obstruction dictionary if the obligation failed.

        The obstruction dictionary can be inserted directly into the manifest's
        obstruction register.  Returns None if the obligation was discharged.

        Returns:
            Obstruction dictionary, or None if no obstruction.
        """
        if self.final_status == DischargeStatus.DISCHARGED:
            return None
        return {
            "obligation_id": self.obligation_id,
            "cohomology_class": self.cohomology_class or "H2",
            "status": self.final_status.value,
            "methods_tried": self.methods_tried(),
            "timestamp": self.timestamp,
            "record_id": self.record_id,
        }

    def summary(self) -> str:
        """Return a compact, human-readable summary of this record.

        Returns:
            Multi-line summary string for console output.
        """
        lines = [
            f"DischargeRecord[{self.record_id[:8]}]",
            f"  obligation : {self.obligation_id[:16]}",
            f"  status     : {self.final_status.value}",
            f"  cohomology : {self.cohomology_class}",
            f"  attempts   : {self.n_attempts()} ({', '.join(self.methods_tried())})",
            f"  trust      : {self.trust_tier()}",
            f"  timestamp  : {self.timestamp}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# DischargeResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DischargeResult:
    """Immutable aggregate result of a full obligation discharge session.

    Produced by ``ObligationDischarger.discharge()`` after the loop completes
    for all obligations in a specification.  Carries the full audit trail,
    aggregate counts, and enough information to generate a compliance
    certificate.

    Attributes:
        session_id: Unique UUID4 identifier for this discharge session.
        records: Tuple of ``DischargeRecord`` objects, one per obligation, in
            the order they were processed.
        n_discharged: Number of obligations with ``DISCHARGED`` final status.
        n_failed: Number of obligations with ``FAILED`` final status.
        n_deferred: Number of obligations in a non-terminal status
            (``DEFERRED``, ``ORACLE_PENDING``, ``BLOCKED_BY_DEPENDENCY``).
        spec_id: Identifier of the source specification, or empty string.
        trust_floor: The minimum trust tier that was required for discharge
            acceptance (supplied to the loop at construction time).
        elapsed_s: Total wall-clock time consumed by the full session.
        metadata: Arbitrary session-level metadata (solver versions, spec
            source file, timestamp, etc.).
    """

    session_id: str
    records: tuple[DischargeRecord, ...]
    n_discharged: int
    n_failed: int
    n_deferred: int
    spec_id: str
    trust_floor: str
    elapsed_s: float
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Convenience predicates
    # ------------------------------------------------------------------

    def all_discharged(self) -> bool:
        """Return True if every obligation in the session was fully discharged.

        Note that ``PARTIALLY_DISCHARGED`` obligations are *not* counted as
        fully discharged here — use ``n_failed == 0`` and ``n_deferred == 0``
        if you want a weaker check.

        Returns:
            True when ``n_discharged == len(records)`` and there are no
            partially-discharged records.
        """
        return self.n_discharged == len(self.records)

    def n_partially_discharged(self) -> int:
        """Return the count of obligations with PARTIALLY_DISCHARGED status.

        Returns:
            Integer count.
        """
        return sum(
            1
            for r in self.records
            if r.final_status == DischargeStatus.PARTIALLY_DISCHARGED
        )

    def failed_obligations(self) -> tuple[DischargeRecord, ...]:
        """Return the subset of records whose final status is FAILED.

        These records carry ``cohomology_class == "H2"`` and should be
        registered as Čech obstructions in the manifest.

        Returns:
            Tuple of ``DischargeRecord`` objects with ``FAILED`` status.
        """
        return tuple(
            r for r in self.records if r.final_status == DischargeStatus.FAILED
        )

    def deferred_obligations(self) -> tuple[DischargeRecord, ...]:
        """Return obligations that are deferred or awaiting oracle response.

        Returns:
            Tuple of ``DischargeRecord`` objects in non-terminal status.
        """
        return tuple(
            r
            for r in self.records
            if r.final_status in (
                DischargeStatus.DEFERRED,
                DischargeStatus.ORACLE_PENDING,
                DischargeStatus.BLOCKED_BY_DEPENDENCY,
            )
        )

    def passed_trust_floor(self, floor: str | None = None) -> tuple[DischargeRecord, ...]:
        """Return records whose best attempt meets the given trust floor.

        Args:
            floor: Minimum trust tier string.  If None, uses
                ``self.trust_floor``.

        Returns:
            Tuple of records where ``record.trust_tier() >= floor``.
        """
        effective_floor = floor if floor is not None else self.trust_floor
        return tuple(
            r for r in self.records if _trust_gte(r.trust_tier(), effective_floor)
        )

    # ------------------------------------------------------------------
    # Compliance certificate
    # ------------------------------------------------------------------

    def to_compliance_certificate(self) -> dict[str, Any]:
        """Generate a compliance certificate dictionary for this session.

        The certificate encodes the overall discharge outcome in a format
        suitable for insertion into the manifest's ``compliance_certificates``
        section.  It includes:

        * Session metadata (ID, spec, trust floor, elapsed time)
        * Per-obligation summary (status, cohomology class, certificate
          reference)
        * Aggregate counts
        * Obstruction register for failed obligations

        Returns:
            JSON-serialisable dictionary.
        """
        obligation_summaries = [
            {
                "obligation_id": r.obligation_id,
                "status": r.final_status.value,
                "cohomology_class": r.cohomology_class,
                "trust_tier": r.trust_tier(),
                "methods_tried": r.methods_tried(),
                "certificate_ref": (
                    r.certificate.get("certificate_id")
                    if r.certificate
                    else None
                ),
            }
            for r in self.records
        ]
        obstructions = [
            obs
            for r in self.failed_obligations()
            for obs in [r.obstruction_dict()]
            if obs is not None
        ]
        return {
            "certificate_type": "obligation_discharge_compliance",
            "session_id": self.session_id,
            "spec_id": self.spec_id,
            "trust_floor": self.trust_floor,
            "issued_at": _now_iso(),
            "elapsed_s": round(self.elapsed_s, 4),
            "aggregate": {
                "total": len(self.records),
                "discharged": self.n_discharged,
                "partially_discharged": self.n_partially_discharged(),
                "failed": self.n_failed,
                "deferred": self.n_deferred,
                "all_discharged": self.all_discharged(),
            },
            "obligations": obligation_summaries,
            "obstructions": obstructions,
            "metadata": self.metadata,
        }

    def to_json(self) -> dict[str, Any]:
        """Serialise this result to a JSON-compatible dictionary.

        Embeds the full record list inline.

        Returns:
            JSON-serialisable dictionary.
        """
        return {
            "session_id": self.session_id,
            "records": [r.to_json() for r in self.records],
            "n_discharged": self.n_discharged,
            "n_failed": self.n_failed,
            "n_deferred": self.n_deferred,
            "spec_id": self.spec_id,
            "trust_floor": self.trust_floor,
            "elapsed_s": round(self.elapsed_s, 4),
            "metadata": self.metadata,
        }

    def summary_report(self) -> str:
        """Return a multi-line human-readable summary of the session.

        Returns:
            Summary string suitable for logging or CLI output.
        """
        lines = [
            "=" * 60,
            "OBLIGATION DISCHARGE SESSION REPORT",
            f"  session_id    : {self.session_id[:16]}",
            f"  spec_id       : {self.spec_id or '(none)'}",
            f"  trust_floor   : {self.trust_floor}",
            f"  elapsed       : {self.elapsed_s:.3f}s",
            f"  total         : {len(self.records)}",
            f"  discharged    : {self.n_discharged}",
            f"  partial       : {self.n_partially_discharged()}",
            f"  failed        : {self.n_failed}",
            f"  deferred      : {self.n_deferred}",
            f"  all_discharged: {self.all_discharged()}",
            "-" * 60,
        ]
        for r in self.records:
            icon = {
                DischargeStatus.DISCHARGED: "✓",
                DischargeStatus.PARTIALLY_DISCHARGED: "△",
                DischargeStatus.FAILED: "✗",
                DischargeStatus.DEFERRED: "⏳",
                DischargeStatus.ORACLE_PENDING: "🔮",
                DischargeStatus.BLOCKED_BY_DEPENDENCY: "🔗",
            }.get(r.final_status, "?")
            lines.append(
                f"  {icon} [{r.obligation_id[:12]}] "
                f"{r.final_status.value:<24} "
                f"cohom={r.cohomology_class} "
                f"trust={r.trust_tier()}"
            )
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# DischargeError
# ---------------------------------------------------------------------------


class DischargeError(Exception):
    """Raised for configuration errors in the discharge machinery itself.

    Note: this is *not* raised for failed obligations — those are recorded in
    the ``DischargeResult`` as ``FAILED`` records.  ``DischargeError`` is
    reserved for programming errors such as passing an unrecognised spec type
    or providing a malformed config dictionary.

    Attributes:
        message: Human-readable description of the error.
        context: Arbitrary context dictionary for debugging.
    """

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        """Initialise with a message and optional context dictionary.

        Args:
            message: Human-readable error description.
            context: Optional dictionary with additional debugging context.
        """
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:
        return f"DischargeError({self.message!r}, context={self.context!r})"


# ---------------------------------------------------------------------------
# ObligationDischarger
# ---------------------------------------------------------------------------


class ObligationDischarger:
    """Main obligation discharge loop for JuGeo's spec-adherence benchmark.

    The discharger accepts a ``ParsedSpecification`` (or a plain dictionary),
    extracts its obligations, and attempts to discharge each one via a
    configurable strategy chain.  It returns a ``DischargeResult`` regardless
    of individual obligation outcomes — failures are recorded, not raised.

    Strategy chain (tried in order for each obligation)
    ---------------------------------------------------
    1. ``tautology``  — Syntactic pattern matching for trivial truths.
    2. ``structural`` — Type-level / annotation-based guarantees.
    3. ``z3``         — SMT discharge via Z3 Python API (if available).
    4. ``runtime``    — Evidence lookup in the manifest / evidence archive.
    5. ``oracle``     — Deferred to external oracle; marks ORACLE_PENDING.

    The chain is short-circuited as soon as one back-end produces a
    ``DISCHARGED`` result.  ``PARTIALLY_DISCHARGED`` results continue the
    chain to attempt full discharge; if no back-end achieves full discharge
    the obligation is recorded as ``PARTIALLY_DISCHARGED``.

    Configuration
    -------------
    The *config* dictionary accepts the following keys (all optional):

    ``strategy``
        List of method name strings defining the strategy chain.  Default:
        ``["tautology", "structural", "z3", "runtime", "oracle"]``.

    ``skip_oracle``
        If True, the oracle back-end is omitted from the chain.  Useful
        for offline/batch runs where no oracle is available.  Default: False.

    ``z3_timeout_ms``
        Timeout in milliseconds for Z3 solve calls.  Default: 5000.

    ``max_attempts_per_obligation``
        Maximum number of back-ends tried per obligation.  Default: 5.

    ``partial_is_sufficient``
        If True, ``PARTIALLY_DISCHARGED`` is treated as a success (the loop
        stops and does not try further back-ends after partial discharge).
        Default: False.

    Thread safety
    -------------
    This class is *not* thread-safe.  Use one instance per thread or wrap
    access in a lock.

    Example usage::

        discharger = ObligationDischarger(config={"skip_oracle": True})
        result = discharger.discharge(parsed_spec, manifest=my_manifest)
        if result.all_discharged():
            print("All obligations satisfied.")
        else:
            print(result.summary_report())
    """

    _DEFAULT_STRATEGY: list[str] = [
        "tautology",
        "structural",
        "z3",
        "runtime",
        "oracle",
    ]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the discharger with an optional configuration dictionary.

        Args:
            config: Optional configuration overrides.  See class docstring
                for supported keys.
        """
        cfg = config or {}

        # Resolve strategy chain from config
        self._strategy: list[str] = list(
            cfg.get("strategy", self._DEFAULT_STRATEGY)
        )
        if cfg.get("skip_oracle", False) and "oracle" in self._strategy:
            self._strategy.remove("oracle")

        self._z3_timeout_ms: int = int(cfg.get("z3_timeout_ms", 5000))
        self._max_attempts: int = int(cfg.get("max_attempts_per_obligation", 5))
        self._partial_sufficient: bool = bool(cfg.get("partial_is_sufficient", False))

        # Internal dispatch table: method name → bound callable
        self._dispatch_table: dict[str, Any] = {
            "tautology": self._try_tautology_discharge,
            "structural": self._try_structural_discharge,
            "z3": self._try_z3_discharge,
            "runtime": self._try_runtime_discharge,
            "oracle": self._try_oracle_discharge,
        }

        logger.debug(
            "ObligationDischarger initialised: strategy=%s z3_available=%s",
            self._strategy,
            _Z3_AVAILABLE,
        )

    # ------------------------------------------------------------------
    # Primary entry points
    # ------------------------------------------------------------------

    def discharge(
        self,
        spec: Any,
        manifest: Any = None,
        *,
        trust_floor: str = "PROPOSAL",
    ) -> DischargeResult:
        """Discharge all obligations from *spec* and return a ``DischargeResult``.

        This is the primary entry point for the discharge loop.  It accepts
        a ``ParsedSpecification`` object, a dictionary, or any object that
        exposes an ``obligations`` attribute.

        The loop never raises.  If an individual obligation encounters an
        unexpected error, the error is captured and the obligation is recorded
        as ``FAILED`` with the error information in the record's metadata.

        Args:
            spec: The specification whose obligations to discharge.  Accepts:
                * ``ParsedSpecification`` (or duck-typed equivalent)
                * A dictionary with an ``"obligations"`` key
                * Any object with an ``obligations`` attribute
            manifest: Optional evidence manifest for runtime witness lookup.
                Can be a ``Manifest`` instance, a plain dictionary, or None.
            trust_floor: Minimum trust tier required for a certificate to be
                accepted as a discharge.  Default ``"PROPOSAL"``.

        Returns:
            A ``DischargeResult`` summarising the full session.

        Raises:
            DischargeError: If *spec* is not in a recognisable format.
        """
        session_start = time.monotonic()
        session_id = _new_id()

        # Extract obligation list from spec
        obligations = self._extract_obligations(spec)
        spec_id = self._extract_spec_id(spec)

        logger.info(
            "Discharge session %s: spec_id=%r n_obligations=%d trust_floor=%s",
            session_id[:8],
            spec_id,
            len(obligations),
            trust_floor,
        )

        # Run the discharge loop
        records: list[DischargeRecord] = []
        for obligation in obligations:
            record = self._safe_discharge_one(obligation, manifest, trust_floor)
            records.append(record)
            logger.debug(
                "Obligation %s → %s (%s)",
                record.obligation_id[:12],
                record.final_status.value,
                record.cohomology_class,
            )

        # Compute aggregate counts
        n_discharged = sum(
            1 for r in records if r.final_status == DischargeStatus.DISCHARGED
        )
        n_failed = sum(
            1 for r in records if r.final_status == DischargeStatus.FAILED
        )
        n_deferred = sum(
            1
            for r in records
            if r.final_status in (
                DischargeStatus.DEFERRED,
                DischargeStatus.ORACLE_PENDING,
                DischargeStatus.BLOCKED_BY_DEPENDENCY,
            )
        )

        elapsed = _elapsed(session_start)
        result = DischargeResult(
            session_id=session_id,
            records=tuple(records),
            n_discharged=n_discharged,
            n_failed=n_failed,
            n_deferred=n_deferred,
            spec_id=spec_id,
            trust_floor=trust_floor,
            elapsed_s=elapsed,
            metadata={
                "strategy": self._strategy,
                "z3_available": _Z3_AVAILABLE,
                "z3_timeout_ms": self._z3_timeout_ms,
                "session_start_iso": _now_iso(),
            },
        )

        logger.info(
            "Discharge session %s complete: %d/%d discharged in %.3fs",
            session_id[:8],
            n_discharged,
            len(records),
            elapsed,
        )
        return result

    def discharge_one(
        self,
        obligation: Any,
        manifest: Any = None,
        *,
        trust_floor: str = "PROPOSAL",
    ) -> DischargeRecord:
        """Discharge a single *obligation* and return its ``DischargeRecord``.

        Unlike ``discharge()``, this method operates on a single obligation
        object rather than a full specification.  It is useful for interactive
        testing or incremental re-discharge of specific obligations.

        Args:
            obligation: A ``ParsedObligation`` or duck-typed equivalent.
            manifest: Optional evidence manifest for runtime back-end.
            trust_floor: Minimum trust tier for acceptance.

        Returns:
            A ``DischargeRecord`` for this obligation.
        """
        return self._safe_discharge_one(obligation, manifest, trust_floor)

    # ------------------------------------------------------------------
    # Internal discharge machinery
    # ------------------------------------------------------------------

    def _safe_discharge_one(
        self,
        obligation: Any,
        manifest: Any,
        trust_floor: str,
    ) -> DischargeRecord:
        """Wrap ``_discharge_one_impl`` in a try/except to prevent loop crashes.

        If an unexpected exception occurs, the obligation is recorded as
        ``FAILED`` with the exception information preserved in metadata.

        Args:
            obligation: The obligation to discharge.
            manifest: Evidence manifest for runtime back-end.
            trust_floor: Minimum trust tier for acceptance.

        Returns:
            A ``DischargeRecord`` — never raises.
        """
        try:
            return self._discharge_one_impl(obligation, manifest, trust_floor)
        except Exception as exc:  # noqa: BLE001
            oid = _obligation_id(obligation)
            logger.exception(
                "Unexpected error discharging obligation %s: %s", oid[:12], exc
            )
            # Construct a FAILED record capturing the exception
            failed_attempt = DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="internal_error",
                status=DischargeStatus.FAILED,
                certificate=None,
                residual=None,
                trust_tier="NONE",
                elapsed_s=0.0,
                metadata={"exception": str(exc), "exception_type": type(exc).__name__},
            )
            return DischargeRecord(
                record_id=_new_id(),
                obligation_id=oid,
                attempts=(failed_attempt,),
                final_status=DischargeStatus.FAILED,
                certificate=None,
                cohomology_class="H2",
                timestamp=_now_iso(),
                metadata={
                    "exception": str(exc),
                    "exception_type": type(exc).__name__,
                    "obligation_kind": _obligation_kind(obligation),
                },
            )

    def _discharge_one_impl(
        self,
        obligation: Any,
        manifest: Any,
        trust_floor: str,
    ) -> DischargeRecord:
        """Core discharge logic for a single obligation.

        Iterates through the strategy chain, calling each back-end in order.
        Stops as soon as a back-end achieves ``DISCHARGED`` status (or
        ``PARTIALLY_DISCHARGED`` if ``_partial_sufficient`` is True).

        Args:
            obligation: The obligation to discharge.
            manifest: Evidence manifest for runtime back-end.
            trust_floor: Minimum trust tier for acceptance.

        Returns:
            A ``DischargeRecord`` with the best result found.
        """
        oid = _obligation_id(obligation)
        record_start = time.monotonic()
        attempts: list[DischargeAttempt] = []
        best_status = DischargeStatus.FAILED
        best_certificate: dict[str, Any] | None = None

        # Check for dependency blocks before attempting discharge
        if self._is_blocked(obligation):
            blocked_attempt = DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="dependency_check",
                status=DischargeStatus.BLOCKED_BY_DEPENDENCY,
                certificate=None,
                residual=None,
                trust_tier="NONE",
                elapsed_s=0.0,
                metadata={"reason": "upstream obligation not yet discharged"},
            )
            cohom = DischargeStatus.BLOCKED_BY_DEPENDENCY.cohomology_class()
            return DischargeRecord(
                record_id=_new_id(),
                obligation_id=oid,
                attempts=(blocked_attempt,),
                final_status=DischargeStatus.BLOCKED_BY_DEPENDENCY,
                certificate=None,
                cohomology_class=cohom,
                timestamp=_now_iso(),
                metadata=self._obligation_meta(obligation),
            )

        # Walk the strategy chain
        for i, method in enumerate(self._strategy[: self._max_attempts]):
            backend = self._dispatch_table.get(method)
            if backend is None:
                logger.warning("Unknown strategy method %r; skipping.", method)
                continue

            attempt: DischargeAttempt = backend(obligation, manifest)
            attempts.append(attempt)

            if attempt.status == DischargeStatus.DISCHARGED:
                # Full discharge — short-circuit the chain
                if _trust_gte(attempt.trust_tier, trust_floor):
                    best_status = DischargeStatus.DISCHARGED
                    best_certificate = attempt.certificate
                    logger.debug(
                        "Obligation %s discharged via %s at tier %s.",
                        oid[:12],
                        method,
                        attempt.trust_tier,
                    )
                    break
                else:
                    # Discharge succeeded but doesn't meet the trust floor;
                    # record as PARTIALLY_DISCHARGED and continue.
                    logger.debug(
                        "Obligation %s: %s succeeded but trust %s < floor %s.",
                        oid[:12],
                        method,
                        attempt.trust_tier,
                        trust_floor,
                    )
                    best_status = DischargeStatus.PARTIALLY_DISCHARGED
                    best_certificate = attempt.certificate
                    if self._partial_sufficient:
                        break

            elif attempt.status == DischargeStatus.PARTIALLY_DISCHARGED:
                best_status = DischargeStatus.PARTIALLY_DISCHARGED
                best_certificate = attempt.certificate
                if self._partial_sufficient:
                    break

            elif attempt.status in (
                DischargeStatus.ORACLE_PENDING,
                DischargeStatus.DEFERRED,
            ):
                best_status = attempt.status
                # Don't break — another back-end might do better
                # (but oracle is usually last in the chain)

        # If no strategy improved on FAILED, keep FAILED
        if not attempts:
            best_status = DischargeStatus.FAILED

        # Assign cohomology class
        cohom = self._assign_cohomology_class_from_status(best_status)

        record_elapsed = _elapsed(record_start)

        return DischargeRecord(
            record_id=_new_id(),
            obligation_id=oid,
            attempts=tuple(attempts),
            final_status=best_status,
            certificate=best_certificate,
            cohomology_class=cohom,
            timestamp=_now_iso(),
            metadata={
                **self._obligation_meta(obligation),
                "total_elapsed_s": round(record_elapsed, 6),
                "n_attempts": len(attempts),
            },
        )

    # ------------------------------------------------------------------
    # Solver back-ends
    # ------------------------------------------------------------------

    def _try_tautology_discharge(
        self,
        obligation: Any,
        manifest: Any,  # noqa: ARG002  (unused — tautology check is context-free)
    ) -> DischargeAttempt:
        """Attempt discharge by recognising the predicate as a known tautology.

        Scans the normalised predicate string against the module-level list of
        ``_TAUTOLOGY_PATTERNS``.  Tautologies are classified at the ``CERTIFIED``
        trust tier because they are structurally guaranteed and require no
        empirical evidence.

        Args:
            obligation: The obligation whose predicate to check.
            manifest: Not used; present for uniform method signature.

        Returns:
            A ``DischargeAttempt`` with status ``DISCHARGED`` if a tautology
            pattern matched, or ``FAILED`` otherwise.
        """
        oid = _obligation_id(obligation)
        predicate = _obligation_predicate(obligation)
        t_start = time.monotonic()

        matched_pattern: str | None = None
        for pattern in _TAUTOLOGY_PATTERNS:
            if pattern.search(predicate):
                matched_pattern = pattern.pattern
                break

        elapsed = _elapsed(t_start)

        if matched_pattern:
            certificate = {
                "certificate_id": _new_id(),
                "method": "tautology",
                "pattern": matched_pattern,
                "predicate": predicate,
                "issued_at": _now_iso(),
            }
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="tautology",
                status=DischargeStatus.DISCHARGED,
                certificate=certificate,
                residual=None,
                trust_tier=_METHOD_TRUST["tautology"],
                elapsed_s=elapsed,
                metadata={"pattern": matched_pattern},
            )

        return DischargeAttempt(
            attempt_id=_new_id(),
            obligation_id=oid,
            method="tautology",
            status=DischargeStatus.FAILED,
            certificate=None,
            residual=None,
            trust_tier=_METHOD_FAILURE_TRUST["tautology"],
            elapsed_s=elapsed,
            metadata={"reason": "no tautology pattern matched", "predicate": predicate[:120]},
        )

    def _try_structural_discharge(
        self,
        obligation: Any,
        manifest: Any = None,  # noqa: ARG002
    ) -> DischargeAttempt:
        """Attempt discharge via structural / type-annotation analysis.

        Checks whether the obligation predicate is a type check, attribute
        existence test, or similar annotation-level claim that is structurally
        guaranteed by the Python object model.  Uses ``_STRUCTURAL_PATTERNS``
        defined at module level.

        Also checks the obligation's ``kind`` attribute for obligations that
        are categorically dischargeable at the structural level (e.g. pure
        well-formedness checks that carry no proof burden beyond existence).

        Args:
            obligation: The obligation to check.
            manifest: Not used directly; present for uniform signature.

        Returns:
            A ``DischargeAttempt`` with the result.
        """
        oid = _obligation_id(obligation)
        predicate = _obligation_predicate(obligation)
        kind = _obligation_kind(obligation)
        t_start = time.monotonic()

        # Check structural patterns on the predicate
        matched_pattern: str | None = None
        for pattern in _STRUCTURAL_PATTERNS:
            if pattern.search(predicate):
                matched_pattern = pattern.pattern
                break

        # Also discharge vacuous / well-formedness obligations by kind
        structural_kinds = {
            "well_formedness", "wellformedness", "wf", "typing",
            "type_check", "annotation", "structural",
        }
        kind_discharge = kind.lower().replace("-", "_") in structural_kinds

        elapsed = _elapsed(t_start)

        if matched_pattern or kind_discharge:
            reason = f"pattern={matched_pattern}" if matched_pattern else f"kind={kind}"
            certificate = {
                "certificate_id": _new_id(),
                "method": "structural",
                "reason": reason,
                "predicate": predicate,
                "obligation_kind": kind,
                "issued_at": _now_iso(),
            }
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="structural",
                status=DischargeStatus.DISCHARGED,
                certificate=certificate,
                residual=None,
                trust_tier=_METHOD_TRUST["structural"],
                elapsed_s=elapsed,
                metadata={"reason": reason},
            )

        return DischargeAttempt(
            attempt_id=_new_id(),
            obligation_id=oid,
            method="structural",
            status=DischargeStatus.FAILED,
            certificate=None,
            residual=None,
            trust_tier=_METHOD_FAILURE_TRUST["structural"],
            elapsed_s=elapsed,
            metadata={
                "reason": "not a structurally dischargeable pattern",
                "kind": kind,
            },
        )

    def _try_z3_discharge(
        self,
        obligation: Any,
        manifest: Any = None,
    ) -> DischargeAttempt:
        """Attempt SMT discharge via the Z3 Python API.

        Constructs a Z3 formula from the obligation predicate string using
        ``_build_z3_formula()``.  If the formula is constructed successfully,
        calls ``z3.solve()`` (or ``z3.Solver.check()``) with a timeout.
        Records the result in a certificate if satisfiable.

        Falls back gracefully to ``FAILED`` if:
        * Z3 is not installed (``_Z3_AVAILABLE == False``)
        * The predicate cannot be parsed into a Z3 formula
        * Z3 returns ``unknown`` or times out

        Args:
            obligation: The obligation whose predicate to discharge.
            manifest: Not used directly in Z3 mode.

        Returns:
            A ``DischargeAttempt`` with the result.
        """
        oid = _obligation_id(obligation)
        predicate = _obligation_predicate(obligation)
        t_start = time.monotonic()

        if not _Z3_AVAILABLE:
            elapsed = _elapsed(t_start)
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="z3",
                status=DischargeStatus.FAILED,
                certificate=None,
                residual=None,
                trust_tier=_METHOD_FAILURE_TRUST["z3"],
                elapsed_s=elapsed,
                metadata={"reason": "z3 not installed"},
            )

        # Attempt to build a Z3 formula from the predicate string
        formula = self._build_z3_formula(predicate)
        if formula is None:
            elapsed = _elapsed(t_start)
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="z3",
                status=DischargeStatus.FAILED,
                certificate=None,
                residual=None,
                trust_tier=_METHOD_FAILURE_TRUST["z3"],
                elapsed_s=elapsed,
                metadata={
                    "reason": "predicate could not be parsed to Z3 formula",
                    "predicate": predicate[:120],
                },
            )

        # Invoke Z3 solver with timeout
        solver = _z3.Solver()
        solver.set("timeout", self._z3_timeout_ms)
        # We want to check that the formula is valid (provable), not just SAT.
        # Validity check: assert ¬formula and check for UNSAT.
        negated = _z3.Not(formula)
        solver.add(negated)
        result_str = str(solver.check())
        elapsed = _elapsed(t_start)

        if result_str == "unsat":
            # ¬formula is UNSAT ⟹ formula is a tautology / valid
            certificate = {
                "certificate_id": _new_id(),
                "method": "z3",
                "verdict": "valid",
                "z3_result": result_str,
                "predicate": predicate,
                "issued_at": _now_iso(),
                "elapsed_s": round(elapsed, 6),
            }
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="z3",
                status=DischargeStatus.DISCHARGED,
                certificate=certificate,
                residual=None,
                trust_tier=_METHOD_TRUST["z3"],
                elapsed_s=elapsed,
                metadata={"z3_result": result_str},
            )
        elif result_str == "sat":
            # ¬formula is SAT ⟹ formula is NOT universally valid (has a counter-model)
            try:
                model = str(solver.model())
            except Exception:  # noqa: BLE001
                model = "(model unavailable)"
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="z3",
                status=DischargeStatus.FAILED,
                certificate=None,
                residual={
                    "counterexample": model,
                    "predicate": predicate,
                    "z3_result": result_str,
                },
                trust_tier=_METHOD_FAILURE_TRUST["z3"],
                elapsed_s=elapsed,
                metadata={"z3_result": result_str, "counterexample": model[:200]},
            )
        else:
            # unknown / timeout
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="z3",
                status=DischargeStatus.DEFERRED,
                certificate=None,
                residual={"z3_result": result_str, "predicate": predicate},
                trust_tier=_METHOD_FAILURE_TRUST["z3"],
                elapsed_s=elapsed,
                metadata={"z3_result": result_str, "reason": "z3 returned unknown or timed out"},
            )

    def _try_runtime_discharge(
        self,
        obligation: Any,
        manifest: Any,
    ) -> DischargeAttempt:
        """Attempt discharge by querying existing runtime evidence in the manifest.

        Searches the manifest (if provided) for any existing evidence that
        directly satisfies this obligation.  Evidence is looked up by
        obligation ID in the manifest's ``obligation_records`` section.

        Supports three manifest shapes:
        * A ``Manifest`` object with a ``get_obligation_record`` method.
        * A dictionary with a top-level ``"obligation_records"`` key.
        * Any other mapping; the obligation ID is used as a key directly.

        Args:
            obligation: The obligation to look up.
            manifest: The evidence manifest (or None).

        Returns:
            A ``DischargeAttempt`` with the result.
        """
        oid = _obligation_id(obligation)
        t_start = time.monotonic()

        if manifest is None:
            elapsed = _elapsed(t_start)
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="runtime",
                status=DischargeStatus.FAILED,
                certificate=None,
                residual=None,
                trust_tier=_METHOD_FAILURE_TRUST["runtime"],
                elapsed_s=elapsed,
                metadata={"reason": "no manifest provided"},
            )

        # Try to retrieve evidence from the manifest
        evidence = self._lookup_manifest_evidence(oid, manifest)
        elapsed = _elapsed(t_start)

        if evidence is None:
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="runtime",
                status=DischargeStatus.FAILED,
                certificate=None,
                residual=None,
                trust_tier=_METHOD_FAILURE_TRUST["runtime"],
                elapsed_s=elapsed,
                metadata={"reason": "obligation not found in manifest"},
            )

        # Determine the status from the evidence record
        ev_status = evidence.get("status", "unknown") if isinstance(evidence, dict) else "found"
        ev_trust = evidence.get("trust_tier", "REVIEWED") if isinstance(evidence, dict) else "REVIEWED"

        if ev_status in ("discharged", "satisfied", "verified", "found"):
            certificate = {
                "certificate_id": _new_id(),
                "method": "runtime",
                "evidence_ref": evidence.get("id") if isinstance(evidence, dict) else str(evidence)[:64],
                "evidence_status": ev_status,
                "issued_at": _now_iso(),
                "trust_tier": ev_trust,
            }
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="runtime",
                status=DischargeStatus.DISCHARGED,
                certificate=certificate,
                residual=None,
                trust_tier=ev_trust if _trust_gte(ev_trust, "PROPOSAL") else "REVIEWED",
                elapsed_s=elapsed,
                metadata={"evidence_status": ev_status},
            )
        elif ev_status in ("partial", "partially_discharged"):
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="runtime",
                status=DischargeStatus.PARTIALLY_DISCHARGED,
                certificate={
                    "certificate_id": _new_id(),
                    "method": "runtime",
                    "evidence_status": ev_status,
                    "issued_at": _now_iso(),
                },
                residual=evidence.get("residual") if isinstance(evidence, dict) else None,
                trust_tier=ev_trust,
                elapsed_s=elapsed,
                metadata={"evidence_status": ev_status},
            )
        else:
            return DischargeAttempt(
                attempt_id=_new_id(),
                obligation_id=oid,
                method="runtime",
                status=DischargeStatus.FAILED,
                certificate=None,
                residual=None,
                trust_tier=_METHOD_FAILURE_TRUST["runtime"],
                elapsed_s=elapsed,
                metadata={"evidence_status": ev_status, "reason": "evidence status not sufficient"},
            )

    def _try_oracle_discharge(
        self,
        obligation: Any,
        manifest: Any = None,  # noqa: ARG002
    ) -> DischargeAttempt:
        """Mark the obligation as pending external oracle review.

        The oracle back-end does not attempt to discharge the obligation
        itself; it simply records that the obligation has been forwarded to an
        external reviewer (LLM, human, trusted third party) and marks the
        status as ``ORACLE_PENDING``.

        The trust tier for oracle results is ``PROPOSAL`` — the weakest
        positive tier — reflecting that oracle outputs have not been
        machine-verified.

        Args:
            obligation: The obligation to defer.
            manifest: Not used; present for uniform signature.

        Returns:
            A ``DischargeAttempt`` with ``ORACLE_PENDING`` status.
        """
        oid = _obligation_id(obligation)
        predicate = _obligation_predicate(obligation)
        t_start = time.monotonic()
        oracle_ref = _new_id()
        elapsed = _elapsed(t_start)

        return DischargeAttempt(
            attempt_id=_new_id(),
            obligation_id=oid,
            method="oracle",
            status=DischargeStatus.ORACLE_PENDING,
            certificate=None,
            residual={
                "oracle_ref": oracle_ref,
                "predicate": predicate,
                "forwarded_at": _now_iso(),
            },
            trust_tier=_METHOD_TRUST["oracle"],
            elapsed_s=elapsed,
            metadata={
                "oracle_ref": oracle_ref,
                "reason": "forwarded to external oracle for review",
            },
        )

    # ------------------------------------------------------------------
    # Z3 formula construction
    # ------------------------------------------------------------------

    def _build_z3_formula(self, predicate: str) -> Any:
        """Parse *predicate* into a Z3 Boolean expression.

        Attempts several parsing strategies in order:

        1. **Direct eval**: eval the predicate in a Z3-augmented namespace.
           Handles simple expressions like ``x > 0``, ``And(p, q)``, etc.
        2. **Literal boolean**: if the predicate is ``"True"`` or ``"False"``,
           return the corresponding Z3 constant.
        3. **SMT-LIB snippet**: if the predicate looks like an SMTLIB2 assert
           clause (starts with ``(``), parse via ``z3.parse_smt2_string``.

        Returns None if none of the strategies succeed, so callers can fall
        back gracefully without raising.

        Args:
            predicate: The obligation predicate string.

        Returns:
            A Z3 Boolean expression, or None if parsing fails.
        """
        if not _Z3_AVAILABLE or not predicate.strip():
            return None

        # Strategy 1: SMTLIB2 format
        stripped = predicate.strip()
        if stripped.startswith("("):
            try:
                formulas = _z3.parse_smt2_string(
                    f"(assert {stripped})\n(check-sat)"
                )
                if formulas:
                    return formulas[0]
            except Exception:  # noqa: BLE001
                pass

        # Strategy 2: Literal booleans
        if stripped in ("True", "true"):
            return _z3.BoolVal(True)
        if stripped in ("False", "false"):
            return _z3.BoolVal(False)

        # Strategy 3: Python eval in Z3 namespace
        # Build a minimal eval namespace with common Z3 symbols
        z3_ns: dict[str, Any] = {
            "And": _z3.And,
            "Or": _z3.Or,
            "Not": _z3.Not,
            "Implies": _z3.Implies,
            "ForAll": _z3.ForAll,
            "Exists": _z3.Exists,
            "BoolVal": _z3.BoolVal,
            "IntVal": _z3.IntVal,
            "RealVal": _z3.RealVal,
            "True": _z3.BoolVal(True),
            "False": _z3.BoolVal(False),
        }
        # Declare any single-character or short variable names as Z3 Bools/Ints
        # by scanning identifiers in the predicate
        for token in re.findall(r"\b([a-zA-Z_]\w*)\b", stripped):
            if token not in z3_ns and len(token) <= 3:
                z3_ns[token] = _z3.Bool(token)

        try:
            result = eval(stripped, {"__builtins__": {}}, z3_ns)  # noqa: S307
            if isinstance(result, _z3.BoolRef):
                return result
        except Exception:  # noqa: BLE001
            pass

        return None

    # ------------------------------------------------------------------
    # Cohomology class assignment
    # ------------------------------------------------------------------

    def _assign_cohomology_class(self, record: DischargeRecord) -> str:
        """Return the cohomology class string for a completed record.

        Delegates to ``record.final_status.cohomology_class()`` after
        performing additional checks for partially-discharged obligations
        that have a large residual gap (which may warrant upgrading to H2).

        Args:
            record: The completed ``DischargeRecord``.

        Returns:
            One of ``"H0"``, ``"H1"``, ``"H2"``, ``"H∞"``.
        """
        base = record.final_status.cohomology_class()
        # If partially discharged with a large residual, escalate to H2
        if record.final_status == DischargeStatus.PARTIALLY_DISCHARGED:
            best = record.best_attempt()
            if best and best.residual:
                n_residual_keys = len(best.residual)
                if n_residual_keys > 5:
                    # Large residual gap — cohomological obstruction is significant
                    return "H2"
        return base

    def _assign_cohomology_class_from_status(self, status: DischargeStatus) -> str:
        """Map a ``DischargeStatus`` directly to a cohomology class string.

        Args:
            status: The final discharge status.

        Returns:
            Cohomology class string.
        """
        return status.cohomology_class()

    # ------------------------------------------------------------------
    # Trust tier computation
    # ------------------------------------------------------------------

    def _compute_trust_tier(self, method: str, status: DischargeStatus) -> str:
        """Return the trust tier awarded for a given method + status combination.

        Uses the module-level ``_METHOD_TRUST`` and ``_METHOD_FAILURE_TRUST``
        tables.  Falls back to ``"NONE"`` for unrecognised combinations.

        Args:
            method: The solver back-end name.
            status: The discharge status of the attempt.

        Returns:
            Trust tier string.
        """
        if status.is_positive():
            return _METHOD_TRUST.get(method, "REVIEWED")
        return _METHOD_FAILURE_TRUST.get(method, "NONE")

    # ------------------------------------------------------------------
    # Manifest wiring
    # ------------------------------------------------------------------

    def wire_to_manifest(
        self,
        result: DischargeResult,
        manifest: Any,
    ) -> None:
        """Update *manifest* with discharge certificates and obstructions.

        Writes the following into the manifest:

        * For each discharged obligation: the certificate dict is inserted
          into the manifest's ``obligation_records`` section.
        * For each failed obligation: an obstruction dictionary is inserted
          into the manifest's ``obstructions`` section.
        * The full compliance certificate from ``result.to_compliance_certificate()``
          is inserted into ``manifest["compliance_certificates"]``.

        Supports two manifest shapes:
        * A dict-like object supporting ``setdefault`` and item assignment.
        * An object with ``set_obligation_record`` and ``add_obstruction``
          methods (e.g. a ``Manifest`` instance).

        Silently skips manifest sections that are not writable.

        Args:
            result: The ``DischargeResult`` to wire in.
            manifest: The manifest to update in place.
        """
        if manifest is None:
            logger.debug("wire_to_manifest: manifest is None; skipping.")
            return

        compliance_cert = result.to_compliance_certificate()

        # Handle dict-like manifests
        if isinstance(manifest, dict):
            obligation_records = manifest.setdefault("obligation_records", {})
            obstructions = manifest.setdefault("obstructions", [])
            certs = manifest.setdefault("compliance_certificates", [])

            for record in result.records:
                if record.certificate:
                    obligation_records[record.obligation_id] = {
                        "status": record.final_status.value,
                        "certificate": record.certificate,
                        "cohomology_class": record.cohomology_class,
                        "trust_tier": record.trust_tier(),
                        "record_id": record.record_id,
                    }
                obs = record.obstruction_dict()
                if obs:
                    obstructions.append(obs)

            certs.append(compliance_cert)
            logger.info(
                "wire_to_manifest: wrote %d records, %d obstructions.",
                len(result.records),
                len(obstructions),
            )
            return

        # Handle object-based manifests (duck-typed API)
        wrote = 0
        obstructed = 0
        for record in result.records:
            if record.certificate and hasattr(manifest, "set_obligation_record"):
                try:
                    manifest.set_obligation_record(
                        record.obligation_id,
                        {
                            "status": record.final_status.value,
                            "certificate": record.certificate,
                            "cohomology_class": record.cohomology_class,
                            "record_id": record.record_id,
                        },
                    )
                    wrote += 1
                except Exception:  # noqa: BLE001
                    pass
            obs = record.obstruction_dict()
            if obs and hasattr(manifest, "add_obstruction"):
                try:
                    manifest.add_obstruction(obs)
                    obstructed += 1
                except Exception:  # noqa: BLE001
                    pass

        if hasattr(manifest, "add_compliance_certificate"):
            try:
                manifest.add_compliance_certificate(compliance_cert)
            except Exception:  # noqa: BLE001
                pass

        logger.info(
            "wire_to_manifest (object API): wrote %d records, %d obstructions.",
            wrote,
            obstructed,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_obligations(self, spec: Any) -> list[Any]:
        """Extract the list of obligations from *spec*.

        Handles multiple input shapes:
        * ``ParsedSpecification`` with an ``obligations`` attribute
        * Dictionary with an ``"obligations"`` key
        * A bare list (treated as the obligation list directly)
        * Any object with an ``obligations`` attribute

        Args:
            spec: The specification to extract from.

        Returns:
            List of obligation objects (may be empty).

        Raises:
            DischargeError: If *spec* is None or has an unrecognisable shape.
        """
        if spec is None:
            raise DischargeError("spec must not be None", {"spec": spec})

        # Bare list
        if isinstance(spec, (list, tuple)):
            return list(spec)

        # Dictionary
        if isinstance(spec, dict):
            obs = spec.get("obligations", [])
            return list(obs) if obs else []

        # Object with obligations attribute
        obs = getattr(spec, "obligations", None)
        if obs is not None:
            return list(obs)

        # Last resort: treat spec as a single obligation
        logger.warning(
            "Could not extract obligation list from spec %r; "
            "treating as single obligation.",
            type(spec).__name__,
        )
        return [spec]

    def _extract_spec_id(self, spec: Any) -> str:
        """Extract a stable string ID from *spec*.

        Args:
            spec: The specification object.

        Returns:
            String ID, or empty string if not determinable.
        """
        if isinstance(spec, dict):
            return str(spec.get("spec_id") or spec.get("id") or "")
        for attr in ("spec_id", "id", "specification_id", "name"):
            val = getattr(spec, attr, None)
            if val:
                return str(val)
        return ""

    def _is_blocked(self, obligation: Any) -> bool:
        """Return True if *obligation* declares unsatisfied upstream dependencies.

        Checks the ``dependency_status`` attribute or dictionary key.  If the
        value indicates an unfulfilled dependency the obligation is considered
        blocked.  This is conservative: the loop prefers attempting a discharge
        over deferring unnecessarily.

        Args:
            obligation: The obligation to check.

        Returns:
            True if the obligation should be marked ``BLOCKED_BY_DEPENDENCY``.
        """
        dep_status = _get_attr(obligation, "dependency_status")
        if dep_status in ("blocked", "unsatisfied", "missing"):
            return True
        return False

    def _lookup_manifest_evidence(
        self,
        obligation_id: str,
        manifest: Any,
    ) -> dict[str, Any] | None:
        """Look up evidence for *obligation_id* in *manifest*.

        Args:
            obligation_id: The obligation ID to look up.
            manifest: The evidence manifest.

        Returns:
            Evidence dictionary if found, or None.
        """
        if manifest is None:
            return None

        # Method-based API
        if hasattr(manifest, "get_obligation_record"):
            try:
                return manifest.get_obligation_record(obligation_id)
            except Exception:  # noqa: BLE001
                return None

        # Dictionary API
        if isinstance(manifest, dict):
            records = manifest.get("obligation_records", {})
            return records.get(obligation_id)

        return None

    def _obligation_meta(self, obligation: Any) -> dict[str, Any]:
        """Build a metadata dictionary from an obligation's attributes.

        Extracts commonly used fields (kind, predicate hash, label) for
        inclusion in record metadata.

        Args:
            obligation: The obligation object or dictionary.

        Returns:
            Metadata dictionary.
        """
        predicate = _obligation_predicate(obligation)
        pred_hash = hashlib.sha256(predicate.encode()).hexdigest()[:16] if predicate else ""
        label = _get_attr(obligation, "label") or ""
        return {
            "obligation_kind": _obligation_kind(obligation),
            "predicate_hash": pred_hash,
            "predicate_len": len(predicate),
            "label": str(label),
        }


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def discharge_spec(
    spec: Any,
    manifest: Any = None,
    **kwargs: Any,
) -> DischargeResult:
    """Discharge all obligations from *spec* using default settings.

    A thin convenience wrapper around ``ObligationDischarger.discharge()``.
    Accepts keyword arguments that are forwarded to the discharger:

    * ``trust_floor`` — minimum trust tier (default ``"PROPOSAL"``)
    * ``config``      — configuration dictionary for ``ObligationDischarger``

    Args:
        spec: A ``ParsedSpecification``, dictionary, or obligation list.
        manifest: Optional evidence manifest for runtime witness lookup.
        **kwargs: Forwarded to ``ObligationDischarger.__init__`` and
            ``ObligationDischarger.discharge()``.

    Returns:
        A ``DischargeResult`` summarising the full session.

    Example::

        result = discharge_spec(parsed_spec, manifest=my_manifest)
        print(result.summary_report())
    """
    config = kwargs.pop("config", None)
    trust_floor: str = kwargs.pop("trust_floor", "PROPOSAL")
    discharger = ObligationDischarger(config=config)
    return discharger.discharge(spec, manifest=manifest, trust_floor=trust_floor)


def discharge_obligations(
    obligations: list[Any],
    manifest: Any = None,
    **kwargs: Any,
) -> DischargeResult:
    """Discharge a bare list of obligation objects.

    Wraps the list in a minimal spec-like dictionary and calls
    ``discharge_spec()``.  Useful when callers have already extracted
    obligations from their specification and do not want to reconstruct the
    full spec object.

    Args:
        obligations: List of ``ParsedObligation`` objects (or duck-typed
            equivalents).
        manifest: Optional evidence manifest.
        **kwargs: Forwarded to ``discharge_spec()``.

    Returns:
        A ``DischargeResult`` summarising the session.

    Example::

        result = discharge_obligations([ob1, ob2, ob3])
        if not result.all_discharged():
            for r in result.failed_obligations():
                print(r.summary())
    """
    pseudo_spec = {
        "spec_id": f"synthetic-{uuid.uuid4().hex[:8]}",
        "obligations": obligations,
    }
    return discharge_spec(pseudo_spec, manifest=manifest, **kwargs)


def quick_check(source: str, **kwargs: Any) -> DischargeResult:
    """Parse *source* as a specification string and immediately discharge it.

    This is a convenience entry point for interactive use and benchmarking.
    The source string is interpreted as either:

    * A JSON string representing a specification dictionary (if it starts
      with ``{`` or ``[``).
    * A newline-separated list of predicate strings, one per obligation.
      Each line becomes a minimal obligation dictionary.

    Args:
        source: Specification source string (JSON or predicate-per-line).
        **kwargs: Forwarded to ``discharge_spec()``.

    Returns:
        A ``DischargeResult`` for the parsed specification.

    Example::

        result = quick_check("x == x\\nlen(items) >= 0")
        print(result.summary_report())
    """
    source = source.strip()
    if source.startswith("{") or source.startswith("["):
        try:
            spec = json.loads(source)
        except json.JSONDecodeError as exc:
            raise DischargeError(
                f"quick_check: JSON parse failed: {exc}",
                {"source_prefix": source[:80]},
            ) from exc
    else:
        # One predicate per non-empty line
        lines = [ln.strip() for ln in source.splitlines() if ln.strip()]
        obligations = [
            {
                "obligation_id": f"qc-{i}-{uuid.uuid4().hex[:8]}",
                "predicate": line,
                "kind": "quick_check",
            }
            for i, line in enumerate(lines)
        ]
        spec = {
            "spec_id": f"quick_check-{uuid.uuid4().hex[:8]}",
            "obligations": obligations,
        }
    return discharge_spec(spec, **kwargs)


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def summarise_results(results: list[DischargeResult]) -> dict[str, Any]:
    """Aggregate multiple ``DischargeResult`` objects into a summary dictionary.

    Useful for benchmarking across many specifications.

    Args:
        results: List of ``DischargeResult`` objects to aggregate.

    Returns:
        Dictionary with total / per-status counts and overall pass rate.
    """
    total = sum(len(r.records) for r in results)
    discharged = sum(r.n_discharged for r in results)
    failed = sum(r.n_failed for r in results)
    deferred = sum(r.n_deferred for r in results)
    partial = sum(r.n_partially_discharged() for r in results)
    return {
        "n_sessions": len(results),
        "total_obligations": total,
        "discharged": discharged,
        "partially_discharged": partial,
        "failed": failed,
        "deferred": deferred,
        "pass_rate": round(discharged / total, 4) if total else 0.0,
        "all_sessions_clean": all(r.all_discharged() for r in results),
    }


def iter_failed_records(result: DischargeResult) -> Iterator[DischargeRecord]:
    """Yield all failed ``DischargeRecord`` objects from *result*.

    Args:
        result: A ``DischargeResult`` from a discharge session.

    Yields:
        ``DischargeRecord`` objects with ``FAILED`` final status.
    """
    yield from result.failed_obligations()


def iter_obstructions(result: DischargeResult) -> Iterator[dict[str, Any]]:
    """Yield all cohomological obstruction dictionaries from *result*.

    Each yielded dictionary is suitable for direct insertion into the
    manifest's obstruction register.

    Args:
        result: A ``DischargeResult`` from a discharge session.

    Yields:
        Obstruction dictionaries for each failed obligation.
    """
    for record in result.failed_obligations():
        obs = record.obstruction_dict()
        if obs:
            yield obs


# ---------------------------------------------------------------------------
# Entry point smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    print("=== ObligationDischarger smoke test ===\n")

    # ------------------------------------------------------------------ #
    # 1. Build a small synthetic specification with a mix of obligations  #
    #    covering each expected discharge path.                           #
    # ------------------------------------------------------------------ #
    synthetic_spec = {
        "spec_id": "smoke-test-spec-001",
        "obligations": [
            # Should be discharged via "tautology" (x == x pattern)
            {
                "obligation_id": "ob-tautology-01",
                "predicate": "x == x",
                "kind": "equality",
                "label": "reflexivity of x",
            },
            # Should be discharged via "tautology" (True literal)
            {
                "obligation_id": "ob-tautology-02",
                "predicate": "True",
                "kind": "trivial",
                "label": "trivial truth",
            },
            # Should be discharged via "structural" (isinstance check)
            {
                "obligation_id": "ob-structural-01",
                "predicate": "isinstance(node, object)",
                "kind": "typing",
                "label": "node is an object",
            },
            # Should be discharged via "structural" (hasattr check)
            {
                "obligation_id": "ob-structural-02",
                "predicate": "hasattr(obj, '__class__')",
                "kind": "annotation",
                "label": "obj has __class__",
            },
            # Should reach "oracle" (non-trivial predicate; z3 may or may not
            # handle it depending on availability)
            {
                "obligation_id": "ob-nontrivial-01",
                "predicate": "forall x in X: f(x) is well_defined",
                "kind": "safety",
                "label": "well-definedness of f",
            },
            # A deliberately empty predicate → should FAIL all back-ends
            {
                "obligation_id": "ob-empty-01",
                "predicate": "",
                "kind": "unknown",
                "label": "empty obligation",
            },
            # len >= 0 tautology
            {
                "obligation_id": "ob-len-01",
                "predicate": "len(items) >= 0",
                "kind": "safety",
                "label": "non-negative length",
            },
        ],
    }

    # ------------------------------------------------------------------ #
    # 2. Build a minimal manifest with one pre-satisfied obligation        #
    # ------------------------------------------------------------------ #
    manifest: dict[str, Any] = {
        "obligation_records": {
            "ob-nontrivial-01": {
                "status": "discharged",
                "trust_tier": "AUDITED",
                "id": "evidence-ref-abc123",
            },
        },
        "obstructions": [],
        "compliance_certificates": [],
    }

    # ------------------------------------------------------------------ #
    # 3. Run the discharger                                                #
    # ------------------------------------------------------------------ #
    discharger = ObligationDischarger(
        config={
            "skip_oracle": False,
            "z3_timeout_ms": 2000,
            "partial_is_sufficient": False,
        }
    )
    result = discharger.discharge(synthetic_spec, manifest=manifest, trust_floor="PROPOSAL")

    # ------------------------------------------------------------------ #
    # 4. Print the summary report                                          #
    # ------------------------------------------------------------------ #
    print(result.summary_report())
    print()

    # ------------------------------------------------------------------ #
    # 5. Wire results back to the manifest                                 #
    # ------------------------------------------------------------------ #
    discharger.wire_to_manifest(result, manifest)
    print(f"Manifest now has {len(manifest['compliance_certificates'])} compliance cert(s).")
    print(f"Obstructions recorded: {len(manifest['obstructions'])}")
    print()

    # ------------------------------------------------------------------ #
    # 6. Compliance certificate                                            #
    # ------------------------------------------------------------------ #
    cert = result.to_compliance_certificate()
    print("Compliance certificate (truncated):")
    cert_preview = {k: v for k, v in cert.items() if k != "obligations"}
    print(json.dumps(cert_preview, indent=2))
    print()

    # ------------------------------------------------------------------ #
    # 7. quick_check convenience function                                  #
    # ------------------------------------------------------------------ #
    print("--- quick_check convenience ---")
    qc_result = quick_check("x == x\nlen(items) >= 0\nTrue")
    print(qc_result.summary_report())
    print()

    # ------------------------------------------------------------------ #
    # 8. discharge_obligations bare-list entry point                       #
    # ------------------------------------------------------------------ #
    print("--- discharge_obligations bare-list ---")
    bare = [
        {"obligation_id": "bare-01", "predicate": "True", "kind": "trivial"},
        {"obligation_id": "bare-02", "predicate": "x == x", "kind": "equality"},
    ]
    br = discharge_obligations(bare)
    print(f"bare-list: {br.n_discharged}/{len(br.records)} discharged in {br.elapsed_s:.3f}s")
    print()

    # ------------------------------------------------------------------ #
    # 9. Summarise across multiple results                                 #
    # ------------------------------------------------------------------ #
    summary = summarise_results([result, qc_result, br])
    print("Aggregate summary:")
    print(json.dumps(summary, indent=2))
    print()

    # ------------------------------------------------------------------ #
    # 10. Assertions                                                        #
    # ------------------------------------------------------------------ #
    assert len(result.records) == len(synthetic_spec["obligations"]), \
        "Record count should match obligation count."
    assert result.n_discharged > 0, "At least some obligations should be discharged."
    assert all(r.cohomology_class is not None for r in result.records), \
        "Every record must have an assigned cohomology class."
    assert "certificate_type" in cert, "Compliance certificate must have a type."
    print("✓ All smoke-test assertions passed.")
