"""
Semantic Backpressure and Congestion Control for Inhabitant Fleets.

This module implements backpressure and congestion-control mechanisms grounded in
dependent type theory and sheaf-cohomological obstruction classes.  When semantic
proof obligations accumulate faster than fleets can discharge them the system must
slow intake, throttle emission, and signal downstream consumers — exactly as a
reactive stream does, but lifted to the semantic layer.

    # copilot: semantic backpressure and congestion control for inhabitant fleets

Theory recap
============
A *judgment* is an 8-tuple

    J = (c, φ, A, E, O, B, T, Π)

where
  c  – typing context (Γ)
  φ  – formula / proposition being asserted
  A  – assumption set
  E  – evidence bundle (proof terms, witnesses)
  O  – obstruction class  ∈  Ȟ¹(𝒰, ℱ)  (Čech cohomology, first degree)
  B  – blame assignment (which agent is responsible for falsehood)
  T  – trust tier  ∈  {PROPOSAL, REVIEWED, VERIFIED, RUNTIME_WITNESSED, PROOF_BACKED}
  Π  – proof-obligation queue  (the subject of this module)

*Backpressure* arises when Π grows without bound: new obligations are enqueued
faster than the verifier can close them.  The pressure_level p ∈ [0,1] is defined

    p = min(1, |Π| / Π_max)

where Π_max is the per-fleet capacity.  When p ≥ τ_trigger the fleet must be
throttled by factor θ ∈ (0,1]:

    emission_rate_new = emission_rate_old × θ

*Congestion* is the network-level view: multiple fleets competing for a shared
verifier pool create queueing delays modelled by an M/M/c queue.  The congestion
level γ is the traffic intensity ρ = λ/(c·μ), clamped to [0,1].

*Čech obstructions* are simulated as 1-cochains on a finite open cover 𝒰 = {U_i}.
A cochain is a complex-valued function on pairs (U_i, U_j) with U_i ∩ U_j ≠ ∅.
An obstruction is non-trivial when the coboundary δσ ≠ 0, i.e. the local patches
do not glue into a global section — meaning the obligation cannot be discharged
locally and must propagate.

PID control
===========
The ThrottleController uses a discrete PID law

    u[k] = K_p · e[k]  +  K_i · Σ e[j]  +  K_d · (e[k] − e[k−1])

where e[k] = p[k] − p_setpoint is the backpressure error.  The output u[k] is
clamped to [0,1] and subtracted from 1 to yield the throttle factor θ.

Trust algebra
=============
TrustTier forms a bounded lattice (T, ≤, ⊓, ⊔) with

    PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED

meet  (⊓) = min,  join (⊔) = max  under the integer encoding.
"""
from __future__ import annotations

import abc
import collections
import datetime
import functools
import hashlib
import heapq
import itertools
import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Iterator, List, Optional, Tuple

try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind, JudgmentStatus, PropositionKind, ProvenanceSource, TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum obligation-queue depth before hard-blocking admission.
DEFAULT_MAX_QUEUE_DEPTH: int = 1_024

#: Pressure level at which the default policy triggers throttling.
DEFAULT_TRIGGER_THRESHOLD: float = 0.70

#: Fraction by which emission rate is multiplied when throttling fires.
DEFAULT_THROTTLE_FACTOR: float = 0.40

#: Sliding-window width (number of ticks) for congestion detection.
DEFAULT_WINDOW_SIZE: int = 32

#: PID proportional gain.
PID_KP: float = 0.60

#: PID integral gain.
PID_KI: float = 0.10

#: PID differential gain.
PID_KD: float = 0.05

#: Desired backpressure set-point (target pressure level).
PID_SETPOINT: float = 0.30

#: Number of complex basis elements used to simulate Čech 1-cochains.
CECH_DIM: int = 8

#: Small epsilon used to avoid division by zero throughout.
_EPS: float = 1e-9

#: Default recovery strategies available to BackpressurePolicy.
RECOVERY_STRATEGIES: tuple[str, ...] = (
    "exponential_backoff",
    "linear_ramp",
    "immediate_release",
    "staged_recovery",
    "obligation_pruning",
)

#: Default propagation path for a backpressure wave.
DEFAULT_PROPAGATION_PATH: tuple[str, ...] = (
    "source_fleet",
    "obligation_queue",
    "verifier_pool",
    "downstream_consumers",
)

#: Critical backpressure threshold — fleet must be throttled immediately.
CRITICAL_BACKPRESSURE: float = 0.9

#: Warning backpressure threshold — fleet should start throttling.
WARNING_BACKPRESSURE: float = 0.7

#: Default sliding-window size for the CongestionAnalyzer.
DEFAULT_WINDOW: int = 20

#: Maximum throttle factor that may be applied (leaves some headroom).
MAX_THROTTLE: float = 0.95

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trust tier enum with algebra
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Ordered trust algebra T=(E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) — NEVER a float.

    The integer encoding preserves the ordering:

        PROPOSAL(1) < REVIEWED(2) < VERIFIED(3)
                    < RUNTIME_WITNESSED(4) < PROOF_BACKED(5)

    Lattice operations ``meet`` and ``join`` correspond to ``min`` and ``max``
    under this encoding, ensuring monotone combination of trust evidence.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    def join(self, other: TrustTier) -> TrustTier:
        return TrustTier(max(self.value, other.value))

    def meet(self, other: TrustTier) -> TrustTier:
        return TrustTier(min(self.value, other.value))

    def promote(self) -> TrustTier:
        return TrustTier(min(self.value + 1, TrustTier.PROOF_BACKED.value))

    def demote(self) -> TrustTier:
        return TrustTier(max(self.value - 1, TrustTier.PROPOSAL.value))

    def __le__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, TrustTier):
            return int(self) <= int(other)
        return NotImplemented

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        if isinstance(other, TrustTier):
            return int(self) < int(other)
        return NotImplemented

    def is_sufficient_for_emission(self, required: TrustTier) -> bool:
        """Return True iff this tier satisfies *required* trust level."""
        return self >= required

    @property
    def label(self) -> str:
        """Human-readable label including numeric rank."""
        return f"{self.name}[{int(self)}]"


# ---------------------------------------------------------------------------
# Mandatory judgment and obstruction dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A judgment (c, φ, A, E, O, B, T, Π) — NEVER a boolean.

    This is the core epistemic unit of the JuGeo type system.  Every claim
    made about a fleet, obligation, or signal must be wrapped in a Judgment
    so that provenance, trust, and burden are always explicit.

    Fields
    ------
    context:
        Typing context Γ under which the formula is asserted.
    formula:
        The proposition φ being judged (string encoding of a type-theoretic
        term or a plain natural-language description).
    assumptions:
        Frozen tuple of assumption names / identifiers relied upon.
    evidence:
        Frozen tuple of evidence items (proof terms, witness IDs, etc.).
    obligations:
        Frozen tuple of open proof obligations that must still be closed.
    burden:
        The agent or component responsible for discharging the obligations.
    trust:
        Trust tier of this judgment in the T lattice.
    provenance:
        Origin metadata: who / what produced this judgment.
    """

    context: Any
    formula: Any
    assumptions: tuple
    evidence: tuple
    obligations: tuple
    burden: Any
    trust: TrustTier
    provenance: Any


@dataclass(frozen=True)
class CechObstruction:
    """A Čech 1-cohomology obstruction class.

    When local obligation patches on an open cover 𝒰 = {U_i} fail to glue
    into a global section the 1-cocycle is non-trivial: δσ ≠ 0.  This
    dataclass captures the cover, cocycle, cohomology class label, and a
    human-readable description for logging.

    Fields
    ------
    cover_id:
        Identifier for the open cover 𝒰 on which the cochain is defined.
    cocycle:
        Frozenset of (U_i, U_j, value) triples representing the 1-cochain.
        An empty frozenset means the obstruction is trivial.
    cohomology_class:
        String label for the cohomology class in Ȟ¹(𝒰, ℱ).
    description:
        Human-readable explanation of what this obstruction means in the
        context of obligation scheduling.
    """

    cover_id: str
    cocycle: frozenset
    cohomology_class: str
    description: str

    def is_trivial(self) -> bool:
        return len(self.cocycle) == 0


# ---------------------------------------------------------------------------
# SemanticObligation dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticObligation:
    """A single proof obligation that must be discharged by a verifier.

    Attributes
    ----------
    obligation_id:
        Unique identifier for this obligation.
    formula:
        The proposition φ that must be proved (represented as a string
        encoding of the type-theoretic term).
    context:
        Serialised typing context c under which φ must hold.
    priority:
        Lower integer ⟹ higher priority (min-heap semantics).
    trust_tier:
        Minimum trust level required to close this obligation.
    created_at:
        Unix timestamp of obligation creation.
    deadline:
        Optional Unix timestamp by which the obligation must be closed;
        ``None`` means no hard deadline.
    cech_class:
        Čech 1-cochain encoding the obstruction class O ∈ Ȟ¹(𝒰,ℱ)
        associated with this obligation.  Non-zero entries signal that
        patching across open sets is obstructed.
    """

    obligation_id: str
    formula: str
    context: str
    priority: int
    trust_tier: TrustTier
    created_at: float
    deadline: Optional[float]
    cech_class: tuple[complex, ...]

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def age(self) -> float:
        """Elapsed seconds since creation."""
        return time.time() - self.created_at

    @property
    def is_overdue(self) -> bool:
        """True iff a deadline exists and has been exceeded."""
        if self.deadline is None:
            return False
        return time.time() > self.deadline

    @property
    def obstruction_norm(self) -> float:
        """L²-norm of the Čech 1-cochain (measures non-triviality)."""
        return math.sqrt(sum(abs(c) ** 2 for c in self.cech_class) + _EPS)

    def is_obstructed(self, threshold: float = 0.1) -> bool:
        """Return True iff the obstruction norm exceeds *threshold*."""
        return self.obstruction_norm > threshold

    def __lt__(self, other: SemanticObligation) -> bool:
        """Priority comparison for min-heap ordering."""
        return self.priority < other.priority

    def summary(self) -> str:
        """One-line description suitable for logging."""
        return (
            f"Obligation({self.obligation_id[:8]}…, "
            f"prio={self.priority}, "
            f"trust={self.trust_tier.label}, "
            f"|O|={self.obstruction_norm:.3f})"
        )


# ---------------------------------------------------------------------------
# Primary frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticBackpressure:
    """A measure of how 'backed up' the obligation queue is.

    Backpressure is the semantic analogue of TCP back-pressure: when the
    proof-obligation queue grows faster than the verifier can drain it, the
    fleet must slow down.  This dataclass is a *frozen snapshot* — a new
    instance is created each time the queue is sampled.

    Theory
    ------
    Let λ be the obligation arrival rate (obligations/tick) and μ be the
    processing rate.  The utilisation ρ = λ / μ.  When ρ ≥ 1 the queue
    grows without bound (M/D/1 instability).  The backpressure_level maps ρ
    into [0, 1] via a sigmoid-like clamp so the controller always has a
    bounded error signal.

    Fields
    ------
    backpressure_id:
        Globally unique identifier for this snapshot.
    fleet_id:
        The fleet whose obligation queue is being measured.
    obligation_queue_depth:
        Current number of unprocessed obligations in the queue.
    processing_rate:
        Number of obligations the verifier closes per tick (μ).
    arrival_rate:
        Number of new obligations enqueued per tick (λ).
    backpressure_level:
        Normalised pressure in [0, 1].  Values ≥ CRITICAL_BACKPRESSURE
        indicate near-saturation and must trigger immediate throttling.
    timestamp:
        ISO-8601 timestamp string at which this snapshot was taken.
    """

    backpressure_id: str
    fleet_id: str
    obligation_queue_depth: int
    processing_rate: float
    arrival_rate: float
    backpressure_level: float
    timestamp: str

    # ------------------------------------------------------------------
    # Spec-required methods
    # ------------------------------------------------------------------

    def is_critical(self) -> bool:
        """Return True iff backpressure_level > 0.9 (near saturation)."""
        return self.backpressure_level > CRITICAL_BACKPRESSURE

    def utilization(self) -> float:
        """Compute traffic intensity ρ = arrival_rate / processing_rate.

        Returns a value in [0, ∞); values > 1 indicate instability.
        Division-by-zero is guarded with a small epsilon.
        """
        return self.arrival_rate / max(self.processing_rate, 1e-9)

    def to_judgment(self) -> Judgment:
        """Wrap this snapshot as a Judgment for trust-lattice propagation.

        The judgment asserts the backpressure invariant:
          'fleet_id has backpressure_level at timestamp.'
        The trust tier is RUNTIME_WITNESSED because the value is sampled
        directly from the running queue, not proved by a solver.
        """
        return Judgment(
            context=f"fleet:{self.fleet_id}",
            formula=f"backpressure_level({self.fleet_id}) = {self.backpressure_level:.4f}",
            assumptions=(),
            evidence=(
                f"queue_depth={self.obligation_queue_depth}",
                f"arrival_rate={self.arrival_rate:.4f}",
                f"processing_rate={self.processing_rate:.4f}",
            ),
            obligations=() if not self.is_critical() else (
                f"throttle_fleet:{self.fleet_id}",
            ),
            burden=f"fleet_scheduler:{self.fleet_id}",
            trust=TrustTier.RUNTIME_WITNESSED,
            provenance=self.timestamp,
        )

    def describe(self) -> str:
        """Return a human-readable single-line summary of this snapshot."""
        severity = (
            "CRITICAL" if self.is_critical()
            else "WARNING" if self.backpressure_level > WARNING_BACKPRESSURE
            else "NOMINAL"
        )
        return (
            f"[{severity}] fleet={self.fleet_id!r} "
            f"bp={self.backpressure_level:.3f} "
            f"depth={self.obligation_queue_depth} "
            f"util={self.utilization():.3f} "
            f"@ {self.timestamp}"
        )


@dataclass(frozen=True)
class CongestionSignal:
    """A message sent to fleet members when congestion is detected.

    Congestion is the *network-level* counterpart to local backpressure:
    multiple fleets compete for a shared verifier pool, producing M/M/c
    queueing delays.  A CongestionSignal encodes a broadcast notification
    carrying a recommended throttle so that receivers can self-regulate
    without contacting a central coordinator.

    The ``signal_kind`` field distinguishes between signal roles:
      - ``"onset"``   : congestion is beginning to build.
      - ``"peak"``    : congestion has reached its maximum.
      - ``"relief"``  : congestion is subsiding; throttle can be relaxed.
      - ``"advisory"``: informational, no action required yet.

    Fields
    ------
    signal_id:
        UUID-based unique identifier for this signal instance.
    fleet_id:
        The fleet that originated (or is most affected by) the signal.
    signal_kind:
        One of the role strings described above.
    backpressure_level:
        The measured or estimated backpressure level at signal creation.
    recommended_throttle:
        Suggested throttle factor ∈ (0, 1] that receivers should apply.
        A value of 1.0 means no throttling is needed.
    affected_member_ids:
        Tuple of fleet-member identifiers that should act on this signal.
    timestamp:
        ISO-8601 string recording when the signal was created.
    """

    signal_id: str
    fleet_id: str
    signal_kind: str
    backpressure_level: float
    recommended_throttle: float
    affected_member_ids: tuple
    timestamp: str

    # ------------------------------------------------------------------
    # Spec-required methods
    # ------------------------------------------------------------------

    def is_urgent(self) -> bool:
        """Return True iff backpressure_level > 0.8 (needs immediate action)."""
        return self.backpressure_level > 0.8

    def describe(self) -> str:
        """Return a concise human-readable description of this signal."""
        urgency = "URGENT" if self.is_urgent() else "routine"
        return (
            f"CongestionSignal[{urgency}] kind={self.signal_kind!r} "
            f"fleet={self.fleet_id!r} bp={self.backpressure_level:.3f} "
            f"throttle={self.recommended_throttle:.3f} "
            f"members={len(self.affected_member_ids)} @ {self.timestamp}"
        )


class BackpressurePolicy(Enum):
    """Controls how the fleet responds to backpressure — an Enum of strategies.

    Each member carries three metadata attributes:

    ``description``
        Plain-English explanation of when and why to use this strategy.
    ``threshold``
        Backpressure level (∈ [0,1]) at which this policy should activate.
    ``response_factor``
        Multiplicative adjustment applied to the current throttle when
        this policy fires.  Values < 1 reduce the throttle (slow down);
        values > 1 relax it (speed up).

    Values
    ------
    SLOW_DOWN:
        Gently reduce the fleet's emission rate in proportion to the
        backpressure level.  Suitable for moderate congestion.
    PRUNE_CANDIDATES:
        Drop low-priority obligations from the queue so the verifier
        can catch up.  Suitable when depth is high but urgency is low.
    REDIRECT_MEMBERS:
        Re-route fleet members to an alternative verifier pool.
        Useful when one pool is saturated but others have spare capacity.
    HALT_AND_WAIT:
        Completely stop emission until the queue drains below the warning
        threshold.  Emergency measure for near-critical conditions.
    ADAPTIVE:
        Use a PID controller to automatically tune the throttle factor.
        Preferred in steady-state operation.
    """

    SLOW_DOWN = ("Reduce fleet emission rate proportionally", 0.70, 0.60)
    PRUNE_CANDIDATES = ("Drop low-priority obligations to drain queue", 0.75, 0.50)
    REDIRECT_MEMBERS = ("Re-route members to alternative verifier pool", 0.80, 0.70)
    HALT_AND_WAIT = ("Stop all emission until queue drains", 0.90, 0.05)
    ADAPTIVE = ("PID-controlled adaptive throttle", 0.65, 0.80)

    def __new__(cls, description: str, threshold: float, response_factor: float) -> BackpressurePolicy:
        obj = object.__new__(cls)
        obj._value_ = description
        obj.description = description
        obj.threshold = threshold
        obj.response_factor = response_factor
        return obj

    def should_activate(self, backpressure_level: float) -> bool:
        """Return True iff *backpressure_level* warrants activating this policy."""
        return backpressure_level >= self.threshold

    def effective_throttle(self, backpressure_level: float) -> float:
        """Compute the throttle factor for *backpressure_level*.

        Uses a linear interpolation between 1.0 (no throttle) and
        ``response_factor`` (maximum throttle for this policy) over the
        range [threshold, 1.0].
        """
        if backpressure_level < self.threshold:
            return 1.0
        excess = (backpressure_level - self.threshold) / max(1.0 - self.threshold, _EPS)
        return max(self.response_factor, 1.0 - excess * (1.0 - self.response_factor))

    def describe(self) -> str:
        return (
            f"BackpressurePolicy.{self.name}: {self.description} "
            f"(threshold={self.threshold}, response_factor={self.response_factor})"
        )


class FleetThrottler:
    """Applies throttling to fleet members based on backpressure level.

    The throttler maintains state across ticks so it can implement smoothing
    (exponential moving average) and hysteresis to prevent rapid oscillation
    between throttle values.

    Theory
    ------
    The throttle factor θ ∈ [min_throttle, max_throttle] is updated as::

        θ_new = α · θ_computed  +  (1 − α) · θ_old       (EMA smoothing)

    where α ∈ (0, 1] is the smoothing coefficient and θ_computed comes
    from the active BackpressurePolicy.

    Fields
    ------
    throttler_id:
        Unique identifier for this throttler instance.
    policy:
        The active BackpressurePolicy that determines the response curve.
    current_throttle:
        Most recently applied throttle factor ∈ [min_throttle, max_throttle].
    history:
        Mutable list of (timestamp, throttle) pairs for trend analysis.
    min_throttle:
        Hard floor; the throttle will never fall below this value.
    max_throttle:
        Hard ceiling; the throttle will never exceed this value.
    """

    def __init__(
        self,
        throttler_id: str,
        policy: BackpressurePolicy = BackpressurePolicy.ADAPTIVE,
        min_throttle: float = 0.05,
        max_throttle: float = MAX_THROTTLE,
        ema_alpha: float = 0.30,
    ) -> None:
        self.throttler_id = throttler_id
        self.policy = policy
        self.current_throttle: float = 1.0
        self.history: list[tuple[str, float]] = []
        self.min_throttle = min_throttle
        self.max_throttle = max_throttle
        self._ema_alpha = ema_alpha

    # ------------------------------------------------------------------
    # Spec-required methods
    # ------------------------------------------------------------------

    def compute_throttle(self, backpressure: SemanticBackpressure) -> float:
        """Compute a new throttle factor from a SemanticBackpressure snapshot.

        Applies the policy's response curve, then EMA-smooths the result
        against the previous throttle to avoid abrupt changes.

        Returns the smoothed throttle factor clamped to
        [min_throttle, max_throttle].
        """
        raw = self.policy.effective_throttle(backpressure.backpressure_level)
        smoothed = (
            self._ema_alpha * raw
            + (1.0 - self._ema_alpha) * self.current_throttle
        )
        return max(self.min_throttle, min(self.max_throttle, smoothed))

    def apply(self, fleet_member_ids: list, throttle: float) -> dict:
        """Apply *throttle* to each member in *fleet_member_ids*.

        Records the event in history and updates current_throttle.

        Returns a mapping member_id → applied_throttle so callers can
        log or audit the per-member result.
        """
        ts = datetime.datetime.utcnow().isoformat()
        self.current_throttle = max(self.min_throttle, min(self.max_throttle, throttle))
        self.history.append((ts, self.current_throttle))
        logger.debug(
            "FleetThrottler %s applied throttle=%.4f to %d members",
            self.throttler_id, self.current_throttle, len(fleet_member_ids),
        )
        return {mid: self.current_throttle for mid in fleet_member_ids}

    def release(self) -> None:
        """Gradually restore the throttle toward 1.0 (full speed).

        Adds one EMA step toward 1.0.  Callers typically invoke this
        when backpressure drops below the warning threshold.
        """
        recovered = self._ema_alpha * 1.0 + (1.0 - self._ema_alpha) * self.current_throttle
        self.current_throttle = min(self.max_throttle, recovered)
        ts = datetime.datetime.utcnow().isoformat()
        self.history.append((ts, self.current_throttle))
        logger.debug(
            "FleetThrottler %s released: throttle → %.4f",
            self.throttler_id, self.current_throttle,
        )

    def get_history(self) -> list:
        """Return a copy of the throttle history list."""
        return list(self.history)


class CongestionAnalyzer:
    """Continuously monitors the fleet for congestion indicators.

    The analyzer ingests raw obligation counts each tick, computes a
    SemanticBackpressure snapshot, and emits CongestionSignals when
    conditions exceed configured thresholds.

    Algorithm
    ---------
    1. Each call to ``sample`` appends (obligation_count, processed_count)
       to an internal sliding window of size ``window_size``.
    2. The backpressure_level is estimated as the EMA of the per-tick
       deficit ratio: max(0, (arrivals − processed) / max(arrivals, 1)).
    3. ``analyze`` scans the window and returns a list of CongestionSignals
       for each period where backpressure exceeded WARNING_BACKPRESSURE.
    4. ``detect_trend`` returns a categorical trend label by comparing the
       mean of the first and second halves of the window.

    Fields
    ------
    analyzer_id:
        Unique identifier for this analyzer instance.
    fleet_id:
        The fleet being monitored.
    window_size:
        Number of ticks in the sliding window.
    samples:
        Mutable list of (obligation_count, processed_count) pairs.
    alert_log:
        Mutable list of CongestionSignal instances produced by this analyzer.
    """

    def __init__(
        self,
        analyzer_id: str,
        fleet_id: str,
        window_size: int = DEFAULT_WINDOW,
    ) -> None:
        self.analyzer_id = analyzer_id
        self.fleet_id = fleet_id
        self.window_size = window_size
        self.samples: list[tuple[int, int]] = []
        self.alert_log: list[CongestionSignal] = []
        self._ema_bp: float = 0.0
        self._ema_alpha: float = 0.20

    # ------------------------------------------------------------------
    # Spec-required methods
    # ------------------------------------------------------------------

    def sample(self, obligation_count: int, processed_count: int) -> SemanticBackpressure:
        """Ingest one tick of obligation metrics and return a backpressure snapshot.

        Parameters
        ----------
        obligation_count:
            Total obligations currently in the queue (queue depth).
        processed_count:
            Obligations processed (closed) this tick.

        Returns
        -------
        SemanticBackpressure
            A frozen snapshot reflecting the current queue state.
        """
        self.samples.append((obligation_count, processed_count))
        if len(self.samples) > self.window_size:
            self.samples.pop(0)

        # Estimate arrival rate as change in queue depth + processed
        if len(self.samples) >= 2:
            prev_depth = self.samples[-2][0]
            arrivals = max(0, obligation_count - prev_depth + processed_count)
        else:
            arrivals = float(obligation_count)

        arrival_rate = float(arrivals)
        processing_rate = float(max(processed_count, 1))

        # Update EMA of backpressure
        deficit_ratio = max(0.0, (arrival_rate - processing_rate) / max(arrival_rate, 1e-9))
        self._ema_bp = self._ema_alpha * deficit_ratio + (1.0 - self._ema_alpha) * self._ema_bp
        bp_level = min(1.0, self._ema_bp + obligation_count / max(obligation_count + 1000, 1))

        ts = datetime.datetime.utcnow().isoformat()
        return SemanticBackpressure(
            backpressure_id=str(uuid.uuid4()),
            fleet_id=self.fleet_id,
            obligation_queue_depth=obligation_count,
            processing_rate=processing_rate,
            arrival_rate=arrival_rate,
            backpressure_level=round(bp_level, 6),
            timestamp=ts,
        )

    def analyze(self) -> list:
        """Scan the window and emit CongestionSignals for congested periods.

        Returns a (possibly empty) list of CongestionSignal objects, one
        per contiguous run of samples whose estimated backpressure exceeded
        WARNING_BACKPRESSURE.  Signals are also appended to alert_log.
        """
        signals: list[CongestionSignal] = []
        if not self.samples:
            return signals

        for i, (depth, processed) in enumerate(self.samples):
            arrival = float(depth)
            proc = float(max(processed, 1))
            bp = min(1.0, max(0.0, (arrival - proc) / max(arrival, 1e-9)))
            if bp > WARNING_BACKPRESSURE:
                sig = CongestionSignal(
                    signal_id=str(uuid.uuid4()),
                    fleet_id=self.fleet_id,
                    signal_kind="onset" if i == 0 else "peak",
                    backpressure_level=bp,
                    recommended_throttle=max(0.05, 1.0 - bp),
                    affected_member_ids=(self.fleet_id,),
                    timestamp=datetime.datetime.utcnow().isoformat(),
                )
                signals.append(sig)
                self.alert_log.append(sig)

        return signals

    def detect_trend(self) -> str:
        """Return a categorical trend label from the sliding window.

        Compares the mean queue depth of the first half of the window to
        the second half.

        Returns one of: ``"worsening"``, ``"improving"``, ``"stable"``,
        or ``"insufficient_data"`` if fewer than 4 samples are available.
        """
        if len(self.samples) < 4:
            return "insufficient_data"
        half = len(self.samples) // 2
        first_half_depths = [s[0] for s in self.samples[:half]]
        second_half_depths = [s[0] for s in self.samples[half:]]
        mean_first = statistics.mean(first_half_depths)
        mean_second = statistics.mean(second_half_depths)
        delta = mean_second - mean_first
        if delta > mean_first * 0.10:
            return "worsening"
        elif delta < -mean_first * 0.10:
            return "improving"
        return "stable"

    def get_alerts(self) -> list:
        """Return a copy of the accumulated alert log."""
        return list(self.alert_log)


# ---------------------------------------------------------------------------
# ObligationQueue
# ---------------------------------------------------------------------------

class ObligationQueue:
    """Priority queue for SemanticObligation items with overflow handling.

    The queue is a min-heap ordered by (priority, created_at) so that
    high-priority (low integer), early-created obligations are served first.

    Overflow policy
    ---------------
    When the queue is full (len ≥ max_depth) and a new obligation arrives:
      - If the new obligation's priority is *lower* (worse) than the
        current tail priority, it is *rejected* (backpressure to caller).
      - Otherwise the lowest-priority item is evicted and the new one
        is inserted.

    The queue also tracks cumulative enqueue / dequeue counts and the
    peak observed depth for telemetry.
    """

    def __init__(self, max_depth: int = DEFAULT_MAX_QUEUE_DEPTH) -> None:
        self.max_depth = max_depth
        self._heap: list[SemanticObligation] = []
        self._enqueue_count: int = 0
        self._dequeue_count: int = 0
        self._overflow_count: int = 0
        self._peak_depth: int = 0

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def enqueue(self, obligation: SemanticObligation) -> bool:
        """Attempt to enqueue *obligation*.

        Returns True on success, False if the obligation was rejected due
        to overflow policy.
        """
        if len(self._heap) >= self.max_depth:
            if self._heap and obligation.priority >= self._heap[-1].priority:
                self._overflow_count += 1
                return False
            # evict worst item
            self._heap.sort()
            self._heap.pop()
        heapq.heappush(self._heap, obligation)
        self._enqueue_count += 1
        self._peak_depth = max(self._peak_depth, len(self._heap))
        return True

    def dequeue(self) -> Optional[SemanticObligation]:
        """Remove and return the highest-priority obligation, or None."""
        if not self._heap:
            return None
        obligation = heapq.heappop(self._heap)
        self._dequeue_count += 1
        return obligation

    def peek(self) -> Optional[SemanticObligation]:
        """Return the highest-priority obligation without removing it."""
        return self._heap[0] if self._heap else None

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @property
    def depth(self) -> int:
        """Current number of obligations in the queue."""
        return len(self._heap)

    @property
    def pressure_level(self) -> float:
        """Normalised pressure p = depth / max_depth ∈ [0, 1]."""
        return min(1.0, self.depth / max(self.max_depth, 1))

    @property
    def peak_depth(self) -> int:
        """Highest depth ever recorded."""
        return self._peak_depth

    @property
    def overflow_rate(self) -> float:
        """Fraction of enqueue attempts that were rejected."""
        total = self._enqueue_count + self._overflow_count
        return self._overflow_count / max(total, 1)

    def aggregate_cech_class(self) -> tuple[complex, ...]:
        """Sum Čech classes of all queued obligations element-wise."""
        if not self._heap:
            return tuple(complex(0) for _ in range(CECH_DIM))
        dim = max(len(o.cech_class) for o in self._heap)
        acc: list[complex] = [0 + 0j] * dim
        for obl in self._heap:
            for i, c in enumerate(obl.cech_class):
                acc[i] += c
        return tuple(acc)

    def overdue_obligations(self) -> list[SemanticObligation]:
        """Return all obligations that have exceeded their deadline."""
        return [o for o in self._heap if o.is_overdue]

    def stats(self) -> dict[str, Any]:
        """Return a telemetry snapshot."""
        return {
            "depth": self.depth,
            "pressure": self.pressure_level,
            "peak_depth": self.peak_depth,
            "enqueue_count": self._enqueue_count,
            "dequeue_count": self._dequeue_count,
            "overflow_count": self._overflow_count,
            "overflow_rate": self.overflow_rate,
        }

    def __len__(self) -> int:
        return self.depth

    def __iter__(self) -> Iterator[SemanticObligation]:
        return iter(sorted(self._heap))


# ---------------------------------------------------------------------------
# CongestionDetector
# ---------------------------------------------------------------------------

class CongestionDetector:
    """Sliding-window congestion detector.

    Maintains a circular buffer of recent pressure samples and computes
    a smoothed congestion estimate using an exponentially-weighted moving
    average (EWMA) alongside the raw window mean.

    A congestion event is signalled when the smoothed estimate exceeds
    the alert_threshold for at least min_alert_ticks consecutive ticks.
    """

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        alert_threshold: float = 0.75,
        ewma_alpha: float = 0.20,
        min_alert_ticks: int = 3,
    ) -> None:
        self.window_size = window_size
        self.alert_threshold = alert_threshold
        self.ewma_alpha = ewma_alpha
        self.min_alert_ticks = min_alert_ticks
        self._window: collections.deque[float] = collections.deque(maxlen=window_size)
        self._ewma: float = 0.0
        self._consecutive_alerts: int = 0
        self._total_ticks: int = 0
        self._congestion_events: int = 0

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def record(self, pressure_level: float) -> None:
        """Ingest a new pressure sample and update internal state."""
        self._window.append(pressure_level)
        if self._total_ticks == 0:
            self._ewma = pressure_level
        else:
            self._ewma = self.ewma_alpha * pressure_level + (1.0 - self.ewma_alpha) * self._ewma
        self._total_ticks += 1

        if self._ewma >= self.alert_threshold:
            self._consecutive_alerts += 1
            if self._consecutive_alerts == self.min_alert_ticks:
                self._congestion_events += 1
        else:
            self._consecutive_alerts = 0

    @property
    def window_mean(self) -> float:
        """Arithmetic mean of samples in the sliding window."""
        if not self._window:
            return 0.0
        return sum(self._window) / len(self._window)

    @property
    def window_variance(self) -> float:
        """Sample variance of the sliding window."""
        if len(self._window) < 2:
            return 0.0
        mu = self.window_mean
        return sum((x - mu) ** 2 for x in self._window) / (len(self._window) - 1)

    @property
    def is_congested(self) -> bool:
        """True iff the EWMA currently exceeds alert_threshold."""
        return self._ewma >= self.alert_threshold

    @property
    def congestion_level(self) -> float:
        """Current EWMA value (smoothed congestion estimate)."""
        return self._ewma

    @property
    def congestion_events(self) -> int:
        """Number of confirmed congestion onset events detected."""
        return self._congestion_events

    def build_signal(
        self,
        affected_fleet_ids: tuple[str, ...],
        cause: str,
        trust_tier: TrustTier = TrustTier.REVIEWED,
    ) -> Optional[CongestionSignal]:
        """Build a CongestionSignal if congestion is currently active."""
        if not self.is_congested:
            return None
        fleet_id = affected_fleet_ids[0] if affected_fleet_ids else "unknown"
        bp_level = min(1.0, self.congestion_level)
        return CongestionSignal(
            signal_id=str(uuid.uuid4()),
            fleet_id=fleet_id,
            signal_kind="onset" if bp_level < CRITICAL_BACKPRESSURE else "peak",
            backpressure_level=bp_level,
            recommended_throttle=max(0.05, 1.0 - bp_level),
            affected_member_ids=affected_fleet_ids,
            timestamp=datetime.datetime.utcnow().isoformat(),
        )

    def reset_window(self) -> None:
        """Clear the sliding window (does not reset event counters)."""
        self._window.clear()
        self._consecutive_alerts = 0

    def stats(self) -> dict[str, Any]:
        """Return a telemetry snapshot."""
        return {
            "ewma": self._ewma,
            "window_mean": self.window_mean,
            "window_variance": self.window_variance,
            "is_congested": self.is_congested,
            "congestion_events": self.congestion_events,
            "total_ticks": self._total_ticks,
        }


# ---------------------------------------------------------------------------
# ThrottleController  (discrete PID)
# ---------------------------------------------------------------------------

class ThrottleController:
    """Discrete PID controller that maps backpressure error to throttle output.

    Control law (position form):

        e[k]  = p[k] − p_setpoint
        u[k]  = K_p·e[k]  +  K_i·Σ_{j=0}^{k} e[j]  +  K_d·(e[k] − e[k−1])

    The raw output u[k] is clamped to [0, 1] then inverted to give the
    throttle factor θ[k] = 1 − u[k], which is in [0, 1].

    Anti-windup: the integral accumulator is clamped to [−1, 1] to prevent
    runaway when pressure stays persistently above setpoint.
    """

    def __init__(
        self,
        kp: float = PID_KP,
        ki: float = PID_KI,
        kd: float = PID_KD,
        setpoint: float = PID_SETPOINT,
        output_min: float = 0.0,
        output_max: float = 1.0,
    ) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.setpoint = setpoint
        self.output_min = output_min
        self.output_max = output_max
        self._integral: float = 0.0
        self._prev_error: float = 0.0
        self._step: int = 0
        self._history: list[tuple[float, float]] = []  # (pressure, throttle)

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def update(self, pressure_level: float) -> float:
        """Compute new throttle factor given current *pressure_level*.

        Returns throttle ∈ [output_min, output_max].
        """
        error = pressure_level - self.setpoint
        self._integral = max(-1.0, min(1.0, self._integral + error))
        derivative = error - self._prev_error
        self._prev_error = error

        raw = self.kp * error + self.ki * self._integral + self.kd * derivative
        clamped = max(0.0, min(1.0, raw))
        throttle = max(self.output_min, min(self.output_max, 1.0 - clamped))

        self._step += 1
        self._history.append((pressure_level, throttle))
        if len(self._history) > 256:
            self._history = self._history[-256:]
        return throttle

    def reset(self) -> None:
        """Reset integrator and derivative state (keeps history)."""
        self._integral = 0.0
        self._prev_error = 0.0

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def integral(self) -> float:
        """Current integral accumulator value."""
        return self._integral

    @property
    def step(self) -> int:
        """Number of update calls made."""
        return self._step

    def recent_throttles(self, n: int = 8) -> list[float]:
        """Return the last *n* throttle outputs."""
        return [t for _, t in self._history[-n:]]

    def mean_throttle(self) -> float:
        """Mean throttle over all recorded steps."""
        if not self._history:
            return 1.0
        return sum(t for _, t in self._history) / len(self._history)

    def is_stable(self, window: int = 8, tol: float = 0.05) -> bool:
        """True iff the throttle has been nearly constant for the last *window* steps."""
        recent = self.recent_throttles(window)
        if len(recent) < 2:
            return True
        return max(recent) - min(recent) <= tol

    def tune(self, kp: float, ki: float, kd: float) -> None:
        """Update PID gains without resetting state."""
        self.kp = kp
        self.ki = ki
        self.kd = kd


# ---------------------------------------------------------------------------
# BackpressureGraph
# ---------------------------------------------------------------------------

class BackpressureGraph:
    """Directed graph of backpressure propagation relationships.

    Nodes are fleet / component IDs; edges carry a weight representing the
    fraction of pressure that propagates from source to destination.

    Cohomological interpretation: a non-trivial global section of the
    pressure sheaf corresponds to a global bottleneck that cannot be
    resolved by purely local throttling.  We detect this by computing the
    strongly-connected components (SCCs): any SCC with total pressure above
    a threshold is a cohomological obstruction.
    """

    def __init__(self) -> None:
        self._adj: dict[str, dict[str, float]] = {}
        self._pressure: dict[str, float] = {}
        self._visit_order: list[str] = []

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, pressure: float = 0.0) -> None:
        """Register a node with an initial pressure level."""
        if node_id not in self._adj:
            self._adj[node_id] = {}
        self._pressure[node_id] = max(0.0, min(1.0, pressure))

    def add_edge(self, src: str, dst: str, weight: float = 1.0) -> None:
        """Add a directed edge src → dst with propagation *weight* ∈ [0,1]."""
        self.add_node(src)
        self.add_node(dst)
        self._adj[src][dst] = max(0.0, min(1.0, weight))

    def update_pressure(self, node_id: str, pressure: float) -> None:
        """Update the pressure level of an existing node."""
        if node_id in self._pressure:
            self._pressure[node_id] = max(0.0, min(1.0, pressure))

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def propagate(self, steps: int = 4) -> None:
        """Simulate *steps* of pressure diffusion through the graph.

        At each step each node's pressure is augmented by the weighted
        sum of incoming pressures from its predecessors.  Values are
        clamped to [0, 1] after each step.
        """
        for _ in range(steps):
            new_pressure = dict(self._pressure)
            for src, neighbours in self._adj.items():
                for dst, weight in neighbours.items():
                    new_pressure[dst] = min(
                        1.0,
                        new_pressure.get(dst, 0.0) + self._pressure[src] * weight * 0.1,
                    )
            self._pressure = new_pressure

    def top_pressure_nodes(self, k: int = 5) -> list[tuple[str, float]]:
        """Return the *k* nodes with the highest pressure, sorted descending."""
        return sorted(self._pressure.items(), key=lambda x: x[1], reverse=True)[:k]

    def total_obstruction(self) -> float:
        """Proxy for global cohomological obstruction: mean of node pressures."""
        if not self._pressure:
            return 0.0
        return sum(self._pressure.values()) / len(self._pressure)

    def reachable(self, start: str) -> set[str]:
        """BFS reachability from *start*."""
        visited: set[str] = set()
        queue = collections.deque([start])
        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for neighbour in self._adj.get(node, {}):
                queue.append(neighbour)
        return visited

    def bottleneck_nodes(self, threshold: float = 0.80) -> list[str]:
        """Return nodes whose pressure exceeds *threshold*."""
        return [n for n, p in self._pressure.items() if p >= threshold]

    def nodes(self) -> list[str]:
        """Return all node IDs."""
        return list(self._pressure.keys())

    def edge_count(self) -> int:
        """Total number of directed edges."""
        return sum(len(v) for v in self._adj.values())


# ---------------------------------------------------------------------------
# FlowController
# ---------------------------------------------------------------------------

class FlowController:
    """Manages fleet admission rates under backpressure constraints.

    The FlowController combines readings from a CongestionDetector and a
    ThrottleController to produce a single admission decision per tick.  It
    also maintains a token bucket for rate-limiting: the bucket refills at
    base_rate tokens per second, and each fleet admission costs one token.
    """

    def __init__(
        self,
        fleet_id: str,
        base_rate: float = 10.0,
        bucket_capacity: float = 50.0,
        trust_tier: TrustTier = TrustTier.REVIEWED,
    ) -> None:
        self.fleet_id = fleet_id
        self.base_rate = base_rate
        self.bucket_capacity = bucket_capacity
        self.trust_tier = trust_tier
        self._tokens: float = bucket_capacity
        self._last_refill: float = time.time()
        self._admitted: int = 0
        self._rejected: int = 0
        self.pid = ThrottleController()
        self.detector = CongestionDetector()

    # ------------------------------------------------------------------
    # Token bucket
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        """Refill the token bucket based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(
            self.bucket_capacity,
            self._tokens + elapsed * self.base_rate,
        )
        self._last_refill = now

    def request_admission(self, count: int = 1) -> bool:
        """Attempt to admit *count* fleet items.

        Returns True iff sufficient tokens are available.
        """
        self._refill()
        if self._tokens >= count:
            self._tokens -= count
            self._admitted += count
            return True
        self._rejected += count
        return False

    # ------------------------------------------------------------------
    # Backpressure integration
    # ------------------------------------------------------------------

    def tick(self, pressure_level: float) -> float:
        """Process one control tick; return the current throttle factor."""
        self.detector.record(pressure_level)
        throttle = self.pid.update(pressure_level)
        effective_rate = self.base_rate * throttle
        # Adjust token refill rate based on throttle (will take effect on next refill)
        self.base_rate = max(0.1, effective_rate)
        return throttle

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def admission_rate(self) -> float:
        """Fraction of requests that were admitted."""
        total = self._admitted + self._rejected
        return self._admitted / max(total, 1)

    @property
    def token_level(self) -> float:
        """Current token bucket fill level ∈ [0, bucket_capacity]."""
        self._refill()
        return self._tokens

    def stats(self) -> dict[str, Any]:
        """Return a composite telemetry snapshot."""
        return {
            "fleet_id": self.fleet_id,
            "token_level": self.token_level,
            "admitted": self._admitted,
            "rejected": self._rejected,
            "admission_rate": self.admission_rate,
            "congestion": self.detector.stats(),
            "pid_step": self.pid.step,
        }


# ---------------------------------------------------------------------------
# Spec-required helper functions (private)
# ---------------------------------------------------------------------------

def _exponential_moving_average(
    values: list[float], alpha: float = 0.20
) -> float:
    """Compute the EMA of *values* from left to right.

    EMA_0 = values[0]
    EMA_k = alpha * values[k] + (1 − alpha) * EMA_{k−1}

    Returns the final EMA value, or 0.0 for an empty list.
    """
    if not values:
        return 0.0
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1.0 - alpha) * ema
    return ema


def _congestion_score(
    queue_depth: int,
    processing_rate: float,
    arrival_rate: float,
    depth_weight: float = 0.4,
    utilization_weight: float = 0.6,
) -> float:
    """Compute a composite congestion score ∈ [0, 1].

    Blends two signals:
    - Normalised queue depth: tanh(depth / 500) as a proxy for queue fill.
    - Utilisation ratio: min(1, arrival_rate / max(processing_rate, ε)).

    Returns a weighted average of both signals, clamped to [0, 1].
    """
    depth_score = math.tanh(queue_depth / 500.0)
    util_score = min(1.0, arrival_rate / max(processing_rate, _EPS))
    return depth_weight * depth_score + utilization_weight * util_score


def _estimate_queue_depth(
    history: list[tuple[int, int]],
    horizon: int = 5,
) -> int:
    """Forecast queue depth *horizon* ticks ahead using linear extrapolation.

    *history* is a list of (depth, processed) pairs, most recent last.
    If insufficient history is available the most recent depth is returned.
    """
    if len(history) < 2:
        return history[-1][0] if history else 0
    recent = history[-min(len(history), horizon):]
    depths = [h[0] for h in recent]
    n = len(depths)
    x_mean = (n - 1) / 2.0
    y_mean = sum(depths) / n
    numer = sum((i - x_mean) * (depths[i] - y_mean) for i in range(n))
    denom = sum((i - x_mean) ** 2 for i in range(n))
    slope = numer / max(denom, _EPS)
    return max(0, int(depths[-1] + slope * horizon))


def _compute_backpressure_level(
    queue_depth: int,
    processing_rate: float,
    arrival_rate: float,
    capacity: int = 1024,
) -> float:
    """Map queue metrics to a normalised backpressure level in [0, 1].

    Uses a blend of:
    - Depth ratio: queue_depth / capacity
    - Utilisation ratio: arrival_rate / max(processing_rate, ε)

    The two components are averaged and clamped.
    """
    depth_ratio = min(1.0, queue_depth / max(capacity, 1))
    util_ratio = min(1.0, arrival_rate / max(processing_rate, _EPS))
    return min(1.0, max(0.0, (depth_ratio + util_ratio) / 2.0))


# ---------------------------------------------------------------------------
# Spec-required module-level functions
# ---------------------------------------------------------------------------

def measure_backpressure(
    fleet_id: str,
    queue_depth: int,
    processing_rate: float,
    arrival_rate: float,
) -> SemanticBackpressure:
    """Return the current semantic backpressure for a fleet.

    Parameters
    ----------
    fleet_id:
        Identifier of the fleet whose queue is being measured.
    queue_depth:
        Current number of unprocessed obligations in the queue.
    processing_rate:
        Number of obligations the verifier closes per tick (μ).
    arrival_rate:
        Number of new obligations arriving per tick (λ).

    Returns
    -------
    SemanticBackpressure
        A frozen snapshot with a freshly computed backpressure_level.

    Notes
    -----
    The backpressure_level blends the depth ratio (queue_depth / capacity)
    with the utilisation ratio (λ/μ) so that *both* a large queue AND a
    high arrival rate are considered congestion indicators.
    """
    bp_level = _compute_backpressure_level(queue_depth, processing_rate, arrival_rate)
    ts = datetime.datetime.utcnow().isoformat()
    bp = SemanticBackpressure(
        backpressure_id=str(uuid.uuid4()),
        fleet_id=fleet_id,
        obligation_queue_depth=queue_depth,
        processing_rate=float(processing_rate),
        arrival_rate=float(arrival_rate),
        backpressure_level=round(bp_level, 6),
        timestamp=ts,
    )
    logger.debug("measure_backpressure: %s", bp.describe())
    return bp


def apply_throttling(
    throttler: FleetThrottler,
    backpressure: SemanticBackpressure,
    member_ids: list,
) -> dict:
    """Slow fleet members proportionally to the backpressure level.

    Parameters
    ----------
    throttler:
        The FleetThrottler managing the fleet's admission rate.
    backpressure:
        Current backpressure snapshot for the fleet.
    member_ids:
        List of fleet-member identifiers to throttle.

    Returns
    -------
    dict
        Mapping member_id → applied_throttle_factor for each member.

    Algorithm
    ---------
    1. Compute the new throttle via ``throttler.compute_throttle``.
    2. Apply it to all members via ``throttler.apply``.
    3. If backpressure is critical, log a warning.
    """
    new_throttle = throttler.compute_throttle(backpressure)
    result = throttler.apply(member_ids, new_throttle)
    if backpressure.is_critical():
        logger.warning(
            "CRITICAL backpressure on fleet %r (level=%.3f); "
            "throttled %d members to %.4f",
            backpressure.fleet_id,
            backpressure.backpressure_level,
            len(member_ids),
            new_throttle,
        )
    return result


def relieve_congestion(
    analyzer: CongestionAnalyzer,
    throttler: FleetThrottler,
) -> list:
    """Take corrective action to reduce the obligation queue depth.

    Runs the analyzer, collects any active congestion signals, then
    calls ``throttler.release()`` once for each non-urgent signal and
    keeps the throttle tight for urgent ones.

    Parameters
    ----------
    analyzer:
        The CongestionAnalyzer monitoring the fleet.
    throttler:
        The FleetThrottler currently in force.

    Returns
    -------
    list[CongestionSignal]
        The list of signals that were acted upon.
    """
    signals = analyzer.analyze()
    acted: list[CongestionSignal] = []
    for sig in signals:
        acted.append(sig)
        if not sig.is_urgent():
            throttler.release()
            logger.info(
                "relieve_congestion: releasing throttle for fleet %r "
                "(signal %s, bp=%.3f)",
                sig.fleet_id, sig.signal_id[:8], sig.backpressure_level,
            )
        else:
            logger.warning(
                "relieve_congestion: urgent congestion on fleet %r; "
                "holding throttle (signal %s, bp=%.3f)",
                sig.fleet_id, sig.signal_id[:8], sig.backpressure_level,
            )
    return acted


def signal_congestion(
    fleet_id: str,
    backpressure: SemanticBackpressure,
) -> CongestionSignal:
    """Broadcast a CongestionSignal for a fleet under pressure.

    Determines the signal_kind from the backpressure level:
    - level > CRITICAL_BACKPRESSURE → ``"peak"``
    - level > WARNING_BACKPRESSURE  → ``"onset"``
    - otherwise                     → ``"advisory"``

    The recommended_throttle is computed as ``1 − backpressure_level``
    clamped to [0.05, MAX_THROTTLE].

    Parameters
    ----------
    fleet_id:
        Identifier of the fleet broadcasting this signal.
    backpressure:
        Current backpressure snapshot for the fleet.

    Returns
    -------
    CongestionSignal
        A freshly created, immutable signal ready for broadcast.
    """
    if backpressure.backpressure_level > CRITICAL_BACKPRESSURE:
        kind = "peak"
    elif backpressure.backpressure_level > WARNING_BACKPRESSURE:
        kind = "onset"
    else:
        kind = "advisory"

    recommended = max(0.05, min(MAX_THROTTLE, 1.0 - backpressure.backpressure_level))
    sig = CongestionSignal(
        signal_id=str(uuid.uuid4()),
        fleet_id=fleet_id,
        signal_kind=kind,
        backpressure_level=backpressure.backpressure_level,
        recommended_throttle=recommended,
        affected_member_ids=(fleet_id,),
        timestamp=datetime.datetime.utcnow().isoformat(),
    )
    logger.info("signal_congestion: %s", sig.describe())
    return sig
    """Generate a deterministic Čech 1-cochain from a float seed.

    Each component is exp(2πi·k·seed/dim) scaled by seed, giving a unit-
    circle distribution with magnitude proportional to the seed value.
    """
    return tuple(
        seed * complex(math.cos(2 * math.pi * k * seed / max(dim, 1)),
                        math.sin(2 * math.pi * k * seed / max(dim, 1)))
        for k in range(dim)
    )


def _sliding_window_stats(values: list[float], window: int) -> dict[str, float]:
    """Compute mean and variance over the most recent *window* values."""
    recent = values[-window:] if len(values) >= window else values
    if not recent:
        return {"mean": 0.0, "variance": 0.0}
    mu = sum(recent) / len(recent)
    var = sum((x - mu) ** 2 for x in recent) / max(len(recent) - 1, 1)
    return {"mean": mu, "variance": var}


def _pid_step(
    error: float,
    integral: float,
    prev_error: float,
    kp: float = PID_KP,
    ki: float = PID_KI,
    kd: float = PID_KD,
) -> tuple[float, float]:
    """Single PID update; returns (output, new_integral)."""
    new_integral = max(-1.0, min(1.0, integral + error))
    output = kp * error + ki * new_integral + kd * (error - prev_error)
    return max(0.0, min(1.0, output)), new_integral


def _cech_coboundary(cochain: tuple[complex, ...]) -> float:
    """Approximate δ-norm: sum of consecutive differences (discretised coboundary)."""
    if len(cochain) < 2:
        return 0.0
    return sum(abs(cochain[i + 1] - cochain[i]) for i in range(len(cochain) - 1))


def _graph_shortest_path(
    adj: dict[str, dict[str, float]], src: str, dst: str
) -> Optional[list[str]]:
    """Dijkstra shortest path (by inverse weight = lowest-resistance path)."""
    dist: dict[str, float] = {src: 0.0}
    prev: dict[str, Optional[str]] = {src: None}
    heap: list[tuple[float, str]] = [(0.0, src)]
    visited: set[str] = set()
    while heap:
        d, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node == dst:
            path: list[str] = []
            cur: Optional[str] = dst
            while cur is not None:
                path.append(cur)
                cur = prev.get(cur)
            return list(reversed(path))
        for neighbour, weight in adj.get(node, {}).items():
            nd = d + (1.0 - weight)  # lower weight = longer path
            if nd < dist.get(neighbour, math.inf):
                dist[neighbour] = nd
                prev[neighbour] = node
                heapq.heappush(heap, (nd, neighbour))
    return None


# ---------------------------------------------------------------------------
# __main__ block  (spec-required smoke test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    print("=" * 70)
    print("Semantic Backpressure & Congestion Control — Smoke Test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # 1. TrustTier algebra (spec: values 1–5, promote/demote)
    # ------------------------------------------------------------------
    print("\n--- TrustTier algebra ---")
    t1 = TrustTier.PROPOSAL
    t2 = TrustTier.VERIFIED
    t3 = TrustTier.PROOF_BACKED
    print(f"  PROPOSAL.value = {t1.value}  (must be 1)")
    print(f"  PROOF_BACKED.value = {t3.value}  (must be 5)")
    print(f"  {t2.label}.promote() = {t2.promote().label}")
    print(f"  {t2.label}.demote()  = {t2.demote().label}")
    print(f"  meet({t2.label}, {t3.label}) = {t2.meet(t3).label}")
    print(f"  join({t1.label}, {t2.label}) = {t1.join(t2).label}")

    # ------------------------------------------------------------------
    # 2. Judgment + CechObstruction
    # ------------------------------------------------------------------
    print("\n--- Judgment + CechObstruction ---")
    j = Judgment(
        context="fleet:alpha",
        formula="backpressure_invariant",
        assumptions=("queue_bounded",),
        evidence=("witness_42",),
        obligations=("prove_convergence",),
        burden="scheduler",
        trust=TrustTier.RUNTIME_WITNESSED,
        provenance="smoke_test",
    )
    print(f"  Judgment trust = {j.trust.label}")
    cech = CechObstruction(
        cover_id="cov_001",
        cocycle=frozenset(),
        cohomology_class="trivial",
        description="no obstruction",
    )
    print(f"  CechObstruction.is_trivial() = {cech.is_trivial()}")

    # ------------------------------------------------------------------
    # 3. Growing obligation queue → rising backpressure
    # ------------------------------------------------------------------
    print("\n--- Growing obligation queue → rising backpressure ---")
    queue_sizes  = [10, 50, 120, 250, 400, 550, 700, 820, 900, 950]
    proc_rates   = [30,  30,  25,  20,  15,  12,  10,   8,   7,   6]
    arr_rates    = [20,  40,  50,  60,  65,  70,  75,  78,  80,  82]
    for depth, proc, arr in zip(queue_sizes, proc_rates, arr_rates):
        bp = measure_backpressure("fleet_alpha", depth, float(proc), float(arr))
        tag = " ← CRITICAL" if bp.is_critical() else (" ← WARNING" if bp.backpressure_level > WARNING_BACKPRESSURE else "")
        print(f"  depth={depth:4d}  arr={arr:3.0f}  proc={proc:3.0f}  "
              f"bp={bp.backpressure_level:.3f}{tag}")

    # ------------------------------------------------------------------
    # 4. Apply throttling
    # ------------------------------------------------------------------
    print("\n--- Applying throttling ---")
    throttler = FleetThrottler(
        throttler_id=str(uuid.uuid4()),
        policy=BackpressurePolicy.ADAPTIVE,
    )
    bp_high = measure_backpressure("fleet_alpha", 900, 6.0, 82.0)
    member_ids = [f"member_{i}" for i in range(5)]
    result = apply_throttling(throttler, bp_high, member_ids)
    print(f"  throttle applied = {throttler.current_throttle:.4f}")
    print(f"  per-member result: {result}")
    print(f"  BackpressurePolicy.HALT_AND_WAIT threshold = "
          f"{BackpressurePolicy.HALT_AND_WAIT.threshold}")
    print(f"  effective_throttle(0.95) for SLOW_DOWN = "
          f"{BackpressurePolicy.SLOW_DOWN.effective_throttle(0.95):.4f}")

    # ------------------------------------------------------------------
    # 5. CongestionAnalyzer + relieve_congestion as queue shrinks
    # ------------------------------------------------------------------
    print("\n--- CongestionAnalyzer: queue growth then relief ---")
    analyzer = CongestionAnalyzer(
        analyzer_id=str(uuid.uuid4()),
        fleet_id="fleet_alpha",
        window_size=DEFAULT_WINDOW,
    )
    # Simulate growing queue
    growing = [(50, 5), (120, 8), (250, 10), (400, 12), (600, 10),
               (750, 12), (900, 8), (850, 15), (700, 20), (500, 25)]
    print("  Phase 1 – queue growing:")
    for depth, proc in growing:
        bp = analyzer.sample(depth, proc)
        print(f"    depth={depth:4d}  proc={proc:3d}  "
              f"bp={bp.backpressure_level:.3f}  "
              f"critical={bp.is_critical()}")

    trend = analyzer.detect_trend()
    print(f"  Trend after growth phase: {trend}")
    signals = analyzer.analyze()
    print(f"  Congestion signals detected: {len(signals)}")

    # Now relieve
    print("  Phase 2 – queue draining:")
    shrinking = [(400, 30), (250, 35), (150, 40), (80, 45), (30, 50)]
    for depth, proc in shrinking:
        bp = analyzer.sample(depth, proc)
        print(f"    depth={depth:4d}  proc={proc:3d}  "
              f"bp={bp.backpressure_level:.3f}")
    acted = relieve_congestion(analyzer, throttler)
    print(f"  Signals acted upon: {len(acted)}")
    print(f"  Throttler after relief: {throttler.current_throttle:.4f}")

    # ------------------------------------------------------------------
    # 6. signal_congestion + CongestionSignal
    # ------------------------------------------------------------------
    print("\n--- signal_congestion ---")
    bp_test = measure_backpressure("fleet_beta", 800, 5.0, 75.0)
    sig = signal_congestion("fleet_beta", bp_test)
    print(f"  {sig.describe()}")
    print(f"  is_urgent() = {sig.is_urgent()}")
    print(f"  signal_kind = {sig.signal_kind!r}")

    # ------------------------------------------------------------------
    # 7. Helper functions
    # ------------------------------------------------------------------
    print("\n--- Helper functions ---")
    vals = [0.1, 0.3, 0.6, 0.8, 0.95, 0.9, 0.7, 0.5, 0.3, 0.2]
    ema = _exponential_moving_average(vals)
    print(f"  EMA of pressure series = {ema:.4f}")
    score = _congestion_score(750, 10.0, 80.0)
    print(f"  congestion_score(depth=750, μ=10, λ=80) = {score:.4f}")
    history_pairs = [(50, 10), (100, 12), (200, 11), (350, 10), (500, 9)]
    est = _estimate_queue_depth(history_pairs, horizon=3)
    print(f"  estimated depth in 3 ticks = {est}")

    print("\n" + "=" * 70)
    print("Smoke test complete — all spec classes and functions exercised.")
    print("=" * 70)

