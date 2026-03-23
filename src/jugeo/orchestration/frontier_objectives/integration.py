"""
Integration layer connecting frontier objectives to the main orchestration
system (Ch47).

This module provides the glue between:
  - FrontierObjective scoring and budget management
  - The main Orchestrator control loop
  - Fleet / negotiation sub-systems (via duck-typed interfaces)
  - Trust-level-aware weight adaptation
  - Descent-engine closure-gain feedback

The central entry point is :class:`IntegrationPipeline` which wires together
all sub-components and exposes a single ``run()`` method for each orchestration
tick.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Upstream guards
# ---------------------------------------------------------------------------
try:
    from jugeo.orchestration.frontier import (
        FrontierItem,
        FrontierState,
        FrontierNode,
        Frontier,
        FrontierSearch,
        FrontierScorer,
        PhaseTransition,
        BackpressureController,
        FrontierDiversity,
        FrontierBudget,
        FrontierHistory,
        FrontierDiagnostics,
        PhaseKind,
        TransitionTrigger,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.controller import (
        OrchestratorState,
        SemanticMove,
        MoveKind,
        ControlLaw,
        GreedyControl,
        LookaheadControl,
        BalancedControl,
        AdaptiveControl,
        Orchestrator,
        ConvergenceMonitor,
        MoveHistory,
        OrchestratorConfiguration,
        MoveGenerator,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.fleet import (
        FleetMember,
        FleetBid,
        Fleet,
        BidEvaluator,
        FleetScheduler,
        CompetitiveSearch,
        FleetCalibration,
        ChallengeRecord,
        FleetHistory,
        FleetDiagnostics,
        FleetState,
        BidOutcome,
        ChallengeOutcome,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.negotiation import (
        NegotiationSession,
        TreatyProposal,
        FrictionPattern,
        CompromiseStrategy,
        NegotiationMemory,
        DeadlockDetector,
        Negotiator,
        NegotiationHistory,
        TreatyArchive,
        NegotiationEventBus,
        NegotiationDiagnostics,
        NegotiationPosition,
        NegotiationRound,
        SessionState,
        DeadlockKind,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import (
        TrustLevel,
        TrustAlgebra,
        TrustComposition,
        TrustAttenuation,
        TrustPromotion,
        TrustCeiling,
        TrustPolicy,
        TrustAuditLog,
        TrustTier,
        TrustProfile,
        join_trust_profiles,
    )
except Exception:
    pass

try:
    from jugeo.geometry.descent import (
        LocalSection,
        OverlapCondition,
        GluingData,
        DescentEngine,
        DescentResult,
        GlobalSection,
        DescentObstruction,
        DescentLog,
        OverlapStatus,
        DescentStrategy,
        DescentConfiguration,
        CohomologyClass,
        RepairFrontier,
        Obstruction,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_objectives.models import (
        FrontierObjective,
        ObjectiveKind,
        ClosureGainEstimate,
        DiversityMetric,
        ScoringState,
        BudgetPolicy,
        PhaseKind as ModelPhaseKind,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_objectives.algorithms import (
        score_frontier_node,
        detect_phase_transition,
        compute_diversity_metric,
        allocate_budget_pareto,
        frontier_beam_search,
        exponential_moving_average,
    )
except Exception:
    # Minimal fallback implementations so the module remains functional even
    # when algorithms.py is not yet importable (e.g. during testing scaffolding)
    def score_frontier_node(node: Any, objectives: Any, state: Any) -> float:  # type: ignore[misc]
        return float(getattr(node, "score", 0.5))

    def detect_phase_transition(history: Any, window: int = 20) -> Any:  # type: ignore[misc]
        return type("R", (), {"detected": False, "from_phase": "unknown", "to_phase": "unknown", "confidence": 0.0, "trigger": "fallback", "evidence": {}})()

    def compute_diversity_metric(nodes: Any, clustering_fn: Any = None) -> Any:  # type: ignore[misc]
        return type("D", (), {"entropy": 0.0, "coverage_ratio": 0.0, "novelty_score": 0.0, "cluster_count": 0})()

    def allocate_budget_pareto(objectives: Any, total: float) -> Any:  # type: ignore[misc]
        return type("B", (), {"allocations": {}, "pareto_efficient": True, "total": 0.0, "objectives_covered": 0})()

    def frontier_beam_search(frontier: Any, objectives: Any, beam_width: int = 5, budget: float = 100.0) -> Any:  # type: ignore[misc]
        return type("BS", (), {"selected_nodes": [], "scores": [], "iterations": 0, "budget_spent": 0.0})()

    def exponential_moving_average(series: list[float], alpha: float = 0.3) -> float:  # type: ignore[misc]
        return series[-1] if series else 0.0


# ---------------------------------------------------------------------------
# IntegrationConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrationConfig:
    """Immutable configuration for the integration layer.

    Attributes
    ----------
    config_id:
        Unique identifier for this configuration snapshot.
    beam_width:
        Beam width for frontier beam-search.
    diversity_threshold:
        Minimum acceptable diversity score before triggering repair.
    phase_window:
        Number of recent scores used for phase-transition detection.
    budget_channels:
        Named budget channels to create during allocation.
    trust_weight_factor:
        Global scale factor applied to trust-derived weight adjustments.
    """

    config_id: str
    beam_width: int = 5
    diversity_threshold: float = 0.4
    phase_window: int = 20
    budget_channels: tuple[str, ...] = ("exploration", "exploitation", "repair")
    trust_weight_factor: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "config_id": self.config_id,
            "beam_width": self.beam_width,
            "diversity_threshold": self.diversity_threshold,
            "phase_window": self.phase_window,
            "budget_channels": list(self.budget_channels),
            "trust_weight_factor": self.trust_weight_factor,
        }

    @classmethod
    def default(cls) -> "IntegrationConfig":
        """Return a default :class:`IntegrationConfig`."""
        return cls(config_id=str(uuid4()))


# ---------------------------------------------------------------------------
# IntegrationEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrationEvent:
    """An immutable event emitted by integration components.

    Attributes
    ----------
    event_id:
        Unique event identifier.
    event_type:
        String label describing the kind of event (e.g. ``phase_transition``).
    payload:
        Arbitrary structured data attached to the event.
    timestamp:
        Unix epoch time at event creation.
    """

    event_id: str
    event_type: str
    payload: dict
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def make(cls, event_type: str, payload: dict) -> "IntegrationEvent":
        """Construct a new :class:`IntegrationEvent` with a generated ID and current timestamp."""
        return cls(
            event_id=str(uuid4()),
            event_type=event_type,
            payload=payload,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# FrontierObjectivesOrchestrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrontierObjectivesOrchestrator:
    """Integrates frontier objectives with the main orchestrator.

    Each call to :meth:`step` performs one orchestration tick:
    1. Scores the current objective set against the provided state.
    2. Checks remaining budget and triggers allocation if depleted.
    3. Emits lifecycle events.

    Attributes
    ----------
    config:
        Integration configuration.
    objective_set:
        Container or list of :class:`FrontierObjective` instances.
    budget_allocator:
        Object providing budget figures (must expose ``total_budget`` or
        ``remaining`` attributes); may be ``None``.
    scoring_history:
        Accumulating list of per-step scoring summaries.
    events:
        Ordered list of emitted :class:`IntegrationEvent` objects.
    """

    config: IntegrationConfig
    objective_set: Any
    budget_allocator: Any
    scoring_history: list[dict] = field(default_factory=list)
    events: list[IntegrationEvent] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def step(self, state: Any) -> dict[str, Any]:
        """Run one orchestration step.

        Parameters
        ----------
        state:
            Current orchestrator state forwarded to objective scorers.

        Returns
        -------
        dict[str, Any]
            Step summary containing ``scores``, ``budget``, ``events``, and
            ``timestamp``.
        """
        objectives = self._extract_objectives()
        scores: dict[str, float] = {}

        for obj in objectives:
            oid = str(getattr(obj, "objective_id", uuid4()))
            score = score_frontier_node(
                node=state,
                objectives=[obj],
                state=state,
            )
            scores[oid] = score

        # Budget check
        budget_info = self._check_budget()

        # Phase check from scoring history
        score_series = [
            entry.get("mean_score", 0.0) for entry in self.scoring_history[-50:]
        ]
        phase_info: dict[str, Any] = {}
        if len(score_series) >= 4:
            result = detect_phase_transition(
                score_series, window=self.config.phase_window
            )
            phase_info = result.to_dict() if hasattr(result, "to_dict") else {}
            if getattr(result, "detected", False):
                self.emit(
                    IntegrationEvent.make(
                        "phase_transition",
                        {
                            "from": getattr(result, "from_phase", "unknown"),
                            "to": getattr(result, "to_phase", "unknown"),
                        },
                    )
                )

        summary: dict[str, Any] = {
            "scores": scores,
            "mean_score": sum(scores.values()) / len(scores) if scores else 0.0,
            "budget": budget_info,
            "phase": phase_info,
            "events_emitted": len(self.events),
            "timestamp": time.time(),
        }
        self.scoring_history.append(
            {"mean_score": summary["mean_score"], "timestamp": summary["timestamp"]}
        )
        return summary

    def add_objective(self, obj: Any) -> None:
        """Add a new objective to the objective set.

        Parameters
        ----------
        obj:
            Objective to add; appended to a list or delegated to the set's
            ``add`` method.
        """
        if hasattr(self.objective_set, "add"):
            self.objective_set.add(obj)
        elif isinstance(self.objective_set, list):
            self.objective_set.append(obj)
        else:
            # Wrap in a list as a last resort
            self.objective_set = [obj]

    def handle_phase_transition(
        self, from_phase: str, to_phase: str
    ) -> IntegrationEvent:
        """Create and emit a phase-transition event.

        Parameters
        ----------
        from_phase:
            Label of the phase being exited.
        to_phase:
            Label of the phase being entered.

        Returns
        -------
        IntegrationEvent
            The emitted event.
        """
        evt = IntegrationEvent.make(
            "phase_transition",
            {"from_phase": from_phase, "to_phase": to_phase},
        )
        self.emit(evt)
        return evt

    def emit(self, event: IntegrationEvent) -> None:
        """Append *event* to the internal event log.

        Parameters
        ----------
        event:
            Event to record.
        """
        self.events.append(event)

    def recent_events(self, n: int = 10) -> list[IntegrationEvent]:
        """Return the *n* most recent events.

        Parameters
        ----------
        n:
            Maximum number of events to return.
        """
        return self.events[-n:]

    def summary(self) -> dict[str, Any]:
        """Return a high-level summary of orchestrator state."""
        objectives = self._extract_objectives()
        return {
            "config": self.config.to_dict(),
            "objective_count": len(objectives),
            "history_length": len(self.scoring_history),
            "event_count": len(self.events),
            "recent_mean_score": (
                self.scoring_history[-1].get("mean_score", 0.0)
                if self.scoring_history
                else 0.0
            ),
        }

    @classmethod
    def make(
        cls, config: "IntegrationConfig | None" = None
    ) -> "FrontierObjectivesOrchestrator":
        """Construct a default :class:`FrontierObjectivesOrchestrator`.

        Parameters
        ----------
        config:
            Optional configuration; a default one is created if ``None``.
        """
        return cls(
            config=config or IntegrationConfig.default(),
            objective_set=[],
            budget_allocator=None,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_objectives(self) -> list[Any]:
        """Extract a flat list of objectives from :attr:`objective_set`."""
        if isinstance(self.objective_set, list):
            return self.objective_set
        if hasattr(self.objective_set, "objectives"):
            return list(getattr(self.objective_set, "objectives", []))
        try:
            return list(self.objective_set)
        except Exception:
            return []

    def _check_budget(self) -> dict[str, Any]:
        """Return budget-status information from the allocator (if any)."""
        if self.budget_allocator is None:
            return {"status": "no_allocator"}
        remaining = float(
            getattr(self.budget_allocator, "remaining", None)
            or getattr(self.budget_allocator, "total_budget", 0.0)
        )
        return {"remaining": remaining, "depleted": remaining <= 0.0}


# ---------------------------------------------------------------------------
# ObjectiveFrontierBridge
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ObjectiveFrontierBridge:
    """Bridges objective scoring to Frontier node management.

    Attributes
    ----------
    bridge_id:
        Unique identifier.
    objective_scorer:
        Callable ``(node, objectives, state) -> float`` or ``None``.
    diversity_enforcer:
        Object that checks diversity constraints or ``None``.
    cost_estimator:
        Callable ``(node) -> float`` or ``None``.
    _frontier_ref:
        Weak reference to the attached frontier; set via :meth:`attach_frontier`.
    """

    bridge_id: str
    objective_scorer: Any
    diversity_enforcer: Any
    cost_estimator: Any
    _frontier_ref: Any = None

    def attach_frontier(self, frontier: Any) -> None:
        """Attach a frontier object for subsequent operations.

        Parameters
        ----------
        frontier:
            The frontier to attach.
        """
        self._frontier_ref = frontier

    def score_node(self, node: Any) -> float:
        """Score a single node using the configured objective scorer.

        Parameters
        ----------
        node:
            Frontier node to score.

        Returns
        -------
        float
            Score in [0, 1].
        """
        if callable(self.objective_scorer):
            try:
                return float(self.objective_scorer(node, [], None))
            except Exception:
                pass
        return float(getattr(node, "score", 0.5))

    def filter_nodes(self, nodes: list[Any]) -> list[Any]:
        """Remove nodes that fail minimum objective thresholds.

        A node is kept when its score >= 0.1 (a low bar to avoid over-filtering
        when objectives are not fully configured).

        Parameters
        ----------
        nodes:
            Candidate nodes.

        Returns
        -------
        list[Any]
            Filtered list.
        """
        _MIN_SCORE = 0.1
        return [n for n in nodes if self.score_node(n) >= _MIN_SCORE]

    def rank_nodes(self, nodes: list[Any]) -> list[tuple[Any, float]]:
        """Return nodes ranked by descending score.

        Parameters
        ----------
        nodes:
            Nodes to rank.

        Returns
        -------
        list[tuple[Any, float]]
            Pairs of ``(node, score)`` in descending order.
        """
        scored = [(n, self.score_node(n)) for n in nodes]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    def should_prune(self, node: Any, threshold: float = 0.1) -> bool:
        """Decide whether a node should be pruned from the frontier.

        Parameters
        ----------
        node:
            Node to evaluate.
        threshold:
            Score below which the node is pruned.

        Returns
        -------
        bool
            ``True`` if the node should be removed.
        """
        return self.score_node(node) < threshold

    def to_dict(self) -> dict[str, Any]:
        """Serialise bridge metadata."""
        return {
            "bridge_id": self.bridge_id,
            "has_frontier": self._frontier_ref is not None,
            "has_scorer": self.objective_scorer is not None,
            "has_diversity_enforcer": self.diversity_enforcer is not None,
            "has_cost_estimator": self.cost_estimator is not None,
        }

    @classmethod
    def make(cls) -> "ObjectiveFrontierBridge":
        """Construct a default :class:`ObjectiveFrontierBridge`."""
        return cls(
            bridge_id=str(uuid4()),
            objective_scorer=score_frontier_node,
            diversity_enforcer=None,
            cost_estimator=None,
        )


# ---------------------------------------------------------------------------
# PhaseTransitionHandler
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PhaseTransitionHandler:
    """Tracks score history and handles phase transitions.

    Attributes
    ----------
    handler_id:
        Unique identifier.
    history:
        Accumulated score observations (oldest first).
    current_phase:
        Label of the currently active phase.
    transition_log:
        Ordered list of transition records.
    """

    handler_id: str
    history: list[float] = field(default_factory=list)
    current_phase: str = "exploration"
    transition_log: list[dict] = field(default_factory=list)

    def observe(self, score: float) -> None:
        """Record a new score observation.

        Parameters
        ----------
        score:
            Latest score to append to the history.
        """
        self.history.append(score)

    def check_transition(self, window: int = 20) -> "dict[str, Any] | None":
        """Check whether a phase transition has occurred.

        Parameters
        ----------
        window:
            Size of the detection window passed to :func:`detect_phase_transition`.

        Returns
        -------
        dict[str, Any] or None
            Transition information dictionary when a transition is detected,
            ``None`` otherwise.
        """
        if len(self.history) < 4:
            return None
        result = detect_phase_transition(self.history, window=window)
        if getattr(result, "detected", False):
            info = self.handle(
                from_phase=getattr(result, "from_phase", self.current_phase),
                to_phase=getattr(result, "to_phase", self.current_phase),
                trigger=getattr(result, "trigger", "score_shift"),
            )
            return info
        return None

    def handle(self, from_phase: str, to_phase: str, trigger: str) -> dict:
        """Process a detected phase transition.

        Updates :attr:`current_phase` and appends to :attr:`transition_log`.

        Parameters
        ----------
        from_phase:
            Phase being exited.
        to_phase:
            Phase being entered.
        trigger:
            Description of what triggered the transition.

        Returns
        -------
        dict
            Record of the transition.
        """
        record = {
            "from_phase": from_phase,
            "to_phase": to_phase,
            "trigger": trigger,
            "timestamp": time.time(),
        }
        self.transition_log.append(record)
        self.current_phase = to_phase
        return record

    def current_phase_recommendation(self) -> str:
        """Suggest the current phase based on recent history.

        Returns
        -------
        str
            ``"exploitation"`` when recent scores are high and stable,
            ``"exploration"`` otherwise.
        """
        if len(self.history) < 5:
            return "exploration"
        recent = self.history[-10:]
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        # High mean, low variance → exploit
        if mean > 0.6 and variance < 0.05:
            return "exploitation"
        return "exploration"

    def to_dict(self) -> dict[str, Any]:
        """Serialise handler state."""
        return {
            "handler_id": self.handler_id,
            "current_phase": self.current_phase,
            "history_length": len(self.history),
            "transition_count": len(self.transition_log),
        }

    @classmethod
    def make(cls) -> "PhaseTransitionHandler":
        """Construct a default :class:`PhaseTransitionHandler`."""
        return cls(handler_id=str(uuid4()))


# ---------------------------------------------------------------------------
# ObjectiveTrustAdapter
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ObjectiveTrustAdapter:
    """Adapts objective weights based on trust levels.

    Attributes
    ----------
    adapter_id:
        Unique identifier.
    base_weights:
        Baseline weights per objective ID, before trust scaling.
    """

    adapter_id: str
    base_weights: dict[str, float] = field(default_factory=dict)

    # Tier-to-scale mapping (higher tier → higher trust → higher weight)
    _TIER_SCALES: dict[str, float] = field(
        default_factory=lambda: {
            "CRITICAL": 1.0,
            "HIGH": 0.9,
            "MEDIUM": 0.7,
            "LOW": 0.5,
            "MINIMAL": 0.3,
            "ZERO": 0.0,
        }
    )

    def adapt_weight(self, objective_id: str, trust_level: Any) -> float:
        """Scale the weight of *objective_id* by the trust tier.

        Parameters
        ----------
        objective_id:
            Identifies which objective's weight to scale.
        trust_level:
            A trust object; its tier or numeric value is extracted
            automatically.

        Returns
        -------
        float
            Scaled weight in [0, 1].
        """
        base = self.base_weights.get(objective_id, 1.0)
        scale = self.trust_to_scale(trust_level)
        return max(0.0, min(1.0, base * scale))

    def adapt_objectives(
        self, objectives: list[Any], trust_profile: Any
    ) -> list[Any]:
        """Return a new list of objectives with weights scaled by trust.

        The function creates lightweight wrapper objects when the original
        objectives are frozen dataclasses (i.e. cannot be mutated in-place).

        Parameters
        ----------
        objectives:
            Original objective list.
        trust_profile:
            A TrustProfile or any object with a ``get_level(objective_id)``
            or ``levels`` attribute.

        Returns
        -------
        list[Any]
            Objectives with updated weights.
        """
        adapted: list[Any] = []
        for obj in objectives:
            oid = str(getattr(obj, "objective_id", uuid4()))
            trust_level = None
            if trust_profile is not None:
                get_fn = getattr(trust_profile, "get_level", None)
                if callable(get_fn):
                    try:
                        trust_level = get_fn(oid)
                    except Exception:
                        pass
                if trust_level is None:
                    levels = getattr(trust_profile, "levels", {})
                    trust_level = levels.get(oid)
            new_weight = self.adapt_weight(oid, trust_level)
            # Wrap the objective to override the weight
            wrapped = _WeightedObjectiveWrapper(obj, new_weight)
            adapted.append(wrapped)
        return adapted

    def trust_to_scale(self, trust: Any) -> float:
        """Convert a trust level or tier to a [0, 1] scale factor.

        Parameters
        ----------
        trust:
            May be a ``TrustLevel``, ``TrustTier``, a string tier name, or a
            numeric value.  Returns 1.0 when ``None``.

        Returns
        -------
        float
        """
        if trust is None:
            return 1.0
        # Numeric shortcut
        if isinstance(trust, (int, float)):
            return max(0.0, min(1.0, float(trust)))
        # Try .value attribute (Enum-like)
        name = getattr(trust, "name", None) or getattr(trust, "tier", None)
        if name is not None:
            return self._TIER_SCALES.get(str(name).upper(), 0.7)
        # Try string representation
        return self._TIER_SCALES.get(str(trust).upper(), 0.7)

    def to_dict(self) -> dict[str, Any]:
        """Serialise adapter state."""
        return {
            "adapter_id": self.adapter_id,
            "base_weights": self.base_weights,
        }

    @classmethod
    def make(
        cls, base_weights: "dict[str, float] | None" = None
    ) -> "ObjectiveTrustAdapter":
        """Construct a default :class:`ObjectiveTrustAdapter`.

        Parameters
        ----------
        base_weights:
            Optional pre-seeded base weights.
        """
        return cls(
            adapter_id=str(uuid4()),
            base_weights=base_weights or {},
        )


class _WeightedObjectiveWrapper:
    """Thin wrapper that overrides the ``weight`` attribute of an objective."""

    __slots__ = ("_inner", "weight")

    def __init__(self, inner: Any, weight: float) -> None:
        self._inner = inner
        self.weight = weight

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# FrontierDescentIntegrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrontierDescentIntegrator:
    """Integrates descent-engine results with frontier objective management.

    After each descent step, :meth:`integrate_result` extracts the closure
    gain and feeds it back into the objective system.  If an obstruction is
    encountered, :meth:`handle_obstruction` adjusts the strategy.

    Attributes
    ----------
    integrator_id:
        Unique identifier.
    gain_history:
        Sequence of observed closure gains (oldest first).
    obstruction_count:
        Running count of obstructions encountered.
    """

    integrator_id: str
    gain_history: list[float] = field(default_factory=list)
    obstruction_count: int = 0

    def integrate_result(
        self, descent_result: Any, frontier_node: Any
    ) -> dict[str, Any]:
        """Extract closure gain from *descent_result* and update history.

        Parameters
        ----------
        descent_result:
            A ``DescentResult``-like object with optional ``gain``,
            ``closure_gain``, or ``improvement`` attributes.
        frontier_node:
            The node that was subject to descent; used for fallback gain
            extraction.

        Returns
        -------
        dict[str, Any]
            Summary of the integration step.
        """
        gain: float = 0.0
        for attr in ("closure_gain", "gain", "improvement", "delta"):
            raw = getattr(descent_result, attr, None)
            if raw is not None:
                try:
                    gain = float(raw)
                    break
                except Exception:
                    pass

        # Fallback to node attribute
        if gain == 0.0:
            gain = float(getattr(frontier_node, "closure_gain", 0.0))

        self.update_gain_history(gain)
        success = getattr(descent_result, "success", gain > 0)

        return {
            "gain": gain,
            "success": success,
            "expected_gain": self.expected_gain(),
            "obstruction_count": self.obstruction_count,
            "timestamp": time.time(),
        }

    def handle_obstruction(self, obstruction: Any) -> dict:
        """Adjust objectives based on an encountered obstruction.

        Parameters
        ----------
        obstruction:
            A ``DescentObstruction``-like or ``Obstruction``-like object.

        Returns
        -------
        dict
            Obstruction record.
        """
        self.obstruction_count += 1
        kind = str(getattr(obstruction, "kind", getattr(obstruction, "type", "unknown")))
        message = str(getattr(obstruction, "message", getattr(obstruction, "description", "")))
        return {
            "obstruction_count": self.obstruction_count,
            "kind": kind,
            "message": message,
            "recommendation": (
                "switch_to_exploration" if self.obstruction_count > 3 else "retry"
            ),
            "timestamp": time.time(),
        }

    def update_gain_history(self, gain: float) -> None:
        """Append *gain* to the gain history, capping at 1000 entries.

        Parameters
        ----------
        gain:
            New gain value to record.
        """
        self.gain_history.append(gain)
        if len(self.gain_history) > 1000:
            self.gain_history = self.gain_history[-1000:]

    def expected_gain(self) -> float:
        """Return the EMA of recent gains as the expected future gain.

        Returns
        -------
        float
            EMA value; 0.0 when no history is available.
        """
        return exponential_moving_average(self.gain_history, alpha=0.3)

    def to_dict(self) -> dict[str, Any]:
        """Serialise integrator state."""
        return {
            "integrator_id": self.integrator_id,
            "gain_history_length": len(self.gain_history),
            "obstruction_count": self.obstruction_count,
            "expected_gain": self.expected_gain(),
        }

    @classmethod
    def make(cls) -> "FrontierDescentIntegrator":
        """Construct a default :class:`FrontierDescentIntegrator`."""
        return cls(integrator_id=str(uuid4()))


# ---------------------------------------------------------------------------
# IntegrationPipeline
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IntegrationPipeline:
    """End-to-end integration pipeline for frontier objectives.

    Wires together all sub-components and exposes a single :meth:`run` method
    for each orchestration tick.

    Attributes
    ----------
    pipeline_id:
        Unique identifier.
    orchestrator:
        The :class:`FrontierObjectivesOrchestrator` instance.
    bridge:
        The :class:`ObjectiveFrontierBridge` instance.
    phase_handler:
        The :class:`PhaseTransitionHandler` instance.
    trust_adapter:
        The :class:`ObjectiveTrustAdapter` instance.
    descent_integrator:
        The :class:`FrontierDescentIntegrator` instance.
    """

    pipeline_id: str
    orchestrator: FrontierObjectivesOrchestrator
    bridge: ObjectiveFrontierBridge
    phase_handler: PhaseTransitionHandler
    trust_adapter: ObjectiveTrustAdapter
    descent_integrator: FrontierDescentIntegrator

    def run(
        self,
        state: Any,
        frontier: Any = None,
        trust_profile: Any = None,
    ) -> dict[str, Any]:
        """Execute one full integration tick.

        Steps:
        1. Adapt objective weights via trust profile (if provided).
        2. Attach frontier to bridge (if provided).
        3. Run orchestrator step.
        4. Check for phase transition.
        5. Return a combined result.

        Parameters
        ----------
        state:
            Current orchestrator state.
        frontier:
            Optional frontier object; attached to the bridge for node
            operations.
        trust_profile:
            Optional trust profile used to scale objective weights.

        Returns
        -------
        dict[str, Any]
            Combined pipeline result.
        """
        # Adapt objective weights
        if trust_profile is not None:
            objectives = self.orchestrator._extract_objectives()
            adapted = self.trust_adapter.adapt_objectives(objectives, trust_profile)
            self.orchestrator.objective_set = adapted

        # Attach frontier
        if frontier is not None:
            self.bridge.attach_frontier(frontier)

        # Orchestrator step
        orch_result = self.orchestrator.step(state)

        # Phase observation
        mean_score = orch_result.get("mean_score", 0.0)
        self.phase_handler.observe(mean_score)
        transition_info = self.phase_handler.check_transition(
            window=self.orchestrator.config.phase_window
        )

        # Beam search on frontier (if attached)
        beam_result: dict[str, Any] = {}
        if self.bridge._frontier_ref is not None:
            bsr = frontier_beam_search(
                frontier=self.bridge._frontier_ref,
                objectives=self.orchestrator._extract_objectives(),
                beam_width=self.orchestrator.config.beam_width,
                budget=50.0,
            )
            beam_result = bsr.to_dict() if hasattr(bsr, "to_dict") else {}

        return {
            "pipeline_id": self.pipeline_id,
            "orchestration": orch_result,
            "phase_transition": transition_info,
            "beam_search": beam_result,
            "current_phase": self.phase_handler.current_phase,
            "descent": self.descent_integrator.to_dict(),
            "timestamp": time.time(),
        }

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the pipeline status."""
        return {
            "pipeline_id": self.pipeline_id,
            "orchestrator": self.orchestrator.summary(),
            "bridge": self.bridge.to_dict(),
            "phase_handler": self.phase_handler.to_dict(),
            "trust_adapter": self.trust_adapter.to_dict(),
            "descent_integrator": self.descent_integrator.to_dict(),
        }

    def reset(self) -> None:
        """Reset mutable state across all sub-components."""
        self.orchestrator.scoring_history.clear()
        self.orchestrator.events.clear()
        self.phase_handler.history.clear()
        self.phase_handler.transition_log.clear()
        self.phase_handler.current_phase = "exploration"
        self.descent_integrator.gain_history.clear()
        self.descent_integrator.obstruction_count = 0

    @classmethod
    def make(
        cls, config: "IntegrationConfig | None" = None
    ) -> "IntegrationPipeline":
        """Construct a default :class:`IntegrationPipeline`.

        Parameters
        ----------
        config:
            Optional configuration; a default one is created if ``None``.
        """
        cfg = config or IntegrationConfig.default()
        return cls(
            pipeline_id=str(uuid4()),
            orchestrator=FrontierObjectivesOrchestrator.make(cfg),
            bridge=ObjectiveFrontierBridge.make(),
            phase_handler=PhaseTransitionHandler.make(),
            trust_adapter=ObjectiveTrustAdapter.make(),
            descent_integrator=FrontierDescentIntegrator.make(),
        )


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def build_default_pipeline(
    config: "IntegrationConfig | None" = None,
) -> IntegrationPipeline:
    """Build and return a default :class:`IntegrationPipeline`.

    Parameters
    ----------
    config:
        Optional configuration; uses :meth:`IntegrationConfig.default` if
        ``None``.

    Returns
    -------
    IntegrationPipeline
    """
    return IntegrationPipeline.make(config=config)


def run_integration_step(
    pipeline: IntegrationPipeline,
    state: Any,
) -> dict[str, Any]:
    """Execute one integration step on *pipeline* with the given *state*.

    This is a convenience wrapper around :meth:`IntegrationPipeline.run` that
    makes the intent explicit at call sites.

    Parameters
    ----------
    pipeline:
        A configured :class:`IntegrationPipeline`.
    state:
        Current orchestrator state.

    Returns
    -------
    dict[str, Any]
        Pipeline run result.
    """
    return pipeline.run(state=state)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "IntegrationConfig",
    "IntegrationEvent",
    "FrontierObjectivesOrchestrator",
    "ObjectiveFrontierBridge",
    "PhaseTransitionHandler",
    "ObjectiveTrustAdapter",
    "FrontierDescentIntegrator",
    "IntegrationPipeline",
    "build_default_pipeline",
    "run_integration_step",
]
