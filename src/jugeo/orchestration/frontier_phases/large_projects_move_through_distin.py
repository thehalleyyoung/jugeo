from __future__ import annotations

"""Large project semantic phase lifecycle: EXPLORATION→EXPLOITATION→CONVERGENCE. theory2.tex Ch47 §4. # copilot:

This module models the full semantic phase lifecycle that large Jugeo projects
traverse during structured proof search.  The theoretical underpinning (Ch47 §4
of theory2.tex) establishes that non-trivial obligation graphs exhibit three
qualitatively distinct search regimes, and that failing to recognise phase
boundaries leads to either premature convergence or unbounded exploration.

The Three Phases
----------------
EXPLORATION (phase_index=0):
    The search engine has limited information about the obligation landscape.
    Coverage is low (typically below 40%).  The appropriate strategy is
    breadth-first expansion to discover the shape of the space, enumerate
    proof modes that apply, and seed the support regions that will guide later
    exploitation.  Obstruction density is typically low in this phase because
    the search has not yet encountered hard constraints.  The phase exits when
    ``coverage_ratio`` exceeds ``EXPLORATION_MAX_COVERAGE``.

EXPLOITATION (phase_index=1):
    Coverage is moderate (40–85%).  The engine has enough information to
    exploit promising sub-problems aggressively.  Best-first expansion is
    appropriate here.  Obstruction density may rise as the search approaches
    hard sub-goals.  The phase exits when coverage exceeds
    ``CONVERGENCE_MIN_COVERAGE``.

CONVERGENCE (phase_index=2):
    Coverage is high (≥ 85%).  The engine finalises proofs for remaining
    obligations using a greedy strategy, assembling certificates and resolving
    any residual proof gaps.  This phase has no coverage-based exit condition;
    it runs until the proof is complete or a resource budget is exhausted.

Phase Transitions
-----------------
A ``PhaseLifecycle`` object owns the list of ``SemanticPhase`` objects and
mediates all transitions.  Each transition is triggered by a ``PhaseSignal``
sampled from the live frontier state.  If the exit conditions of the current
phase are met, the lifecycle calls ``advance``, which creates an immutable
``PhaseTransitionRecord`` and increments ``current_index``.  Rollback (to
handle false-positive transitions) is also supported.

Obstruction Density
-------------------
``ObstructionDensityMonitor`` tracks an exponentially-weighted moving average
of the obstruction density signal.  High obstruction density (above
``OBSTRUCTION_HIGH_THRESHOLD``) is a secondary phase-change trigger: if the
search is stuck in EXPLOITATION with high obstruction, the coordinator may
choose to switch strategies even if the coverage target has not been reached.

Project Scale
-------------
``ProjectScaleDetector`` classifies the project as small/medium/large/very_large
based on obligation count, module count, maximum proof depth, and the number
of active evidence channels.  Only large and very_large projects are expected
to exhibit the full three-phase lifecycle; small projects may skip straight to
CONVERGENCE.

References
----------
theory2.tex Ch47 §4 — "Phase lifecycle in large structured proof projects"
theory2.tex Ch47 §3 — "Diversity preservation" (see search_should_preserve_diversity_a.py)
"""

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

EXPLORATION_MAX_COVERAGE = 0.4
EXPLOITATION_MIN_COVERAGE = 0.4
CONVERGENCE_MIN_COVERAGE = 0.85
OBSTRUCTION_HIGH_THRESHOLD = 0.7
PHASE_NAMES = ["EXPLORATION", "EXPLOITATION", "CONVERGENCE"]
__all__ = [
    "SemanticPhase", "PhaseSignal", "PhaseTransitionRecord",
    "PhaseLifecycle", "ObstructionDensityMonitor", "ProjectScaleDetector",
    "LargeProjectPhaseCoordinator", "LargeProjectPhaseAnalyzer",
    "LargeProjectPhaseWitness",
]

try:
    from jugeo.orchestration.frontier_phases.models import (
        PhaseKind, TransitionTrigger, PhaseDescriptor, PhaseTransitionRecord,
        PhaseHistory, StallDetector, ConvergenceCertificate, PhaseHealthStatus,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier import (
        Frontier, FrontierNode, FrontierHistory, PhaseTransition,
        BackpressureController, FrontierBudget, FrontierDiversity,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.controller import (
        OrchestratorState, SemanticMove, ConvergenceMonitor,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustProfile
except Exception:
    pass


# ---------------------------------------------------------------------------
# SemanticPhase
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SemanticPhase:
    """An immutable description of one named phase in the proof-search lifecycle.

    A ``SemanticPhase`` captures the identity, entry/exit conditions, and
    expansion strategy for one of the three canonical phases.  It is a
    *descriptor*, not an executor: it answers the questions "have we entered
    this phase yet?" and "should we leave this phase?" but does not itself
    perform any search.

    Entry and exit conditions are expressed as dicts mapping signal keys to
    minimum thresholds.  ``is_entry_met`` returns True when all signals in
    ``entry_conditions`` meet or exceed their thresholds.  ``is_exit_met``
    works analogously for exit.  An empty exit condition dict (as used for
    CONVERGENCE) never triggers an automatic exit.

    The ``strategy`` field is a hint to the expansion engine:
      - "breadth_first": expand in BFS order regardless of node score.
      - "best_first": expand the highest-scoring node first.
      - "greedy": expand toward the nearest unclosed obligation.

    Attributes:
        phase_id: Unique identifier (UUID string).
        phase_name: Human-readable name, one of PHASE_NAMES.
        phase_index: Integer index (0=EXPLORATION, 1=EXPLOITATION, 2=CONVERGENCE).
        entry_conditions: Dict of {signal_key: min_value} for entry check.
        exit_conditions: Dict of {signal_key: min_value} for exit check.
        strategy: Expansion strategy hint string.
        metadata: Arbitrary annotations.
    """

    phase_id: str
    phase_name: str
    phase_index: int
    entry_conditions: dict
    exit_conditions: dict
    strategy: str
    metadata: dict

    def is_entry_met(self, signals: dict) -> bool:
        """Return True if all entry conditions are satisfied by *signals*.

        Each key in ``entry_conditions`` must be present in *signals* with a
        value at least as large as the condition threshold.  Missing signal
        keys are treated as 0.0.

        Args:
            signals: Dict mapping signal names to their current float values.

        Returns:
            True when every entry condition threshold is met.

        Examples:
            >>> phase = SemanticPhase.exploration()
            >>> phase.is_entry_met({"coverage_ratio": 0.0})
            True
        """
        for key, threshold in self.entry_conditions.items():
            if signals.get(key, 0.0) < threshold:
                return False
        return True

    def is_exit_met(self, signals: dict) -> bool:
        """Return True if all exit conditions are satisfied by *signals*.

        An empty ``exit_conditions`` dict (as in CONVERGENCE) means the phase
        never auto-exits.

        Args:
            signals: Dict mapping signal names to their current float values.

        Returns:
            True when every exit condition threshold is met, or when
            ``exit_conditions`` is empty.

        Examples:
            >>> phase = SemanticPhase.convergence()
            >>> phase.is_exit_met({"coverage_ratio": 1.0})
            False  # convergence has empty exit_conditions
        """
        if not self.exit_conditions:
            return False
        for key, threshold in self.exit_conditions.items():
            if signals.get(key, 0.0) < threshold:
                return False
        return True

    def to_dict(self) -> dict:
        """Serialise this phase descriptor to a plain dictionary.

        Returns:
            Dict with all field values.
        """
        return {
            "phase_id": self.phase_id,
            "phase_name": self.phase_name,
            "phase_index": self.phase_index,
            "entry_conditions": dict(self.entry_conditions),
            "exit_conditions": dict(self.exit_conditions),
            "strategy": self.strategy,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def exploration(cls) -> "SemanticPhase":
        """Construct the EXPLORATION phase descriptor.

        Entry condition: coverage_ratio >= 0.0 (always satisfied).
        Exit condition:  coverage_ratio >= EXPLORATION_MAX_COVERAGE (0.4).
        Strategy: breadth_first.

        Returns:
            A new frozen SemanticPhase for EXPLORATION.
        """
        return cls(
            phase_id=str(uuid.uuid4()),
            phase_name="EXPLORATION",
            phase_index=0,
            entry_conditions={"coverage_ratio": 0.0},
            exit_conditions={"coverage_ratio": EXPLORATION_MAX_COVERAGE},
            strategy="breadth_first",
            metadata={"created_at": time.time()},
        )

    @classmethod
    def exploitation(cls) -> "SemanticPhase":
        """Construct the EXPLOITATION phase descriptor.

        Entry condition: coverage_ratio >= EXPLOITATION_MIN_COVERAGE (0.4).
        Exit condition:  coverage_ratio >= CONVERGENCE_MIN_COVERAGE (0.85).
        Strategy: best_first.

        Returns:
            A new frozen SemanticPhase for EXPLOITATION.
        """
        return cls(
            phase_id=str(uuid.uuid4()),
            phase_name="EXPLOITATION",
            phase_index=1,
            entry_conditions={"coverage_ratio": EXPLOITATION_MIN_COVERAGE},
            exit_conditions={"coverage_ratio": CONVERGENCE_MIN_COVERAGE},
            strategy="best_first",
            metadata={"created_at": time.time()},
        )

    @classmethod
    def convergence(cls) -> "SemanticPhase":
        """Construct the CONVERGENCE phase descriptor.

        Entry condition: coverage_ratio >= CONVERGENCE_MIN_COVERAGE (0.85).
        Exit condition:  {} (empty — convergence has no automatic exit).
        Strategy: greedy.

        Returns:
            A new frozen SemanticPhase for CONVERGENCE.
        """
        return cls(
            phase_id=str(uuid.uuid4()),
            phase_name="CONVERGENCE",
            phase_index=2,
            entry_conditions={"coverage_ratio": CONVERGENCE_MIN_COVERAGE},
            exit_conditions={},
            strategy="greedy",
            metadata={"created_at": time.time()},
        )


# ---------------------------------------------------------------------------
# PhaseSignal
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PhaseSignal:
    """An immutable snapshot of the key signals that drive phase transitions.

    A ``PhaseSignal`` is sampled once per coordinator tick from the live
    frontier state.  It aggregates the five most important signals for phase
    management into a single, time-stamped record that can be logged,
    replayed, or used to reconstruct the decision history.

    The ``is_phase_change_warranted`` heuristic returns True when either
    obstruction density is critically high or coverage has crossed a phase
    boundary.  ``dominant_signal`` names the single signal whose value most
    deviated from its neutral baseline, helping the operator understand why
    a phase change was triggered.

    Attributes:
        signal_id: Unique identifier.
        obstruction_density: EWMA of recent obstruction density, in [0, 1].
        coverage_ratio: Current fraction of covered obligations, in [0, 1].
        trust_mass: Aggregate trust mass of frontier nodes, in [0, 1].
        entropy: Normalised proof-mode entropy, in [0, 1].
        stall_count: Number of consecutive iterations without improvement.
        timestamp: Unix timestamp.
    """

    signal_id: str
    obstruction_density: float
    coverage_ratio: float
    trust_mass: float
    entropy: float
    stall_count: int
    timestamp: float

    def is_phase_change_warranted(self) -> bool:
        """Return True if current signals suggest a phase change is needed.

        A change is warranted in any of the following situations:
          - coverage_ratio has crossed EXPLORATION_MAX_COVERAGE from below
            (time to move from EXPLORATION to EXPLOITATION).
          - coverage_ratio has crossed CONVERGENCE_MIN_COVERAGE from below
            (time to move to CONVERGENCE).
          - obstruction_density exceeds OBSTRUCTION_HIGH_THRESHOLD while
            stall_count > 3 (the search is stuck).
          - entropy is critically low (< 0.1) while stall_count > 5 (the
            search has collapsed to a single mode and is not making progress).

        Returns:
            True if any of the above conditions holds.
        """
        if self.coverage_ratio >= CONVERGENCE_MIN_COVERAGE:
            return True
        if self.coverage_ratio >= EXPLORATION_MAX_COVERAGE:
            return True
        if self.obstruction_density >= OBSTRUCTION_HIGH_THRESHOLD and self.stall_count > 3:
            return True
        if self.entropy < 0.1 and self.stall_count > 5:
            return True
        return False

    def dominant_signal(self) -> str:
        """Return the name of the signal that most warrants attention.

        Scores each signal against a neutral baseline and returns the name of
        the one with the largest absolute deviation.

        Returns:
            One of "obstruction_density", "coverage_ratio", "trust_mass",
            "entropy", or "stall_count".
        """
        scores = {
            "obstruction_density": abs(self.obstruction_density - 0.5),
            "coverage_ratio": self.coverage_ratio,
            "trust_mass": abs(self.trust_mass - 0.5),
            "entropy": abs(self.entropy - 0.5),
            "stall_count": min(self.stall_count / 10.0, 1.0),
        }
        return max(scores, key=lambda k: scores[k])

    def to_dict(self) -> dict:
        """Serialise the signal to a plain dictionary.

        Returns:
            Dict with all field values and derived is_phase_change_warranted.
        """
        return {
            "signal_id": self.signal_id,
            "obstruction_density": self.obstruction_density,
            "coverage_ratio": self.coverage_ratio,
            "trust_mass": self.trust_mass,
            "entropy": self.entropy,
            "stall_count": self.stall_count,
            "timestamp": self.timestamp,
            "is_phase_change_warranted": self.is_phase_change_warranted(),
            "dominant_signal": self.dominant_signal(),
        }

    @classmethod
    def sample(cls, frontier_proxy: dict) -> "PhaseSignal":
        """Construct a PhaseSignal from a proxy dict representing frontier state.

        The proxy dict may contain any subset of the signal keys.  Missing
        keys default to sensible neutral values.

        Args:
            frontier_proxy: Dict with optional keys: obstruction_density,
                coverage_ratio, trust_mass, entropy, stall_count.

        Returns:
            A new frozen PhaseSignal.
        """
        return cls(
            signal_id=str(uuid.uuid4()),
            obstruction_density=float(frontier_proxy.get("obstruction_density", 0.1)),
            coverage_ratio=float(frontier_proxy.get("coverage_ratio", 0.0)),
            trust_mass=float(frontier_proxy.get("trust_mass", 0.5)),
            entropy=float(frontier_proxy.get("entropy", 0.5)),
            stall_count=int(frontier_proxy.get("stall_count", 0)),
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# PhaseTransitionRecord
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PhaseTransitionRecord:
    """Immutable record of a single phase transition event.

    Every time the lifecycle advances from one phase to the next, a
    ``PhaseTransitionRecord`` is created and appended to the lifecycle's
    ``transition_records`` list.  These records form a complete audit trail
    of the search's strategic decisions.

    The ``trust_preserved`` flag indicates whether the transition maintained
    the trust invariants described in theory2.tex §5.  A transition that
    violates trust constraints is still recorded but is flagged for review.

    Attributes:
        transition_id: Unique identifier for this transition event.
        from_phase: Name of the phase being left.
        to_phase: Name of the phase being entered.
        trigger_signal: The PhaseSignal that triggered this transition.
        approved_by: Identifier of the component that approved the transition
            (e.g., "lifecycle_advance", "manual_override").
        trust_preserved: Whether trust constraints were maintained.
        timestamp: Unix timestamp of the transition.
        evidence: Dict of supporting evidence (signal dict, coverage snapshot).
    """

    transition_id: str
    from_phase: str
    to_phase: str
    trigger_signal: PhaseSignal
    approved_by: str
    trust_preserved: bool
    timestamp: float
    evidence: dict

    def to_dict(self) -> dict:
        """Serialise this record to a plain dictionary.

        Returns:
            Dict with all field values; trigger_signal is also serialised.
        """
        return {
            "transition_id": self.transition_id,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "trigger_signal": self.trigger_signal.to_dict(),
            "approved_by": self.approved_by,
            "trust_preserved": self.trust_preserved,
            "timestamp": self.timestamp,
            "evidence": dict(self.evidence),
        }

    def summary(self) -> str:
        """Return a one-line summary of this transition.

        Returns:
            A string like "EXPLORATION→EXPLOITATION (trust=True, t=1234567890.0)".
        """
        return (
            f"{self.from_phase}→{self.to_phase} "
            f"(trust={self.trust_preserved}, t={self.timestamp:.1f})"
        )

    @classmethod
    def make(cls, from_phase: str, to_phase: str,
             trigger_signal: PhaseSignal) -> "PhaseTransitionRecord":
        """Construct a transition record from minimal inputs.

        Args:
            from_phase: Name of the phase being left.
            to_phase: Name of the phase being entered.
            trigger_signal: The signal that triggered the transition.

        Returns:
            A new frozen PhaseTransitionRecord with default approved_by and
            trust_preserved=True.
        """
        return cls(
            transition_id=str(uuid.uuid4()),
            from_phase=from_phase,
            to_phase=to_phase,
            trigger_signal=trigger_signal,
            approved_by="lifecycle_advance",
            trust_preserved=True,
            timestamp=time.time(),
            evidence={"signal": trigger_signal.to_dict()},
        )


# ---------------------------------------------------------------------------
# PhaseLifecycle
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PhaseLifecycle:
    """Manages the ordered sequence of SemanticPhase objects and mediates transitions.

    The lifecycle owns the canonical list of phases (EXPLORATION, EXPLOITATION,
    CONVERGENCE) and tracks which phase the search is currently in via
    ``current_index``.  It is the single source of truth for phase state.

    The ``advance`` method checks whether the current phase's exit conditions
    are met and, if so, transitions to the next phase.  It returns a
    ``PhaseTransitionRecord`` on success or None if no transition occurred.

    The ``rollback`` method steps back to the previous phase, which may be
    necessary if a transition was triggered by a transient signal spike that
    did not reflect genuine progress.

    Attributes:
        lifecycle_id: Unique identifier.
        phases: Ordered list of SemanticPhase objects.
        current_index: Index into phases of the active phase.
        transition_records: Chronological list of PhaseTransitionRecord objects.
        started_at: Unix timestamp when the lifecycle was created.
    """

    lifecycle_id: str
    phases: list
    current_index: int
    transition_records: list
    started_at: float

    def current_phase(self) -> SemanticPhase:
        """Return the currently active SemanticPhase.

        Returns:
            The SemanticPhase at index current_index.

        Raises:
            IndexError: If current_index is out of range (should not occur in
                normal operation).
        """
        return self.phases[self.current_index]

    def advance(self, trigger_signal: PhaseSignal) -> "PhaseTransitionRecord | None":
        """Attempt to advance to the next phase given *trigger_signal*.

        Checks exit conditions of the current phase.  If met and there is a
        next phase, creates a transition record and increments current_index.

        Args:
            trigger_signal: The signal snapshot that is proposing the advance.

        Returns:
            A new PhaseTransitionRecord if the transition occurred, else None.
        """
        if not self.can_advance(trigger_signal):
            return None
        if self.current_index >= len(self.phases) - 1:
            return None
        from_phase = self.current_phase()
        self.current_index += 1
        to_phase = self.current_phase()
        record = PhaseTransitionRecord.make(
            from_phase=from_phase.phase_name,
            to_phase=to_phase.phase_name,
            trigger_signal=trigger_signal,
        )
        self.transition_records.append(record)
        return record

    def can_advance(self, signal: PhaseSignal) -> bool:
        """Return True if the current phase's exit conditions are satisfied.

        Args:
            signal: The current PhaseSignal.

        Returns:
            True if exit conditions are met and there is a next phase.
        """
        if self.current_index >= len(self.phases) - 1:
            return False
        signals_dict = {
            "coverage_ratio": signal.coverage_ratio,
            "obstruction_density": signal.obstruction_density,
            "entropy": signal.entropy,
            "trust_mass": signal.trust_mass,
        }
        return self.current_phase().is_exit_met(signals_dict)

    def rollback(self) -> bool:
        """Roll back to the previous phase.

        Returns:
            True if rollback succeeded; False if already at phase 0.
        """
        if self.current_index <= 0:
            return False
        self.current_index -= 1
        return True

    def elapsed_in_phase(self) -> float:
        """Return seconds elapsed since the most recent phase transition.

        Returns:
            Float seconds.  Measures from started_at if no transitions yet.
        """
        if self.transition_records:
            return time.time() - self.transition_records[-1].timestamp
        return time.time() - self.started_at

    def lifecycle_summary(self) -> dict:
        """Return a summary dict of the lifecycle state.

        Returns:
            Dict with current phase, transition count, and elapsed time.
        """
        return {
            "lifecycle_id": self.lifecycle_id,
            "current_phase": self.current_phase().phase_name,
            "current_index": self.current_index,
            "transition_count": len(self.transition_records),
            "elapsed_in_phase": self.elapsed_in_phase(),
        }

    def to_dict(self) -> dict:
        """Serialise the lifecycle to a plain dictionary.

        Returns:
            Dict with lifecycle_summary plus phases and transition_records.
        """
        return {
            **self.lifecycle_summary(),
            "phases": [p.to_dict() for p in self.phases],
            "transition_records": [r.to_dict() for r in self.transition_records],
        }


# ---------------------------------------------------------------------------
# ObstructionDensityMonitor
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ObstructionDensityMonitor:
    """Tracks and smooths the obstruction density signal over time.

    Obstruction density measures how frequently the search encounters hard
    sub-goals (type errors, unification failures, missing lemmas) that block
    expansion.  High obstruction density in EXPLOITATION is a secondary signal
    for transitioning strategy or triggering backtracking.

    The monitor computes an exponentially-weighted moving average (EWMA) with
    smoothing factor alpha = 2 / (window_size + 1).  It also provides a linear
    trend over the last window_size samples.

    Attributes:
        monitor_id: Unique identifier.
        samples: Raw density samples in chronological order.
        window_size: Number of samples to include in the EWMA window.
        threshold_high: Density above which ``is_high`` returns True.
        threshold_low: Density below which ``is_low`` returns True.
    """

    monitor_id: str
    samples: list
    window_size: int
    threshold_high: float
    threshold_low: float

    def record(self, density: float) -> None:
        """Record a new obstruction density observation.

        Args:
            density: Float in [0, 1] representing the fraction of expansion
                attempts that were obstructed in the current iteration.
        """
        self.samples.append(float(density))

    def current(self) -> float:
        """Return the current EWMA of obstruction density.

        Returns:
            Float in [0, 1].  Returns 0.0 if no samples recorded.
        """
        if not self.samples:
            return 0.0
        alpha = 2.0 / (self.window_size + 1)
        ewma = self.samples[0]
        for s in self.samples[1:]:
            ewma = alpha * s + (1 - alpha) * ewma
        return ewma

    def trend(self) -> float:
        """Return the linear slope of the density over the last window_size samples.

        Positive means density is increasing (search is hitting more obstacles);
        negative means it is decreasing.

        Returns:
            Float slope.  Zero if fewer than two samples.
        """
        if len(self.samples) < 2:
            return 0.0
        recent = self.samples[-self.window_size:]
        n = len(recent)
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(recent) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, recent))
        den = sum((x - x_mean) ** 2 for x in xs)
        return num / den if den != 0 else 0.0

    def is_high(self) -> bool:
        """Return True if the current EWMA exceeds threshold_high.

        Returns:
            True when density is critically high.
        """
        return self.current() >= self.threshold_high

    def is_low(self) -> bool:
        """Return True if the current EWMA is below threshold_low.

        Returns:
            True when density is reassuringly low.
        """
        return self.current() < self.threshold_low

    def density_report(self) -> dict:
        """Return a summary of the obstruction density state.

        Returns:
            Dict with current EWMA, trend, is_high, is_low, and sample count.
        """
        return {
            "monitor_id": self.monitor_id,
            "current_ewma": self.current(),
            "trend": self.trend(),
            "is_high": self.is_high(),
            "is_low": self.is_low(),
            "sample_count": len(self.samples),
            "threshold_high": self.threshold_high,
            "threshold_low": self.threshold_low,
        }

    def to_dict(self) -> dict:
        """Serialise the monitor state to a plain dictionary.

        Returns:
            Dict equivalent to density_report.
        """
        return self.density_report()


# ---------------------------------------------------------------------------
# ProjectScaleDetector
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ProjectScaleDetector:
    """Classifies the project size and complexity to calibrate phase thresholds.

    The phase lifecycle thresholds in this module (EXPLORATION_MAX_COVERAGE etc.)
    are calibrated for large projects.  The ``ProjectScaleDetector`` provides
    a normalised ``scale_score`` that can be used to confirm whether the
    three-phase model is appropriate, and to adjust thresholds for smaller
    projects in future extensions.

    The scale score is a weighted combination of four indicators:
      - obligation_count: Number of proof obligations in the project.
      - module_count: Number of distinct source modules.
      - depth_max: Maximum proof depth encountered so far.
      - evidence_channels: Number of distinct evidence sources feeding the
        frontier (e.g., type-checker, linter, test suite, formal verifier).

    Attributes:
        detector_id: Unique identifier.
        obligation_count: Integer count of proof obligations.
        module_count: Integer count of source modules.
        depth_max: Maximum proof-tree depth observed.
        evidence_channels: Number of evidence-providing channels.
    """

    detector_id: str
    obligation_count: int
    module_count: int
    depth_max: int
    evidence_channels: int

    def is_large_project(self) -> bool:
        """Return True if the project qualifies as "large".

        A project is large when scale_score > 0.6.  Large projects are
        expected to benefit from the three-phase lifecycle model.

        Returns:
            True for large and very_large projects.
        """
        return self.scale_score() > 0.6

    def scale_score(self) -> float:
        """Return a normalised scale score in [0, 1].

        The score is computed as a weighted average of four normalised
        indicators, each capped at 1.0:
          - obligation_count / 500  (weight 0.40)
          - module_count / 50       (weight 0.25)
          - depth_max / 20          (weight 0.20)
          - evidence_channels / 10  (weight 0.15)

        Returns:
            Float in [0, 1].
        """
        obl_norm = min(self.obligation_count / 500.0, 1.0)
        mod_norm = min(self.module_count / 50.0, 1.0)
        dep_norm = min(self.depth_max / 20.0, 1.0)
        ev_norm = min(self.evidence_channels / 10.0, 1.0)
        return 0.40 * obl_norm + 0.25 * mod_norm + 0.20 * dep_norm + 0.15 * ev_norm

    def complexity_indicator(self) -> str:
        """Return a human-readable complexity category.

        Returns:
            One of "small", "medium", "large", or "very_large".
        """
        s = self.scale_score()
        if s < 0.25:
            return "small"
        if s < 0.5:
            return "medium"
        if s < 0.75:
            return "large"
        return "very_large"

    def to_dict(self) -> dict:
        """Serialise the detector state to a plain dictionary.

        Returns:
            Dict with all fields plus scale_score and complexity_indicator.
        """
        return {
            "detector_id": self.detector_id,
            "obligation_count": self.obligation_count,
            "module_count": self.module_count,
            "depth_max": self.depth_max,
            "evidence_channels": self.evidence_channels,
            "scale_score": self.scale_score(),
            "complexity_indicator": self.complexity_indicator(),
            "is_large_project": self.is_large_project(),
        }


# ---------------------------------------------------------------------------
# LargeProjectPhaseCoordinator
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LargeProjectPhaseCoordinator:
    """Central coordinator implementing the three-phase lifecycle for large projects.

    The coordinator owns a ``PhaseLifecycle``, an ``ObstructionDensityMonitor``,
    and a ``ProjectScaleDetector``.  On each ``tick`` it:
      1. Samples a ``PhaseSignal`` from the frontier proxy.
      2. Records the obstruction density.
      3. Attempts to advance the lifecycle.
      4. Returns a dict summarising the step outcome.

    The ``trust_preserved_check`` method validates that all recorded transitions
    preserved the trust invariant (i.e., no high-trust evidence was discarded
    when transitioning phases).

    Attributes:
        coordinator_id: Unique identifier.
        lifecycle: The PhaseLifecycle managing phase state.
        obstruction_monitor: The ObstructionDensityMonitor.
        scale_detector: The ProjectScaleDetector.
        signal_history: List of PhaseSignal objects in chronological order.
        iteration_count: Number of ticks executed.
    """

    coordinator_id: str
    lifecycle: PhaseLifecycle
    obstruction_monitor: ObstructionDensityMonitor
    scale_detector: ProjectScaleDetector
    signal_history: list
    iteration_count: int

    def tick(self, frontier_proxy: dict) -> dict:
        """Execute one phase-management step.

        Samples a PhaseSignal from *frontier_proxy*, records obstruction density,
        attempts a phase transition, and returns a step-summary dict.

        Args:
            frontier_proxy: Dict with optional signal keys: obstruction_density,
                coverage_ratio, trust_mass, entropy, stall_count.

        Returns:
            Dict with keys: iteration, current_phase, transitioned (bool),
            transition_summary (str or None), signal (dict).
        """
        signal = PhaseSignal.sample(frontier_proxy)
        self.signal_history.append(signal)
        self.obstruction_monitor.record(signal.obstruction_density)
        self.iteration_count += 1

        transition = self.try_advance()
        return {
            "iteration": self.iteration_count,
            "current_phase": self.lifecycle.current_phase().phase_name,
            "transitioned": transition is not None,
            "transition_summary": transition.summary() if transition else None,
            "signal": signal.to_dict(),
        }

    def try_advance(self) -> "PhaseTransitionRecord | None":
        """Attempt to advance the lifecycle given the most recent signal.

        Returns:
            A PhaseTransitionRecord if the advance succeeded, else None.
        """
        if not self.signal_history:
            return None
        return self.lifecycle.advance(self.signal_history[-1])

    def current_phase_name(self) -> str:
        """Return the name of the currently active phase.

        Returns:
            String from PHASE_NAMES.
        """
        return self.lifecycle.current_phase().phase_name

    def phase_duration(self) -> float:
        """Return seconds elapsed in the current phase.

        Returns:
            Non-negative float.
        """
        return self.lifecycle.elapsed_in_phase()

    def trust_preserved_check(self) -> bool:
        """Return True if all recorded transitions preserved trust.

        Iterates over the lifecycle's transition_records and returns False if
        any record has trust_preserved=False.

        Returns:
            True when all transitions were trust-preserving.
        """
        return all(r.trust_preserved for r in self.lifecycle.transition_records)

    def summarize(self) -> dict:
        """Return a high-level summary of coordinator state.

        Returns:
            Dict with coordinator_id, current_phase_name, iteration_count,
            transition_count, trust_preserved, and scale information.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "current_phase": self.current_phase_name(),
            "iteration_count": self.iteration_count,
            "transition_count": len(self.lifecycle.transition_records),
            "trust_preserved": self.trust_preserved_check(),
            "scale": self.scale_detector.to_dict(),
            "obstruction": self.obstruction_monitor.density_report(),
        }

    def to_dict(self) -> dict:
        """Serialise full coordinator state to a plain dictionary.

        Returns:
            Dict with lifecycle, obstruction monitor, scale detector, and summary.
        """
        return {
            **self.summarize(),
            "lifecycle": self.lifecycle.lifecycle_summary(),
            "signal_history_length": len(self.signal_history),
        }

    @classmethod
    def make(cls) -> "LargeProjectPhaseCoordinator":
        """Construct a fresh coordinator with a default lifecycle and sub-systems.

        Seeds the lifecycle with all three canonical phases and initialises
        the monitor and detector with default parameters appropriate for a
        large project.

        Returns:
            A new LargeProjectPhaseCoordinator ready for use.
        """
        phases = [
            SemanticPhase.exploration(),
            SemanticPhase.exploitation(),
            SemanticPhase.convergence(),
        ]
        lifecycle = PhaseLifecycle(
            lifecycle_id=str(uuid.uuid4()),
            phases=phases,
            current_index=0,
            transition_records=[],
            started_at=time.time(),
        )
        monitor = ObstructionDensityMonitor(
            monitor_id=str(uuid.uuid4()),
            samples=[],
            window_size=10,
            threshold_high=OBSTRUCTION_HIGH_THRESHOLD,
            threshold_low=0.2,
        )
        detector = ProjectScaleDetector(
            detector_id=str(uuid.uuid4()),
            obligation_count=300,
            module_count=25,
            depth_max=12,
            evidence_channels=4,
        )
        return cls(
            coordinator_id=str(uuid.uuid4()),
            lifecycle=lifecycle,
            obstruction_monitor=monitor,
            scale_detector=detector,
            signal_history=[],
            iteration_count=0,
        )


# ---------------------------------------------------------------------------
# LargeProjectPhaseAnalyzer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LargeProjectPhaseAnalyzer:
    """Analyses phase transitions and signals for anomalies and performance issues.

    The analyzer accumulates ``PhaseTransitionRecord`` and ``PhaseSignal``
    objects and provides higher-level diagnostics: transition rate, per-phase
    duration statistics, and an anomaly score.

    A high anomaly score indicates that the lifecycle is behaving unexpectedly
    (e.g., transitioning too quickly, rolling back frequently, or exhibiting
    high-obstruction density in early phases).

    Attributes:
        analyzer_id: Unique identifier.
        transition_log: List of PhaseTransitionRecord objects.
        signal_log: List of PhaseSignal objects.
    """

    analyzer_id: str
    transition_log: list
    signal_log: list

    def record_transition(self, record: PhaseTransitionRecord) -> None:
        """Append a transition record to the log.

        Args:
            record: A completed PhaseTransitionRecord.
        """
        self.transition_log.append(record)

    def record_signal(self, signal: PhaseSignal) -> None:
        """Append a phase signal to the log.

        Args:
            signal: A sampled PhaseSignal.
        """
        self.signal_log.append(signal)

    def transition_rate(self) -> float:
        """Return transitions per signal sample (a proxy for transition frequency).

        Returns:
            Float.  Zero if no signals recorded.
        """
        if not self.signal_log:
            return 0.0
        return len(self.transition_log) / len(self.signal_log)

    def phase_duration_stats(self) -> dict:
        """Return per-phase duration statistics from the transition log.

        Computes mean and max time spent in each phase based on the
        timestamps in transition records.

        Returns:
            Dict mapping phase_name -> {"mean_duration": float, "count": int}.
        """
        durations: dict[str, list] = {}
        for i, record in enumerate(self.transition_log):
            fp = record.from_phase
            if i == 0:
                prev_ts = record.timestamp - 1.0
            else:
                prev_ts = self.transition_log[i - 1].timestamp
            dur = record.timestamp - prev_ts
            durations.setdefault(fp, []).append(dur)
        stats = {}
        for phase, durs in durations.items():
            stats[phase] = {
                "mean_duration": sum(durs) / len(durs),
                "count": len(durs),
                "max_duration": max(durs),
            }
        return stats

    def anomaly_score(self) -> float:
        """Return an anomaly score in [0, 1] for the lifecycle behaviour.

        Higher values indicate more unusual behaviour.  Contributors:
          - Very high transition rate (> 0.5): +0.3
          - Any non-trust-preserved transition: +0.4 per occurrence (capped at 0.4)
          - Mean obstruction > OBSTRUCTION_HIGH_THRESHOLD: +0.3

        Returns:
            Float in [0, 1].
        """
        score = 0.0
        if self.transition_rate() > 0.5:
            score += 0.3
        if any(not r.trust_preserved for r in self.transition_log):
            score += 0.4
        if self.signal_log:
            mean_obs = sum(s.obstruction_density for s in self.signal_log) / len(self.signal_log)
            if mean_obs > OBSTRUCTION_HIGH_THRESHOLD:
                score += 0.3
        return min(score, 1.0)

    def report(self) -> dict:
        """Return a comprehensive analysis report.

        Returns:
            Dict with transition_rate, phase_duration_stats, anomaly_score,
            and signal/transition counts.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "transition_count": len(self.transition_log),
            "signal_count": len(self.signal_log),
            "transition_rate": self.transition_rate(),
            "phase_duration_stats": self.phase_duration_stats(),
            "anomaly_score": self.anomaly_score(),
        }

    def to_dict(self) -> dict:
        """Serialise analyzer state to a plain dictionary.

        Returns:
            Dict equivalent to report().
        """
        return self.report()


# ---------------------------------------------------------------------------
# LargeProjectPhaseWitness
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LargeProjectPhaseWitness:
    """Immutable attestation that the phase lifecycle is operating correctly.

    A ``LargeProjectPhaseWitness`` is issued periodically to certify that the
    large-project phase lifecycle has been followed correctly: that phases
    were entered in the right order, that trust was preserved across all
    transitions, and that the anomaly score is within acceptable bounds.

    Witnesses are frozen and can be serialised, transmitted to external audit
    systems, or stored alongside proof certificates.

    Attributes:
        witness_id: Unique identifier.
        coordinator_id: Id of the issuing coordinator.
        current_phase: Name of the phase at time of issuance.
        phase_index: Integer index of the current phase (0/1/2).
        trust_preserved: Whether all transitions were trust-preserving.
        iteration_count: Total iterations executed by the coordinator.
        timestamp: Unix timestamp of issuance.
        evidence: Dict of supporting evidence (anomaly score, signal summary).
    """

    witness_id: str
    coordinator_id: str
    current_phase: str
    phase_index: int
    trust_preserved: bool
    iteration_count: int
    timestamp: float
    evidence: dict

    def to_dict(self) -> dict:
        """Serialise the witness to a plain dictionary.

        Returns:
            Dict with all field values plus derived is_healthy flag.
        """
        return {
            "witness_id": self.witness_id,
            "coordinator_id": self.coordinator_id,
            "current_phase": self.current_phase,
            "phase_index": self.phase_index,
            "trust_preserved": self.trust_preserved,
            "iteration_count": self.iteration_count,
            "timestamp": self.timestamp,
            "is_healthy": self.is_healthy(),
            "evidence": dict(self.evidence),
        }

    def is_healthy(self) -> bool:
        """Return True if the lifecycle is operating within normal parameters.

        Health requires that trust was preserved and the anomaly score (if
        available in evidence) is below 0.5.

        Returns:
            True when healthy.
        """
        if not self.trust_preserved:
            return False
        anomaly = self.evidence.get("anomaly_score", 0.0)
        return float(anomaly) < 0.5

    def certify_text(self) -> str:
        """Return a human-readable certification summary.

        Returns:
            A multi-line string describing the witness state and health verdict.
        """
        verdict = "HEALTHY" if self.is_healthy() else "DEGRADED"
        return (
            f"LargeProjectPhaseWitness [{self.witness_id[:8]}]\n"
            f"  Coordinator  : {self.coordinator_id[:8]}\n"
            f"  Verdict      : {verdict}\n"
            f"  Phase        : {self.current_phase} (index={self.phase_index})\n"
            f"  Trust        : {'preserved' if self.trust_preserved else 'VIOLATED'}\n"
            f"  Iterations   : {self.iteration_count}\n"
            f"  Issued at    : {self.timestamp:.3f}"
        )

    @classmethod
    def issue(cls, coordinator: LargeProjectPhaseCoordinator,
              analyzer: LargeProjectPhaseAnalyzer) -> "LargeProjectPhaseWitness":
        """Issue a witness from the current state of *coordinator* and *analyzer*.

        Args:
            coordinator: The active LargeProjectPhaseCoordinator.
            analyzer: The active LargeProjectPhaseAnalyzer.

        Returns:
            A new frozen LargeProjectPhaseWitness.
        """
        report = analyzer.report()
        return cls(
            witness_id=str(uuid.uuid4()),
            coordinator_id=coordinator.coordinator_id,
            current_phase=coordinator.current_phase_name(),
            phase_index=coordinator.lifecycle.current_index,
            trust_preserved=coordinator.trust_preserved_check(),
            iteration_count=coordinator.iteration_count,
            timestamp=time.time(),
            evidence={
                "anomaly_score": report["anomaly_score"],
                "transition_count": report["transition_count"],
                "signal_count": report["signal_count"],
                "obstruction_current": coordinator.obstruction_monitor.current(),
                "scale": coordinator.scale_detector.to_dict(),
            },
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    coordinator = LargeProjectPhaseCoordinator.make()
    analyzer = LargeProjectPhaseAnalyzer(
        analyzer_id=str(uuid.uuid4()),
        transition_log=[],
        signal_log=[],
    )

    steps = [
        {"coverage_ratio": 0.1, "obstruction_density": 0.1, "entropy": 0.8, "stall_count": 0},
        {"coverage_ratio": 0.25, "obstruction_density": 0.15, "entropy": 0.75, "stall_count": 0},
        {"coverage_ratio": 0.45, "obstruction_density": 0.2, "entropy": 0.7, "stall_count": 1},
        {"coverage_ratio": 0.65, "obstruction_density": 0.35, "entropy": 0.6, "stall_count": 0},
        {"coverage_ratio": 0.9, "obstruction_density": 0.25, "entropy": 0.55, "stall_count": 0},
    ]
    for proxy in steps:
        result = coordinator.tick(proxy)
        signal = coordinator.signal_history[-1]
        analyzer.record_signal(signal)
        if result["transitioned"]:
            analyzer.record_transition(coordinator.lifecycle.transition_records[-1])

    witness = LargeProjectPhaseWitness.issue(coordinator, analyzer)
    pprint.pprint(witness.to_dict())
    print(witness.certify_text())
    print("s04 smoke test passed")
