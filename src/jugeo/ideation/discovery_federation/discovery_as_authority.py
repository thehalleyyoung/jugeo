"""
Discovery as Authority — Step 1 of the Discovery Federation Pipeline.

This module implements the authority promotion pipeline for the JuGeo
Discovery Federation subsystem (theory2.tex Ch61). In the federation
model, a raw discovery result must be promoted to 'authority' status
before it can influence the shared knowledge graph. This promotion is
gated by a set of verifiable conditions:

  1. TRUST_THRESHOLD  — The originating node's trust score must exceed
                        a configurable minimum (default 0.6).
  2. QUORUM_MET       — A sufficient fraction of peer nodes must have
                        acknowledged the discovery.
  3. NOVELTY_SUFFICIENT — The discovery's novelty score must indicate
                        genuine new information (not a duplicate).
  4. REGIME_COMPATIBLE — The discovery must be compatible with the
                        currently active ideation regime.
  5. PACK_AUTHORIZED  — The relevant pack must grant authority for the
                        discovery to be registered.

The AuthorityPromoter class drives the promotion process, checking each
condition and recording the result in a PromotionRecord. The
AuthorityValidator performs deeper validation of each condition. The
AuthorityLifecycleManager handles grant issuance, revocation, and expiry.
The DiscoveryAuthorityRunner orchestrates the full pipeline end-to-end.

copilot: shared-core marker
theory2.tex Ch61 — Federated Discovery Authority
"""

from __future__ import annotations

import time
import uuid
import logging
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Guarded cross-module imports — optional JuGeo internals
# ---------------------------------------------------------------------------
try:
    from jugeo.core.registry import NodeRegistry  # type: ignore
except ImportError:
    NodeRegistry = None  # type: ignore

try:
    from jugeo.ideation.regime import RegimeManager  # type: ignore
except ImportError:
    RegimeManager = None  # type: ignore

try:
    from jugeo.packs.authority import PackAuthorityGateway  # type: ignore
except ImportError:
    PackAuthorityGateway = None  # type: ignore

try:
    from jugeo.telemetry import trace  # type: ignore
except ImportError:
    trace = None  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    "PromotionStatus",
    "AuthorityCondition",
    "PromotionRecord",
    "AuthorityPromoter",
    "AuthorityValidator",
    "AuthorityLifecycleManager",
    "DiscoveryAuthorityRunner",
    "promote_to_authority",
    "validate_authority_conditions",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    This thin wrapper around ``time.time()`` exists so that every timestamp
    produced by this module is generated through a single, easily-patchable
    call site. In testing, callers can monkeypatch ``_utcnow`` to inject
    deterministic timestamps without resorting to freezegun or system-clock
    manipulation.

    Returns:
        float: Seconds since the Unix epoch (UTC), as returned by
               ``time.time()``. Fractional seconds are preserved, giving
               sub-millisecond resolution on most platforms.

    Example::

        t0 = _utcnow()
        time.sleep(0.01)
        t1 = _utcnow()
        assert t1 > t0
    """
    return time.time()


def _uid() -> str:
    """Generate a globally-unique, URL-safe identifier string.

    The identifier is derived from a UUID4 (randomly generated) value,
    formatted without hyphens to produce a compact 32-character hex string.
    The absence of hyphens makes the ID safe for use in filenames, JSON
    keys, and URL path segments without percent-encoding.

    Collision probability follows the standard UUID4 birthday-paradox curve:
    roughly 1-in-2^61 after 2^30 generated IDs, which is negligible for all
    practical JuGeo deployment scales.

    Returns:
        str: A 32-character lowercase hexadecimal string uniquely identifying
             a resource within the JuGeo federation.

    Example::

        id1 = _uid()
        id2 = _uid()
        assert id1 != id2
        assert len(id1) == 32
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a floating-point value to the closed interval [lo, hi].

    Many authority-promotion scores are defined on the unit interval [0, 1].
    Upstream subsystems may occasionally produce scores slightly outside this
    range due to floating-point rounding errors or misconfigured normalisation
    pipelines. ``_clamp`` provides a single, tested call site for bounding
    such values before they are consumed by threshold comparisons.

    Args:
        value (float): The raw score or weight to clamp.
        lo (float): The lower bound of the valid range. Defaults to 0.0.
        hi (float): The upper bound of the valid range. Defaults to 1.0.

    Returns:
        float: The value unchanged if it lies within [lo, hi]; ``lo`` if the
               value is below the lower bound; ``hi`` if above the upper bound.

    Raises:
        ValueError: If ``lo`` > ``hi``, indicating an invalid interval.

    Example::

        assert _clamp(1.5) == 1.0
        assert _clamp(-0.1) == 0.0
        assert _clamp(0.7) == 0.7
    """
    if lo > hi:
        raise ValueError(f"_clamp: lower bound {lo!r} exceeds upper bound {hi!r}")
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class PromotionStatus(str, Enum):
    """Status values for a discovery-to-authority promotion attempt.

    These states model the full lifecycle of a promotion request, from
    initial eligibility assessment through to eventual grant or revocation.
    By inheriting from ``str`` the enum values can be used directly as JSON
    strings without extra serialisation steps.
    """

    ELIGIBLE   = "eligible"    # Discovery passed pre-checks; promotion may proceed
    INELIGIBLE = "ineligible"  # Discovery failed one or more required conditions
    PENDING    = "pending"     # Promotion is awaiting quorum or external approval
    GRANTED    = "granted"     # Authority has been formally conferred on the discovery
    REVOKED    = "revoked"     # A previously granted authority has been withdrawn


class AuthorityCondition(str, Enum):
    """Named conditions that must be satisfied for authority promotion.

    Each member corresponds to one verification step in the AuthorityPromoter
    pipeline. Storing condition names as enum members—rather than bare
    strings—prevents typos and makes exhaustive-condition tooling easier to
    implement in IDEs and static analysis passes.
    """

    TRUST_THRESHOLD    = "trust_threshold"    # Node trust score is above the minimum
    QUORUM_MET         = "quorum_met"         # Enough peers have acknowledged the discovery
    NOVELTY_SUFFICIENT = "novelty_sufficient" # Discovery contains genuinely new information
    REGIME_COMPATIBLE  = "regime_compatible"  # Discovery aligns with current ideation regime
    PACK_AUTHORIZED    = "pack_authorized"    # Owning pack has explicitly granted authority


# ---------------------------------------------------------------------------
# PromotionRecord dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PromotionRecord:
    """Immutable record describing the outcome of a single promotion attempt.

    ``PromotionRecord`` is the primary data transfer object flowing between
    the AuthorityPromoter, AuthorityValidator, and AuthorityLifecycleManager.
    It is deliberately frozen and slotted to enable safe sharing across
    threads, hashing inside sets, and efficient memory layout when thousands
    of records accumulate in the promotion history.

    Attributes:
        record_id (str): Unique identifier for this promotion record.
        discovery_id (str): Identifier of the discovery being promoted.
        status (PromotionStatus): Final status of the promotion attempt.
        conditions_met (tuple[str, ...]): Ordered tuple of condition names
            that were satisfied during evaluation.
        timestamp (float): UTC POSIX timestamp at record creation time.
    """

    record_id:      str
    discovery_id:   str
    status:         PromotionStatus
    conditions_met: tuple[str, ...]
    timestamp:      float

    @classmethod
    def create(
        cls,
        discovery_id: str,
        status: PromotionStatus,
        conditions_met: list[str],
    ) -> "PromotionRecord":
        """Construct a new PromotionRecord with auto-generated ID and timestamp.

        Factory method that encapsulates the creation of identifiers and
        timestamps, keeping callers free from having to call ``_uid()`` and
        ``_utcnow()`` directly. This also ensures that every record produced
        by the system passes through exactly one code path, simplifying
        auditing.

        Args:
            discovery_id (str): The identifier of the discovery being
                promoted. Should match ``discovery['id']`` in the upstream
                discovery dict.
            status (PromotionStatus): The outcome of the promotion attempt.
            conditions_met (list[str]): List of condition-name strings
                (``AuthorityCondition`` values) that were satisfied.

        Returns:
            PromotionRecord: A new, frozen promotion record instance ready for
                storage or transmission.
        """
        return cls(
            record_id=_uid(),
            discovery_id=discovery_id,
            status=status,
            conditions_met=tuple(conditions_met),
            timestamp=_utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain Python dictionary.

        Produces a JSON-compatible representation of the record, converting
        enum values to their underlying string equivalents so that the result
        can be passed directly to ``json.dumps`` without a custom encoder.
        All fields are included; no information is omitted.

        Returns:
            dict[str, Any]: A dictionary with keys ``record_id``,
                ``discovery_id``, ``status``, ``conditions_met``, and
                ``timestamp``. The ``conditions_met`` value is a list of
                strings (not a tuple) to maintain JSON compatibility.
        """
        return {
            "record_id":      self.record_id,
            "discovery_id":   self.discovery_id,
            "status":         self.status.value,
            "conditions_met": list(self.conditions_met),
            "timestamp":      self.timestamp,
        }

    def summary(self) -> str:
        """Return a concise, human-readable description of the promotion outcome.

        Intended for log lines, CLI output, and diagnostic dashboards where
        a single line of text must convey the most important facts about a
        promotion attempt. The summary includes the discovery ID (truncated
        to 8 characters for brevity), the final status, the number of
        conditions met, and the ISO-formatted timestamp.

        Returns:
            str: A single-line summary string, for example:
                ``"[PromotionRecord a1b2c3d4] status=granted "``
                ``"conditions_met=3 ts=2024-01-01T00:00:00Z"``.
        """
        short_did = self.discovery_id[:8] if self.discovery_id else "?"
        n = len(self.conditions_met)
        ts_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp))
        return (
            f"[PromotionRecord {self.record_id[:8]}] "
            f"discovery={short_did}... status={self.status.value} "
            f"conditions_met={n} ts={ts_str}"
        )


# ---------------------------------------------------------------------------
# AuthorityPromoter
# ---------------------------------------------------------------------------

class AuthorityPromoter:
    """Evaluates discovery dicts against configurable thresholds and emits
    PromotionRecords representing the outcome of each evaluation pass.

    The ``AuthorityPromoter`` is the first active component in the Discovery
    Federation authority pipeline (theory2.tex §61.2). It receives raw
    discovery dictionaries—typically emitted by a discovery node after a
    successful ideation run—and applies a sequence of lightweight, in-process
    condition checks. Because these checks are intentionally fast (no I/O,
    no cross-node calls), the promoter is safe to run on the hot path of the
    federation ingestion loop.

    The five conditions evaluated by this class correspond to the five
    ``AuthorityCondition`` enum members. All five must be satisfied for a
    discovery to receive GRANTED status. Failures result in INELIGIBLE status
    and a PromotionRecord documenting exactly which conditions were not met,
    enabling downstream diagnostics and targeted remediation.

    The promoter maintains an internal history of all records it has emitted
    since its last ``clear_history()`` call. This history is useful for
    batch-processing audits, unit testing, and live monitoring dashboards
    that wish to track promotion rates over time.

    Instance configuration is fully mutable after construction via the
    ``set_trust_threshold`` and ``set_novelty_threshold`` methods, allowing
    operators to adjust thresholds at runtime without restarting the process.

    Thread safety: this class is NOT thread-safe. Callers that share a single
    ``AuthorityPromoter`` across threads must provide their own locking.

    Attributes:
        _trust_threshold (float): Minimum trust score for promotion.
        _novelty_threshold (float): Minimum novelty score for promotion.
        _promotion_history (list[PromotionRecord]): Accumulated records.
    """

    def __init__(
        self,
        trust_threshold: float = 0.6,
        novelty_threshold: float = 0.4,
        quorum_size: int = 3,
    ) -> None:
        """Initialise the AuthorityPromoter with threshold configuration.

        Both thresholds are clamped to [0, 1] on entry to guard against
        misconfigured callers passing values such as percentages (60 instead
        of 0.6) or negative weights. A DEBUG log is emitted so that operators
        can confirm threshold values at startup.

        Args:
            trust_threshold (float): Minimum trust score a discovery's
                originating node must have for the TRUST_THRESHOLD condition
                to be met. Defaults to 0.6.
            novelty_threshold (float): Minimum novelty score the discovery
                itself must carry for the NOVELTY_SUFFICIENT condition to be
                met. Defaults to 0.4.
        """
        self._trust_threshold: float = _clamp(trust_threshold)
        self._novelty_threshold: float = _clamp(novelty_threshold)
        self._quorum_size: int = max(1, int(quorum_size))
        self._promotion_history: list[PromotionRecord] = []
        logger.debug(
            "AuthorityPromoter initialised: trust_threshold=%s "
            "novelty_threshold=%s",
            self._trust_threshold,
            self._novelty_threshold,
        )

    def check_trust(self, discovery: dict) -> bool:
        """Check whether a discovery's trust score meets the configured threshold.

        Reads ``discovery['trust_score']``, clamps it to [0, 1], and compares
        it against ``_trust_threshold``. A missing key is treated as a trust
        score of 0.0, which will fail the check unless the threshold has been
        set to 0.

        Args:
            discovery (dict): The discovery dictionary to inspect. Expected
                to contain an optional ``trust_score`` key whose value is a
                float in [0, 1].

        Returns:
            bool: ``True`` if ``discovery.get('trust_score', 0.0)`` after
                clamping is greater than or equal to ``_trust_threshold``;
                ``False`` otherwise.
        """
        raw = discovery.get("trust_score", 0.0)
        score = _clamp(float(raw))
        result = score >= self._trust_threshold
        logger.debug("check_trust: score=%s threshold=%s -> %s", score, self._trust_threshold, result)
        return result

    def check_novelty(self, discovery: dict, existing_ids: list[str] | None = None) -> bool:
        """Check whether a discovery's novelty score meets the configured threshold.

        Novelty scores express how much new information a discovery carries
        relative to the current knowledge graph. A score near 1.0 means the
        discovery is entirely new; a score near 0.0 means it duplicates
        existing nodes. Scores below ``_novelty_threshold`` are rejected to
        avoid cluttering the knowledge graph with redundant entries.

        Args:
            discovery (dict): The discovery dictionary. Expected to contain
                an optional ``novelty_score`` key whose value is a float in
                [0, 1]. Missing key defaults to 0.0.

        Returns:
            bool: ``True`` if the clamped novelty score is at least
                ``_novelty_threshold``; ``False`` otherwise.
        """
        discovery_id = discovery.get("discovery_id", discovery.get("id"))
        if existing_ids is not None and discovery_id is not None:
            return discovery_id not in set(existing_ids)
        if "novelty" in discovery:
            return bool(discovery.get("novelty"))
        raw = discovery.get("novelty_score", 1.0)
        score = _clamp(float(raw))
        result = score >= self._novelty_threshold
        logger.debug("check_novelty: score=%s threshold=%s -> %s", score, self._novelty_threshold, result)
        return result

    def check_conditions(self, discovery: dict, context: dict | None = None) -> dict[str, bool]:
        """Run all promotion condition checks and return their boolean outcomes.

        Iterates over every defined ``AuthorityCondition`` and evaluates the
        corresponding check function. Conditions whose check functions are not
        directly implemented on this class (e.g. QUORUM_MET, REGIME_COMPATIBLE,
        PACK_AUTHORIZED) use sensible defaults based on fields present in the
        discovery dict, making the promoter self-contained without requiring
        injected collaborators.

        Args:
            discovery (dict): The discovery dictionary to evaluate. All
                condition checks are run; there is no short-circuit behaviour.

        Returns:
            list[str]: An ordered list of ``AuthorityCondition`` value strings
                for each condition that was satisfied. The list may be empty if
                all checks fail, or contain up to five entries if all pass.
        """
        context = context or {}
        trust_ok = self.check_trust(discovery)
        novelty_ok = self.check_novelty(discovery)
        quorum_ok = bool(
            context.get(
                "quorum_reached",
                discovery.get("quorum_reached", discovery.get("quorum_size", self._quorum_size) >= self._quorum_size),
            )
        )
        regime_ok = discovery.get("regime", context.get("regime", "default")) == context.get("regime", discovery.get("regime", "default"))
        allow_ok = bool(context.get("allow_promotion", True))
        return {
            "trust": trust_ok,
            "novelty": novelty_ok,
            "quorum": quorum_ok,
            "regime": regime_ok,
            "allowed": allow_ok,
        }

    def promote(self, discovery: dict, context: dict | None = None) -> dict | None:
        """Evaluate a single discovery dict and return a PromotionRecord.

        Runs ``check_conditions`` to determine which of the five authority
        conditions are satisfied. If all five are met, the record receives
        GRANTED status; otherwise INELIGIBLE. The record is appended to the
        internal promotion history before being returned.

        Args:
            discovery (dict): The discovery to evaluate. Must contain at least
                an ``id`` field; all other fields are optional with defined
                defaults.

        Returns:
            PromotionRecord: A frozen record capturing the outcome. Callers
                should inspect ``record.status`` to determine whether the
                discovery was successfully promoted.
        """
        discovery_id = discovery.get("discovery_id", discovery.get("id", _uid()))
        checks = self.check_conditions(discovery, context)
        granted = all(checks.values())
        if not granted:
            result = {
                "discovery_id": discovery_id,
                "grant_id": None,
                "trust_score": float(discovery.get("trust_score", 0.0)),
                "regime": discovery.get("regime", (context or {}).get("regime", "default")),
                "status": "rejected",
                "conditions": checks,
            }
            self._promotion_history.append(result)
            return result

        grant = {
            "discovery_id": discovery_id,
            "grant_id": _uid(),
            "trust_score": float(discovery.get("trust_score", 0.0)),
            "regime": discovery.get("regime", (context or {}).get("regime", "default")),
            "status": "granted",
            "conditions": checks,
            "active": True,
        }
        self._promotion_history.append(grant)
        return grant

    def batch_promote(self, discoveries: list[dict], context: dict | None = None) -> list[dict]:
        """Promote a list of discovery dicts, returning one record per input.

        Iterates through ``discoveries`` in order, calling ``promote()`` for
        each element. Errors raised by individual promote calls are caught,
        logged, and translated into INELIGIBLE records so that a single bad
        discovery cannot abort processing for the remainder of the batch.

        Args:
            discoveries (list[dict]): A list of discovery dictionaries to
                promote. May be empty, in which case an empty list is returned.

        Returns:
            list[PromotionRecord]: A list of promotion records in the same
                order as the input discoveries. Length always equals
                ``len(discoveries)``.
        """
        results: list[dict] = []
        for disc in discoveries:
            try:
                results.append(self.promote(disc, context))
            except Exception as exc:  # noqa: BLE001
                did = disc.get("id", "unknown")
                logger.warning("batch_promote: error promoting %s: %s", did, exc)
                results.append(
                    {
                        "discovery_id": did,
                        "grant_id": None,
                        "status": "failed",
                        "active": False,
                    }
                )
        return results

    def get_promotion_history(self) -> list[PromotionRecord | dict]:
        """Return a snapshot copy of the internal promotion history list.

        Returns a shallow copy so that callers cannot mutate the promoter's
        internal state by modifying the returned list. Individual
        ``PromotionRecord`` objects are frozen dataclasses and are therefore
        safe to share without copying.

        Returns:
            list[PromotionRecord]: All PromotionRecords emitted since the last
                ``clear_history()`` call, in chronological order.
        """
        return list(self._promotion_history)

    def clear_history(self) -> None:
        """Clear the accumulated promotion history.

        Discards all PromotionRecords held in the internal history list.
        Useful at the end of a processing epoch, during test teardown, or
        when memory pressure requires releasing old records. After calling
        this method ``get_promotion_history()`` will return an empty list.

        Returns:
            None
        """
        count = len(self._promotion_history)
        self._promotion_history.clear()
        logger.debug("clear_history: removed %d records", count)

    def set_trust_threshold(self, threshold: float) -> None:
        """Update the trust threshold used for TRUST_THRESHOLD condition checks.

        The new threshold is clamped to [0, 1] before storage, matching the
        behaviour of the constructor. Changing the threshold takes effect
        immediately for all subsequent calls to ``check_trust`` and
        ``promote``; existing PromotionRecords in history are not affected.

        Args:
            threshold (float): The new minimum trust score. Values outside
                [0, 1] are clamped silently.

        Returns:
            None
        """
        self._trust_threshold = _clamp(threshold)
        logger.debug("set_trust_threshold: new value=%s", self._trust_threshold)

    def set_novelty_threshold(self, threshold: float) -> None:
        """Update the novelty threshold used for NOVELTY_SUFFICIENT condition checks.

        Mirrors ``set_trust_threshold`` in behaviour. The updated threshold
        is clamped to [0, 1] and takes effect immediately for all subsequent
        promotions. Historical records are unaffected.

        Args:
            threshold (float): The new minimum novelty score. Values outside
                [0, 1] are clamped silently.

        Returns:
            None
        """
        self._novelty_threshold = _clamp(threshold)
        logger.debug("set_novelty_threshold: new value=%s", self._novelty_threshold)

    def summary(self) -> str:
        """Return a human-readable summary of the promoter's current state.

        Reports the configured thresholds, the total number of promotion
        records in history, and a breakdown of records by status. Intended
        for operator dashboards, CLI ``status`` commands, and DEBUG log lines
        at shutdown time.

        Returns:
            str: A multi-line string summarising the promoter configuration
                and history statistics.
        """
        total = len(self._promotion_history)
        granted = sum(
            1
            for r in self._promotion_history
            if (r.get("status") if isinstance(r, dict) else r.status)
            in ("granted", PromotionStatus.GRANTED)
        )
        ineligible = total - granted
        return (
            f"AuthorityPromoter("
            f"trust_threshold={self._trust_threshold}, "
            f"novelty_threshold={self._novelty_threshold}, "
            f"history_size={total}, "
            f"granted={granted}, "
            f"ineligible={ineligible})"
        )


# ---------------------------------------------------------------------------
# AuthorityValidator
# ---------------------------------------------------------------------------

class AuthorityValidator:
    """Performs deep, rule-based validation of individual authority conditions
    for discoveries under consideration for federation promotion.

    While ``AuthorityPromoter`` provides fast, lightweight threshold checks
    suitable for high-throughput ingestion, ``AuthorityValidator`` is designed
    for use in the slower, more thorough validation phase that follows initial
    screening (theory2.tex §61.4). It accumulates failures and warnings
    across multiple calls in a single validation session, giving callers a
    comprehensive view of why a discovery may have failed rather than just
    a binary pass/fail outcome.

    Each ``validate_*`` method checks one condition and appends a failure
    message to the internal ``_failures`` list if the condition is not met,
    or a warning to ``_warnings`` if the condition is borderline. Callers
    can inspect ``get_failures()`` and ``get_warnings()`` after a validation
    run to drive remediation workflows or to populate user-facing error
    messages in the federation console.

    The validator is stateful within a single validation session. To reuse
    it across multiple discoveries, callers must call ``reset()`` between
    sessions; failing to do so will cause failures from previous discoveries
    to contaminate subsequent results.

    Thread safety: not thread-safe. Use one instance per thread or serialise
    access externally.

    Attributes:
        _failures (list[str]): Accumulated failure messages for the current session.
        _warnings (list[str]): Accumulated warning messages for the current session.
    """

    def __init__(self, strict: bool = True) -> None:
        """Initialise the AuthorityValidator with empty failure and warning lists.

        Constructs two empty mutable lists to hold failure and warning
        messages accumulated during a validation session. No configuration
        parameters are accepted at construction time; all thresholds and
        reference values are passed per-call to the ``validate_*`` methods,
        making the validator purely stateless with respect to configuration
        and therefore easy to share across multiple concurrent validation
        campaigns that use different threshold settings.

        Returns:
            None
        """
        self._failures: list[str] = []
        self._warnings: list[str] = []
        self._strict = strict

    def validate_trust(self, discovery: dict, threshold: float = 0.7) -> bool:
        """Validate that a discovery's trust score meets the supplied threshold.

        Reads ``discovery.get('trust_score', 0.0)``, clamps it, and compares
        it against the threshold. On failure, appends a descriptive message to
        ``_failures``. A borderline score (within 0.05 of the threshold above
        the threshold) appends a warning to alert operators that the discovery
        is close to the boundary.

        Args:
            discovery (dict): Discovery dictionary containing an optional
                ``trust_score`` float field.
            threshold (float): Minimum acceptable trust score, in [0, 1].

        Returns:
            bool: ``True`` if the clamped trust score >= threshold.
        """
        score = _clamp(float(discovery.get("trust_score", 0.0)))
        threshold = _clamp(threshold)
        if score < threshold:
            self._failures.append(
                f"TRUST_THRESHOLD failed: score={score:.3f} < threshold={threshold:.3f}"
            )
            return False
        if score < threshold + 0.05:
            self._warnings.append(
                f"TRUST_THRESHOLD borderline: score={score:.3f} just above threshold={threshold:.3f}"
            )
        return True

    def validate_novelty(self, discovery: dict, threshold: float = 0.5) -> bool:
        """Validate that a discovery's novelty score meets the supplied threshold.

        Reads ``discovery.get('novelty_score', 0.0)``, clamps, and compares.
        A zero novelty score is treated as a hard failure with a distinct error
        message indicating that the discovery may be a duplicate, since a truly
        novel discovery should always carry a non-zero score.

        Args:
            discovery (dict): Discovery dictionary containing an optional
                ``novelty_score`` float field.
            threshold (float): Minimum acceptable novelty score, in [0, 1].

        Returns:
            bool: ``True`` if the clamped novelty score >= threshold.
        """
        confirmed = discovery.get("novelty_confirmed")
        if confirmed is not None:
            if not confirmed:
                self._failures.append("novelty")
            return bool(confirmed)
        score = _clamp(float(discovery.get("novelty_score", 0.0)))
        threshold = _clamp(threshold)
        if score < threshold:
            self._failures.append("novelty")
            return False
        return True

    def validate_quorum(self, discovery: dict, quorum: float | None = None, quorum_size: int | None = None) -> bool:
        """Validate that a discovery has received sufficient peer acknowledgements.

        Computes the acknowledgement ratio as ``acknowledgement_count /
        max(1, peer_count)`` and compares it against ``quorum``. If
        ``peer_count`` is 0 or absent, the denominator is clamped to 1 to
        avoid division-by-zero, and a warning is emitted because a
        zero-peer-count is unusual and may indicate a misconfigured node.

        Args:
            discovery (dict): Discovery dictionary. Relevant keys are
                ``acknowledgement_count`` (int) and ``peer_count`` (int).
            quorum (float): Minimum acknowledgement ratio required, in [0, 1].

        Returns:
            bool: ``True`` if the computed ratio >= quorum.
        """
        required = quorum_size if quorum_size is not None else quorum
        grant_quorum = int(discovery.get("quorum_size", discovery.get("acknowledgement_count", 0)))
        if required is None:
            required = 1
        if grant_quorum < int(required):
            self._failures.append("quorum")
            return False
        return True

    def validate_regime_compatibility(self, discovery: dict, regime_id: str | None = None, regime: str | None = None) -> bool:
        """Validate that a discovery's regime matches the currently active regime.

        Reads ``discovery.get('regime_id')`` and compares it against the
        supplied ``regime_id`` string using an equality check. A missing
        ``regime_id`` field is treated as an implicit failure because
        undeclared regime membership is a sign of a malformed or legacy
        discovery that should not be auto-promoted.

        Args:
            discovery (dict): Discovery dictionary. Expected to contain a
                ``regime_id`` string field.
            regime_id (str): The identifier of the currently active ideation
                regime as known to the federation controller.

        Returns:
            bool: ``True`` if ``discovery['regime_id'] == regime_id``.
        """
        target_regime = regime if regime is not None else regime_id if regime_id is not None else "default"
        disc_regime = discovery.get("regime", discovery.get("regime_id", "default"))
        if disc_regime != target_regime:
            self._failures.append("regime")
            return False
        return True

    def validate_all(
        self,
        discovery: dict,
        trust_threshold: float = 0.7,
        novelty_threshold: float = 0.5,
        quorum: float = 3,
        regime_id: str = "default",
    ) -> dict[str, bool]:
        """Run all five validation checks and accumulate failures and warnings.

        Calls each ``validate_*`` method in turn (no short-circuit). This
        ensures that a complete picture of all failures is available after the
        call, rather than stopping at the first failure and hiding potentially
        valuable diagnostic information about subsequent condition failures.

        Args:
            discovery (dict): The discovery dictionary to validate.
            trust_threshold (float): Minimum trust score for TRUST_THRESHOLD.
            novelty_threshold (float): Minimum novelty score for NOVELTY_SUFFICIENT.
            quorum (float): Minimum acknowledgement ratio for QUORUM_MET.
            regime_id (str): Active regime identifier for REGIME_COMPATIBLE.

        Returns:
            bool: ``True`` only if all five conditions passed with no
                failures recorded.
        """
        self.reset()
        return {
            "trust": self.validate_trust(discovery, trust_threshold),
            "novelty": self.validate_novelty(discovery, novelty_threshold),
            "quorum": self.validate_quorum(discovery, quorum_size=int(quorum)),
            "regime": self.validate_regime_compatibility(discovery, regime=regime_id),
        }

    def validate(self, discovery: dict) -> bool:
        """Compatibility helper returning a single boolean verdict."""
        return all(self.validate_all(discovery).values())

    def get_failures(self, discovery: dict | None = None) -> list[str]:
        """Return a copy of the accumulated failure messages for this session.

        Failures are messages that indicate a condition was definitively not
        met. Each message is a descriptive string identifying which condition
        failed and why.

        Returns:
            list[str]: A shallow copy of ``_failures``. Modifying the
                returned list does not affect the validator's internal state.
        """
        if discovery is not None:
            results = self.validate_all(discovery)
            return [name for name, ok in results.items() if not ok]
        return list(self._failures)

    def get_warnings(self) -> list[str]:
        """Return a copy of the accumulated warning messages for this session.

        Warnings indicate borderline conditions or configuration anomalies
        that did not cause a hard failure but may warrant operator attention.

        Returns:
            list[str]: A shallow copy of ``_warnings``. Modifying the
                returned list does not affect the validator's internal state.
        """
        return list(self._warnings)

    def reset(self) -> None:
        """Clear all accumulated failures and warnings, resetting the session.

        Must be called between validation sessions when the same validator
        instance is reused across multiple discoveries. After ``reset()``,
        both ``get_failures()`` and ``get_warnings()`` will return empty
        lists.

        Returns:
            None
        """
        self._failures.clear()
        self._warnings.clear()

    def summary(self) -> str:
        """Return a concise description of validation session results.

        Produces a single-line string suitable for log output that reports
        the number of failures and warnings accumulated so far in the current
        session, along with a pass/fail verdict.

        Returns:
            str: Summary string, e.g.
                ``"AuthorityValidator: PASS (0 failures, 1 warning)"``.
        """
        nf = len(self._failures)
        nw = len(self._warnings)
        verdict = "PASS" if nf == 0 else "FAIL"
        return f"AuthorityValidator: {verdict} ({nf} failures, {nw} warnings)"


# ---------------------------------------------------------------------------
# AuthorityLifecycleManager
# ---------------------------------------------------------------------------

class AuthorityLifecycleManager:
    """Manages the full lifecycle of authority grants within the federation,
    including issuance, active tracking, revocation, expiry, and pruning.

    In the JuGeo Discovery Federation model, being 'promoted' to authority
    is not a permanent state. An authority grant has an associated time-to-live
    (TTL) and can be explicitly revoked by the originating pack, the federation
    controller, or automated health-check processes (theory2.tex §61.6). The
    ``AuthorityLifecycleManager`` is the single source of truth for which
    authority grants are currently active, which have been revoked, and which
    have expired naturally.

    Each grant is stored as a plain Python dictionary, making it easy to
    serialise to JSON for persistence, transmission over the federation bus,
    or storage in the JuGeo state backend. Revocations are kept in a separate
    list rather than deleted, preserving the full audit trail needed by
    compliance and forensic analysis tools.

    Expiry checks are performed lazily (at query time via ``is_active``) and
    proactively (via ``prune_expired``). This dual approach lets high-throughput
    code paths avoid the O(n) scan of ``prune_expired`` while still allowing
    scheduled maintenance jobs to reclaim memory by removing stale entries.

    Thread safety: not thread-safe. Callers must serialise access externally
    if multiple threads share an instance.

    Attributes:
        _grants (dict[str, dict]): Mapping from authority_id to grant record.
        _revocations (list[dict]): Records for all revoked grants.
    """

    def __init__(self) -> None:
        """Initialise the lifecycle manager with empty grant and revocation stores.

        Creates an empty dictionary for active grants and an empty list for
        revocation records. No configuration is required at construction time;
        all relevant parameters (TTL, grantee identity, reasons) are supplied
        per-call to the lifecycle methods.

        Returns:
            None
        """
        self._grants: dict[str, dict] = {}
        self._revocations: list[dict] = []

    def grant(
        self,
        authority_id: str | dict,
        discovery_id: str | None = None,
        granted_by: str = "AuthorityLifecycleManager",
        reason: str = "",
        ttl: float = 86400.0,
    ) -> dict | str:
        """Issue a new authority grant and register it as active.

        Creates a grant record containing the grant metadata (identifiers,
        grantee, reason, timestamps, expiry) and stores it in ``_grants``
        keyed by ``authority_id``. If a grant with the same ``authority_id``
        already exists, it is silently overwritten — callers should use
        ``refresh()`` if they intend to extend an existing grant's TTL.

        Args:
            authority_id (str): Unique identifier for this authority grant.
                Typically the ``record_id`` of a GRANTED PromotionRecord.
            discovery_id (str): The discovery this grant covers.
            granted_by (str): Identity string of the granting entity (pack ID,
                node ID, or controller handle).
            reason (str): Human-readable reason for the grant, e.g.
                "Passed all five authority conditions in epoch 42".
            ttl (float): Time-to-live in seconds. Defaults to 86400 (24 h).

        Returns:
            dict: The newly created grant record dictionary, including keys
                ``authority_id``, ``discovery_id``, ``granted_by``, ``reason``,
                ``granted_at``, ``expires_at``, and ``status``.
        """
        if isinstance(authority_id, dict):
            grant_id = authority_id.get("grant_id", authority_id.get("authority_id", _uid()))
            record = dict(authority_id)
            record.setdefault("grant_id", grant_id)
            record.setdefault("authority_id", grant_id)
            record.setdefault("discovery_id", authority_id.get("discovery_id", discovery_id or ""))
            record.setdefault("expires_at", _utcnow() + ttl)
            record["active"] = not authority_id.get("is_expired", False)
            record["status"] = "active" if record["active"] else "expired"
            self._grants[grant_id] = record
            return grant_id

        now = _utcnow()
        record = {
            "authority_id": authority_id,
            "grant_id": authority_id,
            "discovery_id": discovery_id or "",
            "granted_by": granted_by,
            "reason": reason,
            "granted_at": now,
            "expires_at": now + ttl,
            "status": "active",
            "active": True,
        }
        self._grants[authority_id] = record
        logger.info(
            "grant: authority_id=%s discovery_id=%s expires_in=%.0fs",
            authority_id, discovery_id, ttl,
        )
        return record

    def issue_grant(self, promotion_result: dict) -> dict:
        """Compatibility wrapper that persists a dict-style promotion result."""
        discovery = promotion_result.get("discovery", promotion_result)
        grant_id = promotion_result.get("grant_id", _uid())
        self.grant(
            {
                "grant_id": grant_id,
                "discovery_id": discovery.get("discovery_id", discovery.get("id", "")),
                "trust_score": discovery.get("trust_score", promotion_result.get("trust_score", 0.0)),
                "regime": discovery.get("regime", promotion_result.get("regime", "default")),
                "active": True,
            }
        )
        return dict(self._grants[grant_id])

    def issue_grant_with_consensus(self, discovery: dict, consensus_result: dict) -> dict | None:
        """Only issue active grants when consensus is accepted."""
        outcome = str(consensus_result.get("outcome", consensus_result.get("status", ""))).upper()
        if outcome and outcome not in {"ACCEPTED", "GRANTED", "APPROVED"}:
            grant = {
                "grant_id": _uid(),
                "discovery_id": discovery.get("discovery_id", discovery.get("id", "")),
                "trust_score": discovery.get("trust_score", 0.0),
                "status": "DENIED",
                "active": False,
            }
            return grant
        return self.issue_grant({"discovery": discovery, "status": "granted"})

    def revoke(self, authority_id: str, reason: str = "") -> bool:
        """Revoke an active authority grant, moving it to the revocation log.

        Removes the grant from ``_grants`` and appends an updated copy (with
        ``status='revoked'`` and ``revoked_at`` timestamp) to ``_revocations``.
        Attempting to revoke a non-existent grant is a no-op that returns
        ``False``.

        Args:
            authority_id (str): The identifier of the grant to revoke.
            reason (str): Human-readable revocation reason. Defaults to
                an empty string if not supplied.

        Returns:
            bool: ``True`` if a grant was found and revoked; ``False`` if
                no grant with the given ID existed in ``_grants``.
        """
        grant = self._grants.pop(authority_id, None)
        if grant is None:
            logger.warning("revoke: authority_id=%s not found", authority_id)
            return False
        grant = dict(grant)
        grant["status"]     = "revoked"
        grant["active"] = False
        grant["revoked_at"] = _utcnow()
        grant["revoke_reason"] = reason
        self._revocations.append(grant)
        logger.info("revoke: authority_id=%s reason=%r", authority_id, reason)
        return True

    def expire(self, authority_id: str) -> bool:
        """Mark an active authority grant as expired due to TTL exhaustion.

        Functionally similar to ``revoke()`` but sets ``status='expired'``
        rather than ``'revoked'``, allowing downstream consumers to
        distinguish between deliberate revocations and natural expiry. The
        expired record is appended to ``_revocations`` alongside normal
        revocations for a unified audit trail.

        Args:
            authority_id (str): The identifier of the grant to expire.

        Returns:
            bool: ``True`` if the grant existed and was expired; ``False``
                if not found.
        """
        grant = self._grants.get(authority_id)
        if grant is None:
            return False
        grant = dict(grant)
        grant["status"]     = "expired"
        grant["active"] = False
        grant["is_expired"] = True
        grant["expired_at"] = _utcnow()
        self._grants[authority_id] = grant
        self._revocations.append(grant)
        logger.debug("expire: authority_id=%s", authority_id)
        return True

    def refresh(self, authority_id: str, ttl: float = 86400.0, new_expiry: str | None = None) -> bool:
        """Extend the TTL of an active authority grant.

        Updates ``expires_at`` to ``_utcnow() + ttl``, effectively resetting
        the grant's remaining lifetime. This is the preferred way to keep
        long-lived grants alive without re-running the full promotion pipeline.
        Refreshing an expired or revoked grant (i.e., one no longer in
        ``_grants``) is a no-op that returns ``False``.

        Args:
            authority_id (str): Identifier of the grant to refresh.
            ttl (float): New time-to-live in seconds from now. Defaults to
                86400 (24 h).

        Returns:
            bool: ``True`` if the grant was found and its expiry extended;
                ``False`` if the grant is no longer in the active grants dict.
        """
        grant = self._grants.get(authority_id)
        if grant is None:
            return False
        grant["expires_at"] = _utcnow() + ttl
        grant["expiry"] = new_expiry or grant.get("expiry", "2099-12-31")
        grant["active"] = True
        grant["status"] = "active"
        grant["is_expired"] = False
        grant["refreshed_at"] = _utcnow()
        logger.debug("refresh: authority_id=%s new_ttl=%.0fs", authority_id, ttl)
        return True

    def is_active(self, authority_id: str) -> bool:
        """Check whether an authority grant currently exists and has not expired.

        Performs a lazy expiry check: if the grant's ``expires_at`` is in the
        past, the grant is moved to revocations (as expired) before returning
        ``False``. This ensures that stale grants do not accumulate silently
        in ``_grants`` when ``prune_expired`` is not called frequently.

        Args:
            authority_id (str): The grant identifier to check.

        Returns:
            bool: ``True`` if the grant exists, its status is 'active', and
                its ``expires_at`` is in the future; ``False`` otherwise.
        """
        grant = self._grants.get(authority_id)
        if grant is None:
            return False
        if grant.get("status") != "active" and not grant.get("active", False):
            return False
        if grant.get("is_expired", False):
            return False
        if _utcnow() > grant.get("expires_at", float("inf")):
            self.expire(authority_id)
            return False
        return True

    def get_active_grants(self) -> list[dict]:
        """Return copies of all currently active (non-expired) grant records.

        Performs a lazy expiry sweep before building the result list, so the
        returned grants are guaranteed to be alive at the time of the call
        (subject to the usual TOCTOU caveat for concurrent systems).

        Returns:
            list[dict]: List of active grant record dictionaries. Each dict
                includes keys as documented in ``grant()``. Returns an empty
                list if no active grants exist.
        """
        # Trigger lazy expiry for all grants
        for aid in list(self._grants.keys()):
            self.is_active(aid)
        return [
            dict(g)
            for g in self._grants.values()
            if g.get("status") == "active" or g.get("active", False)
        ]

    def get_revoked_grants(self) -> list[dict]:
        """Return copies of all revoked and expired grant records.

        Returns records from the ``_revocations`` list, which contains both
        explicitly revoked grants (``status='revoked'``) and those that
        expired naturally (``status='expired'``). The list is ordered by
        revocation/expiry time (insertion order, which is chronological).

        Returns:
            list[dict]: List of revocation record dictionaries. Each dict
                includes all original grant fields plus ``revoked_at`` or
                ``expired_at`` and an updated ``status``.
        """
        return list(self._revocations)

    def prune_expired(self) -> int:
        """Remove all expired grants from the active grants dictionary.

        Scans all entries in ``_grants``, identifies those whose ``expires_at``
        timestamp has passed, and calls ``expire()`` on each one to move them
        to the revocations list. Returns the count of pruned entries, which can
        be used to monitor TTL hygiene.

        Returns:
            int: The number of grants that were pruned (expired and moved to
                revocations) during this call.
        """
        now = _utcnow()
        to_expire = [
            aid for aid, g in self._grants.items()
            if g.get("status") == "expired" or g.get("is_expired", False) or now > g.get("expires_at", float("inf"))
        ]
        for aid in to_expire:
            grant = self._grants.pop(aid, None)
            if grant is not None and grant not in self._revocations:
                self._revocations.append(dict(grant))
        if to_expire:
            logger.debug("prune_expired: removed %d stale grants", len(to_expire))
        return len(to_expire)

    def to_dict(self) -> dict:
        """Serialise the full manager state to a plain Python dictionary.

        Produces a snapshot that can be passed to ``json.dumps`` for
        persistence or transmission. The snapshot includes both active grants
        and revocation records.

        Returns:
            dict: A dictionary with keys ``active_grants`` (list of grant
                dicts) and ``revocations`` (list of revocation dicts).
        """
        return {
            "active_grants": list(self._grants.values()),
            "revocations":   list(self._revocations),
        }

    def summary(self) -> str:
        """Return a concise summary of the manager's current grant inventory.

        Reports the total number of active grants, the number of revocations
        (including expired), and a hint about the next grant to expire if
        any active grants exist.

        Returns:
            str: A single-line summary string suitable for log output.
        """
        active = len(self._grants)
        revoked = len(self._revocations)
        next_expiry: str = "n/a"
        if self._grants:
            earliest = min(
                (g.get("expires_at", float("inf")) for g in self._grants.values()),
                default=float("inf"),
            )
            if earliest < float("inf"):
                next_expiry = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(earliest))
        return (
            f"AuthorityLifecycleManager("
            f"active={active}, revocations={revoked}, "
            f"next_expiry={next_expiry})"
        )


# ---------------------------------------------------------------------------
# DiscoveryAuthorityRunner
# ---------------------------------------------------------------------------

class DiscoveryAuthorityRunner:
    """End-to-end orchestrator for the discovery-to-authority promotion pipeline.

    The ``DiscoveryAuthorityRunner`` composes an ``AuthorityPromoter``, an
    ``AuthorityValidator``, and an ``AuthorityLifecycleManager`` into a single,
    easy-to-use facade that callers can invoke with a single ``run()`` call
    (theory2.tex §61.8). This hides the internal complexity of the three-stage
    pipeline from consumers that do not need fine-grained control over each
    stage.

    The pipeline executed by ``run()`` consists of the following steps:

      1. **Promote** — The AuthorityPromoter evaluates the discovery against
         all five conditions and emits a PromotionRecord.
      2. **Grant** — If the record status is GRANTED, the runner issues a
         lifecycle grant via the AuthorityLifecycleManager.
      3. **Record** — The full result (promotion record + grant info) is
         appended to an internal results list for later inspection.

    Running the pipeline in batch mode via ``run_batch()`` processes an
    arbitrary list of discoveries sequentially and returns one result dict
    per input, maintaining input order.

    Accumulated results are available via ``get_results()`` and can be
    cleared via ``reset()``. The ``summary()`` method provides a one-line
    status snapshot for dashboards.

    Thread safety: not thread-safe. Wrap in a lock if sharing across threads.

    Attributes:
        _promoter (AuthorityPromoter): Handles condition checking and promotion.
        _validator (AuthorityValidator): Provides deep per-condition validation.
        _lifecycle (AuthorityLifecycleManager): Issues and tracks grants.
        _results (list[dict]): Accumulated pipeline run results.
    """

    def __init__(
        self,
        trust_threshold: float = 0.6,
        novelty_threshold: float = 0.4,
        promoter: AuthorityPromoter | None = None,
        validator: AuthorityValidator | None = None,
        lifecycle: AuthorityLifecycleManager | None = None,
    ) -> None:
        """Initialise the runner and its component collaborators.

        Constructs an ``AuthorityPromoter`` configured with the supplied
        thresholds, plus freshly-initialised ``AuthorityValidator`` and
        ``AuthorityLifecycleManager`` instances. All three are owned by this
        runner and should not be mutated externally.

        Args:
            trust_threshold (float): Forwarded to ``AuthorityPromoter``.
                Minimum trust score for TRUST_THRESHOLD condition. Default 0.6.
            novelty_threshold (float): Forwarded to ``AuthorityPromoter``.
                Minimum novelty score for NOVELTY_SUFFICIENT condition. Default 0.4.
        """
        self._promoter = promoter or AuthorityPromoter(trust_threshold, novelty_threshold)
        self._validator = validator or AuthorityValidator()
        self._lifecycle = lifecycle or AuthorityLifecycleManager()
        self._results:  list[dict] = []

    def run(self, discovery: dict, context: dict | None = None) -> dict:
        """Execute the full promotion pipeline for a single discovery dict.

        Runs the AuthorityPromoter, and if the result is GRANTED, issues a
        lifecycle grant via the AuthorityLifecycleManager. The complete result
        — including the PromotionRecord, the grant dict (or None), and a
        top-level ``promoted`` boolean — is stored in ``_results`` and returned
        to the caller.

        Args:
            discovery (dict): The discovery to process. At minimum should
                contain an ``id`` field; all condition-relevant fields are
                optional with defined defaults.

        Returns:
            dict: A result dictionary with keys:
                - ``discovery_id`` (str): The discovery identifier.
                - ``promoted`` (bool): True if status is GRANTED.
                - ``record`` (dict): Serialised PromotionRecord.
                - ``grant`` (dict | None): Grant record if promoted, else None.
        """
        grant = self._promoter.promote(discovery, context)
        promoted = bool(grant and grant.get("status") == "granted")
        if promoted and grant is not None:
            persisted = self._lifecycle.issue_grant(grant)
        else:
            persisted = None

        result = {
            "discovery_id": discovery.get("discovery_id", discovery.get("id", "")),
            "promoted": promoted,
            "status": "granted" if promoted else "rejected",
            "grant": persisted,
        }
        self._results.append(result)
        return result

    def run_batch(self, discoveries: list[dict], context: dict | None = None) -> list[dict]:
        """Run the full promotion pipeline for each discovery in a list.

        Iterates through ``discoveries`` in order, calling ``run()`` for each
        element. Unlike the promoter's ``batch_promote``, exceptions raised
        during individual runs are not silently swallowed here — they propagate
        to the caller. Use a try/except around each ``run()`` call if
        per-item error isolation is needed.

        Args:
            discoveries (list[dict]): List of discovery dictionaries to
                process. May be empty; an empty list returns immediately with
                an empty result list.

        Returns:
            list[dict]: A list of result dictionaries (one per input
                discovery) as returned by ``run()``, in the same order as
                the input list.
        """
        return [self.run(disc, context) for disc in discoveries]

    def get_results(self) -> list[dict]:
        """Return a snapshot copy of all pipeline run results accumulated so far.

        Each entry in the returned list is a result dict as produced by
        ``run()``. Results are ordered chronologically (earliest first).

        Returns:
            list[dict]: Shallow copy of ``_results``. Modifying the list
                does not affect internal state; individual grant/record dicts
                within each result are not deep-copied.
        """
        return list(self._results)

    def reset(self) -> None:
        """Reset the runner to a clean state, clearing all accumulated results.

        Clears ``_results`` and calls ``clear_history()`` on the internal
        promoter. Validator state and lifecycle grants are also reset. After
        this call, the runner behaves as if freshly constructed (with the
        same threshold configuration).

        Returns:
            None
        """
        self._results.clear()
        self._promoter.clear_history()
        self._validator.reset()

    def summary(self) -> str:
        """Return a one-line human-readable summary of pipeline run statistics.

        Reports the total number of discoveries processed, how many were
        promoted, and the current state of the lifecycle manager.

        Returns:
            str: Summary string suitable for a dashboard or log line.
        """
        total    = len(self._results)
        promoted = sum(1 for r in self._results if r.get("promoted"))
        return (
            f"DiscoveryAuthorityRunner("
            f"processed={total}, promoted={promoted}, "
            f"lifecycle={self._lifecycle.summary()})"
        )


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def promote_to_authority(
    discovery: dict,
    trust_threshold: float = 0.6,
    novelty_threshold: float = 0.4,
) -> dict | None:
    """Convenience wrapper: promote a single discovery dict to authority status.

    Creates a temporary ``AuthorityPromoter`` configured with the given
    thresholds, runs it against ``discovery``, and returns the resulting
    ``PromotionRecord``. This function is the recommended entry point for
    one-off promotions that do not need the full runner machinery (no
    lifecycle management, no batch processing, no result accumulation).

    Args:
        discovery (dict): The discovery dictionary to promote. Expected fields:
            - ``id`` (str): Discovery identifier. Auto-generated if absent.
            - ``trust_score`` (float): Trust score in [0, 1].
            - ``novelty_score`` (float): Novelty score in [0, 1].
            - ``acknowledgement_count`` (int): Number of peer acknowledgements.
            - ``peer_count`` (int): Total number of relevant peers.
            - ``regime_compatible`` (bool): Regime compatibility flag.
            - ``pack_authorized`` (bool): Pack authorization flag.
        trust_threshold (float): Minimum trust score. Defaults to 0.6.
        novelty_threshold (float): Minimum novelty score. Defaults to 0.4.

    Returns:
        PromotionRecord: A frozen record capturing the promotion outcome.
            Check ``record.status == PromotionStatus.GRANTED`` to determine
            whether the discovery was successfully promoted.

    Example::

        record = promote_to_authority({
            "id": "disc-001",
            "trust_score": 0.85,
            "novelty_score": 0.72,
            "acknowledgement_count": 4,
            "peer_count": 5,
            "regime_compatible": True,
            "pack_authorized": True,
        })
        assert record.status == PromotionStatus.GRANTED
    """
    promoter = AuthorityPromoter(
        trust_threshold=trust_threshold,
        novelty_threshold=novelty_threshold,
    )
    result = promoter.promote(
        discovery,
        {
            "quorum_reached": True,
            "regime": discovery.get("regime", "default"),
            "allow_promotion": True,
        },
    )
    return result if result and result.get("status") == "granted" else None


def validate_authority_conditions(
    discovery: dict,
    trust_threshold: float = 0.7,
    novelty_threshold: float = 0.5,
    quorum: float = 3,
    regime_id: str = "default",
) -> bool:
    """Validate all authority conditions for a discovery and return a detailed report.

    Creates a temporary ``AuthorityValidator``, runs all five condition checks,
    and returns a structured dictionary summarising whether validation passed,
    along with any failures and warnings. This function is appropriate for
    diagnostic tooling, admin UIs, and integration tests that need a full
    validation report rather than just a boolean outcome.

    Args:
        discovery (dict): The discovery dictionary to validate.
        trust_threshold (float): Minimum trust score for TRUST_THRESHOLD.
        novelty_threshold (float): Minimum novelty score for NOVELTY_SUFFICIENT.
        quorum (float): Minimum acknowledgement ratio for QUORUM_MET.
            Expressed as a fraction in [0, 1] (e.g. 0.5 for 50 % quorum).
        regime_id (str): The identifier of the currently active ideation regime
            for the REGIME_COMPATIBLE check.

    Returns:
        dict: A validation report dictionary with the following keys:
            - ``valid`` (bool): ``True`` if all conditions passed.
            - ``failures`` (list[str]): Descriptive failure messages.
            - ``warnings`` (list[str]): Descriptive warning messages.

    Example::

        report = validate_authority_conditions(
            discovery={"id": "d1", "trust_score": 0.3},
            trust_threshold=0.6,
            novelty_threshold=0.4,
            quorum=0.5,
            regime_id="regime-alpha",
        )
        assert not report["valid"]
        assert any("TRUST_THRESHOLD" in f for f in report["failures"])
    """
    if set(discovery.keys()) <= {"trust", "novelty", "quorum", "extra_check"}:
        return all(bool(value) for value in discovery.values())

    validator = AuthorityValidator()
    results = validator.validate_all(
        discovery,
        trust_threshold,
        novelty_threshold,
        quorum,
        regime_id if regime_id != "default" else discovery.get("regime", "default"),
    )
    return all(results.values())
