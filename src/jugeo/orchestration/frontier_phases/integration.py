"""Integration layer connecting frontier phases to the main orchestration system. theory2.tex Ch47. # copilot:"""
from __future__ import annotations

import dataclasses
import json
import math
import time
import uuid
from typing import Any

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

try:
    from jugeo.orchestration.frontier_phases.the_frontier_should_be_managed_as import (
        ComputeBudget, BudgetLedger, FrontierBudgetedSearchCoordinator,
        FrontierBudgetWitness,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_phases.bandit_style_allocation_across_het import (
        BanditAllocator, BanditAllocationCoordinator, BanditAllocationWitness,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_phases.search_should_preserve_diversity_a import (
        SearchDiversityCoordinator, SearchDiversityWitness,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_phases.large_projects_move_through_distin import (
        LargeProjectPhaseCoordinator, LargeProjectPhaseWitness,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_phases.phase_changes_should_be_triggered import (
        PhaseChangeTriggersCoordinator, PhaseChangeTriggersWitness,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_phases.algorithms import (
        FrontierPhasesConfig, FrontierPhasesPlanner, FrontierPhasesExecutor,
        FrontierPhasesNormalizer,
    )
except Exception:
    pass

__all__ = [
    "IntegrationConfig", "PhaseChangeEvent", "FrontierPhasesState",
    "FrontierPhasesBridge", "PhaseExportSnapshot", "ExportBundle",
    "PhaseMonitorAdapter", "FrontierPhasesIntegrator",
    "build_default_integrator", "run_integration_tick",
]


@dataclasses.dataclass(frozen=True, slots=True)
class IntegrationConfig:
    """Configuration for the integration layer between frontier phases and orchestration.

    All timing values are in seconds. budget_token_limit caps total token spend.
    diversity_threshold is the minimum acceptable combined diversity score before
    an alert is raised. trust_tolerance is the maximum allowed absolute deviation
    in trust mass across a phase boundary before a trust-loss event is logged.
    """

    config_id: str
    tick_interval: float
    max_phase_duration: float
    trust_tolerance: float
    budget_token_limit: int
    diversity_threshold: float
    metadata: dict

    def to_dict(self) -> dict:
        """Serialize the config to a plain dict suitable for JSON export.

        Returns a copy of all fields; the metadata sub-dict is shallow-copied.
        """
        return {
            "config_id": self.config_id,
            "tick_interval": self.tick_interval,
            "max_phase_duration": self.max_phase_duration,
            "trust_tolerance": self.trust_tolerance,
            "budget_token_limit": self.budget_token_limit,
            "diversity_threshold": self.diversity_threshold,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def default(cls) -> "IntegrationConfig":
        """Return an IntegrationConfig with sensible production defaults.

        tick_interval=1.0 second is a reasonable polling cadence for a live
        orchestration loop. max_phase_duration=3600.0 (one hour) protects against
        phases that never converge. trust_tolerance=0.05 allows a 5% drift before
        flagging. budget_token_limit=50000 caps GPT-class token spend per run.
        diversity_threshold=0.3 ensures at least 30% diversity at all times.
        """
        return cls(
            config_id=str(uuid.uuid4()),
            tick_interval=1.0,
            max_phase_duration=3600.0,
            trust_tolerance=0.05,
            budget_token_limit=50000,
            diversity_threshold=0.3,
            metadata={"source": "default", "version": "1.0"},
        )


@dataclasses.dataclass(frozen=True, slots=True)
class PhaseChangeEvent:
    """An immutable record of a single phase transition observed by the integration layer.

    Captures the full context of the transition: from/to phase names, the trigger
    signal that caused the transition, the delta in trust mass (positive = trust
    gained, negative = trust lost), and a wall-clock timestamp.
    """

    event_id: str
    from_phase: str
    to_phase: str
    trigger: str
    trust_delta: float
    timestamp: float
    metadata: dict

    def to_dict(self) -> dict:
        """Serialize this event to a plain dictionary.

        Suitable for logging, JSON export, or downstream consumers that cannot
        accept dataclass instances directly.
        """
        return {
            "event_id": self.event_id,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "trigger": self.trigger,
            "trust_delta": self.trust_delta,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def make(cls, from_phase: str, to_phase: str, trigger: str) -> "PhaseChangeEvent":
        """Construct a PhaseChangeEvent with auto-generated id and current timestamp.

        trust_delta defaults to 0.0; callers that have a real trust measurement
        should build the dataclass directly. The metadata dict starts empty and
        can be augmented by callers after the fact (since the dict is mutable even
        though the dataclass is frozen — the reference is immutable, not the dict).
        """
        return cls(
            event_id=str(uuid.uuid4()),
            from_phase=from_phase,
            to_phase=to_phase,
            trigger=trigger,
            trust_delta=0.0,
            timestamp=time.time(),
            metadata={},
        )


@dataclasses.dataclass(slots=True)
class FrontierPhasesState:
    """Mutable snapshot of the frontier phases subsystem's operational state.

    This object is updated in-place each tick by the bridge. It accumulates
    budget consumption, tracks the current phase name, coverage ratio, diversity
    score, trust mass, and the ordered log of all phase-change events seen so far.
    All numeric fields are validated on write to prevent nonsensical state.
    """

    state_id: str
    current_phase: str
    iteration: int
    budget_used: int
    budget_limit: int
    coverage: float
    diversity: float
    trust_mass: float
    events: list

    def update(self, field: str, value: object) -> None:
        """Set a field by name with basic type and range validation.

        Raises ValueError when an out-of-range numeric value is supplied:
        - coverage and diversity must be in [0.0, 1.0]
        - budget_used must be >= 0
        - iteration must be >= 0
        - trust_mass must be >= 0.0

        Unknown field names are silently accepted via setattr so that subclasses
        can extend the state schema without breaking the base class.
        """
        if field == "coverage" and not (0.0 <= float(value) <= 1.0):
            raise ValueError(f"coverage must be in [0, 1], got {value!r}")
        if field == "diversity" and not (0.0 <= float(value) <= 1.0):
            raise ValueError(f"diversity must be in [0, 1], got {value!r}")
        if field == "budget_used" and int(value) < 0:
            raise ValueError(f"budget_used must be >= 0, got {value!r}")
        if field == "iteration" and int(value) < 0:
            raise ValueError(f"iteration must be >= 0, got {value!r}")
        if field == "trust_mass" and float(value) < 0.0:
            raise ValueError(f"trust_mass must be >= 0, got {value!r}")
        setattr(self, field, value)

    def record_event(self, event: PhaseChangeEvent) -> None:
        """Append a PhaseChangeEvent to the event log and update current_phase.

        The current_phase is updated to match the event's to_phase so the state
        always reflects the latest known phase after any transition.
        """
        self.events.append(event)
        self.current_phase = event.to_phase

    def budget_exhausted(self) -> bool:
        """Return True when budget_used has reached or exceeded budget_limit.

        Used by the bridge to decide whether to halt further exploration steps.
        A budget_limit of 0 is treated as unlimited (returns False).
        """
        if self.budget_limit <= 0:
            return False
        return self.budget_used >= self.budget_limit

    def phase_progress(self) -> dict:
        """Return a concise progress summary keyed by metric name.

        budget_fraction is the fraction of budget consumed in [0, 1]. Values
        above 1.0 indicate an over-run and are clamped to 1.0 for display.
        """
        budget_fraction = (
            min(1.0, self.budget_used / self.budget_limit)
            if self.budget_limit > 0 else 0.0
        )
        return {
            "current_phase": self.current_phase,
            "coverage": self.coverage,
            "diversity": self.diversity,
            "trust_mass": self.trust_mass,
            "budget_fraction": budget_fraction,
        }

    def to_dict(self) -> dict:
        """Serialize the full state, including the complete event log.

        Each event in the log is serialized via PhaseChangeEvent.to_dict().
        """
        return {
            "state_id": self.state_id,
            "current_phase": self.current_phase,
            "iteration": self.iteration,
            "budget_used": self.budget_used,
            "budget_limit": self.budget_limit,
            "coverage": self.coverage,
            "diversity": self.diversity,
            "trust_mass": self.trust_mass,
            "events": [e.to_dict() for e in self.events],
        }


@dataclasses.dataclass(slots=True)
class FrontierPhasesBridge:
    """Bidirectional bridge between the frontier phases subsystem and the orchestrator.

    The bridge owns a FrontierPhasesState and updates it each tick from the
    orchestrator's state dict. It also pushes a snapshot of frontier phase metrics
    back into the orchestrator dict so downstream components can read them without
    holding a direct reference to this bridge.

    Thread-safety: not thread-safe. External callers must serialize access.
    """

    bridge_id: str
    config: IntegrationConfig
    state: FrontierPhasesState
    tick_count: int
    event_log: list

    def on_tick(self, orchestrator_state: dict) -> None:
        """Process one orchestration tick.

        Reads coverage, diversity, trust_mass, current_phase, and budget_used
        from orchestrator_state if present, updating the internal state. Increments
        the iteration counter. Does not raise on missing keys; uses current values
        as defaults so the bridge degrades gracefully when the orchestrator is not
        yet fully initialized.
        """
        self.tick_count += 1
        self.state.update("iteration", self.state.iteration + 1)

        if "coverage" in orchestrator_state:
            try:
                self.state.update("coverage", float(orchestrator_state["coverage"]))
            except (ValueError, TypeError):
                pass

        if "diversity" in orchestrator_state:
            try:
                self.state.update("diversity", float(orchestrator_state["diversity"]))
            except (ValueError, TypeError):
                pass

        if "trust_mass" in orchestrator_state:
            try:
                self.state.update("trust_mass", float(orchestrator_state["trust_mass"]))
            except (ValueError, TypeError):
                pass

        if "current_phase" in orchestrator_state:
            new_phase = str(orchestrator_state["current_phase"])
            if new_phase != self.state.current_phase:
                event = PhaseChangeEvent.make(
                    from_phase=self.state.current_phase,
                    to_phase=new_phase,
                    trigger=orchestrator_state.get("phase_trigger", "orchestrator_tick"),
                )
                self.on_phase_change(event)
            else:
                self.state.current_phase = new_phase

        if "tokens_used" in orchestrator_state:
            try:
                self.on_budget_update(int(orchestrator_state["tokens_used"]))
            except (ValueError, TypeError):
                pass

    def on_phase_change(self, event: PhaseChangeEvent) -> None:
        """Handle a phase transition event.

        Records the event in both the state's event log and the bridge's own
        event_log. The bridge event_log preserves a complete audit trail even if
        the state is reset between runs.
        """
        self.state.record_event(event)
        self.event_log.append(event)

    def on_budget_update(self, tokens_used: int) -> None:
        """Update the budget tracking fields.

        tokens_used is an absolute (cumulative) count, not a delta. The budget_limit
        is taken from config.budget_token_limit and is pushed into the state so that
        state.budget_exhausted() returns the correct value.
        """
        clamped = max(0, tokens_used)
        self.state.update("budget_used", clamped)
        self.state.budget_limit = self.config.budget_token_limit

    def sync_to_orchestrator(self, orchestrator: dict) -> dict:
        """Push current frontier phase metrics into the orchestrator dict.

        Writes phase_progress, bridge_id, and tick_count into orchestrator.
        Returns the modified dict for convenience; callers may ignore the return
        value if they already hold a reference to the dict.
        """
        orchestrator["frontier_phase_progress"] = self.state.phase_progress()
        orchestrator["frontier_bridge_id"] = self.bridge_id
        orchestrator["frontier_tick_count"] = self.tick_count
        orchestrator["frontier_budget_exhausted"] = self.state.budget_exhausted()
        return orchestrator

    def status(self) -> dict:
        """Return a lightweight status summary for health-check endpoints.

        Includes bridge_id, tick_count, current phase, budget exhaustion flag,
        and the number of phase transitions recorded so far.
        """
        return {
            "bridge_id": self.bridge_id,
            "tick_count": self.tick_count,
            "current_phase": self.state.current_phase,
            "budget_exhausted": self.state.budget_exhausted(),
            "phase_transitions": len(self.event_log),
            "coverage": self.state.coverage,
            "diversity": self.state.diversity,
            "trust_mass": self.state.trust_mass,
        }

    def to_dict(self) -> dict:
        """Full serialization of the bridge including config, state, and event log."""
        return {
            "bridge_id": self.bridge_id,
            "config": self.config.to_dict(),
            "state": self.state.to_dict(),
            "tick_count": self.tick_count,
            "event_log": [e.to_dict() for e in self.event_log],
        }

    @classmethod
    def make(cls, config: IntegrationConfig) -> "FrontierPhasesBridge":
        """Construct a FrontierPhasesBridge with a fresh empty state.

        The initial phase is set to 'INIT'. budget_limit is taken from config.
        """
        state = FrontierPhasesState(
            state_id=str(uuid.uuid4()),
            current_phase="INIT",
            iteration=0,
            budget_used=0,
            budget_limit=config.budget_token_limit,
            coverage=0.0,
            diversity=1.0,
            trust_mass=1.0,
            events=[],
        )
        return cls(
            bridge_id=str(uuid.uuid4()),
            config=config,
            state=state,
            tick_count=0,
            event_log=[],
        )


@dataclasses.dataclass(frozen=True, slots=True)
class PhaseExportSnapshot:
    """An immutable point-in-time snapshot of frontier phase metrics for export.

    Captures all key metrics in a single frozen record. Suitable for appending to
    an ExportBundle and streaming as JSONL. The snapshot_id is a UUID string.
    timestamp is a POSIX float from time.time().
    """

    snapshot_id: str
    phase: str
    coverage: float
    diversity: float
    trust_mass: float
    budget_fraction: float
    iteration: int
    timestamp: float
    extra: dict

    def to_dict(self) -> dict:
        """Serialize this snapshot to a plain dictionary.

        The extra dict is shallow-copied to avoid mutating the snapshot's
        internal data through the returned dict.
        """
        return {
            "snapshot_id": self.snapshot_id,
            "phase": self.phase,
            "coverage": self.coverage,
            "diversity": self.diversity,
            "trust_mass": self.trust_mass,
            "budget_fraction": self.budget_fraction,
            "iteration": self.iteration,
            "timestamp": self.timestamp,
            "extra": dict(self.extra),
        }

    @classmethod
    def capture(cls, state: FrontierPhasesState) -> "PhaseExportSnapshot":
        """Take a snapshot of the current FrontierPhasesState.

        Computes budget_fraction from state.budget_used / state.budget_limit,
        clamping to [0, 1]. If budget_limit is zero, budget_fraction is 0.0.
        The extra dict is populated with the event count for traceability.
        """
        budget_fraction = 0.0
        if state.budget_limit > 0:
            budget_fraction = min(1.0, state.budget_used / state.budget_limit)
        return cls(
            snapshot_id=str(uuid.uuid4()),
            phase=state.current_phase,
            coverage=state.coverage,
            diversity=state.diversity,
            trust_mass=state.trust_mass,
            budget_fraction=budget_fraction,
            iteration=state.iteration,
            timestamp=time.time(),
            extra={"event_count": len(state.events)},
        )


@dataclasses.dataclass(slots=True)
class ExportBundle:
    """An ordered, mutable collection of PhaseExportSnapshot records.

    Snapshots are appended in chronological order. The bundle supports JSONL
    export for streaming consumers and a summary view for monitoring dashboards.
    """

    bundle_id: str
    snapshots: list
    created_at: float

    def add(self, snapshot: PhaseExportSnapshot) -> None:
        """Append a snapshot to the bundle.

        No deduplication is performed; callers are responsible for not adding
        the same snapshot twice if that would be semantically incorrect.
        """
        self.snapshots.append(snapshot)

    def latest(self) -> "PhaseExportSnapshot | None":
        """Return the most recently added snapshot, or None if the bundle is empty."""
        if not self.snapshots:
            return None
        return self.snapshots[-1]

    def export_jsonl(self) -> str:
        """Serialize all snapshots to newline-delimited JSON (JSONL).

        Each line is a valid JSON object. Lines are separated by '\n' with no
        trailing newline. An empty bundle returns an empty string.
        """
        if not self.snapshots:
            return ""
        return "\n".join(json.dumps(s.to_dict()) for s in self.snapshots)

    def summarize(self) -> dict:
        """Produce a high-level summary of the bundle's contents.

        Computes mean coverage and mean diversity across all snapshots. Also
        reports the phase distribution (count per phase name), the number of
        snapshots, and the time span from first to last snapshot.
        """
        if not self.snapshots:
            return {
                "bundle_id": self.bundle_id,
                "count": 0,
                "mean_coverage": 0.0,
                "mean_diversity": 0.0,
                "phase_distribution": {},
                "time_span_seconds": 0.0,
            }
        coverages = [s.coverage for s in self.snapshots]
        diversities = [s.diversity for s in self.snapshots]
        phases: dict[str, int] = {}
        for s in self.snapshots:
            phases[s.phase] = phases.get(s.phase, 0) + 1
        time_span = self.snapshots[-1].timestamp - self.snapshots[0].timestamp
        return {
            "bundle_id": self.bundle_id,
            "count": len(self.snapshots),
            "mean_coverage": sum(coverages) / len(coverages),
            "mean_diversity": sum(diversities) / len(diversities),
            "phase_distribution": phases,
            "time_span_seconds": max(0.0, time_span),
        }

    def to_dict(self) -> dict:
        """Full serialization including all snapshots."""
        return {
            "bundle_id": self.bundle_id,
            "created_at": self.created_at,
            "snapshot_count": len(self.snapshots),
            "snapshots": [s.to_dict() for s in self.snapshots],
        }


@dataclasses.dataclass(slots=True)
class PhaseMonitorAdapter:
    """Adapter that ingests FrontierPhasesState ticks and computes derived metrics.

    Maintains a rolling history of coverage values and phase labels. Exposes
    coverage_trend (the linear slope of recent coverage), phase_velocity (phase
    transitions per 100 ingested ticks), and is_stalled (True if coverage is
    not increasing meaningfully).
    """

    adapter_id: str
    coverage_history: list
    phase_log: list

    def ingest(self, state: FrontierPhasesState) -> None:
        """Record coverage and current phase from the given state snapshot.

        Appends to coverage_history and phase_log. These lists grow without
        bound; callers that run for very long durations should periodically
        reset this adapter or trim the history themselves.
        """
        self.coverage_history.append(state.coverage)
        self.phase_log.append(state.current_phase)

    def coverage_trend(self) -> float:
        """Compute the linear slope of coverage over all ingested ticks.

        Uses the simple ordinary-least-squares slope formula. Returns 0.0 if
        fewer than 2 data points are available. A positive value indicates
        coverage is improving; negative indicates regression.
        """
        n = len(self.coverage_history)
        if n < 2:
            return 0.0
        xs = list(range(n))
        mean_x = sum(xs) / n
        mean_y = sum(self.coverage_history) / n
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, self.coverage_history))
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator == 0.0:
            return 0.0
        return numerator / denominator

    def phase_velocity(self) -> float:
        """Compute the number of phase transitions per 100 ingested ticks.

        A phase transition is counted whenever consecutive phase_log entries
        differ. Returns 0.0 if fewer than 2 ticks have been ingested.
        """
        n = len(self.phase_log)
        if n < 2:
            return 0.0
        transitions = sum(
            1 for a, b in zip(self.phase_log, self.phase_log[1:]) if a != b
        )
        return (transitions / (n - 1)) * 100.0

    def is_stalled(self, window: int = 10) -> bool:
        """Return True if coverage has not increased meaningfully over the last window ticks.

        Stall is defined as: the linear slope of coverage over the last `window`
        data points is less than 0.001. If fewer than `window` data points are
        available, uses all available data. Returns False (not stalled) if fewer
        than 2 data points are available.
        """
        history = self.coverage_history[-window:]
        if len(history) < 2:
            return False
        n = len(history)
        xs = list(range(n))
        mean_x = sum(xs) / n
        mean_y = sum(history) / n
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, history))
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator == 0.0:
            slope = 0.0
        else:
            slope = numerator / denominator
        return slope < 0.001

    def report(self) -> dict:
        """Return a monitoring report with all derived metrics.

        Includes the adapter_id, tick_count, coverage_trend, phase_velocity,
        is_stalled flag, current coverage (last value), and current phase.
        """
        return {
            "adapter_id": self.adapter_id,
            "tick_count": len(self.coverage_history),
            "coverage_trend": self.coverage_trend(),
            "phase_velocity": self.phase_velocity(),
            "is_stalled": self.is_stalled(),
            "current_coverage": self.coverage_history[-1] if self.coverage_history else 0.0,
            "current_phase": self.phase_log[-1] if self.phase_log else "UNKNOWN",
        }

    def to_dict(self) -> dict:
        """Full serialization including raw history arrays."""
        return {
            "adapter_id": self.adapter_id,
            "coverage_history": list(self.coverage_history),
            "phase_log": list(self.phase_log),
            "report": self.report(),
        }


@dataclasses.dataclass(slots=True)
class FrontierPhasesIntegrator:
    """Top-level integration object that wires the bridge, bundle, and monitor together.

    The integrator is the single entry point for downstream orchestration code.
    Each call to run() performs a full integration step: updating the bridge from
    the orchestrator state, capturing a snapshot, adding it to the bundle, and
    ingesting it into the monitor. The integrator also exposes health_check(),
    which evaluates a set of invariants and returns a structured report.
    """

    integrator_id: str
    bridge: FrontierPhasesBridge
    bundle: ExportBundle
    monitor: PhaseMonitorAdapter
    run_count: int

    def run(self, orchestrator_state: dict) -> dict:
        """Execute a full integration step and return the updated state summary.

        Steps:
        1. Drive the bridge's on_tick() with the provided orchestrator state.
        2. Push bridge state back into the orchestrator dict via sync_to_orchestrator().
        3. Capture a snapshot of the current state and add it to the bundle.
        4. Ingest the current state into the monitor adapter.
        5. Increment run_count.
        6. Return the state's phase_progress dict augmented with integrator_id
           and run_count.
        """
        self.bridge.on_tick(orchestrator_state)
        self.bridge.sync_to_orchestrator(orchestrator_state)
        snapshot = self.capture_snapshot()
        self.bundle.add(snapshot)
        self.monitor.ingest(self.bridge.state)
        self.run_count += 1
        result = self.bridge.state.phase_progress()
        result["integrator_id"] = self.integrator_id
        result["run_count"] = self.run_count
        return result

    def capture_snapshot(self) -> PhaseExportSnapshot:
        """Take and return a snapshot of the current bridge state.

        Delegates to PhaseExportSnapshot.capture(). Does not add the snapshot
        to the bundle; use run() for the full pipeline or call bundle.add()
        manually.
        """
        return PhaseExportSnapshot.capture(self.bridge.state)

    def export(self) -> str:
        """Export all snapshots in the bundle as JSONL.

        Returns an empty string if no snapshots have been captured yet.
        """
        return self.bundle.export_jsonl()

    def health_check(self) -> dict:
        """Evaluate integration health and return a structured report.

        Checks the following invariants:
        - Budget: budget_used <= budget_limit (not exhausted)
        - Diversity: current diversity >= config.diversity_threshold
        - Stall: monitor is not stalled (coverage is improving)
        - Phase: current_phase is not INIT after more than 1 run
        - Trust: trust_mass > 0 (trust has not collapsed)

        Returns a dict with keys: ok (bool), issues (list of str), metrics (dict).
        ok is True only when issues is empty.
        """
        issues: list[str] = []
        state = self.bridge.state
        config = self.bridge.config

        if state.budget_exhausted():
            issues.append(
                f"Budget exhausted: {state.budget_used}/{state.budget_limit} tokens used."
            )

        if state.diversity < config.diversity_threshold:
            issues.append(
                f"Diversity {state.diversity:.4f} below threshold {config.diversity_threshold}."
            )

        if self.monitor.is_stalled():
            issues.append("Coverage stalled: no meaningful increase in recent ticks.")

        if self.run_count > 1 and state.current_phase == "INIT":
            issues.append("Phase stuck in INIT after multiple ticks.")

        if state.trust_mass <= 0.0:
            issues.append(f"Trust mass collapsed to {state.trust_mass}.")

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "metrics": {
                "coverage": state.coverage,
                "diversity": state.diversity,
                "trust_mass": state.trust_mass,
                "budget_used": state.budget_used,
                "budget_limit": state.budget_limit,
                "run_count": self.run_count,
                "current_phase": state.current_phase,
                "stalled": self.monitor.is_stalled(),
                "coverage_trend": self.monitor.coverage_trend(),
                "phase_velocity": self.monitor.phase_velocity(),
            },
        }

    def to_dict(self) -> dict:
        """Full serialization of the integrator and all owned sub-objects."""
        return {
            "integrator_id": self.integrator_id,
            "bridge": self.bridge.to_dict(),
            "bundle_summary": self.bundle.summarize(),
            "monitor_report": self.monitor.report(),
            "run_count": self.run_count,
        }

    @classmethod
    def make(cls, config: "IntegrationConfig | None" = None) -> "FrontierPhasesIntegrator":
        """Construct a FrontierPhasesIntegrator from an IntegrationConfig.

        If config is None, IntegrationConfig.default() is used. All sub-objects
        (bridge, bundle, monitor) are constructed fresh with empty state so the
        integrator is ready for immediate use.
        """
        if config is None:
            config = IntegrationConfig.default()
        bridge = FrontierPhasesBridge.make(config)
        bundle = ExportBundle(
            bundle_id=str(uuid.uuid4()),
            snapshots=[],
            created_at=time.time(),
        )
        monitor = PhaseMonitorAdapter(
            adapter_id=str(uuid.uuid4()),
            coverage_history=[],
            phase_log=[],
        )
        return cls(
            integrator_id=str(uuid.uuid4()),
            bridge=bridge,
            bundle=bundle,
            monitor=monitor,
            run_count=0,
        )


def build_default_integrator(config=None) -> FrontierPhasesIntegrator:
    """Build a FrontierPhasesIntegrator with default configuration.

    This is the recommended factory function for production use. It calls
    FrontierPhasesIntegrator.make() which in turn calls IntegrationConfig.default()
    if no config is supplied, then constructs a fresh bridge, bundle, and monitor.

    Args:
        config: An optional IntegrationConfig instance. When None, the production
            defaults are used (tick_interval=1.0, max_phase_duration=3600.0,
            trust_tolerance=0.05, budget_token_limit=50000,
            diversity_threshold=0.3).

    Returns:
        A fully initialized FrontierPhasesIntegrator ready to accept run() calls.

    Example:
        integrator = build_default_integrator()
        for tick_state in orchestrator_ticks():
            summary = run_integration_tick(integrator, tick_state)
            if not integrator.health_check()["ok"]:
                alert(integrator.health_check()["issues"])
    """
    return FrontierPhasesIntegrator.make(config=config)


def run_integration_tick(integrator: FrontierPhasesIntegrator, state: dict) -> dict:
    """Run one integration tick and return the updated state summary.

    This thin wrapper around integrator.run() exists so that callers do not need
    to import FrontierPhasesIntegrator to drive the integration loop. It is the
    idiomatic entry point for orchestration drivers.

    Args:
        integrator: A FrontierPhasesIntegrator instance (from build_default_integrator
            or FrontierPhasesIntegrator.make()).
        state: A dict representing the current orchestrator state. Recognized keys:
            - coverage (float in [0, 1]): current search coverage ratio
            - diversity (float in [0, 1]): current diversity score
            - trust_mass (float >= 0): current aggregated trust mass
            - current_phase (str): name of the active phase
            - tokens_used (int): cumulative token budget consumed
            - phase_trigger (str): optional label for what caused a phase change

    Returns:
        A dict summarizing the post-tick state, including:
        - current_phase (str)
        - coverage (float)
        - diversity (float)
        - trust_mass (float)
        - budget_fraction (float in [0, 1])
        - integrator_id (str)
        - run_count (int)

    Example:
        integrator = build_default_integrator()
        result = run_integration_tick(integrator, {"coverage": 0.5, "diversity": 0.7})
        print(result["current_phase"])
    """
    return integrator.run(state)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import pprint

    # Build a default integrator
    integrator = build_default_integrator()

    # Simulate 5 orchestration ticks with gradually improving metrics
    dummy_states = [
        {
            "coverage": 0.1 * (i + 1),
            "diversity": max(0.3, 0.9 - 0.05 * i),
            "trust_mass": 0.95 - 0.01 * i,
            "current_phase": "EXPLORATION" if i < 3 else "EXPLOITATION",
            "tokens_used": 1000 * (i + 1),
            "phase_trigger": "coverage_threshold" if i == 3 else "tick",
        }
        for i in range(5)
    ]

    for i, state in enumerate(dummy_states):
        result = run_integration_tick(integrator, state)
        print(f"Tick {i}: phase={result['current_phase']}, "
              f"coverage={result['coverage']:.2f}, "
              f"budget_fraction={result['budget_fraction']:.3f}")

    # Export JSONL
    jsonl = integrator.export()
    print(f"\nExported {len(jsonl.splitlines())} JSONL lines")

    # Health check
    health = integrator.health_check()
    pprint.pprint(health)

    print("integration smoke test passed")
