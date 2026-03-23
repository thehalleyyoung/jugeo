"""Phase changes triggered by semantic signals: obstruction density, coverage ratio, trust mass. theory2.tex Ch47 §5. # copilot:"""

from __future__ import annotations

import math
import time
import uuid
import json
import random
from dataclasses import dataclass, field

DEFAULT_ALPHA = 0.3
TRUST_TOLERANCE = 0.05
MIN_SIGNAL_STABILITY = 0.6

__all__ = [
    "SemanticSignalVector", "SignalThresholdPolicy", "TriggerEvent",
    "TriggerEngine", "TrustPreservationChecker", "SignalSmoother",
    "PhaseChangeTriggersCoordinator", "PhaseChangeTriggersAnalyzer",
    "PhaseChangeTriggersWitness",
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


@dataclass(frozen=True, slots=True)
class SemanticSignalVector:
    """A vector of semantic signals sampled from the frontier at one instant.

    Each field represents a distinct measurable aspect of the frontier state.
    Together they describe the phase-readiness of the search process.

    Attributes:
        vector_id: Unique identifier for this signal snapshot.
        obstruction_density: Fraction of frontier capacity blocked by obstructions [0, 1].
        coverage_ratio: Fraction of total search space that has been visited [0, 1].
        trust_mass: Aggregate trust accumulated over explored nodes [0, 1].
        bandit_regret: Cumulative regret from bandit arm selection decisions [0, inf].
        diversity_score: Shannon-entropy-derived diversity of proof modes [0, 1].
        stall_count: Number of consecutive stalled iterations with no progress.
        timestamp: Unix time at which this vector was sampled.
    """

    vector_id: str
    obstruction_density: float
    coverage_ratio: float
    trust_mass: float
    bandit_regret: float
    diversity_score: float
    stall_count: int
    timestamp: float

    def norm(self) -> float:
        """Return the L2 norm of the numeric signal fields.

        Treats stall_count as a float for the norm computation.

        Returns:
            Non-negative scalar L2 magnitude of the signal vector.
        """
        components = [
            self.obstruction_density,
            self.coverage_ratio,
            self.trust_mass,
            self.bandit_regret,
            self.diversity_score,
            float(self.stall_count),
        ]
        return math.sqrt(sum(v * v for v in components))

    def dominates(self, other: "SemanticSignalVector") -> bool:
        """Return True if this vector is strictly better than *other* in all fields.

        "Better" means: higher coverage_ratio, higher trust_mass, higher
        diversity_score, lower obstruction_density, lower bandit_regret, and
        lower stall_count.

        Args:
            other: Another SemanticSignalVector to compare against.

        Returns:
            True only when every field satisfies the betterment condition.
        """
        return (
            self.coverage_ratio > other.coverage_ratio
            and self.trust_mass > other.trust_mass
            and self.diversity_score > other.diversity_score
            and self.obstruction_density < other.obstruction_density
            and self.bandit_regret < other.bandit_regret
            and self.stall_count < other.stall_count
        )

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns:
            Dict mapping field names to their values.
        """
        return {
            "vector_id": self.vector_id,
            "obstruction_density": self.obstruction_density,
            "coverage_ratio": self.coverage_ratio,
            "trust_mass": self.trust_mass,
            "bandit_regret": self.bandit_regret,
            "diversity_score": self.diversity_score,
            "stall_count": self.stall_count,
            "timestamp": self.timestamp,
        }

    @classmethod
    def zero(cls) -> "SemanticSignalVector":
        """Return a zero-initialised signal vector with a fresh UUID.

        Useful as a baseline or placeholder when no real data is available yet.

        Returns:
            SemanticSignalVector with all numeric fields at their minimal values.
        """
        return cls(
            vector_id=str(uuid.uuid4()),
            obstruction_density=0.0,
            coverage_ratio=0.0,
            trust_mass=0.0,
            bandit_regret=0.0,
            diversity_score=0.0,
            stall_count=0,
            timestamp=time.time(),
        )

    @classmethod
    def sample(cls, frontier_proxy: dict) -> "SemanticSignalVector":
        """Construct a SemanticSignalVector from a frontier proxy dictionary.

        The proxy is expected to hold keys corresponding to the field names.
        Any missing key falls back to a sensible default so the method is
        robust to partially-populated proxies.

        Args:
            frontier_proxy: Dict optionally containing signal values.

        Returns:
            A fully populated SemanticSignalVector.
        """
        return cls(
            vector_id=str(uuid.uuid4()),
            obstruction_density=float(frontier_proxy.get("obstruction_density", 0.0)),
            coverage_ratio=float(frontier_proxy.get("coverage_ratio", 0.0)),
            trust_mass=float(frontier_proxy.get("trust_mass", 0.0)),
            bandit_regret=float(frontier_proxy.get("bandit_regret", 0.0)),
            diversity_score=float(frontier_proxy.get("diversity_score", 0.0)),
            stall_count=int(frontier_proxy.get("stall_count", 0)),
            timestamp=float(frontier_proxy.get("timestamp", time.time())),
        )


@dataclass(frozen=True, slots=True)
class SignalThresholdPolicy:
    """Declarative policy that fires when signal conditions are jointly satisfied.

    Each entry in *thresholds* defines a required value for a named signal field.
    Each entry in *comparators* specifies how to compare: "gt", "lt", "gte", "lte".
    The policy triggers when ALL conditions are satisfied simultaneously.

    Attributes:
        policy_id: Unique identifier for this policy.
        thresholds: Mapping from field name to required threshold value.
        weights: Optional per-condition weights for the score computation.
        comparators: Mapping from field name to comparison operator string.
    """

    policy_id: str
    thresholds: dict
    weights: dict
    comparators: dict

    def evaluate(self, signal_vector: SemanticSignalVector) -> bool:
        """Return True if every threshold condition in this policy is met.

        Iterates over all entries in *thresholds*, retrieves the matching field
        value from the signal vector, applies the configured comparator, and
        ANDs all results together.

        Args:
            signal_vector: The current semantic signal measurement.

        Returns:
            True when all conditions fire simultaneously; False otherwise.
        """
        sv_dict = signal_vector.to_dict()
        for field_name, threshold in self.thresholds.items():
            actual = sv_dict.get(field_name, 0.0)
            comparator = self.comparators.get(field_name, "gte")
            if comparator == "gt":
                if not (actual > threshold):
                    return False
            elif comparator == "lt":
                if not (actual < threshold):
                    return False
            elif comparator == "gte":
                if not (actual >= threshold):
                    return False
            elif comparator == "lte":
                if not (actual <= threshold):
                    return False
        return True

    def score(self, signal_vector: SemanticSignalVector) -> float:
        """Return a satisfaction score in [0.0, 1.0] for this policy.

        For each condition the individual satisfaction is computed as the
        fractional distance the actual value has moved past the threshold
        (capped at 1.0).  Weighted average is returned.

        Args:
            signal_vector: The current semantic signal measurement.

        Returns:
            Float in [0, 1]; higher means more conditions are satisfied and by
            a larger margin.
        """
        sv_dict = signal_vector.to_dict()
        total_weight = 0.0
        weighted_sum = 0.0
        for field_name, threshold in self.thresholds.items():
            actual = sv_dict.get(field_name, 0.0)
            comparator = self.comparators.get(field_name, "gte")
            w = self.weights.get(field_name, 1.0)
            if threshold == 0.0:
                sat = 1.0 if actual >= 0.0 else 0.0
            elif comparator in ("gte", "gt"):
                sat = min(actual / (threshold if threshold != 0 else 1e-9), 1.0)
            else:
                sat = min(threshold / (actual if actual != 0 else 1e-9), 1.0)
            weighted_sum += w * sat
            total_weight += w
        if total_weight == 0.0:
            return 0.0
        return max(0.0, min(1.0, weighted_sum / total_weight))

    def satisfied_conditions(self, signal_vector: SemanticSignalVector) -> list:
        """Return a list of condition names whose thresholds are currently met.

        Args:
            signal_vector: The current semantic signal measurement.

        Returns:
            List of field name strings for which the condition passes.
        """
        sv_dict = signal_vector.to_dict()
        satisfied = []
        for field_name, threshold in self.thresholds.items():
            actual = sv_dict.get(field_name, 0.0)
            comparator = self.comparators.get(field_name, "gte")
            if comparator == "gt" and actual > threshold:
                satisfied.append(field_name)
            elif comparator == "lt" and actual < threshold:
                satisfied.append(field_name)
            elif comparator == "gte" and actual >= threshold:
                satisfied.append(field_name)
            elif comparator == "lte" and actual <= threshold:
                satisfied.append(field_name)
        return satisfied

    def to_dict(self) -> dict:
        """Serialise the policy to a JSON-compatible dictionary.

        Returns:
            Dict containing policy_id, thresholds, weights, and comparators.
        """
        return {
            "policy_id": self.policy_id,
            "thresholds": dict(self.thresholds),
            "weights": dict(self.weights),
            "comparators": dict(self.comparators),
        }

    @classmethod
    def default_exploration_to_exploitation(cls) -> "SignalThresholdPolicy":
        """Return the canonical exploration→exploitation transition policy.

        Fires when coverage_ratio ≥ 0.4 AND obstruction_density ≤ 0.6,
        indicating that enough of the space has been seen and congestion is
        manageable enough to switch to focused exploitation.

        Returns:
            SignalThresholdPolicy configured for EXPLORATION→EXPLOITATION.
        """
        return cls(
            policy_id="exploration_to_exploitation",
            thresholds={"coverage_ratio": 0.4, "obstruction_density": 0.6},
            weights={"coverage_ratio": 1.0, "obstruction_density": 1.0},
            comparators={"coverage_ratio": "gte", "obstruction_density": "lte"},
        )

    @classmethod
    def default_exploitation_to_convergence(cls) -> "SignalThresholdPolicy":
        """Return the canonical exploitation→convergence transition policy.

        Fires when coverage_ratio ≥ 0.85 AND trust_mass ≥ 0.7, indicating the
        search is nearly complete and the accumulated trust is sufficient to
        certify a convergence claim.

        Returns:
            SignalThresholdPolicy configured for EXPLOITATION→CONVERGENCE.
        """
        return cls(
            policy_id="exploitation_to_convergence",
            thresholds={"coverage_ratio": 0.85, "trust_mass": 0.7},
            weights={"coverage_ratio": 1.5, "trust_mass": 1.0},
            comparators={"coverage_ratio": "gte", "trust_mass": "gte"},
        )


@dataclass(frozen=True, slots=True)
class TriggerEvent:
    """An immutable record of a phase-change trigger that has been detected.

    Attributes:
        event_id: Unique identifier for this trigger event.
        signal_vector: The SemanticSignalVector that caused the trigger.
        policy_id: Identifier of the policy that fired.
        from_phase: Name of the phase the system is transitioning away from.
        to_phase: Name of the phase the system is transitioning into.
        confidence: Confidence in the trigger in [0, 1].
        timestamp: Unix time at which the trigger was detected.
        evidence: Supplementary evidence dict (e.g. satisfied conditions).
    """

    event_id: str
    signal_vector: SemanticSignalVector
    policy_id: str
    from_phase: str
    to_phase: str
    confidence: float
    timestamp: float
    evidence: dict

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns:
            Nested dict representation including the embedded signal_vector.
        """
        return {
            "event_id": self.event_id,
            "signal_vector": self.signal_vector.to_dict(),
            "policy_id": self.policy_id,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "evidence": dict(self.evidence),
        }

    def is_confident(self, threshold: float = 0.8) -> bool:
        """Return True if this trigger's confidence meets *threshold*.

        Args:
            threshold: Minimum confidence required; defaults to 0.8.

        Returns:
            True when self.confidence >= threshold.
        """
        return self.confidence >= threshold

    def summary(self) -> str:
        """Return a human-readable one-line summary of this trigger event.

        Returns:
            A string of the form
            "TriggerEvent(<id>) <from_phase> -> <to_phase> conf=<confidence>"
        """
        return (
            f"TriggerEvent({self.event_id[:8]}) "
            f"{self.from_phase} -> {self.to_phase} "
            f"conf={self.confidence:.3f} policy={self.policy_id}"
        )

    @classmethod
    def make(
        cls,
        signal: SemanticSignalVector,
        policy: SignalThresholdPolicy,
        from_phase: str,
        to_phase: str,
        confidence: float,
    ) -> "TriggerEvent":
        """Construct a TriggerEvent from the given components.

        Args:
            signal: The signal vector that triggered the policy.
            policy: The policy that fired.
            from_phase: Phase being left.
            to_phase: Phase being entered.
            confidence: Confidence score in [0, 1].

        Returns:
            A fully populated TriggerEvent with a fresh UUID.
        """
        satisfied = policy.satisfied_conditions(signal)
        return cls(
            event_id=str(uuid.uuid4()),
            signal_vector=signal,
            policy_id=policy.policy_id,
            from_phase=from_phase,
            to_phase=to_phase,
            confidence=confidence,
            timestamp=time.time(),
            evidence={"satisfied_conditions": satisfied, "score": policy.score(signal)},
        )


@dataclass(slots=True)
class TriggerEngine:
    """Evaluates registered policies against incoming signal vectors.

    Maintains a registry of (from_phase, to_phase) -> policy mappings and
    evaluates them in priority order each time a new signal arrives.

    Attributes:
        engine_id: Unique identifier for this engine instance.
        policies: Dict keyed by "from_phase->to_phase" strings.
        triggered_events: List of all TriggerEvent objects that have fired.
        evaluation_count: Total number of evaluate() calls made.
    """

    engine_id: str
    policies: dict
    triggered_events: list
    evaluation_count: int

    def register_policy(
        self, from_phase: str, to_phase: str, policy: SignalThresholdPolicy
    ) -> None:
        """Register a policy for a specific phase transition.

        Overwrites any previously registered policy for the same transition.

        Args:
            from_phase: The phase the system must currently be in.
            to_phase: The phase to transition into when the policy fires.
            policy: The SignalThresholdPolicy to apply.
        """
        key = f"{from_phase}->{to_phase}"
        self.policies[key] = (from_phase, to_phase, policy)

    def evaluate(
        self, signal_vector: SemanticSignalVector, current_phase: str
    ) -> "TriggerEvent | None":
        """Check all policies whose from_phase matches *current_phase*.

        Returns the first TriggerEvent that fires (policies sorted by key), or
        None if no policy fires for the current phase.

        Args:
            signal_vector: The current SemanticSignalVector.
            current_phase: The name of the phase the system is currently in.

        Returns:
            A TriggerEvent if any applicable policy fires; otherwise None.
        """
        self.evaluation_count += 1
        for key in sorted(self.policies.keys()):
            from_phase, to_phase, policy = self.policies[key]
            if from_phase != current_phase:
                continue
            if policy.evaluate(signal_vector):
                confidence = policy.score(signal_vector)
                event = TriggerEvent.make(
                    signal_vector, policy, from_phase, to_phase, confidence
                )
                return event
        return None

    def fire_trigger(self, event: "TriggerEvent") -> None:
        """Record a trigger event as having been officially fired.

        Args:
            event: The TriggerEvent to record.
        """
        self.triggered_events.append(event)

    def policy_for(
        self, from_phase: str, to_phase: str
    ) -> "SignalThresholdPolicy | None":
        """Look up the policy registered for a specific phase transition.

        Args:
            from_phase: The source phase.
            to_phase: The destination phase.

        Returns:
            The registered SignalThresholdPolicy or None if not found.
        """
        key = f"{from_phase}->{to_phase}"
        if key in self.policies:
            return self.policies[key][2]
        return None

    def to_dict(self) -> dict:
        """Serialise the engine state to a JSON-compatible dictionary.

        Returns:
            Dict with engine_id, policy count, triggered event count, and
            evaluation count.
        """
        return {
            "engine_id": self.engine_id,
            "policy_count": len(self.policies),
            "policy_keys": list(self.policies.keys()),
            "triggered_event_count": len(self.triggered_events),
            "evaluation_count": self.evaluation_count,
        }


@dataclass(slots=True)
class TrustPreservationChecker:
    """Verifies that trust mass is preserved across a phase transition.

    Records the trust level before and after a transition and determines
    whether the change is within acceptable tolerance.

    Attributes:
        checker_id: Unique identifier for this checker instance.
        trust_before: Trust mass recorded before the transition.
        trust_after: Trust mass recorded after the transition (or None if not yet taken).
        checks: List of (pre, post, preserved) tuples from past checks.
    """

    checker_id: str
    trust_before: float
    trust_after: float | None
    checks: list

    def record_pre_transition(self, trust_mass: float) -> None:
        """Record the trust mass level immediately before a transition.

        Args:
            trust_mass: The current trust_mass scalar.
        """
        self.trust_before = trust_mass
        self.trust_after = None

    def record_post_transition(self, trust_mass: float) -> None:
        """Record the trust mass level immediately after a transition.

        Args:
            trust_mass: The trust_mass scalar after the transition completes.
        """
        self.trust_after = trust_mass
        preserved = self.is_preserved()
        self.checks.append({
            "trust_before": self.trust_before,
            "trust_after": trust_mass,
            "preserved": preserved,
        })

    def is_preserved(self, tolerance: float = TRUST_TOLERANCE) -> bool:
        """Return True if trust mass loss is within *tolerance*.

        A decrease in trust is considered a violation; an increase is always
        acceptable.  If post-transition trust has not yet been recorded, returns
        True optimistically.

        Args:
            tolerance: Maximum permissible absolute decrease in trust mass.

        Returns:
            True when trust is preserved within the given tolerance.
        """
        if self.trust_after is None:
            return True
        return (self.trust_before - self.trust_after) <= tolerance

    def trust_delta(self) -> "float | None":
        """Return the signed change in trust mass (after - before).

        Returns:
            Float delta or None if post-transition trust not yet recorded.
        """
        if self.trust_after is None:
            return None
        return self.trust_after - self.trust_before

    def report(self) -> dict:
        """Return a human-readable report dict of preservation status.

        Returns:
            Dict with checker_id, trust_before, trust_after, is_preserved, delta.
        """
        return {
            "checker_id": self.checker_id,
            "trust_before": self.trust_before,
            "trust_after": self.trust_after,
            "is_preserved": self.is_preserved(),
            "trust_delta": self.trust_delta(),
            "check_count": len(self.checks),
        }

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        Returns:
            Full serialisation including all historical check records.
        """
        return {
            "checker_id": self.checker_id,
            "trust_before": self.trust_before,
            "trust_after": self.trust_after,
            "checks": list(self.checks),
        }


@dataclass(slots=True)
class SignalSmoother:
    """Applies exponential moving average (EMA) to incoming signal vectors.

    Smoothing reduces the impact of transient noise on trigger evaluation.

    Attributes:
        smoother_id: Unique identifier for this smoother instance.
        alpha: EMA smoothing factor in (0, 1]; higher = more reactive.
        smoothed: Dict mapping field names to their current smoothed values.
        raw_history: List of raw signal vector dicts for diagnostic use.
    """

    smoother_id: str
    alpha: float
    smoothed: dict
    raw_history: list

    # Fields to smooth (excludes non-numeric/id/timestamp)
    _NUMERIC_FIELDS = (
        "obstruction_density",
        "coverage_ratio",
        "trust_mass",
        "bandit_regret",
        "diversity_score",
        "stall_count",
    )

    def update(self, signal_vector: SemanticSignalVector) -> None:
        """Apply EMA update to each numeric field of the signal vector.

        For each field f: smoothed[f] = alpha * raw[f] + (1 - alpha) * smoothed[f].
        If no smoothed value yet exists for a field it is initialised to the raw value.

        Args:
            signal_vector: The new raw SemanticSignalVector to incorporate.
        """
        raw = signal_vector.to_dict()
        self.raw_history.append(raw)
        for f in self._NUMERIC_FIELDS:
            raw_val = float(raw.get(f, 0.0))
            if f not in self.smoothed:
                self.smoothed[f] = raw_val
            else:
                self.smoothed[f] = (
                    self.alpha * raw_val + (1.0 - self.alpha) * self.smoothed[f]
                )

    def get_smoothed(self) -> dict:
        """Return a copy of the current smoothed signal values.

        Returns:
            Dict mapping field names to smoothed float values.
        """
        return dict(self.smoothed)

    def deviation(self, signal_vector: SemanticSignalVector) -> float:
        """Return the mean absolute deviation of the raw vector from smoothed.

        Args:
            signal_vector: A raw SemanticSignalVector to measure deviation for.

        Returns:
            Non-negative mean absolute deviation scalar.
        """
        if not self.smoothed:
            return 0.0
        raw = signal_vector.to_dict()
        deviations = []
        for f in self._NUMERIC_FIELDS:
            raw_val = float(raw.get(f, 0.0))
            sm_val = self.smoothed.get(f, raw_val)
            deviations.append(abs(raw_val - sm_val))
        return sum(deviations) / len(deviations) if deviations else 0.0

    def is_noise_dominated(self) -> bool:
        """Return True if recent deviation is very high relative to signal magnitude.

        Computes the mean smoothed magnitude and compares it to mean deviation
        from recent history.  If deviation > 0.5 * mean magnitude the signal
        is considered noise-dominated.

        Returns:
            True when signal noise dominates the smoothed signal magnitude.
        """
        if len(self.raw_history) < 3:
            return False
        mean_mag = sum(self.smoothed.get(f, 0.0) for f in self._NUMERIC_FIELDS) / len(
            self._NUMERIC_FIELDS
        )
        recent = self.raw_history[-5:]
        deviations = []
        for snap in recent:
            for f in self._NUMERIC_FIELDS:
                deviations.append(abs(float(snap.get(f, 0.0)) - self.smoothed.get(f, 0.0)))
        mean_dev = sum(deviations) / len(deviations) if deviations else 0.0
        return mean_dev > 0.5 * max(mean_mag, 1e-9)

    def to_dict(self) -> dict:
        """Serialise the smoother state to a JSON-compatible dictionary.

        Returns:
            Dict with smoother_id, alpha, smoothed values, and history length.
        """
        return {
            "smoother_id": self.smoother_id,
            "alpha": self.alpha,
            "smoothed": dict(self.smoothed),
            "raw_history_length": len(self.raw_history),
        }


@dataclass(slots=True)
class PhaseChangeTriggersCoordinator:
    """Top-level coordinator that wires together smoother, engine, and checker.

    Receives raw signal observations, smooths them, evaluates trigger policies,
    and manages the commit of a detected phase transition.

    Attributes:
        coordinator_id: Unique identifier.
        trigger_engine: The TriggerEngine holding phase-transition policies.
        trust_checker: TrustPreservationChecker for trust mass safety.
        signal_smoother: SignalSmoother for noise reduction.
        current_phase: Name of the currently active phase.
        fired_triggers: List of committed TriggerEvent objects.
        iteration_count: Number of observe() calls so far.
    """

    coordinator_id: str
    trigger_engine: TriggerEngine
    trust_checker: TrustPreservationChecker
    signal_smoother: SignalSmoother
    current_phase: str
    fired_triggers: list
    iteration_count: int

    def observe(self, signal_vector: SemanticSignalVector) -> None:
        """Ingest a new raw signal vector and update the smoother.

        Also updates the trust checker with the latest trust_mass reading.

        Args:
            signal_vector: The newly sampled SemanticSignalVector.
        """
        self.signal_smoother.update(signal_vector)
        self.trust_checker.record_pre_transition(signal_vector.trust_mass)
        self.iteration_count += 1

    def check_triggers(self) -> "TriggerEvent | None":
        """Evaluate all applicable policies against the current smoothed signals.

        Constructs a synthetic SemanticSignalVector from smoothed values and
        passes it to the TriggerEngine.  Returns the TriggerEvent if one fires.

        Returns:
            TriggerEvent if a policy fires; None otherwise.
        """
        smoothed = self.signal_smoother.get_smoothed()
        if not smoothed:
            return None
        synthetic = SemanticSignalVector.sample(
            {**smoothed, "timestamp": time.time()}
        )
        return self.trigger_engine.evaluate(synthetic, self.current_phase)

    def commit_transition(self, event: TriggerEvent) -> None:
        """Officially commit a detected phase transition.

        Records the trigger, updates current_phase, fires it in the engine,
        and records the post-transition trust mass.

        Args:
            event: The TriggerEvent to commit.
        """
        self.trigger_engine.fire_trigger(event)
        self.fired_triggers.append(event)
        self.trust_checker.record_post_transition(event.signal_vector.trust_mass)
        self.current_phase = event.to_phase

    def is_phase_change_pending(self) -> bool:
        """Return True if a trigger was detected but not yet committed.

        In this implementation a trigger is detected on-demand so this returns
        True when check_triggers() would fire, without actually firing it.

        Returns:
            True if a trigger would currently fire.
        """
        event = self.check_triggers()
        return event is not None

    def current_signals(self) -> dict:
        """Return the current smoothed signal values.

        Returns:
            Dict of field name -> smoothed value.
        """
        return self.signal_smoother.get_smoothed()

    def summarize(self) -> dict:
        """Return a high-level summary dict of coordinator state.

        Returns:
            Dict with coordinator metadata and phase transition counts.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "current_phase": self.current_phase,
            "iteration_count": self.iteration_count,
            "fired_trigger_count": len(self.fired_triggers),
            "trust_preserved": self.trust_checker.is_preserved(),
            "smoothed_signals": self.signal_smoother.get_smoothed(),
        }

    def to_dict(self) -> dict:
        """Serialise the full coordinator state to a JSON-compatible dictionary.

        Returns:
            Deep nested dict representation.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "trigger_engine": self.trigger_engine.to_dict(),
            "trust_checker": self.trust_checker.to_dict(),
            "signal_smoother": self.signal_smoother.to_dict(),
            "current_phase": self.current_phase,
            "fired_trigger_count": len(self.fired_triggers),
            "iteration_count": self.iteration_count,
        }

    @classmethod
    def make(cls) -> "PhaseChangeTriggersCoordinator":
        """Create a fully wired default PhaseChangeTriggersCoordinator.

        Registers the two canonical policies (exploration→exploitation and
        exploitation→convergence) and sets the initial phase to EXPLORATION.

        Returns:
            Ready-to-use PhaseChangeTriggersCoordinator.
        """
        engine = TriggerEngine(
            engine_id=str(uuid.uuid4()),
            policies={},
            triggered_events=[],
            evaluation_count=0,
        )
        engine.register_policy(
            "EXPLORATION",
            "EXPLOITATION",
            SignalThresholdPolicy.default_exploration_to_exploitation(),
        )
        engine.register_policy(
            "EXPLOITATION",
            "CONVERGENCE",
            SignalThresholdPolicy.default_exploitation_to_convergence(),
        )
        trust_checker = TrustPreservationChecker(
            checker_id=str(uuid.uuid4()),
            trust_before=0.0,
            trust_after=None,
            checks=[],
        )
        smoother = SignalSmoother(
            smoother_id=str(uuid.uuid4()),
            alpha=DEFAULT_ALPHA,
            smoothed={},
            raw_history=[],
        )
        return cls(
            coordinator_id=str(uuid.uuid4()),
            trigger_engine=engine,
            trust_checker=trust_checker,
            signal_smoother=smoother,
            current_phase="EXPLORATION",
            fired_triggers=[],
            iteration_count=0,
        )


@dataclass(slots=True)
class PhaseChangeTriggersAnalyzer:
    """Analyses the historical record of signals and triggers for quality metrics.

    Provides trigger frequency, false-positive estimates, and signal stability
    measures to help diagnose whether the trigger configuration is well-tuned.

    Attributes:
        analyzer_id: Unique identifier for this analyzer instance.
        signal_snapshots: List of SemanticSignalVector dicts (from to_dict()).
        trigger_log: List of TriggerEvent dicts (from to_dict()).
    """

    analyzer_id: str
    signal_snapshots: list
    trigger_log: list

    def record_signal(self, signal: SemanticSignalVector) -> None:
        """Append a signal snapshot to the history.

        Args:
            signal: The SemanticSignalVector to record.
        """
        self.signal_snapshots.append(signal.to_dict())

    def record_trigger(self, event: TriggerEvent) -> None:
        """Append a trigger event to the log.

        Args:
            event: The TriggerEvent to record.
        """
        self.trigger_log.append(event.to_dict())

    def trigger_frequency(self) -> float:
        """Return triggers per 100 observations.

        Returns:
            Float representing trigger rate; 0.0 if no observations yet.
        """
        if not self.signal_snapshots:
            return 0.0
        return 100.0 * len(self.trigger_log) / len(self.signal_snapshots)

    def false_positive_estimate(self) -> float:
        """Estimate the fraction of triggers that were reversed (false positives).

        A reversal is detected when two consecutive triggers go A→B then B→A.
        This is a proxy for instability and over-triggering.

        Returns:
            Float in [0, 1] representing estimated false-positive rate.
        """
        if len(self.trigger_log) < 2:
            return 0.0
        reversals = 0
        for i in range(1, len(self.trigger_log)):
            prev = self.trigger_log[i - 1]
            curr = self.trigger_log[i]
            if (
                prev["to_phase"] == curr["from_phase"]
                and prev["from_phase"] == curr["to_phase"]
            ):
                reversals += 1
        return reversals / (len(self.trigger_log) - 1)

    def signal_stability(self) -> float:
        """Return a stability score in [0, 1] for the observed signal sequence.

        Computes the mean coefficient of variation across numeric signal fields
        over all snapshots.  Low CV = stable signals → high stability score.

        Returns:
            Float in [0, 1]; higher is more stable.
        """
        if len(self.signal_snapshots) < 2:
            return 1.0
        fields = [
            "obstruction_density", "coverage_ratio", "trust_mass",
            "bandit_regret", "diversity_score",
        ]
        cvs = []
        for f in fields:
            vals = [float(snap.get(f, 0.0)) for snap in self.signal_snapshots]
            mean = sum(vals) / len(vals)
            if mean == 0.0:
                continue
            std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
            cvs.append(std / mean)
        if not cvs:
            return 1.0
        mean_cv = sum(cvs) / len(cvs)
        return max(0.0, min(1.0, 1.0 / (1.0 + mean_cv)))

    def report(self) -> dict:
        """Return a structured analysis report.

        Returns:
            Dict with all computed quality metrics.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "observation_count": len(self.signal_snapshots),
            "trigger_count": len(self.trigger_log),
            "trigger_frequency_per_100": self.trigger_frequency(),
            "false_positive_estimate": self.false_positive_estimate(),
            "signal_stability": self.signal_stability(),
        }

    def to_dict(self) -> dict:
        """Serialise the analyzer state to a JSON-compatible dictionary.

        Returns:
            Dict with analyzer_id and computed metrics (logs omitted for size).
        """
        return {
            "analyzer_id": self.analyzer_id,
            "signal_snapshot_count": len(self.signal_snapshots),
            "trigger_log_count": len(self.trigger_log),
        }


@dataclass(frozen=True, slots=True)
class PhaseChangeTriggersWitness:
    """Immutable witness certificate attesting to the health of phase triggering.

    Issued by external examination of the coordinator and analyzer after a
    run completes.  Used for formal verification of trigger correctness.

    Attributes:
        witness_id: Unique identifier for this certificate.
        coordinator_id: ID of the coordinator being witnessed.
        trigger_count: Total number of committed phase transitions.
        trust_preserved: Whether trust mass was preserved throughout.
        current_phase: The phase the system was in at witness time.
        iteration_count: Total observations made.
        timestamp: Unix time of witness issuance.
        evidence: Supporting data dict from analyzer report.
    """

    witness_id: str
    coordinator_id: str
    trigger_count: int
    trust_preserved: bool
    current_phase: str
    iteration_count: int
    timestamp: float
    evidence: dict

    def to_dict(self) -> dict:
        """Serialise the witness to a JSON-compatible dictionary.

        Returns:
            Full dict including all fields and nested evidence.
        """
        return {
            "witness_id": self.witness_id,
            "coordinator_id": self.coordinator_id,
            "trigger_count": self.trigger_count,
            "trust_preserved": self.trust_preserved,
            "current_phase": self.current_phase,
            "iteration_count": self.iteration_count,
            "timestamp": self.timestamp,
            "evidence": dict(self.evidence),
        }

    def is_sound(self) -> bool:
        """Return True if the witness attests to a sound trigger history.

        Soundness requires that trust was preserved and no negative trigger
        counts were recorded (sanity check).

        Returns:
            True when the witness certifies a sound phase-change history.
        """
        return self.trust_preserved and self.trigger_count >= 0

    def certify_text(self) -> str:
        """Return a human-readable certification statement.

        Returns:
            Multi-line string suitable for display in a verification report.
        """
        status = "SOUND" if self.is_sound() else "UNSOUND"
        lines = [
            f"PhaseChangeTriggersWitness Certificate [{status}]",
            f"  witness_id       : {self.witness_id}",
            f"  coordinator_id   : {self.coordinator_id}",
            f"  trigger_count    : {self.trigger_count}",
            f"  trust_preserved  : {self.trust_preserved}",
            f"  current_phase    : {self.current_phase}",
            f"  iteration_count  : {self.iteration_count}",
            f"  signal_stability : {self.evidence.get('signal_stability', 'n/a')}",
            f"  issued at        : {self.timestamp:.3f}",
        ]
        return "\n".join(lines)

    @classmethod
    def issue(
        cls,
        coordinator: PhaseChangeTriggersCoordinator,
        analyzer: "PhaseChangeTriggersAnalyzer",
    ) -> "PhaseChangeTriggersWitness":
        """Issue a witness certificate from the given coordinator and analyzer.

        Args:
            coordinator: The PhaseChangeTriggersCoordinator to certify.
            analyzer: The PhaseChangeTriggersAnalyzer with logged evidence.

        Returns:
            An immutable PhaseChangeTriggersWitness.
        """
        report = analyzer.report()
        return cls(
            witness_id=str(uuid.uuid4()),
            coordinator_id=coordinator.coordinator_id,
            trigger_count=len(coordinator.fired_triggers),
            trust_preserved=coordinator.trust_checker.is_preserved(),
            current_phase=coordinator.current_phase,
            iteration_count=coordinator.iteration_count,
            timestamp=time.time(),
            evidence=report,
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    coordinator = PhaseChangeTriggersCoordinator.make()
    analyzer = PhaseChangeTriggersAnalyzer(
        analyzer_id=str(uuid.uuid4()),
        signal_snapshots=[],
        trigger_log=[],
    )

    coverage_ratios = [0.1, 0.3, 0.5, 0.7, 0.9]
    for coverage in coverage_ratios:
        sv = SemanticSignalVector(
            vector_id=str(uuid.uuid4()),
            obstruction_density=max(0.0, 0.8 - coverage),
            coverage_ratio=coverage,
            trust_mass=min(1.0, coverage * 0.9),
            bandit_regret=max(0.0, 1.0 - coverage),
            diversity_score=min(1.0, coverage * 1.1),
            stall_count=0,
            timestamp=time.time(),
        )
        coordinator.observe(sv)
        analyzer.record_signal(sv)
        event = coordinator.check_triggers()
        if event is not None:
            print(f"[TRIGGER] {event.summary()}")
            coordinator.commit_transition(event)
            analyzer.record_trigger(event)

    witness = PhaseChangeTriggersWitness.issue(coordinator, analyzer)
    print(witness.certify_text())
    print(json.dumps(witness.to_dict(), indent=2))
    print("s05 smoke test passed")
