"""
jugeo.orchestration.frontier_phases.models
============================================
Core data-model layer for the frontier-phases sub-system (Chapter 47).

This module defines the canonical enumerations and dataclasses that the rest of
the frontier_phases package depends on.  Everything here is intentionally
*pure* — no I/O, no external dependencies, no mutable global state.

Conceptual background
---------------------
A *frontier* in jugeo's search/optimisation sense is the set of unexplored
or partially-explored regions of the solution space.  The optimiser proceeds
through a sequence of *phases*, each with a distinct strategy:

* **EXPLORATION** — broad, low-exploitation search to map unknown territory.
* **EXPLOITATION** — deep, high-exploitation refinement of promising regions.
* **TRANSITION** — short bridging period between two stable phases.
* **STALLED** — no measurable progress; automatic recovery logic is active.
* **CONVERGED** — the frontier has been fully closed; no further search warranted.
* **DIVERGED** — the frontier unexpectedly expanded; remediation is required.
* **RECOVERY** — attempting to repair a degraded or critical search state.

Each phase is described by a :class:`PhaseDescriptor`, transitions are logged
via :class:`PhaseTransitionRecord`, the complete ordered history is kept in
:class:`PhaseHistory`, and stall-detection is delegated to
:class:`StallDetector`.  When the optimiser determines the frontier has
definitively converged it can issue a :class:`ConvergenceCertificate`.
"""

from __future__ import annotations

import math
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum coverage ratio required before a CONVERGED transition is allowed.
MIN_CONVERGENCE_COVERAGE: float = 0.95

#: Default expected duration (seconds) for a phase if not otherwise specified.
DEFAULT_PHASE_DURATION: float = 300.0

#: Default window size for :class:`StallDetector` progress history.
DEFAULT_STALL_WINDOW: int = 10

#: Default minimum per-step progress below which stall logic activates.
DEFAULT_MIN_PROGRESS: float = 1e-4

#: Default stall threshold (seconds with no meaningful progress).
DEFAULT_STALL_THRESHOLD: float = 60.0

#: Sentinel value indicating "not yet started".
EPOCH_SENTINEL: float = 0.0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PhaseKind(Enum):
    """Broad categorical label for a frontier search phase.

    Members
    -------
    EXPLORATION
        The optimiser is actively mapping unknown regions of the frontier.
    EXPLOITATION
        The optimiser is deepening its understanding of known good regions.
    TRANSITION
        A short bridging phase between two other phases.
    STALLED
        No measurable progress has been recorded for an extended period.
    CONVERGED
        The frontier has been closed; the search is effectively complete.
    DIVERGED
        The frontier expanded unexpectedly; remediation logic is active.
    RECOVERY
        An attempt to repair a degraded or critical search state is underway.
    """

    EXPLORATION = "exploration"
    EXPLOITATION = "exploitation"
    TRANSITION = "transition"
    STALLED = "stalled"
    CONVERGED = "converged"
    DIVERGED = "diverged"
    RECOVERY = "recovery"

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        """Return ``True`` if this kind represents a terminal (end) state."""
        return self in {PhaseKind.CONVERGED, PhaseKind.DIVERGED}

    def is_active_search(self) -> bool:
        """Return ``True`` if this kind represents an active search strategy."""
        return self in {PhaseKind.EXPLORATION, PhaseKind.EXPLOITATION}

    def requires_intervention(self) -> bool:
        """Return ``True`` if this kind typically requires human or automatic intervention."""
        return self in {PhaseKind.STALLED, PhaseKind.DIVERGED, PhaseKind.RECOVERY}

    def label(self) -> str:
        """Return a human-readable sentence-case label."""
        return self.value.replace("_", " ").capitalize()


class TransitionTrigger(Enum):
    """The reason a phase transition was initiated.

    Members
    -------
    COVERAGE_THRESHOLD
        A pre-defined closure/coverage ratio was reached, prompting a change.
    STALL_DETECTED
        The :class:`StallDetector` signalled that progress had ceased.
    BUDGET_EXHAUSTED
        The computational or time budget allocated to the phase was used up.
    DIVERSITY_DROP
        Population or solution diversity fell below an acceptable level.
    MANUAL
        A human operator or external system explicitly requested a transition.
    SCHEDULED
        The transition was pre-scheduled at a specific point in time.
    """

    COVERAGE_THRESHOLD = "coverage_threshold"
    STALL_DETECTED = "stall_detected"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DIVERSITY_DROP = "diversity_drop"
    MANUAL = "manual"
    SCHEDULED = "scheduled"

    def is_automatic(self) -> bool:
        """Return ``True`` if this trigger fires without human involvement."""
        return self not in {TransitionTrigger.MANUAL, TransitionTrigger.SCHEDULED}

    def severity(self) -> int:
        """Return an integer severity score (higher = more urgent).

        Scores
        ------
        1 — informational
        2 — routine
        3 — warning
        4 — critical
        """
        _map = {
            TransitionTrigger.SCHEDULED: 1,
            TransitionTrigger.MANUAL: 1,
            TransitionTrigger.COVERAGE_THRESHOLD: 2,
            TransitionTrigger.BUDGET_EXHAUSTED: 2,
            TransitionTrigger.DIVERSITY_DROP: 3,
            TransitionTrigger.STALL_DETECTED: 4,
        }
        return _map[self]


class PhaseHealthStatus(Enum):
    """Aggregate health assessment for an active phase.

    Members
    -------
    HEALTHY
        All indicators are within normal operating ranges.
    DEGRADED
        One or more indicators are outside normal ranges but the phase can
        continue with reduced efficiency.
    CRITICAL
        Multiple indicators are severely outside normal ranges; automatic
        remediation may be required imminently.
    TERMINAL
        The phase cannot continue and must be abandoned.
    """

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    TERMINAL = "terminal"

    def numeric(self) -> int:
        """Return an integer representation (0 = healthy, 3 = terminal)."""
        return {
            PhaseHealthStatus.HEALTHY: 0,
            PhaseHealthStatus.DEGRADED: 1,
            PhaseHealthStatus.CRITICAL: 2,
            PhaseHealthStatus.TERMINAL: 3,
        }[self]

    def is_actionable(self) -> bool:
        """Return ``True`` if automated corrective action should be taken."""
        return self in {PhaseHealthStatus.CRITICAL, PhaseHealthStatus.TERMINAL}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseDescriptor:
    """Immutable description of a named frontier phase.

    A :class:`PhaseDescriptor` captures everything the orchestrator needs to
    know about a phase *before* it starts: when to enter it, when to leave it,
    how long it is expected to run, and any supplementary metadata.

    Parameters
    ----------
    phase_id:
        Unique identifier for this descriptor.  Typically a UUID4 string.
    name:
        Human-readable name (e.g. ``"initial_exploration"``).
    kind:
        Categorical label from :class:`PhaseKind`.
    entry_conditions:
        Ordered tuple of condition strings that must all evaluate to ``True``
        for the phase to be entered.  These are symbolic keys resolved by the
        calling orchestrator.
    exit_conditions:
        Ordered tuple of condition strings that must all evaluate to ``True``
        for the phase to be exited normally.
    expected_duration:
        Approximate wall-clock duration in seconds.  Used for scheduling and
        budget allocation; not a hard limit.
    metadata:
        Arbitrary key-value pairs for downstream consumers.
    """

    phase_id: str
    name: str
    kind: PhaseKind
    entry_conditions: tuple[str, ...]
    exit_conditions: tuple[str, ...]
    expected_duration: float
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        name: str,
        kind: PhaseKind,
        entry_conditions: tuple[str, ...] = (),
        exit_conditions: tuple[str, ...] = (),
        expected_duration: float = DEFAULT_PHASE_DURATION,
        metadata: dict[str, Any] | None = None,
    ) -> "PhaseDescriptor":
        """Convenience factory that auto-generates a :attr:`phase_id`.

        Parameters
        ----------
        name:
            Human-readable phase name.
        kind:
            Phase category.
        entry_conditions:
            Symbolic entry condition keys.
        exit_conditions:
            Symbolic exit condition keys.
        expected_duration:
            Expected wall-clock duration in seconds.
        metadata:
            Optional extra data; defaults to an empty dict.

        Returns
        -------
        PhaseDescriptor
            A new, fully-initialised descriptor.
        """
        return cls(
            phase_id=str(uuid.uuid4()),
            name=name,
            kind=kind,
            entry_conditions=entry_conditions,
            exit_conditions=exit_conditions,
            expected_duration=expected_duration,
            metadata=metadata if metadata is not None else {},
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def matches_state(self, state: dict[str, Any]) -> bool:
        """Return ``True`` if all entry conditions are satisfied by *state*.

        The check is purely key-based: a condition key is considered satisfied
        when it exists in *state* and its value is truthy.  This is
        deliberately simple — richer evaluation is the caller's responsibility.

        Parameters
        ----------
        state:
            Mapping of symbolic condition keys to their current values.

        Returns
        -------
        bool
            ``True`` when every entry condition is satisfied, or when
            :attr:`entry_conditions` is empty.
        """
        if not self.entry_conditions:
            return True
        return all(bool(state.get(cond)) for cond in self.entry_conditions)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this descriptor to a plain dictionary.

        The returned mapping is JSON-serialisable (all values are strings,
        numbers, lists, or nested dicts).

        Returns
        -------
        dict[str, Any]
            A JSON-safe representation of this descriptor.
        """
        return {
            "phase_id": self.phase_id,
            "name": self.name,
            "kind": self.kind.value,
            "entry_conditions": list(self.entry_conditions),
            "exit_conditions": list(self.exit_conditions),
            "expected_duration": self.expected_duration,
            "metadata": dict(self.metadata),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PhaseDescriptor(name={self.name!r}, kind={self.kind.value!r}, "
            f"id={self.phase_id[:8]}…)"
        )


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseTransitionRecord:
    """Immutable record of a single phase transition event.

    Each time the frontier-phase orchestrator moves from one phase to another
    it creates a :class:`PhaseTransitionRecord` and appends it to the active
    :class:`PhaseHistory`.  The record captures the *before*-and-*after*
    state, the trigger, and two key delta metrics.

    Parameters
    ----------
    record_id:
        Unique identifier for this transition record.
    from_phase_id:
        The :attr:`PhaseDescriptor.phase_id` of the phase that was exited.
    to_phase_id:
        The :attr:`PhaseDescriptor.phase_id` of the phase that was entered.
    trigger:
        The reason this transition occurred.
    timestamp:
        Unix timestamp (``time.time()``) at which the transition was recorded.
    closure_delta:
        Change in frontier closure ratio caused by (or observed at) this
        transition.  Positive values indicate progress.
    cost_delta:
        Change in accumulated cost (computation units) at this transition.
        Positive values indicate additional expenditure.
    evidence:
        Supporting data used to justify or describe the transition.
    """

    record_id: str
    from_phase_id: str
    to_phase_id: str
    trigger: TransitionTrigger
    timestamp: float
    closure_delta: float
    cost_delta: float
    evidence: dict[str, Any]

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        from_phase_id: str,
        to_phase_id: str,
        trigger: TransitionTrigger,
        closure_delta: float = 0.0,
        cost_delta: float = 0.0,
        evidence: dict[str, Any] | None = None,
    ) -> "PhaseTransitionRecord":
        """Create a new record stamped with the current time.

        Parameters
        ----------
        from_phase_id:
            ID of the phase being exited.
        to_phase_id:
            ID of the phase being entered.
        trigger:
            What caused this transition.
        closure_delta:
            Change in frontier closure ratio (default 0.0).
        cost_delta:
            Change in accumulated cost (default 0.0).
        evidence:
            Optional supporting evidence dict.

        Returns
        -------
        PhaseTransitionRecord
            Fully initialised record with a fresh UUID and current timestamp.
        """
        return cls(
            record_id=str(uuid.uuid4()),
            from_phase_id=from_phase_id,
            to_phase_id=to_phase_id,
            trigger=trigger,
            timestamp=time.time(),
            closure_delta=closure_delta,
            cost_delta=cost_delta,
            evidence=evidence if evidence is not None else {},
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def is_regression(self) -> bool:
        """Return ``True`` if this transition represented a regression.

        A transition is a regression when :attr:`closure_delta` is strictly
        negative, meaning the frontier *grew* (less was closed) rather than
        shrinking.

        Returns
        -------
        bool
        """
        return self.closure_delta < 0.0

    def latency(self) -> float:
        """Return the elapsed time since this transition was recorded.

        Returns
        -------
        float
            Number of seconds since :attr:`timestamp`.
        """
        return time.time() - self.timestamp

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain, JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "record_id": self.record_id,
            "from_phase_id": self.from_phase_id,
            "to_phase_id": self.to_phase_id,
            "trigger": self.trigger.value,
            "timestamp": self.timestamp,
            "closure_delta": self.closure_delta,
            "cost_delta": self.cost_delta,
            "evidence": dict(self.evidence),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PhaseTransitionRecord("
            f"{self.from_phase_id[:8]}… → {self.to_phase_id[:8]}…, "
            f"trigger={self.trigger.value!r})"
        )


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PhaseHistory:
    """Ordered history of all phase transitions for a single search run.

    :class:`PhaseHistory` is the single source of truth for *what happened*
    during a frontier search.  It records the sequence of phase IDs visited,
    every :class:`PhaseTransitionRecord`, and the current active phase.

    Parameters
    ----------
    phase_ids:
        Ordered list of phase IDs visited, starting with the initial phase.
    transitions:
        Ordered list of transition records; ``len(transitions)`` is always
        ``len(phase_ids) - 1``.
    current_phase_id:
        The ID of the phase currently active.
    start_time:
        Unix timestamp when the history was created (i.e. when the run began).
    """

    phase_ids: list[str]
    transitions: list[PhaseTransitionRecord]
    current_phase_id: str
    start_time: float

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def start(cls, initial_phase_id: str) -> "PhaseHistory":
        """Create a new history beginning with *initial_phase_id*.

        Parameters
        ----------
        initial_phase_id:
            The phase ID to begin the history with.

        Returns
        -------
        PhaseHistory
        """
        now = time.time()
        return cls(
            phase_ids=[initial_phase_id],
            transitions=[],
            current_phase_id=initial_phase_id,
            start_time=now,
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def current_phase(self) -> str:
        """Return the ID of the currently active phase.

        Returns
        -------
        str
        """
        return self.current_phase_id

    def record_transition(self, record: PhaseTransitionRecord) -> None:
        """Append a transition record and update the active phase.

        Parameters
        ----------
        record:
            The :class:`PhaseTransitionRecord` to append.
        """
        self.transitions.append(record)
        self.phase_ids.append(record.to_phase_id)
        self.current_phase_id = record.to_phase_id

    def time_in_phase(self, phase_id: str) -> float:
        """Return the total wall-clock time spent in *phase_id* (seconds).

        The calculation uses transition timestamps to compute durations.  If
        *phase_id* is the current (active) phase, the open-ended interval from
        the last transition to ``time.time()`` is included.

        Parameters
        ----------
        phase_id:
            The phase ID to query.

        Returns
        -------
        float
            Total seconds spent in the requested phase, or ``0.0`` if the
            phase was never visited.
        """
        if not self.phase_ids:
            return 0.0

        total = 0.0
        now = time.time()

        # Build a timeline: list of (enter_time, exit_time) per occurrence.
        for i, pid in enumerate(self.phase_ids):
            if pid != phase_id:
                continue
            # Enter time: start_time for index 0, else the transition timestamp.
            enter = self.start_time if i == 0 else self.transitions[i - 1].timestamp
            # Exit time: next transition timestamp, or now if still active.
            if i < len(self.transitions):
                exit_ = self.transitions[i].timestamp
            else:
                exit_ = now
            total += exit_ - enter

        return total

    def transition_rate(self) -> float:
        """Return the average number of transitions per second.

        Returns
        -------
        float
            Transitions per second since :attr:`start_time`.  Returns ``0.0``
            when fewer than two transitions have been recorded or when elapsed
            time is negligible.
        """
        elapsed = time.time() - self.start_time
        if elapsed < 1e-9 or not self.transitions:
            return 0.0
        return len(self.transitions) / elapsed

    def most_common_phase(self) -> str:
        """Return the phase ID that was visited most frequently.

        In the event of a tie the first-encountered phase is returned.

        Returns
        -------
        str
            Phase ID with the highest visit count, or the current phase if
            :attr:`phase_ids` is empty.
        """
        if not self.phase_ids:
            return self.current_phase_id
        counter: Counter[str] = Counter(self.phase_ids)
        return counter.most_common(1)[0][0]

    def total_elapsed(self) -> float:
        """Return the total elapsed time of this history in seconds."""
        return time.time() - self.start_time

    def regression_count(self) -> int:
        """Return the number of regressive transitions recorded."""
        return sum(1 for t in self.transitions if t.is_regression())

    def export(self) -> dict[str, Any]:
        """Export the full history to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            A complete, nested representation of the history.
        """
        return {
            "current_phase_id": self.current_phase_id,
            "start_time": self.start_time,
            "total_elapsed": self.total_elapsed(),
            "phase_ids": list(self.phase_ids),
            "transition_rate": self.transition_rate(),
            "most_common_phase": self.most_common_phase(),
            "regression_count": self.regression_count(),
            "transitions": [t.to_dict() for t in self.transitions],
        }


# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StallDetector:
    """Detects when a frontier search has stalled.

    A stall is defined as a sustained period during which per-step progress
    (measured as :attr:`min_progress`) has not been exceeded.  The detector
    maintains a rolling window of the most recent progress deltas and
    compares the window average against the threshold.

    Parameters
    ----------
    stall_threshold:
        Minimum progress (summed over :attr:`window_size` steps) required to
        avoid declaring a stall.  Defaults to :data:`DEFAULT_STALL_THRESHOLD`.
    window_size:
        Number of recent steps to include in the rolling window.
    min_progress:
        Minimum progress per step to be considered non-trivial.
    _progress_history:
        Internal rolling list of recorded progress deltas (private).
    """

    stall_threshold: float = DEFAULT_STALL_THRESHOLD
    window_size: int = DEFAULT_STALL_WINDOW
    min_progress: float = DEFAULT_MIN_PROGRESS
    _progress_history: list[float] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def record_progress(self, delta: float) -> None:
        """Record a new progress delta.

        The internal history is kept to at most :attr:`window_size` entries by
        discarding the oldest entry when the window is full.

        Parameters
        ----------
        delta:
            The progress made in the most recent step.  Must be ≥ 0.
        """
        self._progress_history.append(max(0.0, delta))
        if len(self._progress_history) > self.window_size:
            self._progress_history.pop(0)

    def is_stalled(self) -> bool:
        """Return ``True`` if the detector has declared a stall.

        The detector fires when the window is full *and* every entry in the
        window is below :attr:`min_progress`.

        Returns
        -------
        bool
        """
        if len(self._progress_history) < self.window_size:
            return False
        return all(p < self.min_progress for p in self._progress_history)

    def stall_duration(self) -> float:
        """Return the estimated stall duration in seconds.

        This is a rough heuristic: the proportion of the window that contains
        sub-threshold progress, multiplied by :attr:`stall_threshold`.

        Returns
        -------
        float
            Estimated stall duration, or ``0.0`` if not stalled.
        """
        if not self._progress_history:
            return 0.0
        sub_threshold = sum(
            1 for p in self._progress_history if p < self.min_progress
        )
        ratio = sub_threshold / max(len(self._progress_history), 1)
        return ratio * self.stall_threshold

    def mean_progress(self) -> float:
        """Return the mean progress across the current window.

        Returns
        -------
        float
            Mean of :attr:`_progress_history`, or ``0.0`` when empty.
        """
        if not self._progress_history:
            return 0.0
        return sum(self._progress_history) / len(self._progress_history)

    def reset(self) -> None:
        """Clear the internal progress history, resetting all stall state.

        After calling :meth:`reset` the detector will not fire again until
        :attr:`window_size` new progress records have been accumulated.
        """
        self._progress_history.clear()

    def health_status(self) -> PhaseHealthStatus:
        """Derive a :class:`PhaseHealthStatus` from the current stall state.

        Returns
        -------
        PhaseHealthStatus
        """
        if self.is_stalled():
            dur = self.stall_duration()
            if dur >= self.stall_threshold * 2:
                return PhaseHealthStatus.TERMINAL
            if dur >= self.stall_threshold:
                return PhaseHealthStatus.CRITICAL
            return PhaseHealthStatus.DEGRADED
        return PhaseHealthStatus.HEALTHY


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConvergenceCertificate:
    """Immutable certificate asserting that the frontier has converged.

    A :class:`ConvergenceCertificate` is issued by the orchestrator when
    sufficient evidence exists that the frontier is fully or near-fully
    explored and no further search is warranted.  Certificates have a finite
    validity window to prevent stale assertions from persisting across restarts
    or significant state changes.

    Parameters
    ----------
    cert_id:
        Unique identifier for this certificate.
    phase_id:
        ID of the phase during which convergence was detected.
    coverage_ratio:
        Fraction of the frontier that has been explored (0.0–1.0).
    closure_gain_rate:
        Rate of closure gain at the time of certification (units/second).
        Low values support the convergence claim.
    stability_score:
        Composite score (0.0–1.0) measuring solution stability.  Higher is
        more stable.
    issued_at:
        Unix timestamp when this certificate was issued.
    valid_until:
        Unix timestamp after which this certificate should be considered
        expired.
    """

    cert_id: str
    phase_id: str
    coverage_ratio: float
    closure_gain_rate: float
    stability_score: float
    issued_at: float
    valid_until: float

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def issue(
        cls,
        phase_id: str,
        coverage_ratio: float,
        closure_gain_rate: float,
        stability_score: float,
        validity_window: float = 3600.0,
    ) -> "ConvergenceCertificate":
        """Issue a new certificate stamped with the current time.

        Parameters
        ----------
        phase_id:
            ID of the phase during which convergence was declared.
        coverage_ratio:
            Current frontier coverage ratio.
        closure_gain_rate:
            Current closure gain rate.
        stability_score:
            Current solution stability score.
        validity_window:
            Duration (seconds) for which the certificate is valid.

        Returns
        -------
        ConvergenceCertificate

        Raises
        ------
        ValueError
            If *coverage_ratio* or *stability_score* is outside [0, 1].
        """
        if not 0.0 <= coverage_ratio <= 1.0:
            raise ValueError(
                f"coverage_ratio must be in [0, 1], got {coverage_ratio!r}"
            )
        if not 0.0 <= stability_score <= 1.0:
            raise ValueError(
                f"stability_score must be in [0, 1], got {stability_score!r}"
            )
        now = time.time()
        return cls(
            cert_id=str(uuid.uuid4()),
            phase_id=phase_id,
            coverage_ratio=coverage_ratio,
            closure_gain_rate=closure_gain_rate,
            stability_score=stability_score,
            issued_at=now,
            valid_until=now + validity_window,
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Return ``True`` if this certificate is still within its validity window.

        A certificate is valid when the current time is strictly before
        :attr:`valid_until` and the :attr:`coverage_ratio` meets the
        :data:`MIN_CONVERGENCE_COVERAGE` threshold.

        Returns
        -------
        bool
        """
        if time.time() >= self.valid_until:
            return False
        return self.coverage_ratio >= MIN_CONVERGENCE_COVERAGE

    def time_remaining(self) -> float:
        """Return the number of seconds until this certificate expires.

        Returns
        -------
        float
            Seconds remaining; negative if already expired.
        """
        return self.valid_until - time.time()

    def confidence(self) -> float:
        """Return a composite confidence score for this certificate.

        The score combines :attr:`coverage_ratio`, :attr:`stability_score`,
        and a penalty for high :attr:`closure_gain_rate` (a high gain rate
        suggests the frontier is still actively changing).

        Returns
        -------
        float
            Score in [0.0, 1.0].
        """
        gain_penalty = 1.0 / (1.0 + math.exp(self.closure_gain_rate))
        raw = (self.coverage_ratio + self.stability_score + gain_penalty) / 3.0
        return max(0.0, min(1.0, raw))

    def summary(self) -> str:
        """Return a single-line human-readable summary of this certificate.

        Returns
        -------
        str
            A descriptive string suitable for log output.
        """
        status = "VALID" if self.is_valid() else "EXPIRED"
        return (
            f"ConvergenceCertificate[{status}] "
            f"id={self.cert_id[:8]}… "
            f"phase={self.phase_id[:8]}… "
            f"coverage={self.coverage_ratio:.1%} "
            f"stability={self.stability_score:.3f} "
            f"confidence={self.confidence():.3f} "
            f"expires_in={self.time_remaining():.0f}s"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "cert_id": self.cert_id,
            "phase_id": self.phase_id,
            "coverage_ratio": self.coverage_ratio,
            "closure_gain_rate": self.closure_gain_rate,
            "stability_score": self.stability_score,
            "issued_at": self.issued_at,
            "valid_until": self.valid_until,
            "is_valid": self.is_valid(),
            "confidence": self.confidence(),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return self.summary()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def make_phase_descriptor(
    kind: PhaseKind,
    name: str | None = None,
    expected_duration: float = DEFAULT_PHASE_DURATION,
    **metadata: Any,
) -> PhaseDescriptor:
    """Convenience wrapper around :meth:`PhaseDescriptor.create`.

    Parameters
    ----------
    kind:
        The :class:`PhaseKind` for the new descriptor.
    name:
        Optional human-readable name; defaults to ``kind.label()``.
    expected_duration:
        Expected wall-clock duration in seconds.
    **metadata:
        Additional keyword arguments stored as metadata.

    Returns
    -------
    PhaseDescriptor
    """
    return PhaseDescriptor.create(
        name=name or kind.label(),
        kind=kind,
        expected_duration=expected_duration,
        metadata=dict(metadata),
    )


def phase_kind_from_string(value: str) -> PhaseKind:
    """Parse a :class:`PhaseKind` from its string value.

    Parameters
    ----------
    value:
        Case-insensitive string matching a :class:`PhaseKind` member value.

    Returns
    -------
    PhaseKind

    Raises
    ------
    ValueError
        If *value* does not correspond to any known :class:`PhaseKind` member.
    """
    normalised = value.strip().lower()
    for member in PhaseKind:
        if member.value == normalised:
            return member
    valid = ", ".join(m.value for m in PhaseKind)
    raise ValueError(f"Unknown PhaseKind value {value!r}. Valid values: {valid}")


# ---------------------------------------------------------------------------
# Public API declaration
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "PhaseKind",
    "TransitionTrigger",
    "PhaseHealthStatus",
    # Dataclasses
    "PhaseDescriptor",
    "PhaseTransitionRecord",
    "PhaseHistory",
    "StallDetector",
    "ConvergenceCertificate",
    # Helpers
    "make_phase_descriptor",
    "phase_kind_from_string",
    # Constants
    "MIN_CONVERGENCE_COVERAGE",
    "DEFAULT_PHASE_DURATION",
    "DEFAULT_STALL_WINDOW",
    "DEFAULT_MIN_PROGRESS",
    "DEFAULT_STALL_THRESHOLD",
    "EPOCH_SENTINEL",
]
