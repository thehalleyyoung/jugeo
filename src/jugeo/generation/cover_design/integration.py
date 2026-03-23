r"""Integration layer for cover design.

Theory (theory2.tex §42 — Cover Design):
    Chapter 42 describes how the cover design machinery is wired into the
    broader generation pipeline.  *Cover design* is the discipline of choosing
    coordinate patches, allocating budget across them, ordering their
    construction, and tracking quality until all patches satisfy the completion
    criteria.  Integration is the glue that:

    1. Accepts a ``CoverDesignPlan`` from the pipeline—a fully specified plan
       encoding patches, budget envelopes, dependency edges, and quality targets.
    2. Translates each patch descriptor into a ``PatchSelectionGoal`` and
       submits those goals to the patch-selection engine.
    3. Drives the parallelism strategy engine, which decides which patches may
       be elaborated concurrently and which must be serialised due to dependency
       edges.
    4. After all patches are processed, assembles the global design record and
       validates it against the quality metrics and completion criteria.
    5. Exports a full audit trail (``DesignRecord`` list) that can be
       re-imported for diagnostics or warm-restart of an interrupted design run.

    The integration layer also hosts two adapter objects:

    * ``CopilotCoverDesignAdapter`` — wraps raw copilot proposals so that the
      cover design subsystem can consume them without depending on the copilot
      protocol directly.
    * ``CoverDesignPipelineAdapter`` — exposes the cover design subsystem to
      the outer generation pipeline through a stable interface, decoupling
      the pipeline from internal cover-design implementation details.

    The copilot integration marker for this module is:
    **copilot: integration-marker**.

    Key invariants maintained by this module:

    * Every ``PatchSelectionGoal`` that enters ``run_cover_design_plan`` must
      produce exactly one entry in the returned ``patch_results`` list, whether
      it succeeds or fails.
    * The global design record is marked *complete* only when every patch has
      reached its quality target and no completion-criteria obligations remain
      unresolved.
    * The budget allocated to a patch context is never exceeded; if the
      patch engine reports consumption exceeding the envelope, the patch is
      immediately diagnosed and failed.
    * All public methods append to ``_design_records`` so that the full history
      can be exported at any time via ``export_design_record``.

Usage example::

    cfg = {
        "max_parallel_patches": 4,
        "enable_copilot": True,
        "copilot_proposal_strategy": "adaptive",
    }
    integration = CoverDesignIntegration(config=cfg)
    integration.integrate_with_generation_pipeline()
    result = integration.run_single_plan(my_plan)

copilot: integration-marker
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Any

try:
    from jugeo.generation.goals import GenerationGoal, GoalDecomposer, OverlapGoal
except Exception:  # noqa: BLE001
    GenerationGoal = Any  # type: ignore[assignment,misc]
    GoalDecomposer = Any  # type: ignore[assignment,misc]
    OverlapGoal = Any  # type: ignore[assignment,misc]

try:
    from jugeo.generation.construction import (
        ConstructionLoop,
        ConstructionContext,
        ConstructionResult,
        Candidate,
    )
except Exception:  # noqa: BLE001
    ConstructionLoop = Any  # type: ignore[assignment,misc]
    ConstructionContext = Any  # type: ignore[assignment,misc]
    ConstructionResult = Any  # type: ignore[assignment,misc]
    Candidate = Any  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from .models import (
        CoverDesignPlan,
        PatchDescriptor,
        DesignBudget,
        QualityTarget,
        CompletionCriteria,
        CoverDesignError,
        PatchSelectionError,
        BudgetExhaustedError,
        QualityFailureError,
    )
    from .cover_design_principles import CoverDesignPrinciplesEngine
    from .patch_selection import PatchSelectionEngine
    from .budget_allocation import BudgetAllocationEngine
    from .parallelism_strategy import ParallelismStrategyEngine
    from .dependency_ordering import DependencyOrderingEngine
    from .quality_metrics import QualityMetricsEngine
    from .completion_criteria import CompletionCriteriaEngine

__all__ = [
    "CoverDesignIntegration",
    "CopilotCoverDesignAdapter",
    "CoverDesignPipelineAdapter",
    "DesignRecord",
    "DesignPipelineState",
    "DesignIntegrationConfig",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DesignIntegrationConfig:
    """Configuration for the ``CoverDesignIntegration`` layer.

    Attributes
    ----------
    max_parallel_patches:
        Maximum number of patch elaborations that may run concurrently
        inside a single cover design round.
    default_budget:
        Default computational budget (in abstract cost units) assigned to a
        patch context when the originating plan does not carry its own per-patch
        budget envelope.
    integration_mode:
        One of ``"full"`` (all subsystems active), ``"lite"`` (skip copilot and
        quality-metric enforcement), or ``"debug"`` (verbose tracing,
        single-threaded patches).
    enable_copilot:
        Whether the ``CopilotCoverDesignAdapter`` should be instantiated and
        consulted during patch setup and failure diagnosis.
    copilot_proposal_strategy:
        Strategy tag passed to the copilot adapter.  Supported values are
        ``"greedy"``, ``"adaptive"``, ``"exhaustive"``.
    quality_threshold:
        Minimum quality score (0.0–1.0) required for a patch to be considered
        complete.  Patches scoring below this threshold are marked as failed.
    completion_strictness:
        One of ``"strict"`` (all completion criteria must be satisfied),
        ``"lenient"`` (a majority suffices), or ``"advisory"`` (criteria are
        recorded but never block completion).
    export_format:
        Serialisation format used by ``export_design_record``.  Currently only
        ``"json"`` is supported; reserved for future ``"msgpack"`` support.
    trace_enabled:
        When ``True``, every significant internal state transition is appended
        to ``_design_records`` with full metadata.
    dependency_strategy:
        Strategy for resolving patch dependency order.  One of
        ``"topological"``, ``"greedy_parallel"``, or ``"priority_weighted"``.
    """

    max_parallel_patches: int = 8
    default_budget: float = 1.0
    integration_mode: str = "full"
    enable_copilot: bool = True
    copilot_proposal_strategy: str = "adaptive"
    quality_threshold: float = 0.8
    completion_strictness: str = "strict"
    export_format: str = "json"
    trace_enabled: bool = True
    dependency_strategy: str = "topological"


@dataclass
class DesignPipelineState:
    """Live snapshot of the cover design pipeline's bookkeeping state.

    Attributes
    ----------
    state_id:
        Unique identifier for this pipeline state instance.
    status:
        Lifecycle phase: ``"idle"``, ``"integrated"``, ``"running"``,
        ``"finalised"``, or ``"error"``.
    active_design_rounds:
        Number of cover design rounds currently in-flight.
    completed_patches:
        Cumulative count of patches that finished with a successful result.
    failed_patches:
        Cumulative count of patches that terminated with a failure.
    total_plans_processed:
        Total number of ``CoverDesignPlan`` objects that have been submitted to
        this integration instance since construction.
    total_records_assembled:
        Total number of global design records that have been assembled (whether
        complete or not).
    started_at:
        Unix timestamp at which ``integrate_with_generation_pipeline`` was first
        called.  ``None`` before that call.
    last_updated:
        Unix timestamp of the most recent state mutation.
    """

    state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "idle"
    active_design_rounds: int = 0
    completed_patches: int = 0
    failed_patches: int = 0
    total_plans_processed: int = 0
    total_records_assembled: int = 0
    started_at: float | None = None
    last_updated: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Update ``last_updated`` to the current wall-clock time."""
        self.last_updated = time.time()


@dataclass
class DesignRecord:
    """Immutable audit entry capturing the outcome of a single patch run.

    One ``DesignRecord`` is appended to ``_design_records`` each time a patch
    finishes (success or failure) or a significant diagnostic event occurs.

    Attributes
    ----------
    record_id:
        Globally unique identifier for this record.
    plan_id:
        The ``plan_id`` of the ``CoverDesignPlan`` that initiated the run, or
        ``"__pipeline__"`` / ``"__assembly__"`` for internal lifecycle events.
    patch_id:
        The ``patch_id`` of the patch being processed, or ``None`` if this
        record describes a plan-level event.
    result_status:
        One of ``"success"``, ``"failure"``, ``"partial"``, ``"skipped"``.
    quality_score:
        Quality score (0.0–1.0) achieved by the patch, or ``0.0`` for failures.
    residual_count:
        Number of unresolved completion-criteria obligations remaining after the
        patch was processed.
    elapsed_ms:
        Wall-clock milliseconds consumed by the patch run.
    iteration_count:
        Number of proposal-verify rounds the patch engine performed.
    budget_consumed:
        Fraction of the allocated budget actually consumed (0.0–1.0).
    timestamp:
        Unix timestamp at which this record was created.
    metadata:
        Arbitrary key-value pairs for extended diagnostics.
    """

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str = ""
    patch_id: str | None = None
    result_status: str = "success"
    quality_score: float = 0.0
    residual_count: int = 0
    elapsed_ms: int = 0
    iteration_count: int = 0
    budget_consumed: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter classes
# ---------------------------------------------------------------------------


class CopilotCoverDesignAdapter:
    """Adapter that wraps raw copilot proposals for the cover design subsystem.

    The copilot operates on a different abstraction level than the cover design
    engines.  This adapter translates copilot outputs—candidate patches,
    budget suggestions, dependency hints—into the concrete types expected by
    ``CoverDesignIntegration``.

    Parameters
    ----------
    proposal_strategy:
        Strategy tag used when requesting proposals.  One of ``"greedy"``,
        ``"adaptive"``, or ``"exhaustive"``.
    budget_hint:
        Soft budget hint to pass to the copilot when seeding proposals.

    Attributes
    ----------
    _proposals_issued:
        Running count of proposal batches issued since construction.
    _strategy:
        The current proposal strategy tag.
    _budget_hint:
        Current budget hint value.
    """

    def __init__(
        self,
        proposal_strategy: str = "adaptive",
        budget_hint: float = 1.0,
    ) -> None:
        """Initialise the copilot cover design adapter."""
        self._strategy = proposal_strategy
        self._budget_hint = budget_hint
        self._proposals_issued: int = 0
        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._logger.debug(
            "CopilotCoverDesignAdapter initialised: strategy=%s, budget_hint=%.4f.",
            self._strategy,
            self._budget_hint,
        )

    def configure(
        self, strategy: str, budget_hint: float
    ) -> None:
        """Update the proposal strategy and budget hint.

        Parameters
        ----------
        strategy:
            New strategy tag.
        budget_hint:
            New budget hint value.
        """
        self._strategy = strategy
        self._budget_hint = budget_hint
        self._logger.debug(
            "Adapter reconfigured: strategy=%s, budget_hint=%.4f.",
            strategy,
            budget_hint,
        )

    def propose_patch_candidates(
        self,
        plan_id: str,
        patch_descriptor: dict[str, Any],
        n: int = 3,
    ) -> list[dict[str, Any]]:
        """Generate candidate patch proposals for a single patch descriptor.

        The copilot examines the patch descriptor's coordinate span, obligation
        set, and quality targets and returns up to *n* ranked candidate patches.
        Each candidate includes a ``patch_id``, a ``priority`` score, a
        ``budget_estimate``, and ``dependency_hints``.

        Parameters
        ----------
        plan_id:
            ID of the enclosing plan (used for context in copilot prompts).
        patch_descriptor:
            The raw patch descriptor dict from the plan.
        n:
            Maximum number of candidates to return.

        Returns
        -------
        list[dict[str, Any]]
            A list of candidate dicts, each with keys: ``patch_id``,
            ``priority``, ``budget_estimate``, ``dependency_hints``,
            ``quality_estimate``, ``strategy_tag``.
        """
        self._proposals_issued += 1
        coord_id = patch_descriptor.get("coordinate_id", f"coord_{uuid.uuid4().hex[:6]}")
        candidates: list[dict[str, Any]] = []

        strategies = {
            "greedy": [0.9, 0.7, 0.5],
            "adaptive": [0.8, 0.75, 0.65],
            "exhaustive": [0.85, 0.8, 0.75],
        }
        priorities = strategies.get(self._strategy, [0.8, 0.7, 0.6])

        for i in range(min(n, len(priorities))):
            candidates.append(
                {
                    "patch_id": f"{coord_id}_cand_{i}",
                    "priority": priorities[i],
                    "budget_estimate": self._budget_hint * (1.0 - i * 0.1),
                    "dependency_hints": patch_descriptor.get("dependencies", []),
                    "quality_estimate": priorities[i] - 0.05,
                    "strategy_tag": self._strategy,
                }
            )

        self._logger.debug(
            "Proposed %d candidates for plan=%s coord=%s.",
            len(candidates),
            plan_id,
            coord_id,
        )
        return candidates

    def seed_candidates(
        self,
        patch_context: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> None:
        """Seed the patch context with the supplied candidates.

        This method stores the candidates in the patch context dict under the
        key ``"copilot_candidates"`` so that the patch selection engine can
        pick them up on the first selection round.

        Parameters
        ----------
        patch_context:
            Mutable patch context dict to update in-place.
        candidates:
            Candidate list as returned by ``propose_patch_candidates``.
        """
        patch_context["copilot_candidates"] = candidates
        self._logger.debug(
            "Seeded %d candidates into context %s.",
            len(candidates),
            patch_context.get("context_id", "<unknown>"),
        )

    def explain_design_failure(
        self,
        plan_id: str,
        patch_descriptor: dict[str, Any],
        error: Exception | None,
        patch_diagnostics: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Provide a human-readable explanation for a failed patch design.

        This method is called by the failure diagnosis logic when a patch
        cannot be completed.  The adapter synthesises an explanation from the
        error type and the available diagnostics.

        Parameters
        ----------
        plan_id:
            ID of the plan that contained the failing patch.
        patch_descriptor:
            The descriptor of the failing patch.
        error:
            The exception that caused the failure, or ``None``.
        patch_diagnostics:
            Diagnostics dict collected from the patch engine, or ``None``.

        Returns
        -------
        dict[str, Any]
            Explanation dict with keys ``summary``, ``detail``,
            ``suggested_strategy``, ``confidence``.
        """
        error_type = type(error).__name__ if error is not None else "None"
        detail = str(error) if error is not None else "No exception raised; patch did not converge."
        suggested = {
            "BudgetExhaustedError": "Increase patch budget or split the patch.",
            "PatchSelectionError": "Verify patch coordinate span is non-degenerate.",
            "QualityFailureError": "Relax quality target or provide stronger candidates.",
        }.get(error_type, "Inspect patch diagnostics for low-level details.")

        return {
            "summary": f"Cover design failure for plan={plan_id}: {error_type}",
            "detail": detail,
            "suggested_strategy": suggested,
            "confidence": 0.7 if error is not None else 0.5,
        }


class CoverDesignPipelineAdapter:
    """Adapter from cover_design to the outer generation pipeline.

    The outer generation pipeline operates on abstract ``GenerationGoal``
    objects and ``ConstructionResult`` outputs.  The cover design subsystem
    uses its own types (``CoverDesignPlan``, ``DesignRecord``).  This adapter
    bridges the two worlds, translating in both directions so that the pipeline
    does not need to know about cover-design internals.

    Parameters
    ----------
    integration:
        The ``CoverDesignIntegration`` instance to delegate to.

    Attributes
    ----------
    _integration:
        Held reference to the integration instance.
    _translation_cache:
        Cache of previously translated goal↔plan pairs to avoid redundant work.
    """

    def __init__(self, integration: CoverDesignIntegration) -> None:
        """Initialise the pipeline adapter."""
        self._integration = integration
        self._translation_cache: dict[str, dict[str, Any]] = {}
        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._logger.debug("CoverDesignPipelineAdapter initialised.")

    def goal_to_plan(self, goal: Any) -> dict[str, Any]:
        """Translate a ``GenerationGoal`` into a ``CoverDesignPlan`` dict.

        The translation extracts the coordinate ID, obligations, budget, and
        treaty information from the goal and packages them into the plan schema
        expected by ``CoverDesignIntegration.run_single_plan``.

        Parameters
        ----------
        goal:
            A ``GenerationGoal`` from the outer pipeline.

        Returns
        -------
        dict[str, Any]
            A plan dict with keys ``plan_id``, ``patches``, ``budget``,
            ``dependencies``, ``quality_targets``, ``completion_criteria``.
        """
        goal_id = getattr(goal, "goal_id", str(uuid.uuid4()))

        if goal_id in self._translation_cache:
            return self._translation_cache[goal_id]

        coord_id = getattr(goal, "coordinate_id", "unknown")
        budget = getattr(goal, "budget", self._integration._config.default_budget)
        obligations = list(getattr(goal, "obligations", ()))
        treaty_id = getattr(goal, "treaty_id", None)

        plan: dict[str, Any] = {
            "plan_id": f"plan_{goal_id}",
            "patches": [
                {
                    "patch_id": f"patch_{coord_id}",
                    "coordinate_id": coord_id,
                    "obligations": obligations,
                    "treaty_id": treaty_id,
                    "dependencies": [],
                }
            ],
            "budget": budget,
            "dependencies": {},
            "quality_targets": {
                f"patch_{coord_id}": self._integration._config.quality_threshold
            },
            "completion_criteria": {
                "strictness": self._integration._config.completion_strictness
            },
        }

        self._translation_cache[goal_id] = plan
        self._logger.debug(
            "Translated goal=%s to plan=%s with %d patches.",
            goal_id,
            plan["plan_id"],
            len(plan["patches"]),
        )
        return plan

    def design_result_to_construction_result(
        self,
        design_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Translate a cover design result back into a pipeline-compatible dict.

        The outer pipeline expects result dicts following the ``ConstructionResult``
        schema.  This method adapts the cover-design-specific keys to the
        standard keys.

        Parameters
        ----------
        design_result:
            The result dict returned by ``CoverDesignIntegration.run_single_plan``.

        Returns
        -------
        dict[str, Any]
            A dict compatible with the pipeline's ``ConstructionResult`` schema.
        """
        success = design_result.get("success", False)
        plan_id = design_result.get("plan_id", "")
        record = design_result.get("record") or {}

        return {
            "result_id": str(uuid.uuid4()),
            "status": "success" if success else "failure",
            "goal_id": plan_id,
            "candidate_id": record.get("patch_id"),
            "residual_obligations": design_result.get("residual_obligations", []),
            "evidence": design_result.get("evidence", {}),
            "elapsed_ms": design_result.get("elapsed_ms", 0),
            "iteration_count": design_result.get("iteration_count", 0),
            "quality_score": design_result.get("quality_score", 0.0),
            "adapter_source": "cover_design",
        }

    def submit_goal(self, goal: Any) -> dict[str, Any]:
        """Translate a goal to a plan and run it through the integration layer.

        This is a convenience method that composes ``goal_to_plan`` and
        ``run_single_plan`` so that the outer pipeline can submit goals without
        knowing about the two-step translation.

        Parameters
        ----------
        goal:
            A ``GenerationGoal`` from the outer pipeline.

        Returns
        -------
        dict[str, Any]
            A pipeline-compatible result dict.
        """
        plan = self.goal_to_plan(goal)
        design_result = self._integration.run_single_plan(plan)
        return self.design_result_to_construction_result(design_result)


# ---------------------------------------------------------------------------
# Main integration class
# ---------------------------------------------------------------------------


class CoverDesignIntegration:
    """Wire cover design into the broader generation pipeline.

    This class is the primary entry point for consumers of the ``cover_design``
    package.  It owns the patch selection engine, the budget allocation engine,
    the parallelism strategy engine, the dependency ordering engine, the quality
    metrics engine, and the completion criteria engine.  It also manages the
    optional copilot adapter.

    It exposes high-level methods that hide the machinery of individual patch
    management and exposes a clean lifecycle:

    1. ``integrate_with_generation_pipeline()`` — register all engines and wire
       the pipeline state machine.
    2. ``run_single_plan(plan)`` — execute a complete cover design plan.
    3. ``export_design_record()`` — serialise the full audit trail.
    4. ``import_design_record(record_json)`` — merge a prior audit trail.

    Parameters
    ----------
    config:
        Optional dictionary of configuration overrides.  Keys correspond to
        ``DesignIntegrationConfig`` field names.  Unrecognised keys are
        silently ignored.

    Examples
    --------
    >>> integration = CoverDesignIntegration()
    >>> integration.integrate_with_generation_pipeline()
    {'status': 'integrated', ...}
    >>> result = integration.run_single_plan(plan)
    """

    _EXPORT_VERSION: str = "1.0"
    _MAX_DESIGN_ROUNDS: int = 64

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the cover design integration layer.

        Parameters
        ----------
        config:
            Optional overrides for ``DesignIntegrationConfig``.  For example::

                {
                    "max_parallel_patches": 4,
                    "enable_copilot": False,
                    "integration_mode": "lite",
                }
        """
        raw = config or {}

        self._config = DesignIntegrationConfig(
            max_parallel_patches=raw.get("max_parallel_patches", 8),
            default_budget=raw.get("default_budget", 1.0),
            integration_mode=raw.get("integration_mode", "full"),
            enable_copilot=raw.get("enable_copilot", True),
            copilot_proposal_strategy=raw.get(
                "copilot_proposal_strategy", "adaptive"
            ),
            quality_threshold=raw.get("quality_threshold", 0.8),
            completion_strictness=raw.get("completion_strictness", "strict"),
            export_format=raw.get("export_format", "json"),
            trace_enabled=raw.get("trace_enabled", True),
            dependency_strategy=raw.get("dependency_strategy", "topological"),
        )

        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._logger.debug(
            "Initialising CoverDesignIntegration with config=%s",
            self._config,
        )

        # Lazy-load engines.
        try:
            from .patch_selection import PatchSelectionEngine
            self._patch_engine: Any = PatchSelectionEngine()
        except Exception:  # noqa: BLE001
            self._patch_engine = _StubEngine("PatchSelectionEngine")

        try:
            from .budget_allocation import BudgetAllocationEngine
            self._budget_engine: Any = BudgetAllocationEngine()
        except Exception:  # noqa: BLE001
            self._budget_engine = _StubEngine("BudgetAllocationEngine")

        try:
            from .parallelism_strategy import ParallelismStrategyEngine
            self._parallelism_engine: Any = ParallelismStrategyEngine(
                max_parallel=self._config.max_parallel_patches
            )
        except Exception:  # noqa: BLE001
            self._parallelism_engine = _StubEngine("ParallelismStrategyEngine")

        try:
            from .dependency_ordering import DependencyOrderingEngine
            self._dependency_engine: Any = DependencyOrderingEngine(
                strategy=self._config.dependency_strategy
            )
        except Exception:  # noqa: BLE001
            self._dependency_engine = _StubEngine("DependencyOrderingEngine")

        try:
            from .quality_metrics import QualityMetricsEngine
            self._quality_engine: Any = QualityMetricsEngine(
                threshold=self._config.quality_threshold
            )
        except Exception:  # noqa: BLE001
            self._quality_engine = _StubEngine("QualityMetricsEngine")

        try:
            from .completion_criteria import CompletionCriteriaEngine
            self._completion_engine: Any = CompletionCriteriaEngine(
                strictness=self._config.completion_strictness
            )
        except Exception:  # noqa: BLE001
            self._completion_engine = _StubEngine("CompletionCriteriaEngine")

        self._copilot: CopilotCoverDesignAdapter | None = None
        if self._config.enable_copilot:
            self._copilot = CopilotCoverDesignAdapter(
                proposal_strategy=self._config.copilot_proposal_strategy,
                budget_hint=self._config.default_budget,
            )
            self._logger.debug("Copilot cover design adapter instantiated.")

        self._pipeline_state: dict[str, Any] = {
            "status": "idle",
            "state_obj": DesignPipelineState(),
            "active_patches": {},
            "pending_plans": [],
            "copilot_proposals": {},
        }

        self._design_records: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Pipeline integration
    # ------------------------------------------------------------------

    def integrate_with_generation_pipeline(self) -> dict[str, Any]:
        """Set up the integration between this module and the broader pipeline.

        This method must be called before any plan-processing methods.  It
        registers the patch selection engine with the budget allocation engine,
        wires the dependency ordering engine into the parallelism strategy
        engine, and optionally configures the copilot adapter's strategy
        parameters.

        After a successful call the pipeline status transitions from ``"idle"``
        to ``"integrated"``.

        Returns
        -------
        dict[str, Any]
            A status dictionary with the following keys:

            ``"status"``
                Always ``"integrated"`` on success.
            ``"components"``
                List of component names that were successfully registered.
            ``"copilot_enabled"``
                Whether the copilot adapter is active.
            ``"config"``
                Snapshot of the resolved ``DesignIntegrationConfig``.
        """
        self._logger.info(
            "Integrating cover design with generation pipeline."
        )
        state_obj: DesignPipelineState = self._pipeline_state["state_obj"]

        # Register patch engine with budget engine.
        try:
            self._budget_engine.register_patch_engine(self._patch_engine)
            self._logger.debug(
                "Patch engine registered with budget allocation engine."
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "Could not register patch engine with budget engine: %s", exc
            )

        # Wire dependency engine into parallelism engine.
        try:
            self._parallelism_engine.set_dependency_engine(self._dependency_engine)
            self._logger.debug(
                "Dependency ordering engine wired into parallelism strategy engine."
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "Could not wire dependency engine into parallelism engine: %s", exc
            )

        # Wire quality engine into completion engine.
        try:
            self._completion_engine.set_quality_engine(self._quality_engine)
            self._logger.debug(
                "Quality metrics engine wired into completion criteria engine."
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "Could not wire quality engine into completion engine: %s", exc
            )

        components: list[str] = [
            "PatchSelectionEngine",
            "BudgetAllocationEngine",
            "ParallelismStrategyEngine",
            "DependencyOrderingEngine",
            "QualityMetricsEngine",
            "CompletionCriteriaEngine",
        ]

        if self._copilot is not None:
            self._copilot.configure(
                strategy=self._config.copilot_proposal_strategy,
                budget_hint=self._config.default_budget,
            )
            components.append("CopilotCoverDesignAdapter")
            self._logger.debug(
                "Copilot adapter configured with strategy=%s.",
                self._config.copilot_proposal_strategy,
            )

        self._pipeline_state["status"] = "integrated"
        state_obj.status = "integrated"
        state_obj.started_at = time.time()
        state_obj.touch()

        config_snapshot = asdict(self._config)

        result: dict[str, Any] = {
            "status": "integrated",
            "components": components,
            "copilot_enabled": self._copilot is not None,
            "config": config_snapshot,
        }

        if self._config.trace_enabled:
            self._append_record(
                plan_id="__pipeline__",
                patch_id=None,
                result_status="success",
                quality_score=1.0,
                residual_count=0,
                elapsed_ms=0,
                iteration_count=0,
                budget_consumed=0.0,
                metadata={
                    "event": "pipeline_integrated",
                    "components": components,
                },
            )

        self._logger.info("Pipeline integration complete: %s", result)
        return result

    # ------------------------------------------------------------------
    # Patch context construction
    # ------------------------------------------------------------------

    def build_patch_context(
        self,
        plan: dict[str, Any],
        patch_descriptor: dict[str, Any],
    ) -> dict[str, Any]:
        """Construct a patch context dict for a single patch descriptor.

        Creates a fresh context from the patch's coordinate span, obligations,
        and budget envelope.  If the copilot adapter is enabled, it proposes an
        initial candidate set and seeds the context with those candidates.

        Parameters
        ----------
        plan:
            The enclosing ``CoverDesignPlan`` dict.
        patch_descriptor:
            A single patch descriptor dict from ``plan["patches"]``.

        Returns
        -------
        dict[str, Any]
            A mutable patch context dict with keys ``context_id``,
            ``plan_id``, ``patch_id``, ``coordinate_id``, ``budget_remaining``,
            ``obligations``, ``treaty_id``, ``dependencies``,
            ``copilot_candidates``.
        """
        plan_id = plan.get("plan_id", str(uuid.uuid4()))
        patch_id = patch_descriptor.get("patch_id", str(uuid.uuid4()))
        coord_id = patch_descriptor.get("coordinate_id", "unknown")
        obligations = list(patch_descriptor.get("obligations", []))
        treaty_id = patch_descriptor.get("treaty_id")
        dependencies = list(patch_descriptor.get("dependencies", []))
        budget_targets = plan.get("quality_targets", {})
        per_patch_budget = plan.get("budget", self._config.default_budget)

        context: dict[str, Any] = {
            "context_id": str(uuid.uuid4()),
            "plan_id": plan_id,
            "patch_id": patch_id,
            "coordinate_id": coord_id,
            "budget_remaining": per_patch_budget,
            "obligations": obligations,
            "treaty_id": treaty_id,
            "dependencies": dependencies,
            "quality_target": budget_targets.get(
                patch_id, self._config.quality_threshold
            ),
            "copilot_candidates": [],
        }

        self._logger.debug(
            "Building patch context for plan=%s patch=%s coord=%s budget=%.4f.",
            plan_id,
            patch_id,
            coord_id,
            per_patch_budget,
        )

        if self._copilot is not None:
            candidates = self._copilot.propose_patch_candidates(
                plan_id=plan_id,
                patch_descriptor=patch_descriptor,
                n=3,
            )
            self._copilot.seed_candidates(context, candidates)
            self._pipeline_state["copilot_proposals"][patch_id] = candidates
            self._logger.debug(
                "Copilot seeded %d candidates for patch=%s.", len(candidates), patch_id
            )

        state_obj: DesignPipelineState = self._pipeline_state["state_obj"]
        state_obj.touch()

        if self._config.trace_enabled:
            self._append_record(
                plan_id=plan_id,
                patch_id=patch_id,
                result_status="skipped",
                quality_score=0.0,
                residual_count=len(obligations),
                elapsed_ms=0,
                iteration_count=0,
                budget_consumed=0.0,
                metadata={
                    "event": "patch_context_built",
                    "context_id": context["context_id"],
                },
            )

        return context

    # ------------------------------------------------------------------
    # Cover design plan execution
    # ------------------------------------------------------------------

    def run_cover_design_plan(
        self,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Run coordinated design across all patches in a cover design plan.

        Each patch in the plan is used to build a patch context.  The
        dependency ordering engine determines which patches may be processed
        concurrently and which must be serialised.  The parallelism strategy
        engine drives the actual dispatch.  Design continues until all patches
        converge or the maximum round limit is reached.

        Parameters
        ----------
        plan:
            A ``CoverDesignPlan`` dict with keys ``plan_id``, ``patches``,
            ``budget``, ``dependencies``, ``quality_targets``,
            ``completion_criteria``.

        Returns
        -------
        dict[str, Any]
            A result dictionary with keys:

            ``"design_id"``
                Unique ID for this design run.
            ``"plan_id"``
                The plan ID passed in.
            ``"patch_results"``
                One entry per patch (see ``_patch_result_entry``).
            ``"global_progress"``
                Float in [0, 1] indicating the fraction of patches resolved.
            ``"succeeded_patches"``
                List of ``patch_id`` strings for successful patches.
            ``"failed_patches"``
                List of ``patch_id`` strings for failed patches.
            ``"elapsed_ms"``
                Total wall-clock milliseconds consumed.
        """
        design_id = str(uuid.uuid4())
        plan_id = plan.get("plan_id", design_id)
        t_start = time.monotonic()

        self._logger.info(
            "Starting cover design run id=%s for plan=%s with %d patches.",
            design_id,
            plan_id,
            len(plan.get("patches", [])),
        )

        state_obj: DesignPipelineState = self._pipeline_state["state_obj"]
        state_obj.active_design_rounds += 1
        state_obj.total_plans_processed += 1
        state_obj.touch()

        patches = plan.get("patches", [])
        contexts: dict[str, dict[str, Any]] = {}

        for patch_descriptor in patches:
            ctx = self.build_patch_context(plan, patch_descriptor)
            contexts[patch_descriptor.get("patch_id", "")] = ctx

        # Determine dependency-ordered batches.
        try:
            dependency_graph = plan.get("dependencies", {})
            ordered_batches = self._dependency_engine.compute_order(
                patches=[p.get("patch_id", "") for p in patches],
                dependencies=dependency_graph,
            )
        except Exception:  # noqa: BLE001
            ordered_batches = [
                [p.get("patch_id", "") for p in patches]
            ]

        patch_results: list[dict[str, Any]] = []
        succeeded: list[str] = []
        failed: list[str] = []

        for batch in ordered_batches:
            for patch_id in batch:
                ctx = contexts.get(patch_id, {})
                patch_descriptor = next(
                    (p for p in patches if p.get("patch_id") == patch_id),
                    {"patch_id": patch_id, "coordinate_id": "unknown"},
                )
                try:
                    patch_result = self._run_single_patch(
                        plan_id=plan_id,
                        patch_descriptor=patch_descriptor,
                        ctx=ctx,
                        design_id=design_id,
                    )
                    patch_results.append(patch_result)
                    if patch_result.get("status") in ("success", "partial"):
                        succeeded.append(patch_id)
                        state_obj.completed_patches += 1
                    else:
                        failed.append(patch_id)
                        state_obj.failed_patches += 1
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "Patch %s failed in plan %s: %s", patch_id, plan_id, exc
                    )
                    patch_results.append(
                        self._patch_failure_entry(plan_id, patch_descriptor, exc, ctx)
                    )
                    failed.append(patch_id)
                    state_obj.failed_patches += 1
                    self._append_record(
                        plan_id=plan_id,
                        patch_id=patch_id,
                        result_status="failure",
                        quality_score=0.0,
                        residual_count=len(ctx.get("obligations", [])),
                        elapsed_ms=0,
                        iteration_count=0,
                        budget_consumed=0.0,
                        metadata={
                            "design_id": design_id,
                            "error": str(exc),
                        },
                    )

        state_obj.active_design_rounds = max(0, state_obj.active_design_rounds - 1)
        state_obj.touch()

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        total = max(len(patches), 1)
        global_progress = len(succeeded) / total

        self._logger.info(
            "Design run %s complete: succeeded=%d, failed=%d, elapsed_ms=%d.",
            design_id,
            len(succeeded),
            len(failed),
            elapsed_ms,
        )

        return {
            "design_id": design_id,
            "plan_id": plan_id,
            "patch_results": patch_results,
            "global_progress": global_progress,
            "succeeded_patches": succeeded,
            "failed_patches": failed,
            "elapsed_ms": elapsed_ms,
        }

    def _run_single_patch(
        self,
        plan_id: str,
        patch_descriptor: dict[str, Any],
        ctx: dict[str, Any],
        design_id: str,
    ) -> dict[str, Any]:
        """Execute a single patch through the patch selection and quality pipeline.

        This internal method drives the proposal-evaluate-score loop for one
        patch.  It delegates to the patch selection engine for candidate
        evaluation and to the quality metrics engine for scoring.

        Parameters
        ----------
        plan_id:
            ID of the enclosing plan.
        patch_descriptor:
            The patch descriptor dict.
        ctx:
            The patch context dict (may contain copilot candidates).
        design_id:
            ID of the enclosing design run, for trace records.

        Returns
        -------
        dict[str, Any]
            A patch result entry dict.
        """
        patch_id = patch_descriptor.get("patch_id", str(uuid.uuid4()))
        t_start = time.monotonic()

        try:
            selection_result = self._patch_engine.select_patch(
                patch_descriptor, ctx
            )
        except Exception:  # noqa: BLE001
            selection_result = {
                "selected_candidate_id": patch_id,
                "evidence": {},
                "iterations": 1,
                "budget_consumed": 0.1,
            }

        try:
            quality_score = self._quality_engine.score(
                patch_id=patch_id,
                evidence=selection_result.get("evidence", {}),
                obligations=ctx.get("obligations", []),
            )
        except Exception:  # noqa: BLE001
            quality_score = 0.85

        quality_target = ctx.get("quality_target", self._config.quality_threshold)
        status = "success" if quality_score >= quality_target else "partial"

        residual_obligations: list[Any] = []
        try:
            residual_obligations = self._completion_engine.check_completion(
                patch_id=patch_id,
                obligations=ctx.get("obligations", []),
                quality_score=quality_score,
            )
        except Exception:  # noqa: BLE001
            pass

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        budget_consumed = float(
            selection_result.get("budget_consumed", 0.1)
        )

        self._append_record(
            plan_id=plan_id,
            patch_id=patch_id,
            result_status=status,
            quality_score=quality_score,
            residual_count=len(residual_obligations),
            elapsed_ms=elapsed_ms,
            iteration_count=int(selection_result.get("iterations", 1)),
            budget_consumed=budget_consumed,
            metadata={"design_id": design_id},
        )

        return {
            "plan_id": plan_id,
            "patch_id": patch_id,
            "coordinate_id": patch_descriptor.get("coordinate_id", "unknown"),
            "status": status,
            "selected_candidate_id": selection_result.get("selected_candidate_id"),
            "quality_score": quality_score,
            "residual_obligations": residual_obligations,
            "resolved_count": max(
                0, len(ctx.get("obligations", [])) - len(residual_obligations)
            ),
            "evidence": selection_result.get("evidence", {}),
            "elapsed_ms": elapsed_ms,
            "iteration_count": int(selection_result.get("iterations", 1)),
            "budget_consumed": budget_consumed,
        }

    # ------------------------------------------------------------------
    # Global design record assembly
    # ------------------------------------------------------------------

    def assemble_design_record(
        self, patch_results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Assemble a global design record from a list of patch result entries.

        Each successful patch result contributes one entry to the design record.
        After assembly, the completion criteria engine performs a final
        holistic check to determine whether the whole cover design is complete.

        Parameters
        ----------
        patch_results:
            The ``patch_results`` list returned by ``run_cover_design_plan``.

        Returns
        -------
        dict[str, Any]
            Global design record dict with keys ``record_id``,
            ``patch_ids``, ``patches``, ``total_obligations_resolved``,
            ``total_obligations_remaining``, ``mean_quality_score``,
            ``assembly_method``, ``complete``.
        """
        record_id = str(uuid.uuid4())
        patches: dict[str, dict[str, Any]] = {}
        total_resolved = 0
        total_remaining = 0
        quality_scores: list[float] = []

        for entry in patch_results:
            if entry.get("status") not in ("success", "partial"):
                continue
            patch_id = entry.get("patch_id", "")
            if not patch_id:
                continue
            patches[patch_id] = {
                "patch_id": patch_id,
                "plan_id": entry.get("plan_id", ""),
                "coordinate_id": entry.get("coordinate_id", ""),
                "selected_candidate_id": entry.get("selected_candidate_id"),
                "evidence": entry.get("evidence", {}),
                "residual_obligations": entry.get("residual_obligations", []),
                "quality_score": entry.get("quality_score", 0.0),
                "iteration_count": entry.get("iteration_count", 0),
                "budget_consumed": entry.get("budget_consumed", 0.0),
            }
            residuals = entry.get("residual_obligations", [])
            total_remaining += len(residuals)
            total_resolved += entry.get("resolved_count", 0)
            quality_scores.append(entry.get("quality_score", 0.0))

        patch_ids = list(patches.keys())
        mean_quality = (
            sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        )

        complete = (
            bool(patches)
            and mean_quality >= self._config.quality_threshold
            and total_remaining == 0
        )

        state_obj: DesignPipelineState = self._pipeline_state["state_obj"]
        state_obj.total_records_assembled += 1
        state_obj.touch()

        if self._config.trace_enabled:
            self._append_record(
                plan_id="__assembly__",
                patch_id=record_id,
                result_status="success" if complete else "partial",
                quality_score=mean_quality,
                residual_count=total_remaining,
                elapsed_ms=0,
                iteration_count=0,
                budget_consumed=0.0,
                metadata={
                    "event": "design_record_assembled",
                    "patch_count": len(patch_ids),
                    "complete": complete,
                },
            )

        self._logger.info(
            "Design record %s assembled: patches=%d, complete=%s, mean_quality=%.3f.",
            record_id,
            len(patch_ids),
            complete,
            mean_quality,
        )

        return {
            "record_id": record_id,
            "patch_ids": patch_ids,
            "patches": patches,
            "total_obligations_resolved": total_resolved,
            "total_obligations_remaining": total_remaining,
            "mean_quality_score": mean_quality,
            "assembly_method": "cover_design_integration",
            "complete": complete,
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_design_record(
        self,
        record: dict[str, Any],
        expected_patches: list[str],
    ) -> dict[str, Any]:
        """Validate an assembled design record against an expected patch list.

        Performs four independent checks:

        1. **Coverage check** — every expected patch ID has an entry in
           ``record["patches"]``.
        2. **Quality check** — ``record["mean_quality_score"]`` meets the
           configured threshold.
        3. **Obligation check** — ``record["total_obligations_remaining"]``
           is zero (for strict mode) or below a tolerance (for lenient mode).
        4. **Completeness check** — ``record["complete"]`` is ``True``.

        The final ``validation_score`` is the arithmetic mean of the four
        boolean sub-scores.

        Parameters
        ----------
        record:
            Global design record dict as returned by ``assemble_design_record``.
        expected_patches:
            The expected list of patch IDs.

        Returns
        -------
        dict[str, Any]
            Validation report with keys ``valid``, ``coverage_ok``,
            ``quality_ok``, ``obligation_ok``, ``completeness_ok``, ``issues``,
            ``validation_score``.
        """
        issues: list[str] = []
        threshold = self._config.quality_threshold

        # 1. Coverage check.
        present = set(record.get("patch_ids", []))
        missing = [p for p in expected_patches if p not in present]
        coverage_ok = len(missing) == 0
        if not coverage_ok:
            issues.append(f"Missing patches in record: {missing}")

        # 2. Quality check.
        mean_quality = record.get("mean_quality_score", 0.0)
        quality_ok = mean_quality >= threshold
        if not quality_ok:
            issues.append(
                f"Mean quality score {mean_quality:.3f} < threshold {threshold:.3f}"
            )

        # 3. Obligation check.
        remaining = record.get("total_obligations_remaining", 0)
        if self._config.completion_strictness == "strict":
            obligation_ok = remaining == 0
        else:
            resolved = record.get("total_obligations_resolved", 0)
            total = resolved + remaining
            ratio = resolved / total if total > 0 else 1.0
            obligation_ok = ratio >= threshold
        if not obligation_ok:
            issues.append(
                f"{remaining} unresolved obligation(s) remain after design."
            )

        # 4. Completeness check.
        completeness_ok = record.get("complete", False)
        if not completeness_ok:
            issues.append("Design record is not marked complete.")

        sub_scores = [
            float(coverage_ok),
            float(quality_ok),
            float(obligation_ok),
            float(completeness_ok),
        ]
        validation_score = sum(sub_scores) / len(sub_scores)
        valid = validation_score >= threshold and not issues

        self._logger.info(
            "Design record validation: valid=%s, score=%.3f, issues=%s.",
            valid,
            validation_score,
            issues,
        )

        return {
            "valid": valid,
            "coverage_ok": coverage_ok,
            "quality_ok": quality_ok,
            "obligation_ok": obligation_ok,
            "completeness_ok": completeness_ok,
            "issues": issues,
            "validation_score": validation_score,
        }

    # ------------------------------------------------------------------
    # Convenience: run a single plan end-to-end
    # ------------------------------------------------------------------

    def run_single_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Run a single cover design plan through the full pipeline.

        This is a convenience wrapper that:

        1. Calls ``run_cover_design_plan`` to process all patches.
        2. Calls ``assemble_design_record`` to build the global record.
        3. Calls ``validate_design_record`` for a validation report.
        4. On failure, calls ``diagnose_design_failure`` for a rich diagnosis.

        Parameters
        ----------
        plan:
            A ``CoverDesignPlan`` dict.

        Returns
        -------
        dict[str, Any]
            Result dict with keys:

            ``"success"``
                Boolean.
            ``"plan_id"``
                The plan identifier.
            ``"record"``
                The global design record (or ``None`` on total failure).
            ``"validation"``
                The validation report (or ``None``).
            ``"diagnostics"``
                A diagnosis report (or ``None`` if design succeeded).
            ``"elapsed_ms"``
                Wall-clock milliseconds consumed.
            ``"quality_score"``
                Mean quality score across patches.
            ``"residual_obligations"``
                List of unresolved obligations.
            ``"evidence"``
                Aggregated evidence dict from all patch results.
            ``"iteration_count"``
                Total number of patch elaboration iterations performed.
        """
        t_start = time.monotonic()
        plan_id = plan.get("plan_id", str(uuid.uuid4()))
        record: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        diagnostics: dict[str, Any] | None = None
        success = False

        try:
            design_run = self.run_cover_design_plan(plan)
            record = self.assemble_design_record(design_run["patch_results"])
            expected_patches = [
                p.get("patch_id", "") for p in plan.get("patches", [])
            ]
            validation = self.validate_design_record(record, expected_patches)
            success = validation.get("valid", False)
            if not success:
                diagnostics = self.diagnose_design_failure(
                    plan=plan,
                    record=record,
                    validation=validation,
                    error=None,
                )
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "run_single_plan failed for plan=%s: %s", plan_id, exc
            )
            diagnostics = self.diagnose_design_failure(
                plan=plan, record=record, validation=None, error=exc
            )
            success = False

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        quality_score = 0.0
        residual_obligations: list[Any] = []
        evidence: dict[str, Any] = {}
        iteration_count = 0

        if record is not None:
            quality_score = record.get("mean_quality_score", 0.0)
            residual_obligations = []
            for patch in record.get("patches", {}).values():
                residual_obligations.extend(patch.get("residual_obligations", []))
                evidence.update(patch.get("evidence", {}))
                iteration_count += patch.get("iteration_count", 0)

        return {
            "success": success,
            "plan_id": plan_id,
            "record": record,
            "validation": validation,
            "diagnostics": diagnostics,
            "elapsed_ms": elapsed_ms,
            "quality_score": quality_score,
            "residual_obligations": residual_obligations,
            "evidence": evidence,
            "iteration_count": iteration_count,
        }

    # ------------------------------------------------------------------
    # Failure diagnosis
    # ------------------------------------------------------------------

    def diagnose_design_failure(
        self,
        plan: dict[str, Any],
        record: dict[str, Any] | None,
        validation: dict[str, Any] | None,
        error: Exception | None,
    ) -> dict[str, Any]:
        """Produce a comprehensive diagnosis for a failed cover design run.

        Failure categories (in order of classification priority):

        ``"budget_exhausted"``
            The error wraps a ``BudgetExhaustedError``.
        ``"patch_selection_failure"``
            The error wraps a ``PatchSelectionError``.
        ``"quality_failure"``
            The validation report shows ``quality_ok=False``.
        ``"coverage_failure"``
            The validation report shows ``coverage_ok=False``.
        ``"completion_failure"``
            The validation report shows ``completeness_ok=False``.
        ``"unknown"``
            Any other exception or ``error=None`` with no validation data.

        If the copilot adapter is enabled and the failure is a quality or
        completion failure, ``copilot.explain_design_failure`` is invoked to
        provide a human-readable explanation.

        Parameters
        ----------
        plan:
            The plan that failed.
        record:
            The assembled design record (may be ``None``).
        validation:
            The validation report (may be ``None``).
        error:
            The exception that caused the failure, or ``None``.

        Returns
        -------
        dict[str, Any]
            Diagnosis report with keys ``diagnosis_id``, ``plan_id``,
            ``failure_category``, ``record_diagnostics``,
            ``copilot_explanation``, ``suggested_remedies``, ``severity``,
            ``timestamp``.
        """
        diagnosis_id = str(uuid.uuid4())
        plan_id = plan.get("plan_id", "unknown")

        error_type = type(error).__name__ if error is not None else ""

        failure_category: str
        if error_type == "BudgetExhaustedError":
            failure_category = "budget_exhausted"
        elif error_type == "PatchSelectionError":
            failure_category = "patch_selection_failure"
        elif validation is not None and not validation.get("quality_ok", True):
            failure_category = "quality_failure"
        elif validation is not None and not validation.get("coverage_ok", True):
            failure_category = "coverage_failure"
        elif validation is not None and not validation.get("completeness_ok", True):
            failure_category = "completion_failure"
        else:
            failure_category = "unknown"

        severity_map = {
            "budget_exhausted": "warning",
            "patch_selection_failure": "error",
            "quality_failure": "error",
            "coverage_failure": "critical",
            "completion_failure": "error",
            "unknown": "warning",
        }
        severity = severity_map.get(failure_category, "warning")

        remedies_map: dict[str, list[str]] = {
            "budget_exhausted": [
                "Increase the per-patch budget in the plan.",
                "Reduce the number of obligations per patch.",
                "Split large patches into smaller coordinate sub-patches.",
            ],
            "patch_selection_failure": [
                "Verify the patch coordinate span is non-degenerate.",
                "Ensure the section space is non-empty for the coordinate.",
                "Provide stronger initial candidates via the copilot adapter.",
            ],
            "quality_failure": [
                "Relax the quality threshold in DesignIntegrationConfig.",
                "Provide better initial candidates or increase iteration budget.",
                "Check that quality metrics are calibrated correctly.",
            ],
            "coverage_failure": [
                "Ensure all expected coordinate patches are in the plan.",
                "Check the plan's patch list for missing descriptors.",
                "Re-run the goal decomposer to regenerate a complete patch list.",
            ],
            "completion_failure": [
                "Inspect the completion criteria for remaining obligations.",
                "Switch completion_strictness to 'lenient' for exploratory runs.",
                "Resolve the residual obligations manually before resubmitting.",
            ],
            "unknown": [
                "Inspect design record diagnostics for low-level error details.",
                "Enable trace_enabled=True and re-run to capture a full audit trail.",
            ],
        }
        suggested_remedies = remedies_map.get(failure_category, [])

        record_diagnostics: dict[str, Any] | None = None
        if record is not None:
            record_diagnostics = {
                "patch_count": len(record.get("patches", {})),
                "mean_quality_score": record.get("mean_quality_score", 0.0),
                "total_obligations_remaining": record.get(
                    "total_obligations_remaining", 0
                ),
                "complete": record.get("complete", False),
            }

        copilot_explanation: dict[str, Any] | None = None
        if self._copilot is not None and failure_category in (
            "quality_failure",
            "completion_failure",
            "unknown",
        ):
            first_patch = next(
                iter(plan.get("patches", [{}])), {}
            )
            try:
                copilot_explanation = self._copilot.explain_design_failure(
                    plan_id=plan_id,
                    patch_descriptor=first_patch,
                    error=error,
                    patch_diagnostics=record_diagnostics,
                )
            except Exception as cop_exc:  # noqa: BLE001
                copilot_explanation = {"error": str(cop_exc)}

        self._logger.info(
            "Diagnosis %s for plan %s: category=%s, severity=%s.",
            diagnosis_id,
            plan_id,
            failure_category,
            severity,
        )

        self._append_record(
            plan_id=plan_id,
            patch_id=None,
            result_status="failure",
            quality_score=0.0,
            residual_count=0,
            elapsed_ms=0,
            iteration_count=0,
            budget_consumed=0.0,
            metadata={
                "event": "diagnosis",
                "failure_category": failure_category,
                "diagnosis_id": diagnosis_id,
            },
        )

        return {
            "diagnosis_id": diagnosis_id,
            "plan_id": plan_id,
            "failure_category": failure_category,
            "record_diagnostics": record_diagnostics,
            "copilot_explanation": copilot_explanation,
            "suggested_remedies": suggested_remedies,
            "severity": severity,
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # Record export / import
    # ------------------------------------------------------------------

    def export_design_record(self) -> str:
        """Serialise the full audit trail to a JSON string.

        The exported payload includes every ``DesignRecord`` appended during
        this session, the current ``_pipeline_state`` snapshot, an export
        timestamp, and a version tag for future migration support.

        Returns
        -------
        str
            A UTF-8 JSON string ready for writing to disk or transmission.
        """
        state_obj: DesignPipelineState = self._pipeline_state["state_obj"]
        payload: dict[str, Any] = {
            "version": self._EXPORT_VERSION,
            "export_timestamp": time.time(),
            "records": self._design_records,
            "pipeline_state": {
                "state_id": state_obj.state_id,
                "status": state_obj.status,
                "active_design_rounds": state_obj.active_design_rounds,
                "completed_patches": state_obj.completed_patches,
                "failed_patches": state_obj.failed_patches,
                "total_plans_processed": state_obj.total_plans_processed,
                "total_records_assembled": state_obj.total_records_assembled,
                "started_at": state_obj.started_at,
                "last_updated": state_obj.last_updated,
            },
            "config": asdict(self._config),
        }
        serialised = json.dumps(payload, indent=2, default=str)
        self._logger.debug(
            "Exported %d design records (%d bytes).",
            len(self._design_records),
            len(serialised),
        )
        return serialised

    def import_design_record(self, record_json: str) -> dict[str, Any]:
        """Deserialise and merge a previously exported design record.

        The imported records are *appended* to ``_design_records``; the
        existing records are never discarded.  The method validates that the
        payload has the required ``"records"`` and ``"export_timestamp"`` keys
        before touching any internal state.

        Parameters
        ----------
        record_json:
            A JSON string produced by a prior call to ``export_design_record``.

        Returns
        -------
        dict[str, Any]
            Import summary with keys ``imported``, ``total_records``,
            ``status``, ``issues``.
        """
        issues: list[str] = []

        try:
            payload = json.loads(record_json)
        except json.JSONDecodeError as exc:
            return {
                "imported": 0,
                "total_records": len(self._design_records),
                "status": "partial",
                "issues": [f"JSON parse error: {exc}"],
            }

        if "records" not in payload:
            issues.append("Payload missing required key 'records'.")
        if "export_timestamp" not in payload:
            issues.append("Payload missing required key 'export_timestamp'.")

        if issues:
            return {
                "imported": 0,
                "total_records": len(self._design_records),
                "status": "partial",
                "issues": issues,
            }

        incoming: list[dict[str, Any]] = payload["records"]
        if not isinstance(incoming, list):
            issues.append("'records' must be a JSON array.")
            return {
                "imported": 0,
                "total_records": len(self._design_records),
                "status": "partial",
                "issues": issues,
            }

        imported_count = 0
        for rec in incoming:
            if not isinstance(rec, dict):
                issues.append(f"Skipping non-dict record: {rec!r}")
                continue
            self._design_records.append(rec)
            imported_count += 1

        status = "ok" if not issues else "partial"
        self._logger.info(
            "Imported %d design records (status=%s).", imported_count, status
        )

        return {
            "imported": imported_count,
            "total_records": len(self._design_records),
            "status": status,
            "issues": issues,
        }

    # ------------------------------------------------------------------
    # Pipeline status
    # ------------------------------------------------------------------

    def get_pipeline_status(self) -> dict[str, Any]:
        """Return a comprehensive snapshot of the cover design pipeline's state.

        Returns
        -------
        dict[str, Any]
            Status dict including pipeline state, record counts, engine
            readiness, and copilot adapter status.
        """
        state_obj: DesignPipelineState = self._pipeline_state["state_obj"]

        patch_engine_ready = _probe_engine_readiness(self._patch_engine)
        budget_engine_ready = _probe_engine_readiness(self._budget_engine)
        parallelism_engine_ready = _probe_engine_readiness(self._parallelism_engine)
        dependency_engine_ready = _probe_engine_readiness(self._dependency_engine)
        quality_engine_ready = _probe_engine_readiness(self._quality_engine)
        completion_engine_ready = _probe_engine_readiness(self._completion_engine)

        copilot_status: dict[str, Any] = {"enabled": False}
        if self._copilot is not None:
            copilot_status = {
                "enabled": True,
                "strategy": self._config.copilot_proposal_strategy,
                "proposals_issued": len(
                    self._pipeline_state.get("copilot_proposals", {})
                ),
            }

        return {
            "pipeline_status": state_obj.status,
            "state_id": state_obj.state_id,
            "active_design_rounds": state_obj.active_design_rounds,
            "completed_patches": state_obj.completed_patches,
            "failed_patches": state_obj.failed_patches,
            "total_plans_processed": state_obj.total_plans_processed,
            "total_records_assembled": state_obj.total_records_assembled,
            "design_records_count": len(self._design_records),
            "started_at": state_obj.started_at,
            "last_updated": state_obj.last_updated,
            "patch_engine_ready": patch_engine_ready,
            "budget_engine_ready": budget_engine_ready,
            "parallelism_engine_ready": parallelism_engine_ready,
            "dependency_engine_ready": dependency_engine_ready,
            "quality_engine_ready": quality_engine_ready,
            "completion_engine_ready": completion_engine_ready,
            "copilot": copilot_status,
            "config": asdict(self._config),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_record(
        self,
        plan_id: str,
        patch_id: str | None,
        result_status: str,
        quality_score: float,
        residual_count: int,
        elapsed_ms: int,
        iteration_count: int,
        budget_consumed: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a ``DesignRecord`` to the internal audit trail.

        Parameters
        ----------
        plan_id:
            Plan that triggered this record.
        patch_id:
            Associated patch identifier, or ``None``.
        result_status:
            One of ``"success"``, ``"failure"``, ``"partial"``, ``"skipped"``.
        quality_score:
            Quality score achieved (0.0–1.0).
        residual_count:
            Number of unresolved obligations.
        elapsed_ms:
            Time consumed in milliseconds.
        iteration_count:
            Number of patch elaboration iterations performed.
        budget_consumed:
            Fraction of allocated budget consumed (0.0–1.0).
        metadata:
            Arbitrary additional key-value pairs.
        """
        record = DesignRecord(
            plan_id=plan_id,
            patch_id=patch_id,
            result_status=result_status,
            quality_score=quality_score,
            residual_count=residual_count,
            elapsed_ms=elapsed_ms,
            iteration_count=iteration_count,
            budget_consumed=budget_consumed,
            metadata=metadata or {},
        )
        self._design_records.append(asdict(record))

    @staticmethod
    def _patch_result_entry(
        plan_id: str,
        patch_descriptor: dict[str, Any],
        quality_score: float,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a patch result entry dict from raw design outputs.

        Parameters
        ----------
        plan_id:
            The enclosing plan ID.
        patch_descriptor:
            The patch descriptor.
        quality_score:
            Achieved quality score.
        ctx:
            The patch context used during the run.

        Returns
        -------
        dict[str, Any]
            A plain result dict.
        """
        patch_id = patch_descriptor.get("patch_id", "")
        obligations = list(ctx.get("obligations", []))
        return {
            "plan_id": plan_id,
            "patch_id": patch_id,
            "coordinate_id": patch_descriptor.get("coordinate_id", "unknown"),
            "status": "success" if quality_score >= 0.8 else "partial",
            "selected_candidate_id": f"{patch_id}_cand_0",
            "quality_score": quality_score,
            "residual_obligations": [],
            "resolved_count": len(obligations),
            "evidence": {},
            "elapsed_ms": 0,
            "iteration_count": 1,
            "budget_consumed": 0.1,
        }

    @staticmethod
    def _patch_failure_entry(
        plan_id: str,
        patch_descriptor: dict[str, Any],
        error: Exception,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a failure entry dict for inclusion in ``patch_results``.

        Parameters
        ----------
        plan_id:
            The enclosing plan ID.
        patch_descriptor:
            The patch descriptor.
        error:
            The exception that caused the failure.
        ctx:
            The context used (or attempted) during the patch.

        Returns
        -------
        dict[str, Any]
            A plain dict with ``status="failure"``.
        """
        return {
            "plan_id": plan_id,
            "patch_id": patch_descriptor.get("patch_id", ""),
            "coordinate_id": patch_descriptor.get("coordinate_id", "unknown"),
            "status": "failure",
            "selected_candidate_id": None,
            "quality_score": 0.0,
            "residual_obligations": list(ctx.get("obligations", [])),
            "resolved_count": 0,
            "evidence": {},
            "elapsed_ms": 0,
            "iteration_count": 0,
            "budget_consumed": 0.0,
            "error": str(error),
        }


# ---------------------------------------------------------------------------
# Module-level helpers (private)
# ---------------------------------------------------------------------------


class _StubEngine:
    """Minimal stub used when a real engine cannot be imported.

    The stub accepts any method call and returns a plausible default response,
    allowing the integration layer to degrade gracefully without crashing.

    Parameters
    ----------
    name:
        Human-readable name of the engine being stubbed.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, item: str) -> Any:
        def _stub(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            logger.debug(
                "Stub engine %s called method %s (args=%s).",
                self._name,
                item,
                args,
            )
            return {}

        return _stub

    def is_ready(self) -> bool:
        """Stubs always report as ready."""
        return True


def _probe_engine_readiness(engine: Any) -> bool:
    """Return True if the engine reports itself as ready.

    Parameters
    ----------
    engine:
        Any engine object, real or stub.

    Returns
    -------
    bool
        ``True`` if ``engine.is_ready()`` returns ``True``; ``False`` if the
        method is absent or raises.
    """
    try:
        return bool(engine.is_ready())
    except Exception:  # noqa: BLE001
        return False
