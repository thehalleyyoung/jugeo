"""Integration layer for JuGeo ideation optimization (Ch50).

Connects the optimization subsystem to IdeationSchedule, IdeationRegime, and
provides event-driven orchestration, copilot advisory, and a full optimization
pipeline.

The integration layer is intentionally decoupled from the concrete schedule and
regime types; it accesses their attributes through ``getattr`` with sensible
defaults so that the module remains importable even when the parent packages are
absent.

Architecture overview
---------------------
* :class:`OptimizationEventBus` — publish-subscribe hub for algorithm lifecycle
  events (:class:`OptimizationEventType`).
* :class:`OptimizationEvent` — immutable event value objects.
* :class:`CopilotOptimizationAdvisor` — translates raw :class:`OptimizationResult`
  objects into human-readable advisory text.
* :class:`SchedulerOptimizationBridge` — adapts
  :class:`~jugeo.ideation.scheduling.IdeationSchedule` for use as a candidate
  source.
* :class:`RegimeOptimizationBridge` — derives objective weights from an
  :class:`~jugeo.ideation.regimes.IdeationRegime`.
* :class:`OptimizationIntegration` — orchestrates the complete pipeline end-to-end.
"""
from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from .models import (
    IdeationObjective,
    ObjectiveDirection,
    OptimizationProblem,
    OptimizationResult,
    ParetoFront,
    SolutionCandidate,
)
from .algorithms import (
    AlgorithmSelector,
    OptimizationAlgorithm,
    WeightedSumOptimizer,
)

try:
    from .objective_functions import ObjectiveEvaluator, ObjectiveFactory
    _HAS_OBJ_FACTORY = True
except ImportError:  # pragma: no cover
    ObjectiveEvaluator = Any  # type: ignore
    ObjectiveFactory = Any  # type: ignore
    _HAS_OBJ_FACTORY = False

try:
    from jugeo.ideation.ideas import IdeaProposal
except ImportError:  # pragma: no cover
    IdeaProposal = Any  # type: ignore

try:
    from jugeo.ideation.scheduling import IdeationSchedule
except ImportError:  # pragma: no cover
    IdeationSchedule = Any  # type: ignore

try:
    from jugeo.ideation.regimes import IdeationRegime
except ImportError:  # pragma: no cover
    IdeationRegime = Any  # type: ignore

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Module-level helper functions
# ---------------------------------------------------------------------------

def _make_event_id() -> str:
    """Return a new random UUID string suitable for use as an event identifier.

    Generates a version-4 UUID via :func:`uuid.uuid4` and converts it to a
    lowercase hyphenated string.
    """
    return str(uuid.uuid4())


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with timezone suffix.

    Example output: ``"2025-01-15T08:30:00.123456+00:00"``
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _notify_observers(
    observers: list[Callable],
    result: OptimizationResult,
) -> None:
    """Call every registered observer with *result*, ignoring exceptions.

    Observers are called in registration order.  A failing observer is logged
    at WARNING level but does not prevent subsequent observers from being
    notified.

    Parameters
    ----------
    observers:
        List of callable objects registered via
        :meth:`OptimizationIntegration.register_observer`.
    result:
        The completed :class:`OptimizationResult` to pass to each observer.
    """
    for obs in observers:
        try:
            obs(result)
        except Exception as exc:  # pragma: no cover
            _log.warning("Observer %r raised an exception: %s", obs, exc)


def _safe_getattr(obj: Any, attr: str, default: Any = None) -> Any:
    """Return ``getattr(obj, attr, default)`` safely.

    Provides a uniform way to access attributes on duck-typed schedule / regime
    objects without raising :class:`AttributeError`.
    """
    return getattr(obj, attr, default)


def _format_score_table(scores: dict[str, float]) -> str:
    """Format a score dict as an aligned text table for advisory output.

    Parameters
    ----------
    scores:
        Mapping of objective name → numeric score.
    """
    if not scores:
        return "  (no scores)"
    lines = []
    max_name_len = max(len(k) for k in scores) if scores else 10
    for name, val in sorted(scores.items()):
        bar_len = int(round(val * 20))
        bar = "█" * bar_len + "░" * (20 - bar_len)
        lines.append(f"  {name:<{max_name_len}}  {val:.4f}  [{bar}]")
    return "\n".join(lines)


def _build_standard_objectives(
    weights: dict[str, float] | None = None,
) -> list[IdeationObjective]:
    """Return the standard three-objective suite used by the full pipeline.

    Creates objectives for *novelty*, *feasibility*, and *purpose* with
    MAXIMIZE direction.  Weights are drawn from *weights* dict when provided,
    defaulting to ``1.0``.

    Parameters
    ----------
    weights:
        Optional override mapping of objective name → weight.
    """
    w = weights or {}
    return [
        IdeationObjective(
            name="novelty",
            direction=ObjectiveDirection.MAXIMIZE,
            weight=w.get("novelty", 1.0),
            description="Degree of conceptual novelty relative to existing literature.",
        ),
        IdeationObjective(
            name="feasibility",
            direction=ObjectiveDirection.MAXIMIZE,
            weight=w.get("feasibility", 1.0),
            description="Estimated feasibility of developing the idea into a proof.",
        ),
        IdeationObjective(
            name="purpose",
            direction=ObjectiveDirection.MAXIMIZE,
            weight=w.get("purpose", 1.0),
            description="Alignment with the current research regime's stated purpose.",
        ),
    ]


# ---------------------------------------------------------------------------
# 2. Optimization event infrastructure
# ---------------------------------------------------------------------------

class OptimizationEventType(str, Enum):
    """Life-cycle event categories for the optimization event bus.

    Attributes
    ----------
    STARTED:
        Emitted immediately before an algorithm begins execution.
    COMPLETED:
        Emitted immediately after an algorithm returns a result.
    STEP:
        Emitted at each internal algorithm iteration (optional, algorithm-level).
    IMPROVED:
        Emitted when a better candidate is discovered during a run.
    CONVERGED:
        Emitted when the algorithm detects convergence before exhausting its
        iteration budget.
    FAILED:
        Emitted if an algorithm raises an unhandled exception.
    """

    STARTED = "started"
    COMPLETED = "completed"
    STEP = "step"
    IMPROVED = "improved"
    CONVERGED = "converged"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OptimizationEvent:
    """Immutable value object representing a single optimization lifecycle event.

    Attributes
    ----------
    event_id:
        Unique identifier for this event instance.
    event_type:
        The lifecycle category of this event.
    timestamp:
        ISO-8601 UTC timestamp string of when the event was created.
    payload:
        Arbitrary key/value pairs carrying event-specific data.
    source:
        Optional identifier of the component that emitted this event (e.g.
        algorithm name, class name).
    """

    event_id: str
    event_type: OptimizationEventType
    timestamp: str
    payload: dict[str, Any]
    source: str = ""

    # ------------------------------------------------------------------
    def summary(self) -> str:
        """Return a compact one-line description of this event.

        Includes the event type, source, timestamp, and a truncated payload
        repr for quick debugging output.
        """
        payload_snippet = repr(self.payload)[:80]
        return (
            f"[{self.event_type.value.upper()}] "
            f"source={self.source!r} "
            f"ts={self.timestamp} "
            f"payload={payload_snippet}"
        )

    # ------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        event_type: OptimizationEventType,
        payload: dict[str, Any],
        source: str = "",
    ) -> OptimizationEvent:
        """Factory method: construct an :class:`OptimizationEvent` with a new ID.

        Parameters
        ----------
        event_type:
            The event category to assign.
        payload:
            Data to embed in the event.
        source:
            Identifier of the emitting component.
        """
        return cls(
            event_id=_make_event_id(),
            event_type=event_type,
            timestamp=_now_iso(),
            payload=payload,
            source=source,
        )


# ---------------------------------------------------------------------------
# 3. Event bus
# ---------------------------------------------------------------------------

class OptimizationEventBus:
    """Publish-subscribe event bus for optimization lifecycle events.

    Subscribers register a callback for a specific :class:`OptimizationEventType`.
    When an event of that type is published, all matching callbacks are invoked
    in registration order.  The bus also maintains a complete history of all
    published events for post-hoc analysis.

    Attributes (private)
    --------------------
    _subscribers:
        Mapping of event type → list of registered callbacks.
    _history:
        Ordered list of every event ever published on this bus instance.
    """

    def __init__(self) -> None:
        self._subscribers: dict[OptimizationEventType, list[Callable]] = {
            et: [] for et in OptimizationEventType
        }
        self._history: list[OptimizationEvent] = []

    # ------------------------------------------------------------------
    def subscribe(
        self,
        event_type: OptimizationEventType,
        callback: Callable,
    ) -> None:
        """Register *callback* to be called whenever *event_type* is published.

        The same callback may be registered multiple times; it will be called
        once per registration.

        Parameters
        ----------
        event_type:
            The event type to subscribe to.
        callback:
            A callable that accepts a single :class:`OptimizationEvent` argument.
        """
        self._subscribers[event_type].append(callback)
        _log.debug(
            "EventBus: subscribed %r to %r (total=%d)",
            getattr(callback, "__name__", repr(callback)),
            event_type.value,
            len(self._subscribers[event_type]),
        )

    # ------------------------------------------------------------------
    def publish(self, event: OptimizationEvent) -> None:
        """Broadcast *event* to all subscribers and append it to history.

        Exceptions raised by subscribers are caught and logged at WARNING level
        so that one failing subscriber does not prevent others from receiving
        the event.

        Parameters
        ----------
        event:
            The :class:`OptimizationEvent` to publish.
        """
        self._history.append(event)
        for callback in self._subscribers.get(event.event_type, []):
            try:
                callback(event)
            except Exception as exc:  # pragma: no cover
                _log.warning(
                    "EventBus subscriber %r raised: %s",
                    getattr(callback, "__name__", repr(callback)),
                    exc,
                )
        _log.debug("EventBus: published %s", event.summary())

    # ------------------------------------------------------------------
    def history(self) -> list[OptimizationEvent]:
        """Return a shallow copy of the complete event history list."""
        return list(self._history)

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Clear both the event history and all subscriber registrations.

        After calling this method the bus is in the same state as a freshly
        constructed instance.
        """
        self._history.clear()
        for et in OptimizationEventType:
            self._subscribers[et] = []
        _log.debug("EventBus: cleared history and subscribers.")

    # ------------------------------------------------------------------
    def history_for(
        self,
        event_type: OptimizationEventType,
    ) -> list[OptimizationEvent]:
        """Return the subset of history events matching *event_type*.

        Parameters
        ----------
        event_type:
            The event category to filter by.
        """
        return [e for e in self._history if e.event_type == event_type]


# ---------------------------------------------------------------------------
# 4. Copilot advisory
# ---------------------------------------------------------------------------

class CopilotOptimizationAdvisor:
    """Translates raw optimization results into human-readable advisory text.

    The advisor does not make decisions; it surfaces the most important
    observations from an :class:`OptimizationResult` in a form suitable for
    display to a research copilot interface or log.

    Attributes
    ----------
    event_bus:
        The shared :class:`OptimizationEventBus`; advisory events may be
        published here if needed.
    """

    def __init__(self, event_bus: OptimizationEventBus) -> None:
        self.event_bus = event_bus

    # ------------------------------------------------------------------
    def advise(self, result: OptimizationResult) -> str:
        """Generate rich multi-line advisory text from *result*.

        Covers: algorithm used, number of iterations, front size, best
        candidate details, score breakdown, convergence status, and a suggested
        next action.

        Parameters
        ----------
        result:
            A completed :class:`OptimizationResult` to analyse.
        """
        algo_name = result.metadata.get("algorithm", "unknown")
        duration = result.metadata.get("duration_s", 0.0)
        front_size = result.front_size()
        n_eval = result.n_evaluated()
        best_title = result.metadata.get("best_idea_title", "N/A")
        best_total = result.metadata.get("best_total_score", 0.0)
        converged = result.converged

        lines = [
            "╔══════════════════════════════════════════════════════════╗",
            "║         JuGeo Optimization Advisory Report               ║",
            "╚══════════════════════════════════════════════════════════╝",
            "",
            f"  Algorithm   : {algo_name}",
            f"  Duration    : {duration:.4f}s",
            f"  Iterations  : {result.iterations_run}",
            f"  Evaluated   : {n_eval} candidate(s)",
            f"  Pareto front: {front_size} non-dominated solution(s)",
            f"  Converged   : {converged}",
            "",
            "  ── Best Candidate ─────────────────────────────────────",
            f"  Title       : {best_title}",
            f"  Total score : {best_total:.4f}",
            "",
        ]

        # Score breakdown for the best candidate.
        if result.pareto_front and result.pareto_front.members:
            best = max(result.pareto_front.members,
                       key=lambda c: sum(c.scores.values()))
            lines.append("  ── Objective Scores ────────────────────────────────────")
            lines.append(_format_score_table(best.scores))
            lines.append("")

        # Pareto front summary.
        if front_size > 0:
            lines.append("  ── Pareto Front Summary ────────────────────────────────")
            lines.append(self.summarize_pareto(result.pareto_front))  # type: ignore[arg-type]
            lines.append("")

        lines.append("  ── Recommended Next Action ─────────────────────────────")
        lines.append(f"  {self.suggest_next_action(result)}")
        lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def summarize_pareto(self, front: ParetoFront) -> str:
        """Return a human-readable summary of the Pareto front.

        Reports the size of the front, the score range for each objective, and
        highlights the best member per objective.

        Parameters
        ----------
        front:
            The :class:`ParetoFront` to summarise.
        """
        if not front or not front.members:
            return "  (empty front)"

        lines = [
            f"  Front size: {front.size()} | "
            f"Generation: {front.generation} | "
            f"Hypervolume: {front.hypervolume:.4f}"
        ]
        ranges = front.score_ranges()
        for obj_name, (lo, hi) in sorted(ranges.items()):
            best_member = front.best_by(obj_name)
            best_title = (
                getattr(best_member.idea, "title", "?")[:25]
                if best_member is not None
                else "?"
            )
            lines.append(
                f"  {obj_name:<20} range=[{lo:.3f}, {hi:.3f}]  "
                f"best_idea={best_title!r}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def suggest_next_action(self, result: OptimizationResult) -> str:
        """Suggest a next action based on the optimization result.

        Heuristics
        ----------
        * If the Pareto front is empty or has only one member: "explore more" —
          the problem is under-sampled.
        * If the front is large (> 5 members): "adjust weights" — the user
          should supply weights to narrow the trade-off surface.
        * Otherwise: "exploit best" — enough information is available to commit
          to the top candidate.

        Parameters
        ----------
        result:
            The completed :class:`OptimizationResult` to reason about.
        """
        front_size = result.front_size()
        n_cand = len(result.all_candidates)

        if front_size == 0:
            return (
                "No candidates were evaluated. Add idea proposals to the "
                "OptimizationProblem and re-run."
            )
        if front_size == 1:
            best_title = result.metadata.get("best_idea_title", "the top candidate")
            return (
                f"Exploit the single Pareto-optimal idea '{best_title}'. "
                "Consider adding more candidate ideas to broaden the search."
            )
        if front_size > 5:
            return (
                f"The front contains {front_size} non-dominated solutions — "
                "trade-offs are wide. Supply objective weights via "
                "WeightedSumOptimizer to narrow to a single recommendation."
            )
        # 2–5 members: moderate front.
        return (
            f"Exploit the best of the {front_size} Pareto-optimal ideas. "
            f"The remaining {n_cand - front_size} dominated candidate(s) can be "
            "deprioritised or used as fallbacks."
        )


# ---------------------------------------------------------------------------
# 5. Scheduler bridge
# ---------------------------------------------------------------------------

class SchedulerOptimizationBridge:
    """Adapts an :class:`~jugeo.ideation.scheduling.IdeationSchedule` for optimization.

    The bridge extracts the raw idea title strings stored in the schedule's
    ``planned_explorations`` and ``planned_exploitations`` tuples.  Because
    constructing real :class:`IdeaProposal` instances requires a
    ``SupportRegion`` (not available here), this bridge works at the title-string
    level and returns an empty candidate list with a warning when called from a
    context where full proposals are needed.

    Attributes
    ----------
    schedule:
        The schedule instance to adapt.
    """

    def __init__(self, schedule: Any) -> None:
        self.schedule = schedule

    # ------------------------------------------------------------------
    def extract_candidates(self, schedule: Any) -> list[Any]:
        """Extract candidate idea strings from *schedule*.

        Returns the combined list of exploration and exploitation titles from
        ``schedule.planned_explorations`` and ``schedule.planned_exploitations``.
        Since only strings are available at this level, real integration that
        requires scored :class:`IdeaProposal` instances must supply them
        separately.

        Parameters
        ----------
        schedule:
            The :class:`IdeationSchedule` (or compatible duck type) to read.
        """
        explorations = list(_safe_getattr(schedule, "planned_explorations", ()))
        exploitations = list(_safe_getattr(schedule, "planned_exploitations", ()))
        combined = explorations + exploitations

        if combined:
            _log.warning(
                "SchedulerOptimizationBridge.extract_candidates: returning %d "
                "title strings. Real IdeaProposal instances are required for "
                "scored optimization — supply them via OptimizationProblem.add_idea().",
                len(combined),
            )
        else:
            _log.debug(
                "SchedulerOptimizationBridge: no explorations or exploitations found "
                "on schedule of type %r.",
                type(schedule).__name__,
            )
        return combined

    # ------------------------------------------------------------------
    def apply_result(
        self,
        result: OptimizationResult,
        schedule: Any,
    ) -> dict[str, Any]:
        """Return a metadata dict summarising what schedule changes the result implies.

        Because :class:`IdeationSchedule` is frozen (immutable dataclass), this
        method cannot mutate it.  Instead it returns a plain ``dict`` describing
        the re-prioritisation that would be applied if a mutable schedule were
        available.

        Parameters
        ----------
        result:
            The completed :class:`OptimizationResult` to interpret.
        schedule:
            The current :class:`IdeationSchedule` (read-only).
        """
        best_title = result.metadata.get("best_idea_title", None)
        front_titles = [
            getattr(c.idea, "title", str(c.idea))
            for c in (result.pareto_front.members if result.pareto_front else [])
        ]
        current_explorations = list(_safe_getattr(schedule, "planned_explorations", ()))
        current_exploitations = list(_safe_getattr(schedule, "planned_exploitations", ()))

        proposed_explorations = front_titles[:3] if front_titles else current_explorations[:3]
        proposed_exploitations = (
            [best_title] if best_title else current_exploitations[:1]
        )

        summary = {
            "action": "reprioritise",
            "best_candidate": best_title,
            "proposed_explorations": proposed_explorations,
            "proposed_exploitations": proposed_exploitations,
            "original_explorations": current_explorations,
            "original_exploitations": current_exploitations,
            "front_size": result.front_size(),
            "note": (
                "IdeationSchedule is frozen; create a new instance with these "
                "values to apply the proposed changes."
            ),
        }
        _log.info(
            "SchedulerOptimizationBridge.apply_result: best=%r, front_size=%d",
            best_title,
            result.front_size(),
        )
        return summary

    # ------------------------------------------------------------------
    def priority_from_scores(self, scores: dict[str, float]) -> float:
        """Return a scalar priority value as a weighted average of *scores*.

        Uses equal weighting across all score dimensions.  Returns ``0.0`` for
        an empty scores dict.

        Parameters
        ----------
        scores:
            Mapping of objective name → score value.
        """
        if not scores:
            return 0.0
        return sum(scores.values()) / len(scores)


# ---------------------------------------------------------------------------
# 6. Regime bridge
# ---------------------------------------------------------------------------

class RegimeOptimizationBridge:
    """Derives objective weights from an :class:`~jugeo.ideation.regimes.IdeationRegime`.

    The bridge reads the ``novelty_metric`` and ``trust_policy`` string
    attributes of the regime to determine how much to emphasize novelty versus
    feasibility in the optimization.  It also produces a human-readable summary
    of what regime changes would look like post-optimization.

    Attributes
    ----------
    regime:
        The :class:`IdeationRegime` (or duck-typed equivalent) to adapt.
    """

    def __init__(self, regime: Any) -> None:
        self.regime = regime

    # ------------------------------------------------------------------
    def weights_from_regime(self, regime: Any) -> dict[str, float]:
        """Derive objective weights from *regime* attributes.

        Decision rules
        --------------
        * If ``regime.novelty_metric`` contains ``"high"``:
          ``{novelty: 0.8, feasibility: 0.2, purpose: 0.0}``
        * If ``regime.trust_policy`` contains ``"strict"``:
          ``{novelty: 0.3, feasibility: 0.7, purpose: 0.0}``
        * Otherwise (default balanced):
          ``{novelty: 0.4, feasibility: 0.4, purpose: 0.2}``

        Parameters
        ----------
        regime:
            The regime object to read.
        """
        novelty_metric = str(_safe_getattr(regime, "novelty_metric", "")).lower()
        trust_policy = str(_safe_getattr(regime, "trust_policy", "")).lower()

        if "high" in novelty_metric:
            weights = {"novelty": 0.8, "feasibility": 0.2, "purpose": 0.0}
            _log.debug(
                "RegimeBridge: high-novelty regime → weights=%s", weights
            )
        elif "strict" in trust_policy:
            weights = {"novelty": 0.3, "feasibility": 0.7, "purpose": 0.0}
            _log.debug(
                "RegimeBridge: strict-trust regime → weights=%s", weights
            )
        else:
            weights = {"novelty": 0.4, "feasibility": 0.4, "purpose": 0.2}
            _log.debug(
                "RegimeBridge: balanced default → weights=%s", weights
            )
        return weights

    # ------------------------------------------------------------------
    def update_regime_from_result(
        self,
        regime: Any,
        result: OptimizationResult,
    ) -> dict[str, Any]:
        """Return a metadata dict summarising a hypothetical regime update.

        :class:`IdeationRegime` is frozen; this method describes what a new
        regime constructed from the optimization result's insights would look
        like, without actually mutating anything.

        The heuristic: if the best candidate's total score is high (≥ 0.7) and
        the novelty score is dominant, suggest raising the ``novelty_metric``
        toward ``"high"``.  If feasibility dominates, suggest tightening the
        ``trust_policy`` toward ``"strict"``.

        Parameters
        ----------
        regime:
            The current :class:`IdeationRegime`.
        result:
            The completed :class:`OptimizationResult` to reason about.
        """
        current_novelty_metric = str(_safe_getattr(regime, "novelty_metric", "standard"))
        current_trust_policy = str(_safe_getattr(regime, "trust_policy", "provisional"))

        best_total = result.metadata.get("best_total_score", 0.0)
        suggested_novelty = current_novelty_metric
        suggested_trust = current_trust_policy
        rationale = "No change recommended."

        if result.pareto_front and result.pareto_front.members:
            best_member = max(
                result.pareto_front.members,
                key=lambda c: sum(c.scores.values()),
            )
            novelty_score = best_member.scores.get("novelty", 0.5)
            feasibility_score = best_member.scores.get("feasibility", 0.5)

            if best_total >= 0.7 and novelty_score > feasibility_score:
                suggested_novelty = "high_semantic"
                rationale = (
                    "Best candidate shows strong novelty signal; "
                    "elevating novelty_metric to 'high_semantic'."
                )
            elif best_total >= 0.7 and feasibility_score >= novelty_score:
                suggested_trust = "strict"
                rationale = (
                    "Best candidate is highly feasible; "
                    "tightening trust_policy to 'strict' to maintain quality bar."
                )
            elif best_total < 0.4:
                rationale = (
                    "Overall scores are low; consider broadening the candidate pool "
                    "or relaxing constraints before adjusting the regime."
                )

        return {
            "action": "regime_update_proposal",
            "current_novelty_metric": current_novelty_metric,
            "current_trust_policy": current_trust_policy,
            "suggested_novelty_metric": suggested_novelty,
            "suggested_trust_policy": suggested_trust,
            "best_total_score": best_total,
            "rationale": rationale,
            "note": (
                "IdeationRegime is frozen; construct a new instance with the "
                "suggested fields to apply this proposal."
            ),
        }


# ---------------------------------------------------------------------------
# 7. Full integration orchestrator
# ---------------------------------------------------------------------------

class OptimizationIntegration:
    """End-to-end orchestrator for the JuGeo ideation optimization pipeline.

    Ties together algorithm selection, event publication, observer notification,
    and advisory generation into a single façade.  Suitable for both programmatic
    use and command-line invocation.

    Private Attributes
    ------------------
    _event_bus:
        Shared :class:`OptimizationEventBus` used throughout the pipeline.
    _advisor:
        :class:`CopilotOptimizationAdvisor` wired to the event bus.
    _selector:
        :class:`AlgorithmSelector` for automatic algorithm choice.
    _observers:
        List of callables notified with the final :class:`OptimizationResult`
        after each run.
    """

    def __init__(self) -> None:
        self._event_bus: OptimizationEventBus = OptimizationEventBus()
        self._advisor: CopilotOptimizationAdvisor = CopilotOptimizationAdvisor(
            self._event_bus
        )
        self._selector: AlgorithmSelector = AlgorithmSelector()
        self._observers: list[Callable] = []

    # ------------------------------------------------------------------
    def run(
        self,
        problem: OptimizationProblem,
        algorithm: OptimizationAlgorithm,
    ) -> OptimizationResult:
        """Execute *algorithm* on *problem* with full event instrumentation.

        Steps
        -----
        1. Publish a STARTED event.
        2. Invoke ``algorithm.optimize(problem)``.
        3. Publish a COMPLETED event carrying the result summary.
        4. Notify all registered observers with the result.
        5. Return the :class:`OptimizationResult`.

        On exception, a FAILED event is published and the exception is
        re-raised.

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to solve.
        algorithm:
            The :class:`OptimizationAlgorithm` to run.
        """
        _log.info(
            "OptimizationIntegration.run: algorithm=%r, n_candidates=%d",
            algorithm.name,
            len(problem.candidate_ideas),
        )

        started_event = OptimizationEvent.create(
            event_type=OptimizationEventType.STARTED,
            payload={
                "algorithm": algorithm.name,
                "n_candidates": len(problem.candidate_ideas),
                "n_objectives": len(problem.objectives),
                "problem_id": problem.problem_id,
            },
            source=algorithm.name,
        )
        self._event_bus.publish(started_event)

        try:
            result = algorithm.optimize(problem)
        except Exception as exc:
            failed_event = OptimizationEvent.create(
                event_type=OptimizationEventType.FAILED,
                payload={"algorithm": algorithm.name, "error": str(exc)},
                source=algorithm.name,
            )
            self._event_bus.publish(failed_event)
            _log.error(
                "OptimizationIntegration.run: algorithm %r raised: %s",
                algorithm.name,
                exc,
            )
            raise

        completed_event = OptimizationEvent.create(
            event_type=OptimizationEventType.COMPLETED,
            payload={
                "algorithm": algorithm.name,
                "result_id": result.result_id,
                "front_size": result.front_size(),
                "n_evaluated": result.n_evaluated(),
                "iterations_run": result.iterations_run,
                "duration_s": result.metadata.get("duration_s", 0.0),
                "best_idea_title": result.metadata.get("best_idea_title", "N/A"),
            },
            source=algorithm.name,
        )
        self._event_bus.publish(completed_event)
        _notify_observers(self._observers, result)
        _log.info(
            "OptimizationIntegration.run: completed. front_size=%d",
            result.front_size(),
        )
        return result

    # ------------------------------------------------------------------
    def register_observer(self, callback: Callable) -> None:
        """Register *callback* to be called after every optimization run.

        The callback receives the completed :class:`OptimizationResult` as its
        single argument.  Registration order is preserved.

        Parameters
        ----------
        callback:
            A callable accepting one :class:`OptimizationResult` argument.
        """
        self._observers.append(callback)
        _log.debug(
            "OptimizationIntegration: registered observer %r (total=%d)",
            getattr(callback, "__name__", repr(callback)),
            len(self._observers),
        )

    # ------------------------------------------------------------------
    def full_pipeline(
        self,
        ideas: list[Any],
        budget: float = 100.0,
    ) -> OptimizationResult:
        """Run the complete optimization pipeline on *ideas*.

        This is the high-level entry point for callers that have a list of
        :class:`IdeaProposal` objects and want a result without manually
        constructing objectives, problems, or algorithms.

        Steps
        -----
        1. Build the standard three-objective suite (novelty, feasibility,
           purpose) via :func:`_build_standard_objectives`.
        2. Construct an :class:`OptimizationProblem` wrapping *ideas*.
        3. Use :class:`AlgorithmSelector` to pick an algorithm.
        4. Run via :meth:`run` and return the result.

        Parameters
        ----------
        ideas:
            List of :class:`IdeaProposal` (or duck-typed) objects to optimize.
        budget:
            Computational budget hint stored in ``problem.metadata["budget"]``.
        """
        _log.info(
            "OptimizationIntegration.full_pipeline: %d ideas, budget=%.1f",
            len(ideas),
            budget,
        )
        objectives = _build_standard_objectives()
        problem = OptimizationProblem(
            objectives=objectives,
            candidate_ideas=list(ideas),
            description="Full pipeline run",
            metadata={"budget": budget},
        )
        algorithm = self._selector.select(problem)
        _log.info("full_pipeline: selected algorithm=%r", algorithm.name)
        return self.run(problem, algorithm)

    # ------------------------------------------------------------------
    def event_bus(self) -> OptimizationEventBus:
        """Return the shared :class:`OptimizationEventBus` instance."""
        return self._event_bus

    # ------------------------------------------------------------------
    def advisor(self) -> CopilotOptimizationAdvisor:
        """Return the :class:`CopilotOptimizationAdvisor` instance."""
        return self._advisor


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "OptimizationEventType",
    "OptimizationEvent",
    "OptimizationEventBus",
    "CopilotOptimizationAdvisor",
    "SchedulerOptimizationBridge",
    "RegimeOptimizationBridge",
    "OptimizationIntegration",
]
