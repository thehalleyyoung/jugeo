"""Semantic backpressure for JuGeo generation pipelines.

In ``theory2.tex`` (§ Local inhabitant synthesis, AI fleets, and semantic
backpressure), backpressure regulates proposal pressure when the overlap
structure is becoming unstable.  If too many local sections are being proposed
without successful gluing, the system must slow down production and prioritize
integration.  Backpressure is a *semantic* concept — it measures the gap
between local production rate and global integration rate.

This module implements the full backpressure subsystem:

* **Signal model** — :class:`BackpressureSignal` carries a continuous pressure
  level (0.0–1.0) with a typed cause (:class:`BackpressureKind`).
* **Policy** — :class:`BackpressurePolicy` encodes configurable thresholds,
  response curves, damping, recovery rates, and copilot sensitivity.
* **Monitoring** — :class:`BackpressureMonitor` samples instantaneous and
  historical pressure from the production and integration trackers.
* **Control** — :class:`BackpressureController` evaluates the current state and
  emits :class:`PressureResponse` directives (throttle, pause, redirect,
  escalate, shed load).
* **Rate tracking** — :class:`ProductionRateTracker` and
  :class:`IntegrationRateTracker` record event streams for local-section
  proposals and global gluing outcomes.
* **Damping** — :class:`BackpressureDamper` smooths noisy signals with
  exponential moving averages and hysteresis filters.
* **Load shedding** — :class:`LoadShedder` drops low-value proposals when
  pressure is critical.
* **History** — :class:`BackpressureHistory` maintains a queryable timeline of
  pressure episodes and recovery events.
* **Diagnostics** — :class:`BackpressureDiagnostics` produces structured
  summaries for operators and copilot-assisted workflows.

Theory alignment
----------------

* Backpressure arises when the *local production rate* exceeds the *global
  integration rate* — the sheaf-theoretic analogy is that sections are being
  proposed faster than they can be glued into global sections.
* The five backpressure kinds correspond to the five obstruction families
  identified in the theory: integration lag, treaty instability, obligation
  overflow, evidence exhaustion, and budget criticality.
* Copilot channels contribute to production pressure; copilot sensitivity
  in the policy allows the controller to modulate copilot proposal rates
  without affecting higher-trust channels.

Backward compatibility
----------------------

The legacy :class:`BackpressureLevel` enum and :func:`compute_backpressure`
factory are preserved so that existing call sites continue to work.  The new
:class:`BackpressureSignal` exposes a :pyattr:`level` property that maps the
continuous pressure to the discrete legacy enum.

copilot: shared-core module — every public surface is designed for LLM
orchestration and copilot-assisted generation workflows.
"""

from __future__ import annotations

import math
import statistics
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Mapping, Sequence

from jugeo.generation.integration import IntegrationPlan


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    """Return a monotonic-compatible wall-clock timestamp."""
    return time.time()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _generate_id(prefix: str = 'bp') -> str:
    """Return a short prefixed UUID."""
    return f'{prefix}-{uuid.uuid4().hex[:12]}'


# ===================================================================
# Enumerations
# ===================================================================


class BackpressureKind(str, Enum):
    """Typed cause of backpressure.

    Each kind corresponds to one of the five obstruction families described in
    ``theory2.tex``.  A backpressure signal always carries exactly one kind so
    that the controller can select the appropriate response curve.
    """

    INTEGRATION_LAG = 'integration-lag'
    """Gluing is falling behind production."""

    TREATY_INSTABILITY = 'treaty-instability'
    """Overlap treaties are being renegotiated too frequently."""

    OBLIGATION_OVERFLOW = 'obligation-overflow'
    """Proof obligations are accumulating faster than discharge."""

    EVIDENCE_EXHAUSTION = 'evidence-exhaustion'
    """Evidence channels are saturated or returning low-trust results."""

    BUDGET_CRITICAL = 'budget-critical'
    """Computational budget is approaching its ceiling."""


class BackpressureLevel(IntEnum):
    """Discrete pressure level (legacy API).

    New code should use the continuous :pyattr:`BackpressureSignal.pressure_level`
    instead.  This enum is retained for backward compatibility with existing
    call sites that match on ``CLEAR``, ``WATCH``, or ``THROTTLE``.
    """

    CLEAR = 0
    NORMAL = 0
    WATCH = 1
    THROTTLE = 2


class PressureResponseKind(str, Enum):
    """Kind of response the controller may issue."""

    THROTTLE = 'throttle'
    """Reduce production rate proportionally."""

    PAUSE = 'pause'
    """Halt production entirely on targeted channels."""

    REDIRECT = 'redirect'
    """Redirect production to coordinates with lower pressure."""

    ESCALATE = 'escalate'
    """Signal upstream that manual intervention is needed."""

    SHED_LOAD = 'shed-load'
    """Drop low-priority proposals to relieve pressure."""


# ===================================================================
# 1. BackpressureSignal — immutable pressure observation
# ===================================================================


def _pressure_from_legacy_level(level: BackpressureLevel | int) -> float:
    normalized = BackpressureLevel(level)
    if normalized is BackpressureLevel.CLEAR:
        return 0.0
    if normalized is BackpressureLevel.WATCH:
        return 0.5
    return 0.85


@dataclass(frozen=True, slots=True, init=False)
class BackpressureSignal:
    """A single backpressure observation at a point in the site.

    Parameters
    ----------
    signal_id:
        Unique identifier for this signal (auto-generated when omitted).
    source_coordinate:
        The coordinate (or coordinate pattern) where the pressure was measured.
    pressure_level:
        Continuous pressure in [0.0, 1.0].  0 means no pressure; 1 means the
        system is at capacity and must shed load.
    kind:
        The obstruction family that generated this signal.
    timestamp:
        Wall-clock time at which the measurement was taken.
    details:
        Arbitrary key–value metadata for diagnostics and copilot summaries.
    """

    signal_id: str
    source_coordinate: str
    pressure_level: float
    kind: BackpressureKind
    timestamp: float
    details: dict[str, Any] = field(default_factory=dict)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Support both the modern and legacy backpressure constructors.

        Modern form:
            ``BackpressureSignal(signal_id=..., source_coordinate=..., pressure_level=..., kind=..., timestamp=..., details=...)``

        Legacy forms still used by surrounding code:
            ``BackpressureSignal(BackpressureLevel.WATCH, ('blocked',))``
            ``BackpressureSignal(level=BackpressureLevel.NORMAL)``
        """

        details = dict(kwargs.pop("details", {}))

        if args and isinstance(args[0], (BackpressureLevel, int)) and "pressure_level" not in kwargs:
            level = BackpressureLevel(args[0])
            reasons = args[1] if len(args) > 1 else kwargs.pop("reasons", ())
            if len(args) > 2:
                raise TypeError("legacy BackpressureSignal accepts at most level and reasons")
            signal_id = kwargs.pop("signal_id", _generate_id())
            source_coordinate = kwargs.pop("source_coordinate", kwargs.pop("coordinate", "*"))
            kind = kwargs.pop("kind", BackpressureKind.INTEGRATION_LAG)
            timestamp = kwargs.pop("timestamp", _now())
            if reasons:
                details["reasons"] = list(reasons)
            pressure_level = _pressure_from_legacy_level(level)
        else:
            level = kwargs.pop("level", None)
            signal_id = kwargs.pop("signal_id", _generate_id())
            source_coordinate = kwargs.pop("source_coordinate", kwargs.pop("coordinate", "*"))
            pressure_level = kwargs.pop("pressure_level", _pressure_from_legacy_level(level) if level is not None else 0.0)
            kind = kwargs.pop("kind", BackpressureKind.INTEGRATION_LAG)
            timestamp = kwargs.pop("timestamp", _now())
            reasons = kwargs.pop("reasons", ())
            if reasons:
                details["reasons"] = list(reasons)

        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"unexpected BackpressureSignal arguments: {unexpected}")

        if not isinstance(kind, BackpressureKind):
            kind = BackpressureKind(str(kind))

        object.__setattr__(self, "signal_id", str(signal_id))
        object.__setattr__(self, "source_coordinate", str(source_coordinate))
        object.__setattr__(self, "pressure_level", _clamp(float(pressure_level)))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "timestamp", float(timestamp))
        object.__setattr__(self, "details", details)

    # -- backward-compat helpers -------------------------------------------

    @property
    def level(self) -> BackpressureLevel:
        """Map continuous pressure to the legacy discrete enum.

        * ``CLEAR``    — pressure < 0.3
        * ``WATCH``    — 0.3 ≤ pressure < 0.7
        * ``THROTTLE`` — pressure ≥ 0.7
        """
        if self.pressure_level < 0.3:
            return BackpressureLevel.CLEAR
        if self.pressure_level < 0.7:
            return BackpressureLevel.WATCH
        return BackpressureLevel.THROTTLE

    @property
    def reasons(self) -> tuple[str, ...]:
        """Legacy compatibility — extract human-readable reason strings."""
        explicit = self.details.get('reasons')
        if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes, bytearray, memoryview)):
            return tuple(str(reason) for reason in explicit)
        if explicit not in (None, ""):
            return (str(explicit),)
        reasons: list[str] = []
        if self.kind is not None:
            reasons.append(self.kind.value)
        for key, val in self.details.items():
            if key == 'reasons':
                continue
            reasons.append(f'{key}={val}')
        return tuple(reasons)

    # -- factories ---------------------------------------------------------

    @classmethod
    def clear(cls, coordinate: str = '*') -> BackpressureSignal:
        """Create a zero-pressure signal (no backpressure)."""
        return cls(
            signal_id=_generate_id(),
            source_coordinate=coordinate,
            pressure_level=0.0,
            kind=BackpressureKind.INTEGRATION_LAG,
            timestamp=_now(),
        )

    @classmethod
    def from_integration_plan(
        cls,
        plan: IntegrationPlan,
        coordinate: str = '*',
    ) -> BackpressureSignal:
        """Derive a signal from an :class:`IntegrationPlan`.

        Backward-compatible bridge: the number of blockers drives pressure.
        """
        n = len(plan.blockers)
        if n == 0:
            pressure = 0.0
        elif n == 1:
            pressure = 0.5
        else:
            pressure = _clamp(0.5 + 0.1 * n)
        return cls(
            signal_id=_generate_id(),
            source_coordinate=coordinate,
            pressure_level=pressure,
            kind=BackpressureKind.INTEGRATION_LAG,
            timestamp=_now(),
            details={'blockers': list(plan.blockers)},
        )


# ===================================================================
# 2. PressureResponse — controller output
# ===================================================================


@dataclass(frozen=True, slots=True)
class PressureResponse:
    """A directive emitted by the backpressure controller.

    Parameters
    ----------
    kind:
        The type of response action to take.
    magnitude:
        Strength of the response in [0.0, 1.0].  For ``THROTTLE`` this is the
        fraction by which to reduce the production rate; for ``SHED_LOAD`` it
        is the fraction of proposals to drop.
    target_channels:
        Evidence channels (by name) that the response targets.  An empty tuple
        means the response applies to all channels.
    duration:
        Suggested duration in seconds for time-limited responses.
    rationale:
        Human-readable explanation of why this response was selected, suitable
        for copilot diagnostic summaries.
    """

    kind: PressureResponseKind
    magnitude: float
    target_channels: tuple[str, ...] = ()
    duration: float = 0.0
    rationale: str = ''

    @property
    def is_severe(self) -> bool:
        """Return ``True`` if the response is escalation or full shed."""
        return self.kind in (PressureResponseKind.ESCALATE,
                             PressureResponseKind.SHED_LOAD)

    def describe(self) -> str:
        """One-line human-readable summary for logs and copilot displays."""
        channels = ', '.join(self.target_channels) if self.target_channels else 'all'
        return (
            f'{self.kind.value} (magnitude={self.magnitude:.2f}, '
            f'channels={channels}, duration={self.duration:.1f}s)'
        )

    def scaled(self, factor: float) -> PressureResponse:
        """Return a copy with *magnitude* multiplied by *factor*."""
        return PressureResponse(
            kind=self.kind,
            magnitude=_clamp(self.magnitude * factor),
            target_channels=self.target_channels,
            duration=self.duration,
            rationale=self.rationale,
        )

    def with_rationale(self, rationale: str) -> PressureResponse:
        """Return a copy with a new *rationale*."""
        return PressureResponse(
            kind=self.kind,
            magnitude=self.magnitude,
            target_channels=self.target_channels,
            duration=self.duration,
            rationale=rationale,
        )

    def with_channels(self, channels: tuple[str, ...]) -> PressureResponse:
        """Return a copy targeting different *channels*."""
        return PressureResponse(
            kind=self.kind,
            magnitude=self.magnitude,
            target_channels=channels,
            duration=self.duration,
            rationale=self.rationale,
        )


# ===================================================================
# 3. BackpressurePolicy — configurable thresholds and curves
# ===================================================================


class BackpressurePolicy:
    """Configurable policy governing backpressure responses.

    The policy encodes per-kind thresholds, response-curve shapes, damping
    parameters, and a special *copilot sensitivity* knob that lets the
    controller modulate copilot proposal rates independently from solver and
    runtime channels.

    Parameters
    ----------
    thresholds:
        Mapping from :class:`BackpressureKind` to the pressure level at which
        the controller should begin responding.  Defaults to 0.5 for each kind.
    response_curves:
        Mapping from :class:`BackpressureKind` to a curve exponent (float).  An
        exponent of 1.0 means linear response; < 1.0 is aggressive (responds
        early); > 1.0 is conservative (responds late).
    damping_factor:
        Smoothing factor for the :class:`BackpressureDamper`.  A value of 0.0
        means no smoothing; 1.0 means full smoothing (signal never moves).
    recovery_rate:
        How fast pressure relaxes once the cause is removed, in units per
        second.  Larger values allow quicker recovery.
    copilot_sensitivity:
        Multiplier applied to copilot-channel pressure before evaluation.
        Values > 1.0 make the controller more sensitive to copilot overload;
        values < 1.0 make it more tolerant.
    escalation_rules:
        Mapping from :class:`BackpressureKind` to the pressure level at which
        the controller should escalate rather than merely throttle.
    """

    def __init__(
        self,
        *,
        thresholds: Mapping[BackpressureKind, float] | None = None,
        response_curves: Mapping[BackpressureKind, float] | None = None,
        damping_factor: float = 0.3,
        recovery_rate: float = 0.05,
        copilot_sensitivity: float = 1.2,
        escalation_rules: Mapping[BackpressureKind, float] | None = None,
    ) -> None:
        default_threshold = 0.5
        self.thresholds: dict[BackpressureKind, float] = {
            kind: default_threshold for kind in BackpressureKind
        }
        if thresholds:
            self.thresholds.update(thresholds)

        self.response_curves: dict[BackpressureKind, float] = {
            kind: 1.0 for kind in BackpressureKind
        }
        if response_curves:
            self.response_curves.update(response_curves)

        self.damping_factor = _clamp(damping_factor)
        self.recovery_rate = max(0.0, recovery_rate)
        self.copilot_sensitivity = max(0.0, copilot_sensitivity)

        default_escalation = 0.9
        self.escalation_rules: dict[BackpressureKind, float] = {
            kind: default_escalation for kind in BackpressureKind
        }
        if escalation_rules:
            self.escalation_rules.update(escalation_rules)

    # -- query methods -----------------------------------------------------

    def threshold_for(self, kind: BackpressureKind) -> float:
        """Return the activation threshold for *kind*."""
        return self.thresholds.get(kind, 0.5)

    def curve_exponent_for(self, kind: BackpressureKind) -> float:
        """Return the response-curve exponent for *kind*."""
        return self.response_curves.get(kind, 1.0)

    def escalation_threshold_for(self, kind: BackpressureKind) -> float:
        """Return the escalation threshold for *kind*."""
        return self.escalation_rules.get(kind, 0.9)

    def should_escalate(self, signal: BackpressureSignal) -> bool:
        """Return ``True`` if *signal* exceeds the escalation threshold."""
        return signal.pressure_level >= self.escalation_threshold_for(signal.kind)

    def apply_curve(self, signal: BackpressureSignal) -> float:
        """Map raw pressure through the response curve for *signal.kind*.

        Returns a response magnitude in [0.0, 1.0].  When pressure is below
        the threshold the response is 0; above the threshold it follows
        ``((p - t) / (1 - t)) ** exponent``.
        """
        threshold = self.threshold_for(signal.kind)
        if signal.pressure_level <= threshold:
            return 0.0
        span = 1.0 - threshold
        if span <= 0:
            return 1.0
        normalized = (signal.pressure_level - threshold) / span
        exponent = self.curve_exponent_for(signal.kind)
        return _clamp(normalized ** exponent)

    def adjust_for_copilot(self, pressure: float) -> float:
        """Apply copilot sensitivity scaling to *pressure*."""
        return _clamp(pressure * self.copilot_sensitivity)

    def merge(self, other: BackpressurePolicy) -> BackpressurePolicy:
        """Return a new policy that takes the stricter threshold of each kind."""
        merged_thresholds = {
            kind: min(self.thresholds[kind], other.thresholds.get(kind, 1.0))
            for kind in BackpressureKind
        }
        merged_escalation = {
            kind: min(self.escalation_rules[kind],
                      other.escalation_rules.get(kind, 1.0))
            for kind in BackpressureKind
        }
        return BackpressurePolicy(
            thresholds=merged_thresholds,
            response_curves=self.response_curves,
            damping_factor=min(self.damping_factor, other.damping_factor),
            recovery_rate=max(self.recovery_rate, other.recovery_rate),
            copilot_sensitivity=max(self.copilot_sensitivity,
                                    other.copilot_sensitivity),
            escalation_rules=merged_escalation,
        )

    def summary(self) -> dict[str, Any]:
        """Return a JSON-safe summary suitable for copilot diagnostics."""
        return {
            'thresholds': {k.value: v for k, v in self.thresholds.items()},
            'response_curves': {k.value: v
                                for k, v in self.response_curves.items()},
            'damping_factor': self.damping_factor,
            'recovery_rate': self.recovery_rate,
            'copilot_sensitivity': self.copilot_sensitivity,
            'escalation_rules': {k.value: v
                                 for k, v in self.escalation_rules.items()},
        }


# ===================================================================
# 4. ProductionRateTracker — local-section production monitoring
# ===================================================================


class ProductionRateTracker:
    """Track the rate at which local sections are being proposed.

    Each call to :meth:`record_production` logs a production event with an
    associated coordinate and channel.  The tracker computes instantaneous and
    smoothed rates and can detect bursts that contribute to backpressure.
    """

    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = window_seconds
        self._events: deque[tuple[float, str, str]] = deque()
        self._total: int = 0

    # -- recording ---------------------------------------------------------

    def record_production(
        self,
        coordinate: str,
        channel: str = 'default',
        timestamp: float | None = None,
    ) -> None:
        """Log a production event at *coordinate* via *channel*."""
        ts = timestamp if timestamp is not None else _now()
        self._events.append((ts, coordinate, channel))
        self._total += 1
        self._trim(ts)

    # -- rate queries ------------------------------------------------------

    def production_rate(self, timestamp: float | None = None) -> float:
        """Return events per second within the sliding window."""
        ts = timestamp if timestamp is not None else _now()
        self._trim(ts)
        if not self._events:
            return 0.0
        return len(self._events) / self._window

    def rate_by_coordinate(
        self, timestamp: float | None = None,
    ) -> dict[str, float]:
        """Return per-coordinate production rate within the window."""
        ts = timestamp if timestamp is not None else _now()
        self._trim(ts)
        counts: dict[str, int] = defaultdict(int)
        for _, coord, _ in self._events:
            counts[coord] += 1
        return {coord: count / self._window for coord, count in counts.items()}

    def rate_by_channel(
        self, timestamp: float | None = None,
    ) -> dict[str, float]:
        """Return per-channel production rate within the window."""
        ts = timestamp if timestamp is not None else _now()
        self._trim(ts)
        counts: dict[str, int] = defaultdict(int)
        for _, _, ch in self._events:
            counts[ch] += 1
        return {ch: count / self._window for ch, count in counts.items()}

    def burst_detection(
        self,
        burst_window: float = 5.0,
        burst_threshold: int = 10,
        timestamp: float | None = None,
    ) -> bool:
        """Return ``True`` if a burst was detected in the last *burst_window* seconds.

        A burst occurs when more than *burst_threshold* events land within
        *burst_window* seconds — a sign that a copilot or solver channel is
        flooding proposals faster than integration can absorb them.
        """
        ts = timestamp if timestamp is not None else _now()
        cutoff = ts - burst_window
        count = sum(1 for t, _, _ in self._events if t >= cutoff)
        return count >= burst_threshold

    def smoothed_rate(
        self,
        alpha: float = 0.3,
        timestamp: float | None = None,
    ) -> float:
        """Return an exponentially smoothed production rate.

        Uses a simple EMA over 1-second buckets within the window.  *alpha*
        controls responsiveness: higher values track recent activity more
        closely.
        """
        ts = timestamp if timestamp is not None else _now()
        self._trim(ts)
        if not self._events:
            return 0.0
        start = ts - self._window
        buckets: dict[int, int] = defaultdict(int)
        for t, _, _ in self._events:
            bucket = int(t - start)
            buckets[bucket] += 1
        n_buckets = max(1, int(self._window))
        ema = 0.0
        for i in range(n_buckets):
            val = float(buckets.get(i, 0))
            ema = alpha * val + (1.0 - alpha) * ema
        return ema

    @property
    def total_produced(self) -> int:
        """Lifetime count of recorded production events."""
        return self._total

    # -- internal ----------------------------------------------------------

    def _trim(self, now: float) -> None:
        """Remove events outside the sliding window."""
        cutoff = now - self._window
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()


# ===================================================================
# 5. IntegrationRateTracker — global gluing outcome monitoring
# ===================================================================


class IntegrationRateTracker:
    """Track the rate and success of global integration (gluing) attempts.

    Integration events record whether a gluing attempt succeeded or failed.
    The tracker derives success / failure rates, backlog estimates, and the
    estimated time to drain the backlog at the current integration velocity.
    """

    def __init__(self, window_seconds: float = 60.0) -> None:
        self._window = window_seconds
        self._events: deque[tuple[float, bool, str]] = deque()
        self._backlog: int = 0
        self._total_success: int = 0
        self._total_failure: int = 0

    # -- recording ---------------------------------------------------------

    def record_integration(
        self,
        success: bool,
        coordinate: str = '*',
        timestamp: float | None = None,
    ) -> None:
        """Log an integration attempt outcome.

        Parameters
        ----------
        success:
            ``True`` if the gluing produced a valid global section; ``False``
            if the attempt failed (e.g. due to treaty violation or residual
            obstruction).
        coordinate:
            The coordinate at which the gluing was attempted.
        timestamp:
            Override for the event timestamp (testing convenience).
        """
        ts = timestamp if timestamp is not None else _now()
        self._events.append((ts, success, coordinate))
        if success:
            self._total_success += 1
            self._backlog = max(0, self._backlog - 1)
        else:
            self._total_failure += 1
            self._backlog += 1
        self._trim(ts)

    def add_to_backlog(self, count: int = 1) -> None:
        """Manually increment the integration backlog by *count*.

        Called by the production tracker bridge when new sections are proposed
        without a corresponding integration attempt.
        """
        self._backlog += count

    # -- rate queries ------------------------------------------------------

    def integration_rate(self, timestamp: float | None = None) -> float:
        """Return integration attempts per second within the window."""
        ts = timestamp if timestamp is not None else _now()
        self._trim(ts)
        if not self._events:
            return 0.0
        return len(self._events) / self._window

    def success_rate(self, timestamp: float | None = None) -> float:
        """Return the fraction of successful integrations within the window."""
        ts = timestamp if timestamp is not None else _now()
        self._trim(ts)
        if not self._events:
            return 0.0
        successes = sum(1 for _, ok, _ in self._events if ok)
        return successes / len(self._events)

    def failure_rate(self, timestamp: float | None = None) -> float:
        """Return the fraction of failed integrations within the window."""
        return 1.0 - self.success_rate(timestamp)

    def gluing_backlog(self) -> int:
        """Return the current estimated integration backlog.

        The backlog grows when production outpaces integration and shrinks
        when integration succeeds.  A large backlog is a primary driver of
        ``INTEGRATION_LAG`` backpressure.
        """
        return self._backlog

    def estimated_drain_time(self, timestamp: float | None = None) -> float:
        """Estimate seconds to drain the backlog at the current success rate.

        Returns ``float('inf')`` if the success rate is zero.
        """
        rate = self.integration_rate(timestamp)
        sr = self.success_rate(timestamp)
        effective = rate * sr
        if effective <= 0:
            return float('inf')
        return self._backlog / effective

    @property
    def total_attempts(self) -> int:
        """Lifetime count of integration attempts."""
        return self._total_success + self._total_failure

    # -- internal ----------------------------------------------------------

    def _trim(self, now: float) -> None:
        cutoff = now - self._window
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()


# ===================================================================
# 6. BackpressureDamper — smoothing and oscillation control
# ===================================================================


class BackpressureDamper:
    """Smooth noisy backpressure signals to prevent control oscillation.

    Raw pressure measurements may spike and drop rapidly.  The damper applies
    exponential moving average (EMA) smoothing and hysteresis filtering to
    produce a stable signal that the controller can act on without chattering.

    Parameters
    ----------
    alpha:
        EMA smoothing factor.  Closer to 1.0 tracks recent values; closer to
        0.0 retains history.
    hysteresis_band:
        Width of the hysteresis band.  The damped output only moves when the
        raw input exceeds the current output by at least half the band width.
    """

    def __init__(
        self,
        alpha: float = 0.3,
        hysteresis_band: float = 0.05,
    ) -> None:
        self._alpha = _clamp(alpha, 0.01, 0.99)
        self._hysteresis_band = max(0.0, hysteresis_band)
        self._ema: float | None = None
        self._output: float = 0.0
        self._history: deque[tuple[float, float, float]] = deque(maxlen=500)
        self._oscillation_count: int = 0
        self._last_direction: int = 0  # +1 rising, -1 falling, 0 unknown

    def damp(self, raw_pressure: float, timestamp: float | None = None) -> float:
        """Apply full damping pipeline to *raw_pressure*.

        Returns the smoothed, hysteresis-filtered pressure level.
        """
        ts = timestamp if timestamp is not None else _now()
        ema_val = self.exponential_moving_average(raw_pressure)
        output = self.hysteresis_filter(ema_val)
        self._history.append((ts, raw_pressure, output))
        return output

    def exponential_moving_average(self, raw: float) -> float:
        """Update and return the EMA of pressure.

        If no prior state exists the EMA is initialized to *raw*.
        """
        if self._ema is None:
            self._ema = raw
        else:
            self._ema = self._alpha * raw + (1.0 - self._alpha) * self._ema
        return self._ema

    def hysteresis_filter(self, value: float) -> float:
        """Apply hysteresis to avoid chattering around a threshold.

        The output only changes when *value* differs from the current output by
        more than half the hysteresis band.
        """
        half = self._hysteresis_band / 2.0
        if value > self._output + half:
            direction = 1
        elif value < self._output - half:
            direction = -1
        else:
            return self._output

        if self._last_direction != 0 and direction != self._last_direction:
            self._oscillation_count += 1
        self._last_direction = direction
        self._output = _clamp(value)
        return self._output

    def detect_oscillation(self, threshold: int = 5) -> bool:
        """Return ``True`` if the signal has oscillated more than *threshold* times.

        Rapid oscillation suggests the controller and the environment are
        fighting — the damper should be tightened (lower alpha) or the
        hysteresis band widened.
        """
        return self._oscillation_count >= threshold

    def stabilize(self) -> float:
        """Force the damper toward its current EMA, resetting oscillation state.

        Useful when the controller detects persistent oscillation and wants to
        break the cycle by imposing a stable operating point.
        """
        if self._ema is not None:
            self._output = self._ema
        self._oscillation_count = 0
        self._last_direction = 0
        return self._output

    @property
    def current_value(self) -> float:
        """The most recent damped output."""
        return self._output

    @property
    def oscillation_count(self) -> int:
        """Number of direction reversals detected since last stabilization."""
        return self._oscillation_count


# ===================================================================
# 7. BackpressureHistory — timeline and episode tracking
# ===================================================================


@dataclass(frozen=True, slots=True)
class _PressureEpisode:
    """A contiguous interval during which pressure exceeded a threshold."""

    start: float
    end: float
    peak: float
    kind: BackpressureKind
    coordinate: str


class BackpressureHistory:
    """Queryable history of backpressure observations.

    Records every signal and derives episodes (contiguous intervals of elevated
    pressure) and recovery times.  Used by :class:`BackpressureDiagnostics` to
    produce reports for operators and copilot summaries.
    """

    def __init__(self, max_records: int = 10_000) -> None:
        self._records: deque[BackpressureSignal] = deque(maxlen=max_records)
        self._episodes: list[_PressureEpisode] = []
        self._active_episodes: dict[str, tuple[float, float, BackpressureKind, str]] = {}
        self._episode_threshold: float = 0.3

    def record(self, signal: BackpressureSignal) -> None:
        """Append *signal* to history and update episode tracking."""
        self._records.append(signal)
        key = f'{signal.kind.value}:{signal.source_coordinate}'
        if signal.pressure_level >= self._episode_threshold:
            if key not in self._active_episodes:
                self._active_episodes[key] = (
                    signal.timestamp,
                    signal.pressure_level,
                    signal.kind,
                    signal.source_coordinate,
                )
            else:
                start, peak, kind, coord = self._active_episodes[key]
                self._active_episodes[key] = (
                    start,
                    max(peak, signal.pressure_level),
                    kind,
                    coord,
                )
        else:
            if key in self._active_episodes:
                start, peak, kind, coord = self._active_episodes.pop(key)
                self._episodes.append(_PressureEpisode(
                    start=start,
                    end=signal.timestamp,
                    peak=peak,
                    kind=kind,
                    coordinate=coord,
                ))

    def timeline(
        self,
        since: float | None = None,
        until: float | None = None,
    ) -> list[BackpressureSignal]:
        """Return signals in [*since*, *until*]."""
        result: list[BackpressureSignal] = []
        for sig in self._records:
            if since is not None and sig.timestamp < since:
                continue
            if until is not None and sig.timestamp > until:
                break
            result.append(sig)
        return result

    def peak_pressure(
        self,
        since: float | None = None,
        until: float | None = None,
    ) -> float:
        """Return the maximum pressure observed in the given interval."""
        signals = self.timeline(since, until)
        if not signals:
            return 0.0
        return max(s.pressure_level for s in signals)

    def average_pressure(
        self,
        since: float | None = None,
        until: float | None = None,
    ) -> float:
        """Return the mean pressure in the given interval."""
        signals = self.timeline(since, until)
        if not signals:
            return 0.0
        return statistics.mean(s.pressure_level for s in signals)

    def episodes(
        self,
        kind: BackpressureKind | None = None,
    ) -> list[_PressureEpisode]:
        """Return completed pressure episodes, optionally filtered by *kind*."""
        if kind is None:
            return list(self._episodes)
        return [ep for ep in self._episodes if ep.kind is kind]

    def recovery_times(self) -> list[float]:
        """Return durations (seconds) of all completed pressure episodes.

        Short recovery times indicate a healthy system; long episodes suggest
        persistent obstructions that may need operator or copilot intervention.
        """
        return [ep.end - ep.start for ep in self._episodes]

    @property
    def record_count(self) -> int:
        """Total number of signals stored in history."""
        return len(self._records)


# ===================================================================
# 8. BackpressureMonitor — real-time pressure measurement
# ===================================================================


class BackpressureMonitor:
    """Monitor that samples pressure from production and integration trackers.

    The monitor is the primary interface for the controller to query current
    system pressure.  It combines production rate, integration rate, backlog
    depth, and treaty stability into per-kind pressure signals.
    """

    def __init__(
        self,
        production: ProductionRateTracker,
        integration: IntegrationRateTracker,
        policy: BackpressurePolicy,
        history: BackpressureHistory | None = None,
    ) -> None:
        self._production = production
        self._integration = integration
        self._policy = policy
        self._history = history or BackpressureHistory()
        self._dampers: dict[BackpressureKind, BackpressureDamper] = {
            kind: BackpressureDamper(alpha=1.0 - policy.damping_factor)
            for kind in BackpressureKind
        }

    def measure(self, timestamp: float | None = None) -> list[BackpressureSignal]:
        """Take a full pressure measurement across all kinds.

        Returns one :class:`BackpressureSignal` per kind that has non-zero raw
        pressure.  Each signal is damped before being returned.
        """
        ts = timestamp if timestamp is not None else _now()
        signals: list[BackpressureSignal] = []

        raw_pressures = self._compute_raw_pressures(ts)
        for kind, (raw, coord, details) in raw_pressures.items():
            damped = self._dampers[kind].damp(raw, ts)
            sig = BackpressureSignal(
                signal_id=_generate_id(),
                source_coordinate=coord,
                pressure_level=damped,
                kind=kind,
                timestamp=ts,
                details=details,
            )
            self._history.record(sig)
            signals.append(sig)
        return signals

    def current_pressure(self, timestamp: float | None = None) -> float:
        """Return the maximum damped pressure across all kinds."""
        signals = self.measure(timestamp)
        if not signals:
            return 0.0
        return max(s.pressure_level for s in signals)

    def pressure_by_kind(
        self, timestamp: float | None = None,
    ) -> dict[BackpressureKind, float]:
        """Return damped pressure grouped by kind."""
        signals = self.measure(timestamp)
        return {s.kind: s.pressure_level for s in signals}

    def pressure_by_coordinate(
        self, timestamp: float | None = None,
    ) -> dict[str, float]:
        """Return maximum pressure per coordinate.

        When multiple kinds contribute pressure at the same coordinate, the
        highest value is reported.
        """
        signals = self.measure(timestamp)
        result: dict[str, float] = {}
        for s in signals:
            existing = result.get(s.source_coordinate, 0.0)
            result[s.source_coordinate] = max(existing, s.pressure_level)
        return result

    def trend(
        self,
        lookback_seconds: float = 30.0,
        timestamp: float | None = None,
    ) -> float:
        """Return the pressure trend as a slope (positive = rising).

        The trend is computed by simple linear regression over the signals
        within the lookback window.  A copilot summary might report this as
        "pressure rising" or "pressure stabilizing".
        """
        ts = timestamp if timestamp is not None else _now()
        since = ts - lookback_seconds
        signals = self._history.timeline(since=since, until=ts)
        if len(signals) < 2:
            return 0.0
        xs = [s.timestamp - since for s in signals]
        ys = [s.pressure_level for s in signals]
        n = len(xs)
        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        sum_x2 = sum(x * x for x in xs)
        denom = n * sum_x2 - sum_x * sum_x
        if abs(denom) < 1e-12:
            return 0.0
        return (n * sum_xy - sum_x * sum_y) / denom

    def is_critical(self, timestamp: float | None = None) -> bool:
        """Return ``True`` if any kind exceeds its escalation threshold."""
        signals = self.measure(timestamp)
        return any(self._policy.should_escalate(s) for s in signals)

    def historical_pressure(
        self,
        since: float | None = None,
        until: float | None = None,
    ) -> list[BackpressureSignal]:
        """Return raw signal history in the given time range."""
        return self._history.timeline(since, until)

    # -- internal ----------------------------------------------------------

    def _compute_raw_pressures(
        self, ts: float,
    ) -> dict[BackpressureKind, tuple[float, str, dict[str, Any]]]:
        """Derive raw pressure levels from tracker state."""
        result: dict[BackpressureKind, tuple[float, str, dict[str, Any]]] = {}

        # Integration lag: production outpacing integration
        prod_rate = self._production.production_rate(ts)
        int_rate = self._integration.integration_rate(ts)
        if prod_rate > 0:
            gap = max(0.0, prod_rate - int_rate) / prod_rate
        else:
            gap = 0.0
        backlog = self._integration.gluing_backlog()
        backlog_pressure = _clamp(backlog / 50.0)
        lag_pressure = _clamp(max(gap, backlog_pressure))
        result[BackpressureKind.INTEGRATION_LAG] = (
            lag_pressure,
            '*',
            {'prod_rate': round(prod_rate, 4),
             'int_rate': round(int_rate, 4),
             'backlog': backlog},
        )

        # Treaty instability: high failure rate signals renegotiation churn
        failure = self._integration.failure_rate(ts)
        result[BackpressureKind.TREATY_INSTABILITY] = (
            _clamp(failure),
            '*',
            {'failure_rate': round(failure, 4)},
        )

        # Obligation overflow: proxy via backlog depth
        obligation_pressure = _clamp(backlog / 30.0)
        result[BackpressureKind.OBLIGATION_OVERFLOW] = (
            obligation_pressure,
            '*',
            {'backlog': backlog},
        )

        # Evidence exhaustion: low success rate under load
        success = self._integration.success_rate(ts)
        if int_rate > 0 and success < 0.3:
            exhaustion = _clamp(1.0 - success)
        else:
            exhaustion = 0.0
        result[BackpressureKind.EVIDENCE_EXHAUSTION] = (
            exhaustion,
            '*',
            {'success_rate': round(success, 4)},
        )

        # Budget critical: based on total production + drain time
        drain = self._integration.estimated_drain_time(ts)
        if math.isinf(drain):
            budget_pressure = _clamp(backlog / 20.0)
        else:
            budget_pressure = _clamp(drain / 300.0)
        result[BackpressureKind.BUDGET_CRITICAL] = (
            budget_pressure,
            '*',
            {'drain_time': round(drain, 2) if not math.isinf(drain) else 'inf'},
        )

        return result


# ===================================================================
# 9. LoadShedder — critical-pressure load shedding
# ===================================================================


class LoadShedder:
    """Shed low-value proposals when pressure is critical.

    Load shedding is the last resort before full system halt.  The shedder
    selects *victims* — pending proposals that can be safely dropped — using
    priority-based heuristics.  The copilot channel is typically the first to
    be shed because its proposals carry the lowest trust ceiling and can be
    re-generated cheaply.

    Parameters
    ----------
    priority_map:
        Mapping from channel name to priority (higher = more important, less
        likely to be shed).  Channels not in the map default to priority 0.
    critical_channels:
        Channel names that must never be shed (e.g. ``'solver'``).
    """

    def __init__(
        self,
        priority_map: Mapping[str, int] | None = None,
        critical_channels: Sequence[str] = ('solver', 'formal-proof'),
    ) -> None:
        self._priority: dict[str, int] = dict(priority_map) if priority_map else {
            'solver': 100,
            'formal-proof': 90,
            'runtime': 70,
            'human': 60,
            'oracle': 40,
            'copilot': 20,
            'composed': 10,
        }
        self._critical: frozenset[str] = frozenset(critical_channels)
        self._shed_log: list[dict[str, Any]] = []

    def shed(
        self,
        proposals: Sequence[dict[str, Any]],
        target_count: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Shed proposals until at most *target_count* remain.

        Returns ``(kept, shed)`` where *kept* are the proposals that survive
        and *shed* are the ones dropped.

        Each proposal dict must contain at least ``'channel'`` and
        ``'coordinate'`` keys.
        """
        if len(proposals) <= target_count:
            return list(proposals), []

        victims = self.select_victims(proposals, len(proposals) - target_count)
        victim_set = set(id(v) for v in victims)
        kept = [p for p in proposals if id(p) not in victim_set]
        self._shed_log.append({
            'timestamp': _now(),
            'total': len(proposals),
            'shed_count': len(victims),
            'target': target_count,
        })
        return kept, victims

    def select_victims(
        self,
        proposals: Sequence[dict[str, Any]],
        count: int,
    ) -> list[dict[str, Any]]:
        """Select up to *count* proposals to shed, lowest priority first."""
        candidates = self._filter_non_critical(proposals)
        sorted_candidates = sorted(
            candidates,
            key=lambda p: self._priority.get(p.get('channel', ''), 0),
        )
        return sorted_candidates[:count]

    def priority_based_shedding(
        self,
        proposals: Sequence[dict[str, Any]],
        pressure: float,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Shed a fraction of proposals proportional to *pressure*.

        At pressure=0.5 no proposals are shed; at pressure=1.0 up to 50% of
        shedable proposals may be dropped.  The fraction scales linearly above
        the shedding threshold.
        """
        shed_fraction = _clamp((pressure - 0.5) * 2.0) * 0.5
        n_shed = int(math.ceil(len(proposals) * shed_fraction))
        if n_shed == 0:
            return list(proposals), []
        target = max(0, len(proposals) - n_shed)
        return self.shed(proposals, target)

    def least_valuable_first(
        self,
        proposals: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return proposals sorted from least to most valuable.

        Useful for incremental shedding: callers can pop from the front.
        """
        candidates = list(proposals)
        candidates.sort(
            key=lambda p: self._priority.get(p.get('channel', ''), 0),
        )
        return candidates

    def preserve_critical(
        self,
        proposals: Sequence[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Partition proposals into (critical, non-critical).

        Critical proposals are never shed regardless of pressure.
        """
        critical: list[dict[str, Any]] = []
        non_critical: list[dict[str, Any]] = []
        for p in proposals:
            if p.get('channel', '') in self._critical:
                critical.append(p)
            else:
                non_critical.append(p)
        return critical, non_critical

    @property
    def shed_log(self) -> list[dict[str, Any]]:
        """Return the history of shedding events for diagnostics."""
        return list(self._shed_log)

    # -- internal ----------------------------------------------------------

    def _filter_non_critical(
        self,
        proposals: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return proposals that are eligible for shedding."""
        return [p for p in proposals if p.get('channel', '') not in self._critical]


# ===================================================================
# 10. BackpressureController — main control loop
# ===================================================================


class BackpressureController:
    """Main backpressure controller.

    The controller ties together the monitor, policy, damper, load shedder, and
    history into a single evaluate→respond loop.  On each call to
    :meth:`evaluate` it measures current pressure, applies the policy response
    curve, and emits zero or more :class:`PressureResponse` directives.

    The :meth:`copilot_pressure_advice` method produces a structured summary
    designed for consumption by a copilot agent that must decide whether to
    continue generating proposals or yield to integration.
    """

    def __init__(
        self,
        monitor: BackpressureMonitor,
        policy: BackpressurePolicy,
        shedder: LoadShedder | None = None,
    ) -> None:
        self._monitor = monitor
        self._policy = policy
        self._shedder = shedder or LoadShedder()
        self._paused_channels: set[str] = set()
        self._throttle_factor: float = 1.0
        self._last_evaluation: float = 0.0

    def evaluate(
        self, timestamp: float | None = None,
    ) -> list[PressureResponse]:
        """Evaluate current pressure and produce response directives.

        Returns a list of :class:`PressureResponse` objects that the
        orchestrator should apply to the generation pipeline.
        """
        ts = timestamp if timestamp is not None else _now()
        self._last_evaluation = ts
        signals = self._monitor.measure(ts)
        responses: list[PressureResponse] = []

        for signal in signals:
            magnitude = self._policy.apply_curve(signal)
            if magnitude <= 0:
                continue

            if self._policy.should_escalate(signal):
                responses.append(PressureResponse(
                    kind=PressureResponseKind.ESCALATE,
                    magnitude=magnitude,
                    rationale=(
                        f'{signal.kind.value} at {signal.pressure_level:.2f} '
                        f'exceeds escalation threshold'
                    ),
                ))
            elif signal.pressure_level >= 0.8:
                responses.append(PressureResponse(
                    kind=PressureResponseKind.SHED_LOAD,
                    magnitude=magnitude,
                    target_channels=('copilot', 'composed'),
                    rationale=(
                        f'{signal.kind.value} pressure {signal.pressure_level:.2f} '
                        f'requires load shedding'
                    ),
                ))
            elif signal.pressure_level >= 0.5:
                responses.append(PressureResponse(
                    kind=PressureResponseKind.THROTTLE,
                    magnitude=magnitude,
                    rationale=(
                        f'{signal.kind.value} pressure {signal.pressure_level:.2f} '
                        f'triggering proportional throttle'
                    ),
                ))
            else:
                responses.append(PressureResponse(
                    kind=PressureResponseKind.REDIRECT,
                    magnitude=magnitude,
                    rationale=(
                        f'{signal.kind.value} low-level pressure; '
                        f'redirecting to less-loaded coordinates'
                    ),
                ))
        return responses

    def apply_response(self, response: PressureResponse) -> None:
        """Execute a single response directive.

        Updates internal state (throttle factor, paused channels) so that
        subsequent calls to :meth:`throttle_production` and related methods
        reflect the response.
        """
        if response.kind is PressureResponseKind.THROTTLE:
            self._throttle_factor = _clamp(
                self._throttle_factor * (1.0 - response.magnitude), 0.1, 1.0,
            )
        elif response.kind is PressureResponseKind.PAUSE:
            self._paused_channels.update(response.target_channels)
        elif response.kind is PressureResponseKind.SHED_LOAD:
            self._throttle_factor = _clamp(
                self._throttle_factor * (1.0 - response.magnitude * 0.5),
                0.05,
                1.0,
            )
        elif response.kind is PressureResponseKind.REDIRECT:
            pass  # redirect is handled by the orchestrator
        elif response.kind is PressureResponseKind.ESCALATE:
            self._paused_channels.update(response.target_channels)
            self._throttle_factor = _clamp(self._throttle_factor * 0.5, 0.05, 1.0)

    def throttle_production(self) -> float:
        """Return the current throttle factor in (0, 1].

        A value of 1.0 means unrestricted production; 0.1 means production is
        throttled to 10% of its normal rate.
        """
        return self._throttle_factor

    def boost_integration(self) -> float:
        """Return a boost factor for integration priority.

        When production is throttled, integration should receive proportionally
        more resources.  The boost factor is the inverse of the throttle
        factor, clamped to [1.0, 10.0].
        """
        if self._throttle_factor <= 0:
            return 10.0
        return _clamp(1.0 / self._throttle_factor, 1.0, 10.0)

    def pause_exploration(self, channels: Sequence[str] = ()) -> None:
        """Pause production on the specified channels (or all if empty)."""
        if channels:
            self._paused_channels.update(channels)
        else:
            self._paused_channels.add('*')

    def resume(self, channels: Sequence[str] = ()) -> None:
        """Resume production on the specified channels (or all if empty).

        Also begins recovering the throttle factor at the policy recovery rate.
        """
        if channels:
            self._paused_channels -= set(channels)
        else:
            self._paused_channels.clear()
        recovery_step = self._policy.recovery_rate
        self._throttle_factor = _clamp(self._throttle_factor + recovery_step)

    def adaptive_control(
        self,
        timestamp: float | None = None,
    ) -> list[PressureResponse]:
        """Run one cycle of adaptive control.

        Evaluates pressure, applies all responses, and adjusts the throttle
        factor toward recovery when pressure is falling.
        """
        responses = self.evaluate(timestamp)
        for resp in responses:
            self.apply_response(resp)

        # Recovery: if no severe responses, relax throttle toward 1.0
        if not any(r.is_severe for r in responses):
            self._throttle_factor = _clamp(
                self._throttle_factor + self._policy.recovery_rate,
            )

        return responses

    def copilot_pressure_advice(
        self,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Produce a structured advisory for copilot agents.

        The returned dictionary contains:

        * ``should_propose`` — whether the copilot should generate new sections.
        * ``throttle`` — suggested throttle fraction (0 = stop, 1 = full speed).
        * ``reason`` — human-readable explanation.
        * ``pressure_snapshot`` — per-kind pressure values.
        * ``trend`` — whether pressure is ``'rising'``, ``'falling'``, or
          ``'stable'``.
        * ``recommended_action`` — one of ``'continue'``, ``'slow-down'``,
          ``'pause'``, ``'shed-load'``.

        A copilot integration layer should inspect ``should_propose`` first and
        respect the throttle fraction to avoid worsening backpressure.
        """
        ts = timestamp if timestamp is not None else _now()
        pressure_map = self._monitor.pressure_by_kind(ts)
        max_pressure = max(pressure_map.values()) if pressure_map else 0.0
        copilot_pressure = self._policy.adjust_for_copilot(max_pressure)
        trend_val = self._monitor.trend(timestamp=ts)

        if trend_val > 0.01:
            trend_label = 'rising'
        elif trend_val < -0.01:
            trend_label = 'falling'
        else:
            trend_label = 'stable'

        if copilot_pressure >= 0.9:
            action = 'shed-load'
            should_propose = False
            reason = ('Backpressure is critical — copilot proposals are '
                      'suspended to allow integration to catch up.')
        elif copilot_pressure >= 0.7:
            action = 'pause'
            should_propose = False
            reason = ('Backpressure is high — copilot proposals paused '
                      'until integration rate improves.')
        elif copilot_pressure >= 0.4:
            action = 'slow-down'
            should_propose = True
            reason = ('Moderate backpressure — copilot should reduce proposal '
                      'rate to avoid overwhelming integration.')
        else:
            action = 'continue'
            should_propose = True
            reason = 'Backpressure is within normal bounds — copilot may propose freely.'

        return {
            'should_propose': should_propose,
            'throttle': _clamp(1.0 - copilot_pressure),
            'reason': reason,
            'pressure_snapshot': {
                k.value: round(v, 4) for k, v in pressure_map.items()
            },
            'trend': trend_label,
            'recommended_action': action,
            'copilot_adjusted_pressure': round(copilot_pressure, 4),
        }

    @property
    def paused_channels(self) -> frozenset[str]:
        """Return the set of currently paused channels."""
        return frozenset(self._paused_channels)

    @property
    def is_any_paused(self) -> bool:
        """Return ``True`` if any channel is paused."""
        return bool(self._paused_channels)


# ===================================================================
# 11. BackpressureDiagnostics — reporting and analysis
# ===================================================================


class BackpressureDiagnostics:
    """Diagnostic reporting for the backpressure subsystem.

    Produces structured summaries for operators, log systems, and copilot
    advisory layers.  All reports are JSON-safe dictionaries.
    """

    def __init__(
        self,
        monitor: BackpressureMonitor,
        controller: BackpressureController,
        production: ProductionRateTracker,
        integration: IntegrationRateTracker,
        history: BackpressureHistory,
    ) -> None:
        self._monitor = monitor
        self._controller = controller
        self._production = production
        self._integration = integration
        self._history = history

    def pressure_summary(
        self, timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Return a snapshot of all pressure dimensions.

        Includes current pressure by kind, overall maximum, trend direction,
        throttle state, and whether the system is critical.
        """
        ts = timestamp if timestamp is not None else _now()
        by_kind = self._monitor.pressure_by_kind(ts)
        max_p = max(by_kind.values()) if by_kind else 0.0
        trend = self._monitor.trend(timestamp=ts)
        return {
            'timestamp': ts,
            'pressure_by_kind': {k.value: round(v, 4) for k, v in by_kind.items()},
            'max_pressure': round(max_p, 4),
            'trend_slope': round(trend, 6),
            'is_critical': self._monitor.is_critical(ts),
            'throttle_factor': round(self._controller.throttle_production(), 4),
            'paused_channels': sorted(self._controller.paused_channels),
        }

    def bottleneck_analysis(
        self, timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Identify the primary bottleneck in the generation pipeline.

        Compares production and integration rates, identifies which
        backpressure kind is dominant, and estimates how long until the
        bottleneck clears at the current rate.
        """
        ts = timestamp if timestamp is not None else _now()
        by_kind = self._monitor.pressure_by_kind(ts)
        if not by_kind:
            return {'bottleneck': None, 'message': 'No pressure data available.'}

        dominant_kind = max(by_kind, key=by_kind.get)  # type: ignore[arg-type]
        dominant_pressure = by_kind[dominant_kind]
        drain_time = self._integration.estimated_drain_time(ts)

        return {
            'dominant_kind': dominant_kind.value,
            'dominant_pressure': round(dominant_pressure, 4),
            'production_rate': round(self._production.production_rate(ts), 4),
            'integration_rate': round(self._integration.integration_rate(ts), 4),
            'integration_success_rate': round(
                self._integration.success_rate(ts), 4,
            ),
            'backlog': self._integration.gluing_backlog(),
            'estimated_drain_seconds': (
                round(drain_time, 2) if not math.isinf(drain_time) else None
            ),
            'recommendation': self._bottleneck_recommendation(
                dominant_kind, dominant_pressure,
            ),
        }

    def production_vs_integration_report(
        self, timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Compare production and integration rates across dimensions.

        This is the core diagnostic for understanding the gap between local
        section production and global gluing — the semantic essence of
        backpressure as described in theory2.tex.
        """
        ts = timestamp if timestamp is not None else _now()
        prod_rate = self._production.production_rate(ts)
        int_rate = self._integration.integration_rate(ts)
        success = self._integration.success_rate(ts)
        effective_int = int_rate * success

        if prod_rate > 0:
            ratio = effective_int / prod_rate
        else:
            ratio = 1.0

        if ratio >= 0.9:
            health = 'healthy'
        elif ratio >= 0.5:
            health = 'strained'
        elif ratio >= 0.2:
            health = 'overloaded'
        else:
            health = 'critical'

        by_coord = self._production.rate_by_coordinate(ts)
        by_channel = self._production.rate_by_channel(ts)
        burst = self._production.burst_detection(timestamp=ts)

        episodes = self._history.episodes()
        recovery = self._history.recovery_times()

        return {
            'production_rate': round(prod_rate, 4),
            'integration_rate': round(int_rate, 4),
            'effective_integration_rate': round(effective_int, 4),
            'integration_success_rate': round(success, 4),
            'integration_to_production_ratio': round(ratio, 4),
            'health': health,
            'backlog': self._integration.gluing_backlog(),
            'burst_detected': burst,
            'production_by_coordinate': {
                k: round(v, 4) for k, v in by_coord.items()
            },
            'production_by_channel': {
                k: round(v, 4) for k, v in by_channel.items()
            },
            'total_produced': self._production.total_produced,
            'total_integration_attempts': self._integration.total_attempts,
            'completed_episodes': len(episodes),
            'mean_recovery_seconds': (
                round(statistics.mean(recovery), 2) if recovery else None
            ),
        }

    def copilot_backpressure_summary(
        self, timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Produce a summary tailored for copilot advisory consumption.

        Combines the controller's copilot advice with history analytics and a
        plain-language narrative.  The copilot integration layer should use
        this to decide whether to generate new proposals, slow down, or pause.
        """
        ts = timestamp if timestamp is not None else _now()
        advice = self._controller.copilot_pressure_advice(ts)
        avg = self._history.average_pressure()
        peak = self._history.peak_pressure()
        episodes = self._history.episodes()
        recovery = self._history.recovery_times()

        narrative_parts: list[str] = []
        narrative_parts.append(
            f'Current recommendation: {advice["recommended_action"]}.'
        )
        if advice['trend'] == 'rising':
            narrative_parts.append(
                'Pressure is rising — consider reducing proposal frequency.'
            )
        elif advice['trend'] == 'falling':
            narrative_parts.append(
                'Pressure is falling — conditions are improving.'
            )
        else:
            narrative_parts.append('Pressure is stable.')

        if episodes:
            narrative_parts.append(
                f'{len(episodes)} pressure episode(s) recorded.'
            )
        if recovery:
            avg_recovery = statistics.mean(recovery)
            narrative_parts.append(
                f'Average recovery time: {avg_recovery:.1f}s.'
            )

        return {
            **advice,
            'history': {
                'average_pressure': round(avg, 4),
                'peak_pressure': round(peak, 4),
                'episode_count': len(episodes),
                'mean_recovery_seconds': (
                    round(statistics.mean(recovery), 2) if recovery else None
                ),
            },
            'narrative': ' '.join(narrative_parts),
        }

    # -- internal ----------------------------------------------------------

    @staticmethod
    def _bottleneck_recommendation(
        kind: BackpressureKind,
        pressure: float,
    ) -> str:
        """Return a textual recommendation for the identified bottleneck."""
        recommendations: dict[BackpressureKind, str] = {
            BackpressureKind.INTEGRATION_LAG: (
                'Gluing is falling behind production. Prioritize integration '
                'and consider throttling copilot proposals.'
            ),
            BackpressureKind.TREATY_INSTABILITY: (
                'Overlap treaties are unstable. Stabilize existing treaties '
                'before proposing new local sections.'
            ),
            BackpressureKind.OBLIGATION_OVERFLOW: (
                'Proof obligations are accumulating. Allocate solver budget '
                'to discharge pending obligations before generating new ones.'
            ),
            BackpressureKind.EVIDENCE_EXHAUSTION: (
                'Evidence channels are saturated. Wait for pending evidence '
                'requests to complete or reduce request complexity.'
            ),
            BackpressureKind.BUDGET_CRITICAL: (
                'Computational budget is nearly exhausted. Shed low-priority '
                'work and focus remaining budget on critical obligations.'
            ),
        }
        base = recommendations.get(kind, 'Unknown bottleneck kind.')
        if pressure >= 0.9:
            return f'CRITICAL: {base}'
        if pressure >= 0.7:
            return f'WARNING: {base}'
        return base


# ===================================================================
# Backward-compatible API
# ===================================================================


def compute_backpressure(plan: IntegrationPlan) -> BackpressureSignal:
    """Derive a :class:`BackpressureSignal` from an integration plan.

    This is the legacy entry point preserved for backward compatibility.
    The returned signal exposes ``.level`` (a :class:`BackpressureLevel`)
    and ``.reasons`` (a tuple of strings) matching the original API.

    New code should prefer the full monitor/controller pipeline.
    """
    return BackpressureSignal.from_integration_plan(plan)


# ===================================================================
# Cross-subsystem backpressure helpers
# ===================================================================


def evidence_backpressure(manifest: Any) -> dict[str, Any]:
    """Adjust backpressure based on evidence coverage from the manifest.

    Uses :mod:`jugeo.evidence.manifests` to inspect how much of the
    obligation space is covered by existing evidence.  When coverage is
    low the returned dict recommends *higher* backpressure to avoid
    producing sections that lack evidential support.
    """
    try:
        from jugeo.evidence.manifests import get_coverage  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        get_coverage = None

    if get_coverage is not None:
        coverage = get_coverage(manifest)
    else:
        coverage = getattr(manifest, "coverage", 0.5)

    pressure = max(0.0, min(1.0, 1.0 - float(coverage)))
    return {
        "evidence_coverage": coverage,
        "recommended_pressure": pressure,
        "source": "jugeo.evidence.manifests",
    }


def solver_backpressure(session: Any) -> dict[str, Any]:
    """Factor solver load into the backpressure calculation.

    Queries the active :mod:`jugeo.solver.z3_session` for its current
    utilisation and pending-constraint count so the controller can
    throttle generation when the solver is saturated.
    """
    try:
        from jugeo.solver.z3_session import get_session_load  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        get_session_load = None

    if get_session_load is not None:
        load = get_session_load(session)
    else:
        load = getattr(session, "load", 0.0)

    pressure = max(0.0, min(1.0, float(load)))
    return {
        "solver_load": load,
        "recommended_pressure": pressure,
        "source": "jugeo.solver.z3_session",
    }


def trust_threshold_backpressure(trust_level: Any) -> dict[str, Any]:
    """Derive backpressure from the current trust threshold.

    Low-trust contexts (as modelled by :mod:`jugeo.evidence.trust`)
    warrant higher backpressure to ensure only well-supported
    proposals proceed through the pipeline.
    """
    try:
        from jugeo.evidence.trust import evaluate_trust  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        evaluate_trust = None

    if evaluate_trust is not None:
        score = evaluate_trust(trust_level)
    else:
        score = float(getattr(trust_level, "score", 0.5))

    pressure = max(0.0, min(1.0, 1.0 - score))
    return {
        "trust_score": score,
        "recommended_pressure": pressure,
        "source": "jugeo.evidence.trust",
    }


# ===================================================================
# Module exports
# ===================================================================

__all__ = [
    'BackpressureKind',
    'BackpressureLevel',
    'PressureResponseKind',
    'BackpressureSignal',
    'PressureResponse',
    'BackpressurePolicy',
    'ProductionRateTracker',
    'IntegrationRateTracker',
    'BackpressureDamper',
    'BackpressureHistory',
    'BackpressureMonitor',
    'LoadShedder',
    'BackpressureController',
    'BackpressureDiagnostics',
    'compute_backpressure',
    'evidence_backpressure',
    'solver_backpressure',
    'trust_threshold_backpressure',
]

# copilot: shared-core module — every public surface is designed for LLM
# orchestration and copilot-assisted generation workflows.
