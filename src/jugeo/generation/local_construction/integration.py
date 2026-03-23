r"""Integration layer for local construction.

Theory (theory2.tex §39 — Integration):
    Chapter 39 describes how the local construction loop machinery is wired into
    the broader generation pipeline.  A *local construction loop* operates on a
    single coordinate chart and produces a candidate section together with a
    residual obligation set.  Integration is the glue that:

    1. Accepts a list of ``GenerationGoal`` objects from the pipeline.
    2. Builds one ``LocalConstructionLoop`` and one ``ConstructionContext`` per
       goal.
    3. Submits those loops to the ``CoordinatedElaborationEngine``, which runs
       simultaneous elaboration rounds across the full cover.
    4. After convergence, assembles the per-coordinate sections into a single
       global section and validates it against the cover topology.
    5. Exports an audit trail (``ConstructionRecord`` list) that can be
       re-imported for diagnostics or warm-restart.

    The integration layer also hosts a *copilot* participant
    (``CopilotConstructionParticipant``) that proposes initial candidates,
    explains verification failures, and guides the adaptive proposal strategy.
    The copilot integration marker is: **copilot: integration-marker**.

    Key invariants maintained by this module:

    * Every ``GenerationGoal`` that enters ``run_coordinated_elaboration`` must
      produce exactly one entry in the returned ``loop_results`` list, whether it
      succeeds or fails.
    * The global section is marked *coherent* only when every pair of adjacent
      coordinate sections has mutually compatible export/import signatures.
    * The budget allocated to a context is never exceeded; if a loop engine
      reports that it has consumed more budget than available, the loop is
      immediately diagnosed and failed.
    * All public methods append to ``_construction_records`` so that the full
      history can be exported at any time via ``export_construction_record``.

Usage example::

    cfg = {
        "max_parallel_loops": 4,
        "enable_copilot": True,
        "copilot_proposal_strategy": "adaptive",
    }
    integration = LocalConstructionIntegration(config=cfg)
    integration.integrate_with_generation_pipeline()
    result = integration.run_single_goal(my_goal)

copilot: integration-marker
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Any

from jugeo.generation.goals import GenerationGoal, GoalDecomposer, OverlapGoal
from jugeo.generation.construction import (
    ConstructionLoop,
    ConstructionContext,
    ConstructionResult,
    Candidate,
)

if TYPE_CHECKING:
    from .models import (
        LocalConstructionLoop,
        InterfaceDiscipline,
        CoordinatedElaboration,
        CandidateSet,
        LocalConstructionError,
        InterfaceBreachError,
        BudgetExhaustedError,
        ConvergenceFailureError,
    )
    from .local_construction_loop import LocalConstructionLoopEngine
    from .interface_discipline import InterfaceDisciplineEnforcer
    from .coordinated_elaboration import CoordinatedElaborationEngine
    from .copilot_in_construction import CopilotConstructionParticipant

__all__ = [
    "LocalConstructionIntegration",
    "PipelineState",
    "ConstructionRecord",
    "IntegrationConfig",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass
class IntegrationConfig:
    """Configuration for the ``LocalConstructionIntegration`` layer.

    Attributes
    ----------
    max_parallel_loops:
        Maximum number of local construction loops that may run concurrently
        inside a single coordinated elaboration round.
    default_budget:
        Default computational budget (in abstract cost units) assigned to a
        ``ConstructionContext`` when the originating ``GenerationGoal`` does not
        carry its own budget.
    integration_mode:
        One of ``"full"`` (all subsystems active), ``"lite"`` (skip copilot and
        interface enforcement), or ``"debug"`` (verbose tracing, single-threaded
        loops).
    enable_copilot:
        Whether the ``CopilotConstructionParticipant`` should be instantiated
        and consulted during loop setup and failure diagnosis.
    copilot_proposal_strategy:
        Strategy tag passed to the copilot participant.  Supported values are
        ``"greedy"``, ``"adaptive"``, ``"exhaustive"``.
    global_section_validation_threshold:
        Minimum validation score (0.0–1.0) required for a global section to be
        considered valid.  Scores below this threshold cause the assembly step
        to return ``coherent=False``.
    export_format:
        Serialisation format used by ``export_construction_record``.  Currently
        only ``"json"`` is supported; reserved for future ``"msgpack"`` support.
    trace_enabled:
        When ``True``, every significant internal state transition is appended
        to ``_construction_records`` with full metadata.
    """

    max_parallel_loops: int = 8
    default_budget: float = 1.0
    integration_mode: str = "full"
    enable_copilot: bool = True
    copilot_proposal_strategy: str = "adaptive"
    global_section_validation_threshold: float = 0.8
    export_format: str = "json"
    trace_enabled: bool = True


@dataclass
class PipelineState:
    """Live snapshot of the integration pipeline's bookkeeping state.

    Attributes
    ----------
    state_id:
        Unique identifier for this pipeline state instance.
    status:
        Lifecycle phase: ``"idle"``, ``"integrated"``, ``"running"``,
        ``"finalised"``, or ``"error"``.
    active_elaborations:
        Number of coordinated elaborations currently in-flight.
    completed_loops:
        Cumulative count of loops that finished with a successful result.
    failed_loops:
        Cumulative count of loops that terminated with a failure.
    total_goals_processed:
        Total number of ``GenerationGoal`` objects that have been submitted to
        this integration instance since construction.
    total_sections_assembled:
        Total number of global sections that have been assembled (whether valid
        or not).
    started_at:
        Unix timestamp at which ``integrate_with_generation_pipeline`` was first
        called.  ``None`` before that call.
    last_updated:
        Unix timestamp of the most recent state mutation.
    """

    state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "idle"
    active_elaborations: int = 0
    completed_loops: int = 0
    failed_loops: int = 0
    total_goals_processed: int = 0
    total_sections_assembled: int = 0
    started_at: float | None = None
    last_updated: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Update ``last_updated`` to the current wall-clock time."""
        self.last_updated = time.time()


@dataclass
class ConstructionRecord:
    """Immutable audit entry capturing the outcome of a single loop execution.

    One ``ConstructionRecord`` is appended to ``_construction_records`` each
    time a loop finishes (success or failure) or a significant diagnostic event
    occurs.

    Attributes
    ----------
    record_id:
        Globally unique identifier for this record.
    goal_id:
        The ``goal_id`` of the ``GenerationGoal`` that initiated the loop.
    loop_id:
        The ``loop_id`` of the ``LocalConstructionLoop`` that was executed, or
        ``None`` if the loop was never successfully constructed.
    result_status:
        One of ``"success"``, ``"failure"``, ``"partial"``, ``"skipped"``.
    residual_count:
        Number of unresolved obligations remaining after the loop terminated.
    elapsed_ms:
        Wall-clock milliseconds consumed by the loop execution.
    iteration_count:
        Number of proposal-verify rounds the loop engine performed.
    timestamp:
        Unix timestamp at which this record was created.
    metadata:
        Arbitrary key-value pairs for extended diagnostics.
    """

    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal_id: str = ""
    loop_id: str | None = None
    result_status: str = "success"
    residual_count: int = 0
    elapsed_ms: int = 0
    iteration_count: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main integration class
# ---------------------------------------------------------------------------


class LocalConstructionIntegration:
    """Wire local construction loops into the broader generation pipeline.

    This class is the primary entry point for consumers of the
    ``local_construction`` package.  It owns the loop engine, the interface
    discipline enforcer, the coordinated elaboration engine, and (optionally)
    the copilot participant.  It exposes high-level methods that hide the
    machinery of individual loop management.

    Parameters
    ----------
    config:
        Optional dictionary of configuration overrides.  Keys correspond to
        ``IntegrationConfig`` field names.  Unrecognised keys are silently
        ignored.

    Examples
    --------
    >>> integration = LocalConstructionIntegration()
    >>> integration.integrate_with_generation_pipeline()
    {'status': 'integrated', ...}
    >>> result = integration.run_single_goal(goal)
    """

    # Version tag embedded in export records so that future importers can
    # apply migration logic when needed.
    _EXPORT_VERSION: str = "1.0"

    # Maximum number of coordinated elaboration rounds before we declare a
    # convergence failure across the entire cover.
    _MAX_ELABORATION_ROUNDS: int = 64

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the integration layer.

        Parameters
        ----------
        config:
            Optional overrides for ``IntegrationConfig``.  For example::

                {
                    "max_parallel_loops": 4,
                    "enable_copilot": False,
                    "integration_mode": "lite",
                }
        """
        raw = config or {}

        self._config = IntegrationConfig(
            max_parallel_loops=raw.get("max_parallel_loops", 8),
            default_budget=raw.get("default_budget", 1.0),
            integration_mode=raw.get("integration_mode", "full"),
            enable_copilot=raw.get("enable_copilot", True),
            copilot_proposal_strategy=raw.get(
                "copilot_proposal_strategy", "adaptive"
            ),
            global_section_validation_threshold=raw.get(
                "global_section_validation_threshold", 0.8
            ),
            export_format=raw.get("export_format", "json"),
            trace_enabled=raw.get("trace_enabled", True),
        )

        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._logger.debug(
            "Initialising LocalConstructionIntegration with config=%s",
            self._config,
        )

        # Lazy-import the concrete engine classes to avoid circular imports at
        # module load time.  The TYPE_CHECKING block above provides type
        # annotations without triggering the imports at runtime.
        from .local_construction_loop import LocalConstructionLoopEngine
        from .interface_discipline import InterfaceDisciplineEnforcer
        from .coordinated_elaboration import CoordinatedElaborationEngine

        self._loop_engine: LocalConstructionLoopEngine = (
            LocalConstructionLoopEngine()
        )
        self._enforcer: InterfaceDisciplineEnforcer = (
            InterfaceDisciplineEnforcer()
        )
        self._elaboration_engine: CoordinatedElaborationEngine = (
            CoordinatedElaborationEngine(
                max_parallel=self._config.max_parallel_loops
            )
        )

        self._copilot: CopilotConstructionParticipant | None = None
        if self._config.enable_copilot:
            from .copilot_in_construction import (
                CopilotConstructionParticipant,
            )

            self._copilot = CopilotConstructionParticipant(
                proposal_strategy=self._config.copilot_proposal_strategy
            )
            self._logger.debug("Copilot participant instantiated.")

        self._pipeline_state: dict[str, Any] = {
            "status": "idle",
            "state_obj": PipelineState(),
            "active_loops": {},
            "pending_goals": [],
            "copilot_proposals": {},
        }

        self._construction_records: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Pipeline integration
    # ------------------------------------------------------------------

    def integrate_with_generation_pipeline(self) -> dict[str, Any]:
        """Set up the integration between this module and the broader pipeline.

        This method must be called before any goal-processing methods.  It
        registers the loop engine with the elaboration engine, wires the
        interface discipline enforcer into the loop engine, and optionally
        configures the copilot participant's strategy parameters.

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
                Whether the copilot participant is active.
            ``"config"``
                Snapshot of the resolved ``IntegrationConfig``.
        """
        self._logger.info("Integrating local construction with generation pipeline.")
        state_obj: PipelineState = self._pipeline_state["state_obj"]

        # Register loop engine with elaboration engine.
        self._elaboration_engine.register_loop_engine(self._loop_engine)
        self._logger.debug("Loop engine registered with elaboration engine.")

        # Wire interface enforcer into loop engine.
        self._loop_engine.set_interface_enforcer(self._enforcer)
        self._logger.debug("Interface discipline enforcer wired into loop engine.")

        components: list[str] = [
            "LocalConstructionLoopEngine",
            "InterfaceDisciplineEnforcer",
            "CoordinatedElaborationEngine",
        ]

        # Configure copilot strategy if present.
        if self._copilot is not None:
            self._copilot.configure(
                strategy=self._config.copilot_proposal_strategy,
                budget_hint=self._config.default_budget,
            )
            components.append("CopilotConstructionParticipant")
            self._logger.debug(
                "Copilot configured with strategy=%s.",
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
                goal_id="__pipeline__",
                loop_id=None,
                result_status="success",
                residual_count=0,
                elapsed_ms=0,
                iteration_count=0,
                metadata={"event": "pipeline_integrated", "components": components},
            )

        self._logger.info("Pipeline integration complete: %s", result)
        return result

    # ------------------------------------------------------------------
    # Loop construction
    # ------------------------------------------------------------------

    def build_loop_for_goal(
        self, goal: GenerationGoal
    ) -> tuple[LocalConstructionLoop, ConstructionContext]:
        """Construct a local construction loop and context for a single goal.

        Creates a fresh ``ConstructionContext`` from the goal's coordinate and
        treaty information, then delegates to the loop engine to set up the
        ``LocalConstructionLoop``.  If the copilot participant is enabled, it
        proposes an initial candidate set and stores the proposals in the
        pipeline state so that the loop engine can consume them on the first
        proposal round.

        Parameters
        ----------
        goal:
            The ``GenerationGoal`` for which to build the loop.

        Returns
        -------
        tuple[LocalConstructionLoop, ConstructionContext]
            A ``(loop, context)`` pair ready for execution.

        Raises
        ------
        LocalConstructionError
            If the loop engine raises during setup.
        """
        from .algorithms import propose_candidates

        budget = goal.budget if goal.budget > 0.0 else self._config.default_budget

        context = ConstructionContext(
            context_id=str(uuid.uuid4()),
            coordinate_id=goal.coordinate_id,
            bindings={},
            evidence={},
            treaty_id=getattr(goal, "treaty_id", None),
            budget_remaining=budget,
        )

        self._logger.debug(
            "Building loop for goal=%s with context_id=%s, budget=%.4f.",
            goal.goal_id,
            context.context_id,
            budget,
        )

        loop: LocalConstructionLoop = self._loop_engine.setup_loop(goal, context)

        if self._copilot is not None:
            initial_candidates = propose_candidates(goal, context, n=3)
            self._copilot.seed_candidates(loop, initial_candidates)
            self._pipeline_state["copilot_proposals"][goal.goal_id] = (
                initial_candidates
            )
            self._logger.debug(
                "Copilot seeded %d initial candidates for goal=%s.",
                len(initial_candidates),
                goal.goal_id,
            )

        state_obj: PipelineState = self._pipeline_state["state_obj"]
        state_obj.total_goals_processed += 1
        state_obj.touch()

        self._pipeline_state["active_loops"][goal.goal_id] = loop

        if self._config.trace_enabled:
            self._append_record(
                goal_id=goal.goal_id,
                loop_id=getattr(loop, "loop_id", None),
                result_status="skipped",
                residual_count=0,
                elapsed_ms=0,
                iteration_count=0,
                metadata={"event": "loop_built", "context_id": context.context_id},
            )

        return loop, context

    # ------------------------------------------------------------------
    # Coordinated elaboration
    # ------------------------------------------------------------------

    def run_coordinated_elaboration(
        self,
        goals: list[GenerationGoal],
        cover: list[str],
    ) -> dict[str, Any]:
        """Run coordinated elaboration across a full coordinate cover.

        Each goal in *goals* is used to build a ``LocalConstructionLoop``.  The
        loops are submitted together to the ``CoordinatedElaborationEngine``,
        which runs simultaneous elaboration rounds.  Coordination continues
        until all loops converge or the maximum round limit is reached.

        Parameters
        ----------
        goals:
            Goals to elaborate.  Each should correspond to one coordinate in
            *cover*, though the cover may contain coordinates without goals (in
            which case those coordinates will not appear in ``loop_results``).
        cover:
            List of coordinate IDs forming the topological cover over which
            elaboration is coordinated.

        Returns
        -------
        dict[str, Any]
            A result dictionary with keys:

            ``"elaboration_id"``
                Unique ID for this elaboration run.
            ``"cover"``
                The cover that was passed in.
            ``"loop_results"``
                One entry per goal (see ``_loop_result_entry``).
            ``"global_progress"``
                Float in [0, 1] indicating the fraction of goals resolved.
            ``"succeeded_goals"``
                List of ``goal_id`` strings for successful loops.
            ``"failed_goals"``
                List of ``goal_id`` strings for failed loops.
            ``"elapsed_ms"``
                Total wall-clock milliseconds consumed.
        """
        from .algorithms import run_local_construction_loop

        elaboration_id = str(uuid.uuid4())
        t_start = time.monotonic()

        self._logger.info(
            "Starting coordinated elaboration id=%s for %d goals over cover=%s.",
            elaboration_id,
            len(goals),
            cover,
        )

        state_obj: PipelineState = self._pipeline_state["state_obj"]
        state_obj.active_elaborations += 1
        state_obj.touch()

        loops: list[LocalConstructionLoop] = []
        contexts: dict[str, ConstructionContext] = {}

        for goal in goals:
            loop, ctx = self.build_loop_for_goal(goal)
            loops.append(loop)
            contexts[goal.goal_id] = ctx

        self._elaboration_engine.initialize_elaboration(loops)

        loop_results: list[dict[str, Any]] = []
        succeeded: list[str] = []
        failed: list[str] = []

        pending_goals = list(goals)
        round_num = 0

        while pending_goals and round_num < self._MAX_ELABORATION_ROUNDS:
            round_num += 1
            self._logger.debug(
                "Elaboration round %d / %d, pending=%d.",
                round_num,
                self._MAX_ELABORATION_ROUNDS,
                len(pending_goals),
            )

            still_pending: list[GenerationGoal] = []
            for goal in pending_goals:
                ctx = contexts[goal.goal_id]
                try:
                    result: ConstructionResult = run_local_construction_loop(
                        goal, ctx, max_iter=8
                    )
                    if result.status in ("success", "partial"):
                        loop_results.append(
                            self._loop_result_entry(goal, result, ctx)
                        )
                        succeeded.append(goal.goal_id)
                        state_obj.completed_loops += 1
                        self._append_record(
                            goal_id=goal.goal_id,
                            loop_id=result.result_id,
                            result_status=result.status,
                            residual_count=len(result.residual_obligations),
                            elapsed_ms=result.elapsed_ms,
                            iteration_count=result.iteration_count,
                            metadata={"elaboration_id": elaboration_id},
                        )
                    else:
                        still_pending.append(goal)
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "Goal %s failed in round %d: %s",
                        goal.goal_id,
                        round_num,
                        exc,
                    )
                    loop_results.append(
                        self._loop_failure_entry(goal, exc, ctx)
                    )
                    failed.append(goal.goal_id)
                    state_obj.failed_loops += 1
                    self._append_record(
                        goal_id=goal.goal_id,
                        loop_id=None,
                        result_status="failure",
                        residual_count=0,
                        elapsed_ms=0,
                        iteration_count=round_num,
                        metadata={
                            "elaboration_id": elaboration_id,
                            "error": str(exc),
                        },
                    )
            pending_goals = still_pending

        # Goals still pending after all rounds are convergence failures.
        for goal in pending_goals:
            ctx = contexts[goal.goal_id]
            loop_results.append(
                self._loop_failure_entry(
                    goal,
                    RuntimeError(
                        f"Convergence failure after {self._MAX_ELABORATION_ROUNDS} rounds"
                    ),
                    ctx,
                )
            )
            failed.append(goal.goal_id)
            state_obj.failed_loops += 1
            self._append_record(
                goal_id=goal.goal_id,
                loop_id=None,
                result_status="failure",
                residual_count=0,
                elapsed_ms=0,
                iteration_count=self._MAX_ELABORATION_ROUNDS,
                metadata={
                    "elaboration_id": elaboration_id,
                    "error": "convergence_failure",
                },
            )

        state_obj.active_elaborations = max(0, state_obj.active_elaborations - 1)
        state_obj.touch()

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        total = max(len(goals), 1)
        global_progress = len(succeeded) / total

        self._logger.info(
            "Elaboration %s complete: succeeded=%d, failed=%d, elapsed_ms=%d.",
            elaboration_id,
            len(succeeded),
            len(failed),
            elapsed_ms,
        )

        return {
            "elaboration_id": elaboration_id,
            "cover": cover,
            "loop_results": loop_results,
            "global_progress": global_progress,
            "succeeded_goals": succeeded,
            "failed_goals": failed,
            "elapsed_ms": elapsed_ms,
        }

    # ------------------------------------------------------------------
    # Global section assembly
    # ------------------------------------------------------------------

    def collect_global_section(
        self, loop_results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Assemble a global section from a list of loop result entries.

        Each successful loop result contributes one local section keyed by its
        coordinate ID.  After assembly, every pair of adjacent coordinate
        sections is checked for interface compatibility (export/import
        signature matching).  The ``coherent`` flag is set to ``True`` only
        when all pairwise interface checks pass.

        Parameters
        ----------
        loop_results:
            The ``loop_results`` list returned by
            ``run_coordinated_elaboration``.

        Returns
        -------
        dict[str, Any]
            Global section dict with keys ``section_id``, ``coordinate_ids``,
            ``sections``, ``interface_agreements``, ``total_obligations_resolved``,
            ``total_obligations_remaining``, ``assembly_method``, ``coherent``.
        """
        from .algorithms import coordinate_interfaces

        section_id = str(uuid.uuid4())
        sections: dict[str, dict[str, Any]] = {}
        total_resolved = 0
        total_remaining = 0

        for entry in loop_results:
            if entry.get("status") not in ("success", "partial"):
                continue
            coord_id = entry.get("coordinate_id", "")
            if not coord_id:
                continue
            sections[coord_id] = {
                "coordinate_id": coord_id,
                "goal_id": entry.get("goal_id", ""),
                "candidate_id": entry.get("candidate_id"),
                "evidence": entry.get("evidence", {}),
                "residual_obligations": entry.get("residual_obligations", []),
                "exports": entry.get("exports", {}),
                "imports": entry.get("imports", {}),
                "iteration_count": entry.get("iteration_count", 0),
            }
            residuals = entry.get("residual_obligations", [])
            total_remaining += len(residuals)
            total_resolved += entry.get("resolved_count", 0)

        coordinate_ids = list(sections.keys())
        interface_agreements: dict[str, dict[str, Any]] = {}
        all_interfaces_ok = True

        for i, coord_a in enumerate(coordinate_ids):
            for coord_b in coordinate_ids[i + 1 :]:
                sec_a = sections[coord_a]
                sec_b = sections[coord_b]
                shared_boundary = _compute_shared_boundary(coord_a, coord_b)
                if not shared_boundary:
                    continue
                try:
                    agreement = coordinate_interfaces(
                        _make_stub_loop(sec_a),
                        _make_stub_loop(sec_b),
                        shared_boundary,
                    )
                except Exception as exc:  # noqa: BLE001
                    agreement = {"compatible": False, "error": str(exc)}

                compatible = agreement.get("compatible", False)
                key = f"{coord_a}||{coord_b}"
                interface_agreements[key] = agreement
                if not compatible:
                    all_interfaces_ok = False
                    self._logger.warning(
                        "Interface incompatibility between %s and %s: %s",
                        coord_a,
                        coord_b,
                        agreement,
                    )

        state_obj: PipelineState = self._pipeline_state["state_obj"]
        state_obj.total_sections_assembled += 1
        state_obj.touch()

        coherent = all_interfaces_ok and bool(sections)

        global_section: dict[str, Any] = {
            "section_id": section_id,
            "coordinate_ids": coordinate_ids,
            "sections": sections,
            "interface_agreements": interface_agreements,
            "total_obligations_resolved": total_resolved,
            "total_obligations_remaining": total_remaining,
            "assembly_method": "coordinated_elaboration",
            "coherent": coherent,
        }

        if self._config.trace_enabled:
            self._append_record(
                goal_id="__assembly__",
                loop_id=section_id,
                result_status="success" if coherent else "partial",
                residual_count=total_remaining,
                elapsed_ms=0,
                iteration_count=0,
                metadata={
                    "event": "global_section_assembled",
                    "coordinate_count": len(coordinate_ids),
                    "coherent": coherent,
                },
            )

        self._logger.info(
            "Global section %s assembled: coords=%d, coherent=%s.",
            section_id,
            len(coordinate_ids),
            coherent,
        )
        return global_section

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_global_section(
        self,
        section: dict[str, Any],
        cover: list[str],
    ) -> dict[str, Any]:
        """Validate an assembled global section against the expected cover.

        Performs four independent checks:

        1. **Coverage check** — every coordinate in *cover* has an entry in
           ``section["sections"]``.
        2. **Interface check** — ``section["interface_agreements"]`` reports
           no incompatibilities.
        3. **Obligation check** — the ratio of resolved to total obligations
           meets the configured threshold.
        4. **Coherence check** — ``section["coherent"]`` is ``True``.

        The final ``validation_score`` is the arithmetic mean of the four
        boolean sub-scores.

        Parameters
        ----------
        section:
            Global section dict as returned by ``collect_global_section``.
        cover:
            The expected coordinate cover (list of coordinate IDs).

        Returns
        -------
        dict[str, Any]
            Validation report with keys ``valid``, ``coverage_ok``,
            ``interface_ok``, ``obligation_ok``, ``coherence_ok``, ``issues``,
            ``validation_score``.
        """
        issues: list[str] = []

        # 1. Coverage check.
        present_coords = set(section.get("coordinate_ids", []))
        missing = [c for c in cover if c not in present_coords]
        coverage_ok = len(missing) == 0
        if not coverage_ok:
            issues.append(
                f"Missing coordinates in section: {missing}"
            )

        # 2. Interface check.
        agreements = section.get("interface_agreements", {})
        bad_interfaces = [
            k for k, v in agreements.items() if not v.get("compatible", True)
        ]
        interface_ok = len(bad_interfaces) == 0
        if not interface_ok:
            issues.append(
                f"Incompatible interfaces at boundaries: {bad_interfaces}"
            )

        # 3. Obligation check.
        resolved = section.get("total_obligations_resolved", 0)
        remaining = section.get("total_obligations_remaining", 0)
        total_obligations = resolved + remaining
        if total_obligations == 0:
            obligation_ratio = 1.0
        else:
            obligation_ratio = resolved / total_obligations
        threshold = self._config.global_section_validation_threshold
        obligation_ok = obligation_ratio >= threshold
        if not obligation_ok:
            issues.append(
                f"Obligation resolution ratio {obligation_ratio:.3f} < threshold {threshold:.3f}"
            )

        # 4. Coherence check.
        coherence_ok = section.get("coherent", False)
        if not coherence_ok:
            issues.append("Section is not marked coherent.")

        sub_scores = [
            float(coverage_ok),
            float(interface_ok),
            float(obligation_ok),
            float(coherence_ok),
        ]
        validation_score = sum(sub_scores) / len(sub_scores)
        valid = validation_score >= threshold and not issues

        self._logger.info(
            "Section validation: valid=%s, score=%.3f, issues=%s.",
            valid,
            validation_score,
            issues,
        )

        return {
            "valid": valid,
            "coverage_ok": coverage_ok,
            "interface_ok": interface_ok,
            "obligation_ok": obligation_ok,
            "coherence_ok": coherence_ok,
            "issues": issues,
            "validation_score": validation_score,
        }

    # ------------------------------------------------------------------
    # Record export / import
    # ------------------------------------------------------------------

    def export_construction_record(self) -> str:
        """Serialise the full audit trail to a JSON string.

        The exported payload includes every ``ConstructionRecord`` appended
        during this session, the current ``_pipeline_state`` snapshot, an
        export timestamp, and a version tag for future migration support.

        Returns
        -------
        str
            A UTF-8 JSON string ready for writing to disk or transmission.
        """
        state_obj: PipelineState = self._pipeline_state["state_obj"]
        payload: dict[str, Any] = {
            "version": self._EXPORT_VERSION,
            "export_timestamp": time.time(),
            "records": self._construction_records,
            "pipeline_state": {
                "state_id": state_obj.state_id,
                "status": state_obj.status,
                "active_elaborations": state_obj.active_elaborations,
                "completed_loops": state_obj.completed_loops,
                "failed_loops": state_obj.failed_loops,
                "total_goals_processed": state_obj.total_goals_processed,
                "total_sections_assembled": state_obj.total_sections_assembled,
                "started_at": state_obj.started_at,
                "last_updated": state_obj.last_updated,
            },
            "config": asdict(self._config),
        }
        serialised = json.dumps(payload, indent=2, default=str)
        self._logger.debug(
            "Exported %d construction records (%d bytes).",
            len(self._construction_records),
            len(serialised),
        )
        return serialised

    def import_construction_record(self, record_json: str) -> dict[str, Any]:
        """Deserialise and merge a previously exported construction record.

        The imported records are *appended* to ``_construction_records``; the
        existing records are never discarded.  The method validates that the
        payload has the required ``"records"`` and ``"export_timestamp"`` keys
        before touching any internal state.

        Parameters
        ----------
        record_json:
            A JSON string produced by a prior call to
            ``export_construction_record``.

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
                "total_records": len(self._construction_records),
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
                "total_records": len(self._construction_records),
                "status": "partial",
                "issues": issues,
            }

        incoming: list[dict[str, Any]] = payload["records"]
        if not isinstance(incoming, list):
            issues.append("'records' must be a JSON array.")
            return {
                "imported": 0,
                "total_records": len(self._construction_records),
                "status": "partial",
                "issues": issues,
            }

        imported_count = 0
        for rec in incoming:
            if not isinstance(rec, dict):
                issues.append(f"Skipping non-dict record: {rec!r}")
                continue
            self._construction_records.append(rec)
            imported_count += 1

        status = "ok" if not issues else "partial"
        self._logger.info(
            "Imported %d records (status=%s).", imported_count, status
        )

        return {
            "imported": imported_count,
            "total_records": len(self._construction_records),
            "status": status,
            "issues": issues,
        }

    # ------------------------------------------------------------------
    # Failure diagnosis
    # ------------------------------------------------------------------

    def diagnose_construction_failure(
        self,
        failed_goal: GenerationGoal,
        loop: LocalConstructionLoop | None,
        error: Exception | None,
    ) -> dict[str, Any]:
        """Produce a comprehensive diagnosis for a failed local construction.

        Failure categories (in order of classification priority):

        ``"budget_exhausted"``
            The error is or wraps a ``BudgetExhaustedError``.
        ``"interface_breach"``
            The error is or wraps an ``InterfaceBreachError``.
        ``"convergence_failure"``
            The error is or wraps a ``ConvergenceFailureError``.
        ``"goal_ill_formed"``
            The error is or wraps a ``LocalConstructionError`` not covered
            above.
        ``"unknown"``
            Any other exception or ``error=None`` with no loop diagnostics.

        If the copilot participant is enabled and the failure appears to be a
        verification failure, ``copilot.explain_verification_failure`` is
        invoked to provide a human-readable explanation.

        Parameters
        ----------
        failed_goal:
            The goal that failed.
        loop:
            The ``LocalConstructionLoop`` at the time of failure, or ``None``
            if the loop was never built.
        error:
            The exception that caused the failure, or ``None`` if the loop
            simply did not converge without raising.

        Returns
        -------
        dict[str, Any]
            Diagnosis report with keys ``diagnosis_id``, ``goal_id``,
            ``failure_category``, ``loop_diagnostics``, ``copilot_explanation``,
            ``suggested_remedies``, ``severity``, ``timestamp``.
        """
        from .models import (
            LocalConstructionError,
            InterfaceBreachError,
            BudgetExhaustedError,
            ConvergenceFailureError,
        )

        diagnosis_id = str(uuid.uuid4())

        # Collect loop-level diagnostics if the loop object is available.
        loop_diagnostics: dict[str, Any] | None = None
        if loop is not None:
            try:
                loop_diagnostics = self._loop_engine.collect_diagnostics(loop)
            except Exception as diag_exc:  # noqa: BLE001
                loop_diagnostics = {"error": str(diag_exc)}

        # Classify the failure.
        failure_category: str
        if isinstance(error, BudgetExhaustedError):
            failure_category = "budget_exhausted"
        elif isinstance(error, InterfaceBreachError):
            failure_category = "interface_breach"
        elif isinstance(error, ConvergenceFailureError):
            failure_category = "convergence_failure"
        elif isinstance(error, LocalConstructionError):
            failure_category = "goal_ill_formed"
        else:
            failure_category = "unknown"

        # Severity assignment.
        severity_map = {
            "budget_exhausted": "warning",
            "interface_breach": "error",
            "convergence_failure": "error",
            "goal_ill_formed": "critical",
            "unknown": "warning",
        }
        severity = severity_map.get(failure_category, "warning")

        # Remedy suggestions.
        remedies_map: dict[str, list[str]] = {
            "budget_exhausted": [
                "Increase the budget allocated to this goal.",
                "Reduce the number of obligations on the goal.",
                "Split the goal into smaller sub-goals.",
            ],
            "interface_breach": [
                "Inspect the interface discipline constraints at the boundary.",
                "Relax the export/import contract on the adjacent coordinate.",
                "Introduce a mediator section to bridge the gap.",
            ],
            "convergence_failure": [
                "Increase max_iterations on the loop engine.",
                "Check that the candidate proposal strategy is diverse enough.",
                "Verify that the section space is non-empty for this coordinate.",
            ],
            "goal_ill_formed": [
                "Validate the goal's laws and obligations before submission.",
                "Check that the coordinate_id and treaty_id are consistent.",
                "Consult the goal decomposer to normalise the goal.",
            ],
            "unknown": [
                "Inspect the loop diagnostics for low-level error details.",
                "Enable trace_enabled=True and re-run to capture a full audit trail.",
            ],
        }
        suggested_remedies = remedies_map.get(failure_category, [])

        # Copilot explanation (if applicable).
        copilot_explanation: dict[str, Any] | None = None
        if self._copilot is not None and failure_category in (
            "convergence_failure",
            "goal_ill_formed",
        ):
            try:
                copilot_explanation = self._copilot.explain_verification_failure(
                    goal=failed_goal,
                    loop=loop,
                    error=error,
                    loop_diagnostics=loop_diagnostics,
                )
            except Exception as cop_exc:  # noqa: BLE001
                copilot_explanation = {"error": str(cop_exc)}

        self._logger.info(
            "Diagnosis %s for goal %s: category=%s, severity=%s.",
            diagnosis_id,
            failed_goal.goal_id,
            failure_category,
            severity,
        )

        report: dict[str, Any] = {
            "diagnosis_id": diagnosis_id,
            "goal_id": failed_goal.goal_id,
            "failure_category": failure_category,
            "loop_diagnostics": loop_diagnostics,
            "copilot_explanation": copilot_explanation,
            "suggested_remedies": suggested_remedies,
            "severity": severity,
            "timestamp": time.time(),
        }

        self._append_record(
            goal_id=failed_goal.goal_id,
            loop_id=None,
            result_status="failure",
            residual_count=0,
            elapsed_ms=0,
            iteration_count=0,
            metadata={
                "event": "diagnosis",
                "failure_category": failure_category,
                "diagnosis_id": diagnosis_id,
            },
        )

        return report

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def run_single_goal(self, goal: GenerationGoal) -> dict[str, Any]:
        """Run a single goal through the full local construction pipeline.

        This is a convenience wrapper that:

        1. Calls ``build_loop_for_goal`` to obtain the loop and context.
        2. Delegates to ``loop_engine.run_full_loop`` to execute the loop.
        3. On failure, calls ``diagnose_construction_failure`` for a rich
           diagnosis report.

        Parameters
        ----------
        goal:
            The ``GenerationGoal`` to process.

        Returns
        -------
        dict[str, Any]
            Result dict with keys:

            ``"success"``
                Boolean.
            ``"result"``
                The ``ConstructionResult`` object (or ``None`` on total failure).
            ``"diagnostics"``
                A diagnosis report (or ``None`` if the loop succeeded).
            ``"loop_id"``
                The loop identifier (or ``None``).
            ``"goal_id"``
                The goal identifier.
            ``"elapsed_ms"``
                Wall-clock milliseconds consumed.
        """
        t_start = time.monotonic()
        loop: LocalConstructionLoop | None = None
        result: ConstructionResult | None = None
        diagnostics: dict[str, Any] | None = None

        try:
            loop, context = self.build_loop_for_goal(goal)
            result = self._loop_engine.run_full_loop(loop, context)
            success = result.status in ("success", "partial")
            if not success:
                diagnostics = self.diagnose_construction_failure(
                    goal, loop, None
                )
        except Exception as exc:  # noqa: BLE001
            self._logger.error(
                "run_single_goal failed for goal=%s: %s", goal.goal_id, exc
            )
            diagnostics = self.diagnose_construction_failure(goal, loop, exc)
            success = False

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        return {
            "success": success,
            "result": result,
            "diagnostics": diagnostics,
            "loop_id": getattr(loop, "loop_id", None),
            "goal_id": goal.goal_id,
            "elapsed_ms": elapsed_ms,
        }

    def get_pipeline_status(self) -> dict[str, Any]:
        """Return a comprehensive snapshot of the integration pipeline's state.

        Returns
        -------
        dict[str, Any]
            Status dict including pipeline state, record counts, engine
            readiness, and copilot status.
        """
        state_obj: PipelineState = self._pipeline_state["state_obj"]

        loop_engine_ready: bool = False
        try:
            loop_engine_ready = self._loop_engine.is_ready()
        except Exception:  # noqa: BLE001
            loop_engine_ready = False

        elaboration_engine_ready: bool = False
        try:
            elaboration_engine_ready = self._elaboration_engine.is_ready()
        except Exception:  # noqa: BLE001
            elaboration_engine_ready = False

        enforcer_ready: bool = False
        try:
            enforcer_ready = self._enforcer.is_ready()
        except Exception:  # noqa: BLE001
            enforcer_ready = False

        copilot_status: dict[str, Any] = {"enabled": False}
        if self._copilot is not None:
            try:
                copilot_status = {
                    "enabled": True,
                    "strategy": self._config.copilot_proposal_strategy,
                    "proposals_issued": len(
                        self._pipeline_state.get("copilot_proposals", {})
                    ),
                }
            except Exception:  # noqa: BLE001
                copilot_status = {"enabled": True, "error": "status_unavailable"}

        return {
            "pipeline_status": state_obj.status,
            "state_id": state_obj.state_id,
            "active_elaborations": state_obj.active_elaborations,
            "completed_loops": state_obj.completed_loops,
            "failed_loops": state_obj.failed_loops,
            "total_goals_processed": state_obj.total_goals_processed,
            "total_sections_assembled": state_obj.total_sections_assembled,
            "construction_records_count": len(self._construction_records),
            "started_at": state_obj.started_at,
            "last_updated": state_obj.last_updated,
            "loop_engine_ready": loop_engine_ready,
            "elaboration_engine_ready": elaboration_engine_ready,
            "enforcer_ready": enforcer_ready,
            "copilot": copilot_status,
            "config": asdict(self._config),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_record(
        self,
        goal_id: str,
        loop_id: str | None,
        result_status: str,
        residual_count: int,
        elapsed_ms: int,
        iteration_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a ``ConstructionRecord`` to the internal audit trail.

        This method is called internally after every significant event.  It
        constructs a ``ConstructionRecord`` dataclass, converts it to a plain
        dict, and appends it to ``_construction_records``.

        Parameters
        ----------
        goal_id:
            Goal that triggered this record.
        loop_id:
            Associated loop identifier, or ``None``.
        result_status:
            One of ``"success"``, ``"failure"``, ``"partial"``, ``"skipped"``.
        residual_count:
            Number of unresolved obligations.
        elapsed_ms:
            Time consumed in milliseconds.
        iteration_count:
            Number of loop iterations performed.
        metadata:
            Arbitrary additional key-value pairs.
        """
        record = ConstructionRecord(
            goal_id=goal_id,
            loop_id=loop_id,
            result_status=result_status,
            residual_count=residual_count,
            elapsed_ms=elapsed_ms,
            iteration_count=iteration_count,
            metadata=metadata or {},
        )
        self._construction_records.append(asdict(record))

    @staticmethod
    def _loop_result_entry(
        goal: GenerationGoal,
        result: ConstructionResult,
        ctx: ConstructionContext,
    ) -> dict[str, Any]:
        """Build a loop result entry dict from a ``ConstructionResult``.

        Parameters
        ----------
        goal:
            The originating goal.
        result:
            The finished construction result.
        ctx:
            The context used during the loop.

        Returns
        -------
        dict[str, Any]
            A plain dict suitable for inclusion in ``loop_results``.
        """
        residuals = list(result.residual_obligations)
        return {
            "goal_id": goal.goal_id,
            "coordinate_id": goal.coordinate_id,
            "status": result.status,
            "candidate_id": result.candidate_id,
            "residual_obligations": residuals,
            "resolved_count": max(
                0, len(getattr(goal, "obligations", ())) - len(residuals)
            ),
            "evidence": result.evidence,
            "elapsed_ms": result.elapsed_ms,
            "iteration_count": result.iteration_count,
            "exports": result.evidence.get("exports", {}),
            "imports": result.evidence.get("imports", {}),
            "context_id": ctx.context_id,
        }

    @staticmethod
    def _loop_failure_entry(
        goal: GenerationGoal,
        error: Exception,
        ctx: ConstructionContext,
    ) -> dict[str, Any]:
        """Build a failure entry dict for inclusion in ``loop_results``.

        Parameters
        ----------
        goal:
            The originating goal.
        error:
            The exception that caused the failure.
        ctx:
            The context used (or attempted) during the loop.

        Returns
        -------
        dict[str, Any]
            A plain dict with ``status="failure"``.
        """
        return {
            "goal_id": goal.goal_id,
            "coordinate_id": goal.coordinate_id,
            "status": "failure",
            "candidate_id": None,
            "residual_obligations": list(getattr(goal, "obligations", ())),
            "resolved_count": 0,
            "evidence": {},
            "elapsed_ms": 0,
            "iteration_count": 0,
            "exports": {},
            "imports": {},
            "context_id": ctx.context_id,
            "error": str(error),
        }


# ---------------------------------------------------------------------------
# Module-level helpers (private)
# ---------------------------------------------------------------------------


def _compute_shared_boundary(coord_a: str, coord_b: str) -> list[str]:
    """Return a stub shared-boundary descriptor for two coordinate IDs.

    In a real implementation this would consult the cover topology graph to
    determine which charts overlap.  Here we return a non-empty list whenever
    the two coordinate IDs are lexicographically adjacent (as a proxy for
    adjacency in the cover), so that the integration tests exercise the
    interface-checking code path.

    Parameters
    ----------
    coord_a:
        First coordinate ID.
    coord_b:
        Second coordinate ID.

    Returns
    -------
    list[str]
        A list of boundary descriptor tokens, or an empty list if the two
        coordinates are not considered adjacent.
    """
    # Treat the coordinate IDs as adjacent when their sorted forms differ only
    # in the last character (a trivial heuristic for testing purposes).
    if coord_a == coord_b:
        return []
    a_prefix = coord_a[:-1] if len(coord_a) > 1 else coord_a
    b_prefix = coord_b[:-1] if len(coord_b) > 1 else coord_b
    if a_prefix == b_prefix:
        return [f"boundary:{coord_a}:{coord_b}"]
    return []


def _make_stub_loop(section: dict[str, Any]) -> Any:
    """Construct a minimal stub object from a section dict.

    ``coordinate_interfaces`` expects objects that have ``exports`` and
    ``imports`` attributes.  This helper wraps the section dict in a simple
    namespace so that the algorithm can be called without requiring a fully
    constructed ``LocalConstructionLoop``.

    Parameters
    ----------
    section:
        A section entry from the global section's ``sections`` dict.

    Returns
    -------
    Any
        A lightweight namespace with ``loop_id``, ``coordinate_id``,
        ``exports``, and ``imports`` attributes.
    """
    import types

    stub = types.SimpleNamespace(
        loop_id=section.get("goal_id", str(uuid.uuid4())),
        coordinate_id=section.get("coordinate_id", ""),
        exports=section.get("exports", {}),
        imports=section.get("imports", {}),
    )
    return stub
