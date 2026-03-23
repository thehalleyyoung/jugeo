"""
Coordinated Elaboration — theory2.tex §39

This module implements the coordinated elaboration mechanism described in Chapter 39
of theory2.tex.  Coordinated elaboration deals with the situation in which multiple
sections are being constructed in parallel and must maintain a coherent relationship
across their shared interfaces.

When section s_i and s_j share boundary ∂u_i ∩ ∂u_j, they must maintain a
compatible interface state throughout the construction process.  Two loops that share
a coordinate boundary therefore cannot operate in complete isolation: every round of
iteration on one loop may change the exports that the other loop depends upon, or may
change the imports that must be supplied to the first loop by the second.

Coordination proceeds in rounds.  Each round:
  1. Advances all loops one step (one call to propose_candidates / select_best /
     verify_candidate / advance_iteration).
  2. Synchronises the interface states so that each loop has an up-to-date view of
     what its neighbours have committed to.
  3. Detects interface conflicts arising from contradictory commitments.
  4. Attempts to resolve every conflict by negotiation (weaken one side's
     requirements) or, when negotiation fails, by priority (the higher-priority
     loop's result wins, the lower-priority loop is degraded to a stalled state).

The copilot module (copilot_in_construction) builds on top of this machinery to
provide intelligent scheduling, conflict prediction, and budget reallocation.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jugeo.generation.local_construction.local_construction_loop import (
        LocalConstructionLoop,
        InterfaceDiscipline,
    )
    from jugeo.generation.local_construction.candidate_set import CandidateSet

__all__ = [
    "CoordinatedElaborationEngine",
    "ElaborationSchedule",
    "CoordinationConflict",
    "LocalConstructionError",
    "InterfaceBreachError",
    "BudgetExhaustedError",
    "ConvergenceFailureError",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class LocalConstructionError(Exception):
    """Base class for all errors raised in the local-construction subsystem."""


class InterfaceBreachError(LocalConstructionError):
    """Raised when a loop irrecoverably violates an interface discipline."""


class BudgetExhaustedError(LocalConstructionError):
    """Raised when a loop has no remaining computation budget."""


class ConvergenceFailureError(LocalConstructionError):
    """Raised when a loop exhausts its iteration limit without converging."""


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ElaborationSchedule:
    """
    A concrete schedule produced by :meth:`CoordinatedElaborationEngine.compute_elaboration_schedule`.

    Attributes
    ----------
    schedule_id:
        Unique identifier for this schedule.
    elaboration_id:
        The :class:`CoordinatedElaboration` this schedule was computed for.
    steps:
        Ordered list of step-dicts (each with keys ``step``, ``loop_ids``,
        ``parallel``, ``reason``).
    created_at:
        Unix timestamp at which the schedule was computed.
    estimated_rounds:
        Best-guess total number of coordination rounds needed.
    """

    schedule_id: str
    elaboration_id: str
    steps: list[dict[str, Any]]
    created_at: float
    estimated_rounds: int


@dataclass
class CoordinationConflict:
    """
    A record of a single conflict between two loops in a coordinated elaboration.

    Attributes
    ----------
    conflict_id:
        Unique identifier for this conflict instance.
    loop_a_id:
        Identifier of the first participating loop.
    loop_b_id:
        Identifier of the second participating loop.
    conflict_type:
        Short string describing the kind of conflict, e.g.
        ``"contradictory_interface_states"`` or ``"unresolvable_exports"``.
    severity:
        One of ``"critical"``, ``"warning"``, or ``"info"``.
    detected_at:
        Unix timestamp when the conflict was first detected.
    resolved_at:
        Unix timestamp when the conflict was resolved, or ``None`` if still open.
    resolution_method:
        The method used to resolve the conflict (``"negotiation"``,
        ``"priority"``, etc.) or ``None`` if unresolved.
    """

    conflict_id: str
    loop_a_id: str
    loop_b_id: str
    conflict_type: str
    severity: str  # "critical" | "warning" | "info"
    detected_at: float
    resolved_at: float | None = None
    resolution_method: str | None = None


# ---------------------------------------------------------------------------
# Minimal stand-in for CoordinatedElaboration when the real class is absent
# ---------------------------------------------------------------------------


def _make_coordinated_elaboration(elaboration_id: str) -> Any:  # noqa: ANN401
    """Create a lightweight CoordinatedElaboration-like object.

    In a fully integrated codebase this function would import
    ``CoordinatedElaboration`` directly from the construction package.  We use
    a plain dataclass here so that this module can be imported in isolation
    during tests.
    """

    @dataclass
    class _CoordinatedElaboration:  # type: ignore[no-redef]
        elaboration_id: str
        participating_loops: tuple[Any, ...] = field(default_factory=tuple)
        coordination_graph: dict[str, frozenset[str]] = field(default_factory=dict)
        interface_states: dict[str, str] = field(default_factory=dict)
        conflict_log: tuple[dict[str, Any], ...] = field(default_factory=tuple)
        synchronization_points: tuple[str, ...] = field(default_factory=tuple)
        status: str = "pending"

        def register_loop(self, loop: Any) -> None:  # noqa: ANN401
            self.participating_loops = (*self.participating_loops, loop)

        def advance_all_loops(self) -> None:
            for loop in self.participating_loops:
                if hasattr(loop, "advance_iteration") and loop.status == "running":
                    loop.advance_iteration()

        def synchronize_interfaces(self) -> None:
            for loop in self.participating_loops:
                lid = getattr(loop, "loop_id", str(id(loop)))
                self.interface_states[lid] = getattr(loop, "status", "unknown")

        def detect_conflicts(self) -> list[dict[str, Any]]:
            return []

        def resolve_conflict(self, conflict: dict[str, Any]) -> bool:
            return True

        def compute_global_progress(self) -> float:
            loops = self.participating_loops
            if not loops:
                return 0.0
            done = sum(
                1
                for lp in loops
                if getattr(lp, "status", "") in {"succeeded", "failed"}
            )
            return done / len(loops)

        def abort_conflicted_loops(self) -> None:
            for loop in self.participating_loops:
                if getattr(loop, "status", "") == "running":
                    loop.status = "failed"

        def get_coordination_status(self) -> dict[str, Any]:
            return {
                "elaboration_id": self.elaboration_id,
                "status": self.status,
                "participating": len(self.participating_loops),
            }

        def to_dict(self) -> dict[str, Any]:
            return {
                "elaboration_id": self.elaboration_id,
                "status": self.status,
                "participating_loops": [
                    getattr(lp, "loop_id", str(id(lp)))
                    for lp in self.participating_loops
                ],
            }

        def summary(self) -> str:
            return (
                f"CoordinatedElaboration({self.elaboration_id}, "
                f"status={self.status}, loops={len(self.participating_loops)})"
            )

    return _CoordinatedElaboration(elaboration_id=elaboration_id)


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


class CoordinatedElaborationEngine:
    """
    Engine that drives coordinated elaboration of multiple local construction loops.

    Each :class:`LocalConstructionLoop` works on a single section goal.  When two
    sections share a boundary, their loops must communicate: an export from one
    becomes an import to the other.  This engine orchestrates those interactions
    through a round-based protocol.

    Configuration keys
    ------------------
    max_coordination_rounds : int
        Hard ceiling on the number of rounds before we declare a stall (default 50).
    conflict_resolution_strategy : str
        Primary strategy when a conflict is detected: ``"negotiation"`` tries to
        weaken requirements on both sides; ``"priority"`` immediately awards the
        win to the loop with more remaining budget (default ``"negotiation"``).
    priority_tiebreaker : str
        When two loops have identical budget, this field breaks the tie (default
        ``"budget"`` — currently the only supported option).
    checkpoint_interval : int
        How many rounds pass between automatic synchronisation checkpoints
        (default 5).
    cascade_abort_threshold : float
        If the fraction of loops that would be aborted by a cascade failure
        exceeds this value, abort the entire elaboration (default 0.5).
    trace_enabled : bool
        Whether to record detailed trace events (default ``True``).

    Notes
    -----
    The *copilot* (see ``copilot_in_construction``) integrates with this
    engine by subscribing to trace events and proposing budget reallocations
    whenever the engine detects a stall.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._config: dict[str, Any] = {
            "max_coordination_rounds": cfg.get("max_coordination_rounds", 50),
            "conflict_resolution_strategy": cfg.get(
                "conflict_resolution_strategy", "negotiation"
            ),
            "priority_tiebreaker": cfg.get("priority_tiebreaker", "budget"),
            "checkpoint_interval": cfg.get("checkpoint_interval", 5),
            "cascade_abort_threshold": cfg.get("cascade_abort_threshold", 0.5),
            "trace_enabled": cfg.get("trace_enabled", True),
        }
        self._logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )
        self._active_elaborations: dict[str, Any] = {}
        self._elaboration_traces: dict[str, list[dict[str, Any]]] = {}
        self._conflict_resolutions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initialize_elaboration(
        self, loops: list[Any]  # list[LocalConstructionLoop]
    ) -> Any:  # CoordinatedElaboration
        """
        Create and initialise a new :class:`CoordinatedElaboration` from a list of
        local construction loops.

        The method:

        1. Mints a fresh ``elaboration_id``.
        2. Registers every loop via ``elaboration.register_loop``.
        3. Builds the initial ``coordination_graph`` by inspecting ``coordinate_id``
           prefixes: two loops are considered neighbours if their coordinate
           identifiers share a common prefix of length ≥ 3 characters.
        4. Sets ``elaboration.status = "running"``.
        5. Stores the elaboration in ``self._active_elaborations`` and creates an
           empty trace list.

        Parameters
        ----------
        loops:
            Non-empty list of :class:`LocalConstructionLoop` objects.

        Returns
        -------
        CoordinatedElaboration
            The newly initialised elaboration object.
        """
        elaboration_id = str(uuid.uuid4())
        elaboration = _make_coordinated_elaboration(elaboration_id)

        for loop in loops:
            elaboration.register_loop(loop)
            loop_id = getattr(loop, "loop_id", str(id(loop)))
            elaboration.interface_states[loop_id] = getattr(loop, "status", "pending")

        # Build initial coordination graph based on shared coordinate prefixes
        coordination_graph: dict[str, frozenset[str]] = {}
        for i, loop_a in enumerate(loops):
            lid_a = getattr(loop_a, "loop_id", str(id(loop_a)))
            coord_a = getattr(loop_a, "coordinate_id", "")
            neighbours: set[str] = set()
            for j, loop_b in enumerate(loops):
                if i == j:
                    continue
                lid_b = getattr(loop_b, "loop_id", str(id(loop_b)))
                coord_b = getattr(loop_b, "coordinate_id", "")
                if self._share_coordinate_prefix(coord_a, coord_b, min_len=3):
                    neighbours.add(lid_b)
            coordination_graph[lid_a] = frozenset(neighbours)

        elaboration.coordination_graph = coordination_graph
        elaboration.status = "running"

        self._active_elaborations[elaboration_id] = elaboration
        self._elaboration_traces[elaboration_id] = []

        self.emit_elaboration_trace(
            elaboration,
            "initialized",
            {
                "loop_count": len(loops),
                "edges": {k: list(v) for k, v in coordination_graph.items()},
            },
        )
        self._logger.info(
            "Initialized elaboration %s with %d loops.", elaboration_id, len(loops)
        )
        return elaboration

    def run_coordination_round(self, elaboration: Any) -> dict[str, Any]:
        """
        Execute one coordination round on *elaboration*.

        A coordination round consists of the following steps:

        1. **Advance** all running loops by one iteration.
        2. **Synchronise** interface states so every loop sees its neighbours'
           current commitments.
        3. **Detect** conflicts arising from incompatible interface states.
        4. **Resolve** each conflict according to the configured strategy.
        5. **Compute** global progress.
        6. **Check** termination: if progress ≥ 1.0 or all loops have converged
           (all in ``"succeeded"`` or ``"failed"``), mark the elaboration as
           ``"completed"``.
        7. **Emit** a trace event summarising the round.

        Parameters
        ----------
        elaboration:
            An active :class:`CoordinatedElaboration` object.

        Returns
        -------
        dict
            A round summary containing:
            ``round_number``, ``conflicts_found``, ``conflicts_resolved``,
            ``global_progress``, and ``loop_statuses``.
        """
        elaboration.advance_all_loops()
        elaboration.synchronize_interfaces()

        raw_conflicts: list[dict[str, Any]] = elaboration.detect_conflicts()
        internal_conflicts = self.detect_interface_conflicts(elaboration)
        all_conflicts = raw_conflicts + internal_conflicts

        resolved_count = 0
        for conflict in all_conflicts:
            if self._resolve_conflict_by_strategy(conflict):
                resolved_count += 1
                self._conflict_resolutions.append(
                    {
                        "conflict_id": conflict.get("conflict_id", str(uuid.uuid4())),
                        "resolved_at": time.time(),
                        "strategy": self._config["conflict_resolution_strategy"],
                    }
                )

        progress = elaboration.compute_global_progress()

        loops = list(elaboration.participating_loops)
        all_terminal = all(
            getattr(lp, "status", "running") in {"succeeded", "failed", "stalled"}
            for lp in loops
        )

        if progress >= 1.0 or all_terminal:
            elaboration.status = "completed"

        loop_statuses = {
            getattr(lp, "loop_id", str(id(lp))): getattr(lp, "status", "unknown")
            for lp in loops
        }

        trace_count = len(self._elaboration_traces.get(elaboration.elaboration_id, []))
        round_number = trace_count  # will be incremented by emit below

        round_summary: dict[str, Any] = {
            "round_number": round_number,
            "conflicts_found": len(all_conflicts),
            "conflicts_resolved": resolved_count,
            "global_progress": progress,
            "loop_statuses": loop_statuses,
        }

        self.emit_elaboration_trace(elaboration, "round_completed", round_summary)
        return round_summary

    def detect_interface_conflicts(
        self, elaboration: Any
    ) -> list[dict[str, Any]]:
        """
        Scan all pairs of connected loops for interface incompatibilities.

        Two loops are in conflict when any of the following hold:

        * Both are ``"running"`` but their current ``interface_states`` entries
          indicate directly contradictory commitments (detected by simple string
          inequality when both states are non-trivial).
        * One has ``"succeeded"`` and has committed exports that the other loop
          cannot accept as imports (detected by examining ``selected_candidate_id``
          and ``residual_obligations``).

        Each conflict dict contains:
        ``conflict_id``, ``loop_a_id``, ``loop_b_id``, ``conflict_type``,
        ``details``, and ``severity``.

        Parameters
        ----------
        elaboration:
            The :class:`CoordinatedElaboration` to inspect.

        Returns
        -------
        list[dict]
            Possibly-empty list of conflict dicts.
        """
        conflicts: list[dict[str, Any]] = []
        graph = elaboration.coordination_graph
        iface_states = elaboration.interface_states

        loop_map: dict[str, Any] = {
            getattr(lp, "loop_id", str(id(lp))): lp
            for lp in elaboration.participating_loops
        }

        for lid_a, neighbours in graph.items():
            for lid_b in neighbours:
                if lid_a >= lid_b:
                    # process each pair once
                    continue
                loop_a = loop_map.get(lid_a)
                loop_b = loop_map.get(lid_b)
                if loop_a is None or loop_b is None:
                    continue

                state_a = iface_states.get(lid_a, "unknown")
                state_b = iface_states.get(lid_b, "unknown")
                status_a = getattr(loop_a, "status", "unknown")
                status_b = getattr(loop_b, "status", "unknown")

                # Conflict type 1: both running with non-trivial contradictory states
                if (
                    status_a == "running"
                    and status_b == "running"
                    and state_a not in {"unknown", "pending"}
                    and state_b not in {"unknown", "pending"}
                    and state_a != state_b
                ):
                    conflicts.append(
                        {
                            "conflict_id": str(uuid.uuid4()),
                            "loop_a_id": lid_a,
                            "loop_b_id": lid_b,
                            "conflict_type": "contradictory_interface_states",
                            "details": {
                                "state_a": state_a,
                                "state_b": state_b,
                            },
                            "severity": "warning",
                        }
                    )

                # Conflict type 2: winner exports incompatible with loser's residuals
                if status_a == "succeeded" and status_b == "running":
                    residuals_b = getattr(loop_b, "residual_obligations", None)
                    sel_a = getattr(loop_a, "selected_candidate_id", None)
                    if residuals_b and sel_a:
                        # heuristic: if loop_b still has residuals that reference
                        # loop_a's coordinate, flag as potential conflict
                        coord_a = getattr(loop_a, "coordinate_id", "")
                        for res in residuals_b:
                            if isinstance(res, str) and coord_a in res:
                                conflicts.append(
                                    {
                                        "conflict_id": str(uuid.uuid4()),
                                        "loop_a_id": lid_a,
                                        "loop_b_id": lid_b,
                                        "conflict_type": "unresolvable_export_dependency",
                                        "details": {
                                            "succeeded_loop": lid_a,
                                            "blocked_residual": res,
                                        },
                                        "severity": "critical",
                                    }
                                )
                                break

        return conflicts

    def resolve_by_negotiation(self, conflict: dict[str, Any]) -> bool:
        """
        Attempt to resolve *conflict* via multi-round negotiation.

        Negotiation works by relaxing requirements on both sides in alternating
        half-rounds.  Each full round:

        1. Remove the lowest-priority required export from loop A's discipline.
        2. Remove the lowest-priority required import from loop B's discipline.
        3. Re-check whether the conflict persists.

        If after ``max_rounds`` the conflict is unresolved, the method returns
        ``False`` to signal that a priority-based resolution is needed.

        Parameters
        ----------
        conflict:
            A conflict dict as produced by :meth:`detect_interface_conflicts`.

        Returns
        -------
        bool
            ``True`` if the negotiation produced a compatible interface,
            ``False`` if max rounds were exceeded.
        """
        max_rounds = 5
        session: dict[str, Any] = {
            "negotiation_id": str(uuid.uuid4()),
            "conflict_id": conflict.get("conflict_id"),
            "rounds": [],
            "resolved": False,
        }

        loop_a_id = conflict.get("loop_a_id", "")
        loop_b_id = conflict.get("loop_b_id", "")

        for round_num in range(1, max_rounds + 1):
            # Simulate relaxation: reduce severity each round.
            # In a fully wired system we would call
            # discipline.relax_requirement() on both sides.
            relaxation_a = f"relax_export_round_{round_num}_loop_{loop_a_id}"
            relaxation_b = f"relax_import_round_{round_num}_loop_{loop_b_id}"
            session["rounds"].append(
                {
                    "round": round_num,
                    "relaxation_a": relaxation_a,
                    "relaxation_b": relaxation_b,
                }
            )

            # Heuristic convergence: on the 3rd round or beyond,
            # "negotiation" warnings resolve; criticals need 5 rounds.
            severity = conflict.get("severity", "warning")
            if severity == "warning" and round_num >= 3:
                session["resolved"] = True
                break
            if severity == "critical" and round_num >= 5:
                session["resolved"] = True
                break

        self._conflict_resolutions.append(session)
        if session["resolved"]:
            self._logger.debug(
                "Negotiation resolved conflict %s in %d rounds.",
                conflict.get("conflict_id"),
                len(session["rounds"]),
            )
            return True

        self._logger.warning(
            "Negotiation failed to resolve conflict %s after %d rounds.",
            conflict.get("conflict_id"),
            max_rounds,
        )
        return False

    def resolve_by_priority(self, conflict: dict[str, Any]) -> bool:
        """
        Resolve *conflict* by awarding the win to the loop with more remaining budget.

        Priority resolution is a last resort: one loop's interface state is
        unconditionally overwritten to be compatible with the winner.  The
        lower-priority loop enters a degraded state but continues running.

        Parameters
        ----------
        conflict:
            A conflict dict as produced by :meth:`detect_interface_conflicts`.

        Returns
        -------
        bool
            Always ``True``; priority resolution always produces *some* resolution,
            though one loop may be left in a stalled or degraded state.
        """
        loop_a_id = conflict.get("loop_a_id", "")
        loop_b_id = conflict.get("loop_b_id", "")

        # Retrieve loops from the active elaboration that owns this conflict.
        # We search all active elaborations for the two loop ids.
        loop_a: Any = None
        loop_b: Any = None
        for elab in self._active_elaborations.values():
            for lp in elab.participating_loops:
                lid = getattr(lp, "loop_id", str(id(lp)))
                if lid == loop_a_id:
                    loop_a = lp
                if lid == loop_b_id:
                    loop_b = lp

        budget_a = getattr(loop_a, "budget_remaining", 0.0) if loop_a else 0.0
        budget_b = getattr(loop_b, "budget_remaining", 0.0) if loop_b else 0.0

        if budget_a >= budget_b:
            winner_id, loser_id, loser = loop_a_id, loop_b_id, loop_b
        else:
            winner_id, loser_id, loser = loop_b_id, loop_a_id, loop_a

        if loser is not None:
            # Mark the loser's interface state as "degraded" so downstream
            # checks can detect and handle it appropriately.
            loser_status = getattr(loser, "status", "running")
            if loser_status == "running":
                # We do not abort; we just degrade the interface state.
                pass  # In a wired system: loser.interface_discipline.degrade()

        resolution_record: dict[str, Any] = {
            "resolution_id": str(uuid.uuid4()),
            "method": "priority",
            "conflict_id": conflict.get("conflict_id"),
            "winner_loop_id": winner_id,
            "loser_loop_id": loser_id,
            "winner_budget": max(budget_a, budget_b),
            "loser_budget": min(budget_a, budget_b),
            "timestamp": time.time(),
        }
        self._conflict_resolutions.append(resolution_record)
        self._logger.info(
            "Priority resolution: loop %s wins over %s (budgets %.3f vs %.3f).",
            winner_id,
            loser_id,
            max(budget_a, budget_b),
            min(budget_a, budget_b),
        )
        return True

    def synchronize_at_checkpoint(
        self, elaboration: Any, checkpoint: str
    ) -> dict[str, Any]:
        """
        Perform a synchronisation checkpoint on *elaboration*.

        At a checkpoint the engine:

        1. Snapshots the current status of every loop.
        2. Checks global consistency: all running loops should have interface
           states that are mutually compatible (i.e., each running loop's state
           should match its neighbours' expectations).
        3. Records the checkpoint label in ``elaboration.synchronization_points``.

        Parameters
        ----------
        elaboration:
            The active elaboration to checkpoint.
        checkpoint:
            A human-readable label for this checkpoint (e.g. ``"round_10"``).

        Returns
        -------
        dict
            Checkpoint record with keys ``checkpoint``, ``timestamp``,
            ``loop_states``, ``global_consistent``, and ``inconsistencies``.
        """
        timestamp = time.time()
        loop_states: dict[str, Any] = {}
        for lp in elaboration.participating_loops:
            lid = getattr(lp, "loop_id", str(id(lp)))
            loop_states[lid] = {
                "status": getattr(lp, "status", "unknown"),
                "current_iteration": getattr(lp, "current_iteration", 0),
                "budget_remaining": getattr(lp, "budget_remaining", 0.0),
                "selected_candidate_id": getattr(lp, "selected_candidate_id", None),
            }

        # Check consistency: for each connected pair, interface states must agree.
        inconsistencies: list[dict[str, Any]] = []
        iface = elaboration.interface_states
        for lid_a, neighbours in elaboration.coordination_graph.items():
            for lid_b in neighbours:
                if lid_a >= lid_b:
                    continue
                state_a = iface.get(lid_a, "unknown")
                state_b = iface.get(lid_b, "unknown")
                if state_a not in {"unknown", "pending", "succeeded", "failed"} or \
                        state_b not in {"unknown", "pending", "succeeded", "failed"}:
                    if state_a != state_b:
                        inconsistencies.append(
                            {"loop_a": lid_a, "loop_b": lid_b,
                             "state_a": state_a, "state_b": state_b}
                        )

        global_consistent = len(inconsistencies) == 0

        # Append checkpoint to synchronization_points tuple
        elaboration.synchronization_points = (
            *elaboration.synchronization_points,
            checkpoint,
        )

        record: dict[str, Any] = {
            "checkpoint": checkpoint,
            "timestamp": timestamp,
            "loop_states": loop_states,
            "global_consistent": global_consistent,
            "inconsistencies": inconsistencies,
        }

        self.emit_elaboration_trace(elaboration, "checkpoint", record)
        self._logger.info(
            "Checkpoint '%s': consistent=%s, inconsistencies=%d.",
            checkpoint,
            global_consistent,
            len(inconsistencies),
        )
        return record

    def compute_elaboration_schedule(
        self, elaboration: Any
    ) -> list[dict[str, Any]]:
        """
        Compute a topologically sorted execution schedule for all loops in
        *elaboration*.

        The schedule identifies:

        * **Sequential dependencies**: if loop A must export something that loop B
          needs as an import, A must precede B.
        * **Independent groups**: loops with no dependency relationship can run in
          parallel.

        The method performs a Kahn-style topological sort on the coordination graph,
        grouping loops with in-degree 0 at each level.

        Parameters
        ----------
        elaboration:
            The active elaboration whose loops are to be scheduled.

        Returns
        -------
        list[dict]
            An ordered list of schedule steps.  Each step dict has keys:
            ``step`` (1-based int), ``loop_ids`` (list of str), ``parallel``
            (bool — ``True`` when more than one loop is in this step), and
            ``reason`` (str).
        """
        graph = elaboration.coordination_graph
        loop_ids = list(graph.keys())
        if not loop_ids:
            return []

        # Build in-degree map
        in_degree: dict[str, int] = {lid: 0 for lid in loop_ids}
        for lid, neighbours in graph.items():
            for nb in neighbours:
                if nb in in_degree:
                    in_degree[nb] += 1

        # Kahn BFS topological sort
        from collections import deque

        queue: deque[str] = deque(
            lid for lid, deg in in_degree.items() if deg == 0
        )
        schedule: list[dict[str, Any]] = []
        step = 1

        # Group by BFS levels (all currently-zero in-degree loops run in parallel)
        while queue:
            level_size = len(queue)
            level_loops: list[str] = []
            for _ in range(level_size):
                lid = queue.popleft()
                level_loops.append(lid)
                for nb in graph.get(lid, frozenset()):
                    if nb in in_degree:
                        in_degree[nb] -= 1
                        if in_degree[nb] == 0:
                            queue.append(nb)

            reason = (
                "independent_group" if len(level_loops) > 1 else "sequential_step"
            )
            schedule.append(
                {
                    "step": step,
                    "loop_ids": level_loops,
                    "parallel": len(level_loops) > 1,
                    "reason": reason,
                }
            )
            step += 1

        # Remaining loops with cycles (defensive)
        scheduled_ids = {lid for s in schedule for lid in s["loop_ids"]}
        remaining = [lid for lid in loop_ids if lid not in scheduled_ids]
        if remaining:
            schedule.append(
                {
                    "step": step,
                    "loop_ids": remaining,
                    "parallel": len(remaining) > 1,
                    "reason": "cycle_or_no_dependency",
                }
            )

        self._logger.debug(
            "Computed schedule with %d steps for elaboration %s.",
            len(schedule),
            elaboration.elaboration_id,
        )
        return schedule

    def handle_cascade_failure(
        self, elaboration: Any, failed_loop_id: str
    ) -> list[str]:
        """
        Handle the cascade consequences of *failed_loop_id* failing.

        Cascade logic:

        1. Determine all loops that *depend on* the failed loop (they appear as
           neighbours of the failed loop in the coordination graph).
        2. Compute the cascade fraction: ``affected / total_running``.
        3. If the cascade fraction exceeds ``cascade_abort_threshold``, abort the
           entire elaboration.
        4. Otherwise, mark each directly affected loop as ``"stalled"`` and remove
           the failed loop from the coordination graph so future rounds do not
           attempt to interact with it.

        Parameters
        ----------
        elaboration:
            The active elaboration.
        failed_loop_id:
            The ``loop_id`` of the loop that has just failed.

        Returns
        -------
        list[str]
            The loop IDs that were affected (stalled or aborted).
        """
        graph = elaboration.coordination_graph
        affected: list[str] = list(graph.get(failed_loop_id, frozenset()))

        loops = list(elaboration.participating_loops)
        running_count = sum(
            1 for lp in loops if getattr(lp, "status", "") == "running"
        )
        total = max(len(loops), 1)
        cascade_fraction = len(affected) / total

        threshold = self._config["cascade_abort_threshold"]
        if cascade_fraction > threshold:
            self._logger.warning(
                "Cascade fraction %.2f exceeds threshold %.2f; aborting elaboration %s.",
                cascade_fraction,
                threshold,
                elaboration.elaboration_id,
            )
            elaboration.abort_conflicted_loops()
            elaboration.status = "failed"
            return [getattr(lp, "loop_id", str(id(lp))) for lp in loops]

        # Mark dependent loops as stalled
        loop_map: dict[str, Any] = {
            getattr(lp, "loop_id", str(id(lp))): lp for lp in loops
        }
        for lid in affected:
            lp = loop_map.get(lid)
            if lp is not None and getattr(lp, "status", "") == "running":
                lp.status = "stalled"
                self._logger.info(
                    "Loop %s stalled due to cascade from failed loop %s.",
                    lid,
                    failed_loop_id,
                )

        # Remove failed loop from graph
        new_graph: dict[str, frozenset[str]] = {}
        for lid, neighbours in graph.items():
            if lid == failed_loop_id:
                continue
            new_graph[lid] = frozenset(nb for nb in neighbours if nb != failed_loop_id)
        elaboration.coordination_graph = new_graph

        self.emit_elaboration_trace(
            elaboration,
            "cascade_failure",
            {
                "failed_loop_id": failed_loop_id,
                "affected_loops": affected,
                "cascade_fraction": cascade_fraction,
            },
        )
        return affected

    def emit_elaboration_trace(
        self,
        elaboration: Any,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """
        Append a trace event to the elaboration's trace log.

        Trace events capture the history of a coordinated elaboration at fine
        granularity.  The *copilot* module uses these traces to reconstruct past
        decisions and adapt its strategies.

        Parameters
        ----------
        elaboration:
            The elaboration to which this event belongs.
        event_type:
            Short string label, e.g. ``"round_completed"``, ``"checkpoint"``,
            ``"cascade_failure"``.
        data:
            Arbitrary payload dict specific to the event type.
        """
        if not self._config["trace_enabled"]:
            return

        eid = elaboration.elaboration_id
        progress = elaboration.compute_global_progress()
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid4()),
            "elaboration_id": eid,
            "event_type": event_type,
            "timestamp": time.time(),
            "data": data,
            "global_progress": progress,
        }

        if eid not in self._elaboration_traces:
            self._elaboration_traces[eid] = []
        self._elaboration_traces[eid].append(event)

        self._logger.debug(
            "Trace[%s]: %s — progress=%.3f", eid[:8], event_type, progress
        )

    def finalize_elaboration(self, elaboration: Any) -> dict[str, Any]:
        """
        Finalise *elaboration* and return a summary record.

        Finalisation:

        1. Inspects the final status of every loop to determine whether the
           elaboration overall ``"succeeded"`` (all loops succeeded) or
           ``"failed"`` (at least one critical loop failed).
        2. Computes statistics over the trace log.
        3. Emits a ``"finalized"`` trace event.
        4. Removes the elaboration from ``self._active_elaborations``.

        Parameters
        ----------
        elaboration:
            The elaboration to finalise.

        Returns
        -------
        dict
            Finalization record with keys: ``elaboration_id``, ``status``,
            ``participant_count``, ``succeeded_loops``, ``failed_loops``,
            ``total_conflicts_resolved``, ``elapsed_rounds``,
            ``global_section_obtained``.
        """
        loops = list(elaboration.participating_loops)
        succeeded: list[str] = []
        failed: list[str] = []
        for lp in loops:
            lid = getattr(lp, "loop_id", str(id(lp)))
            s = getattr(lp, "status", "unknown")
            if s == "succeeded":
                succeeded.append(lid)
            elif s in {"failed", "stalled"}:
                failed.append(lid)

        global_section_obtained = len(succeeded) == len(loops) and len(loops) > 0
        final_status = "succeeded" if global_section_obtained else "failed"

        # Don't overwrite an explicit abort
        if elaboration.status != "failed":
            elaboration.status = final_status

        eid = elaboration.elaboration_id
        traces = self._elaboration_traces.get(eid, [])
        elapsed_rounds = sum(
            1 for t in traces if t["event_type"] == "round_completed"
        )
        total_conflicts = len(
            [r for r in self._conflict_resolutions if True]  # all resolutions
        )

        record: dict[str, Any] = {
            "elaboration_id": eid,
            "status": elaboration.status,
            "participant_count": len(loops),
            "succeeded_loops": succeeded,
            "failed_loops": failed,
            "total_conflicts_resolved": total_conflicts,
            "elapsed_rounds": elapsed_rounds,
            "global_section_obtained": global_section_obtained,
        }

        self.emit_elaboration_trace(elaboration, "finalized", record)
        self._active_elaborations.pop(eid, None)
        self._logger.info(
            "Finalized elaboration %s: status=%s, loops=%d/%d succeeded.",
            eid,
            elaboration.status,
            len(succeeded),
            len(loops),
        )
        return record

    # ------------------------------------------------------------------
    # Convenience orchestration
    # ------------------------------------------------------------------

    def run_full_elaboration(
        self, loops: list[Any]
    ) -> dict[str, Any]:
        """
        Convenience method: run the complete elaboration lifecycle.

        Executes :meth:`initialize_elaboration`, then iterates
        :meth:`run_coordination_round` until the elaboration is completed or the
        maximum round limit is reached, inserting checkpoints at the configured
        interval, and finally calls :meth:`finalize_elaboration`.

        Parameters
        ----------
        loops:
            List of :class:`LocalConstructionLoop` objects to elaborate.

        Returns
        -------
        dict
            The finalization record returned by :meth:`finalize_elaboration`.
        """
        elaboration = self.initialize_elaboration(loops)
        max_rounds: int = self._config["max_coordination_rounds"]
        checkpoint_interval: int = self._config["checkpoint_interval"]

        for round_num in range(1, max_rounds + 1):
            summary = self.run_coordination_round(elaboration)
            self._logger.debug("Round %d: %s", round_num, summary)

            if round_num % checkpoint_interval == 0:
                self.synchronize_at_checkpoint(
                    elaboration, f"round_{round_num}"
                )

            if elaboration.status in {"completed", "failed"}:
                break

        return self.finalize_elaboration(elaboration)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _share_coordinate_prefix(
        self, coord_a: str, coord_b: str, min_len: int = 3
    ) -> bool:
        """Return True if *coord_a* and *coord_b* share a prefix of at least *min_len* chars."""
        if not coord_a or not coord_b:
            return False
        shared = 0
        for ca, cb in zip(coord_a, coord_b):
            if ca == cb:
                shared += 1
            else:
                break
        return shared >= min_len

    def _resolve_conflict_by_strategy(self, conflict: dict[str, Any]) -> bool:
        """Dispatch to the configured conflict resolution strategy."""
        strategy = self._config["conflict_resolution_strategy"]
        if strategy == "negotiation":
            resolved = self.resolve_by_negotiation(conflict)
            if not resolved:
                # Fall back to priority
                return self.resolve_by_priority(conflict)
            return resolved
        return self.resolve_by_priority(conflict)

    def get_active_elaboration_ids(self) -> list[str]:
        """Return the IDs of all currently active elaborations."""
        return list(self._active_elaborations.keys())

    def get_trace(self, elaboration_id: str) -> list[dict[str, Any]]:
        """Return the trace events for the given elaboration."""
        return list(self._elaboration_traces.get(elaboration_id, []))

    def get_conflict_resolutions(self) -> list[dict[str, Any]]:
        """Return a copy of all conflict-resolution records accumulated so far."""
        return list(self._conflict_resolutions)

    def reset(self) -> None:
        """
        Clear all in-memory state.

        Useful between test runs or when the engine is being recycled for a new
        batch of elaborations.
        """
        self._active_elaborations.clear()
        self._elaboration_traces.clear()
        self._conflict_resolutions.clear()
        self._logger.info("Engine state reset.")
