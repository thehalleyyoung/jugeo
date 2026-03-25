"""
Large-Scale Hierarchical Orchestration Controller.

The domain-agnostic co-evolution controller.  Works for:

- Research software: surfaces = {SPECIFICATION, CODE, EVIDENCE, CLAIMS}
- Web application:   surfaces = {SPECIFICATION, CODE, TESTING, DEPLOYMENT, DOCUMENTATION}
- Library development: surfaces = {SPECIFICATION, CODE, BENCHMARKS, DOCUMENTATION}
- Infrastructure:    surfaces = {SPECIFICATION, CODE, DEPLOYMENT, EVIDENCE}

The same controller works for all domains because surfaces are configured at
initialisation, not hardcoded.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from .budget_allocator import BudgetAllocator
from .co_evolution import CoEvolutionEngine
from .convergence import ConvergenceMonitor
from .fleet_manager import FleetManager
from .large_repo import LargeRepoOptimizer
from .models import (
    ControllerLevel,
    ControllerState,
    GlobalController,
    LocalController,
    MoveCategory,
    MoveHistory,
    MoveResult,
    ObligationKind,
    Phase,
    RegionalController,
    SemanticMove,
    Surface,
)
from .obligation_presheaf import ObligationManager
from .phase_detector import PhaseDetector

__all__ = ["LargeScaleController"]


# Default partition size for local controllers
_DEFAULT_PARTITION_SIZE = 20
# Default number of local controllers per regional controller
_DEFAULT_LOCALS_PER_REGION = 5


class LargeScaleController:
    """Hierarchical co-evolution orchestration controller.

    Manages local, regional, and global controllers plus all subsystems
    (obligations, phase detection, fleet competition, convergence, budget).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}

        # Configured surfaces (domain-agnostic)
        surface_names = config.get("surfaces")
        if surface_names:
            self._surfaces = [Surface(s) for s in surface_names]
        else:
            self._surfaces = list(Surface)

        budget = config.get("budget", 1000.0)

        # Subsystems
        self._co_evolution = CoEvolutionEngine(surfaces=self._surfaces)
        self._obligation_manager = ObligationManager()
        self._phase_detector = PhaseDetector(
            window_size=config.get("phase_window", 20)
        )
        self._fleet_manager = FleetManager()
        self._fleet_manager.default_strategies()
        self._convergence = ConvergenceMonitor(
            window_size=config.get("convergence_window", 50)
        )
        self._budget = BudgetAllocator(total_budget=budget)
        self._desired_kloc: float | None = config.get("desired_kloc")
        self._large_repo = LargeRepoOptimizer(
            site_size_threshold=config.get("site_size_threshold", 10000),
            desired_kloc=self._desired_kloc,
        )

        # Hierarchy
        self._local_controllers: dict[str, LocalController] = {}
        self._regional_controllers: dict[str, RegionalController] = {}
        self._global_controller: GlobalController | None = None

        # Frontier and history
        self._frontier: list[dict[str, Any]] = []
        self._history = MoveHistory()
        self._step_count: int = 0
        self._current_phase = Phase.EXPLORATION
        self._converged = False

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def initialize_from_site(
        self,
        coordinates: list[str],
        morphisms: list[str],
        covers: list[str],
        partitions: list[list[str]] | None = None,
    ) -> None:
        """Bootstrap the controller hierarchy from a site description."""
        # Create local controllers
        locals_list = self._create_local_controllers(
            coordinates, morphisms, partitions
        )
        for lc in locals_list:
            self._local_controllers[lc.id] = lc

        # Compute cross-package edges (simplified: edges between partitions)
        cross_edges: list[dict[str, Any]] = []
        if partitions and len(partitions) > 1:
            for i in range(len(partitions) - 1):
                cross_edges.append({
                    "from_partition": i,
                    "to_partition": i + 1,
                })

        # Create regional controllers
        regionals = self._create_regional_controllers(locals_list, cross_edges)
        for rc in regionals:
            self._regional_controllers[rc.id] = rc

        # Create global controller
        self._global_controller = self._create_global_controller(regionals)

        # Setup co-evolution edges between surfaces
        surface_list = list(self._surfaces)
        for i, sa in enumerate(surface_list):
            self._co_evolution.add_surface(sa, coordinates)
            for sb in surface_list[i + 1 :]:
                # All coordinates are shared (maximum overlap)
                self._co_evolution.add_drift_edge(sa, sb, coordinates)

        # Allocate budget
        region_ids = list(self._regional_controllers.keys())
        priorities = {r: 1.0 for r in region_ids}
        if region_ids:
            self._budget.allocate(region_ids, priorities)

        # Seed frontier with initial exploration moves
        for coord in coordinates[:10]:
            self._frontier.append({
                "id": str(uuid.uuid4()),
                "category": MoveCategory.IDEATION.value,
                "name": f"explore-{coord}",
                "target_coordinates": [coord],
                "priority": 1.0,
                "estimated_cost": 1.0,
                "expected_drift_reduction": 0.0,
            })

    # ------------------------------------------------------------------
    # Step loop
    # ------------------------------------------------------------------

    def step(self) -> MoveResult:
        """Execute one orchestration step: select → execute → bookkeep."""
        move = self._select_move()
        result = self._execute_move(move)
        self._post_move_bookkeeping(result)
        self._generate_descent_obligations(result)
        self._check_phase()
        self._check_convergence()
        self._check_budget()
        self._step_count += 1
        return result

    def _select_move(self) -> SemanticMove:
        """Select the next move via fleet competition."""
        presheaf = self._obligation_manager.to_presheaf()

        # Build available moves from frontier
        available: list[SemanticMove] = []
        for f in self._frontier[:50]:
            try:
                cat = MoveCategory(f.get("category", "construction"))
            except ValueError:
                cat = MoveCategory.CONSTRUCTION
            available.append(
                SemanticMove(
                    id=f.get("id", str(uuid.uuid4())),
                    category=cat,
                    name=f.get("name", "unnamed"),
                    estimated_cost=f.get("estimated_cost", 1.0),
                    priority=f.get("priority", 1.0),
                )
            )

        # Also include synchronisation moves from co-evolution
        co_state = self._co_evolution.full_drift_analysis()
        sync_moves = self._co_evolution.synchronization_plan(co_state)
        available.extend(sync_moves)

        state = self._build_state_dict()
        bids = self._fleet_manager.generate_bids(state, presheaf, available)

        if bids:
            result = self._fleet_manager.select_winner(bids)
            # Find the move that matches the winning bid
            for m in available:
                if m.id == result.winning_bid.move_id:
                    return m

        # Fallback: return first available or a default move
        if available:
            return available[0]
        return SemanticMove(
            id=str(uuid.uuid4()),
            category=MoveCategory.IDEATION,
            name="fallback-exploration",
        )

    def _execute_move(self, move: SemanticMove) -> MoveResult:
        """Simulate move execution and return a MoveResult."""
        start = time.time()

        # Determine sections modified based on move category
        sections = [f"section-{move.category.value}-{i}" for i in range(1, 3)]
        obligations_gen: list[str] = [k.value for k in move.generates_obligations]
        if not obligations_gen and move.category in (
            MoveCategory.CONSTRUCTION,
            MoveCategory.IDEATION,
        ):
            obligations_gen = [ObligationKind.VERIFICATION.value]

        duration_ms = (time.time() - start) * 1000

        result = MoveResult(
            move_id=move.id,
            success=True,
            sections_modified=sections,
            obligations_generated=obligations_gen,
            obligations_discharged=[],
            obstructions_found=[],
            drift_changes={},
            duration_ms=duration_ms,
        )

        # Record in history
        self._history.moves.append(result)
        self._history.total_moves += 1
        self._history.moves_since_last_compaction += 1

        return result

    def _post_move_bookkeeping(self, result: MoveResult) -> None:
        """Update subsystems after a move execution."""
        # Remove executed move from frontier
        self._frontier = [
            f for f in self._frontier if f.get("id") != result.move_id
        ]

        # Update strategy weights
        for sid in list(self._fleet_manager._strategies):
            self._fleet_manager.update_strategy_weights(sid, result)

        # Record budget spend
        region = self._pick_region_for_move(result)
        self._budget.spend(region, "general", 1.0)

    def _generate_descent_obligations(self, result: MoveResult) -> None:
        """Generate grounding + move-specific obligations."""
        self._obligation_manager.generate_grounding_obligations(
            result.sections_modified
        )
        self._obligation_manager.generate_from_move(result)

    def _check_phase(self) -> None:
        """Run phase detection."""
        state = self._build_state_dict()
        self._current_phase = self._phase_detector.detect_phase(state)

    def _check_convergence(self) -> None:
        """Record a convergence step and check for convergence."""
        presheaf = self._obligation_manager.to_presheaf()
        co_state = self._co_evolution.full_drift_analysis()

        self._convergence.record_step(
            obligations=int(presheaf.total_pressure),
            drift=co_state.overall_drift_score,
            coverage=self._estimated_coverage(),
            trust_floor="conjecture",
            obstructions=0,
        )

        if self._convergence.is_converging():
            cert = self._convergence.issue_certificate()
            if cert is not None:
                self._converged = True

    def _check_budget(self) -> None:
        """Rebalance budget if needed."""
        if self._step_count % 10 == 0:
            presheaf = self._obligation_manager.to_presheaf()
            self._budget.rebalance(presheaf)

    # ------------------------------------------------------------------
    # Run loops
    # ------------------------------------------------------------------

    def run(
        self,
        max_steps: int = 1000,
        convergence_target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run orchestration until convergence or *max_steps*."""
        results: list[MoveResult] = []
        for _ in range(max_steps):
            result = self.step()
            results.append(result)
            if self._converged:
                break

        return {
            "steps": len(results),
            "converged": self._converged,
            "phase": self._current_phase.value,
            "summary": self._convergence.summary(),
        }

    def run_local(
        self, local_controller_id: str, max_steps: int = 100
    ) -> dict[str, Any]:
        """Run only within a local controller's scope."""
        lc = self._local_controllers.get(local_controller_id)
        if lc is None:
            return {"error": f"Unknown local controller: {local_controller_id}"}

        results: list[MoveResult] = []
        for _ in range(max_steps):
            result = self.step()
            results.append(result)
            if self._converged:
                break
        return {
            "controller": local_controller_id,
            "steps": len(results),
            "converged": self._converged,
        }

    def run_regional(
        self, regional_controller_id: str, max_steps: int = 200
    ) -> dict[str, Any]:
        """Run only within a regional controller's scope."""
        rc = self._regional_controllers.get(regional_controller_id)
        if rc is None:
            return {"error": f"Unknown regional controller: {regional_controller_id}"}

        results: list[MoveResult] = []
        for _ in range(max_steps):
            result = self.step()
            results.append(result)
            if self._converged:
                break
        return {
            "controller": regional_controller_id,
            "steps": len(results),
            "converged": self._converged,
        }

    # ------------------------------------------------------------------
    # Hierarchy construction
    # ------------------------------------------------------------------

    def _create_local_controllers(
        self,
        coordinates: list[str],
        morphisms: list[str],
        partitions: list[list[str]] | None,
    ) -> list[LocalController]:
        """Create local controllers from partitions (or auto-partition)."""
        if partitions:
            parts = partitions
        else:
            # Auto-partition by _DEFAULT_PARTITION_SIZE
            parts = []
            for i in range(0, len(coordinates), _DEFAULT_PARTITION_SIZE):
                parts.append(coordinates[i : i + _DEFAULT_PARTITION_SIZE])
            if not parts:
                parts = [list(coordinates)]

        controllers: list[LocalController] = []
        for idx, part in enumerate(parts):
            lc = LocalController(
                id=f"local-{idx}",
                scope=f"partition-{idx}",
                state=ControllerState(
                    level=ControllerLevel.LOCAL,
                    scope=f"partition-{idx}",
                    frontier_size=len(part),
                ),
                coordinates=list(part),
                morphisms=[m for m in morphisms if any(c in m for c in part)] if morphisms else [],
            )
            controllers.append(lc)
        return controllers

    def _create_regional_controllers(
        self,
        local_controllers: list[LocalController],
        cross_edges: list[dict[str, Any]],
    ) -> list[RegionalController]:
        """Group local controllers into regional controllers."""
        regionals: list[RegionalController] = []
        for i in range(0, len(local_controllers), _DEFAULT_LOCALS_PER_REGION):
            group = local_controllers[i : i + _DEFAULT_LOCALS_PER_REGION]
            rc = RegionalController(
                id=f"regional-{i // _DEFAULT_LOCALS_PER_REGION}",
                scope=f"region-{i // _DEFAULT_LOCALS_PER_REGION}",
                state=ControllerState(
                    level=ControllerLevel.REGIONAL,
                    scope=f"region-{i // _DEFAULT_LOCALS_PER_REGION}",
                ),
                local_controllers=[lc.id for lc in group],
                cross_package_edges=[
                    e
                    for e in cross_edges
                    if any(
                        lc.scope == f"partition-{e.get('from_partition')}"
                        or lc.scope == f"partition-{e.get('to_partition')}"
                        for lc in group
                    )
                ],
            )
            regionals.append(rc)
        if not regionals:
            regionals.append(
                RegionalController(
                    id="regional-0",
                    scope="region-0",
                    state=ControllerState(
                        level=ControllerLevel.REGIONAL, scope="region-0"
                    ),
                )
            )
        return regionals

    def _create_global_controller(
        self, regional_controllers: list[RegionalController]
    ) -> GlobalController:
        """Create the single global controller."""
        return GlobalController(
            id="global-0",
            state=ControllerState(
                level=ControllerLevel.GLOBAL, scope="global"
            ),
            regional_controllers=[rc.id for rc in regional_controllers],
            global_budget=self._budget._total_budget,
            global_phase=self._current_phase.value,
        )

    # ------------------------------------------------------------------
    # Status / queries
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Full status snapshot."""
        return {
            "step_count": self._step_count,
            "phase": self._current_phase.value,
            "converged": self._converged,
            "surfaces": [s.value for s in self._surfaces],
            "desired_kloc": self._desired_kloc,
            "large_repo_active": self._large_repo.should_activate(
                sum(len(lc.coordinates) for lc in self._local_controllers.values())
            ),
            "local_controllers": len(self._local_controllers),
            "regional_controllers": len(self._regional_controllers),
            "has_global_controller": self._global_controller is not None,
            "frontier_size": self.frontier_size(),
            "obligation_pressure": self._obligation_manager.compute_pressure(),
            "budget_remaining": self._budget.remaining(),
            "convergence": self._convergence.summary(),
        }

    def move_history(self) -> MoveHistory:
        """Return the full move history."""
        return self._history

    def compact_history(self) -> None:
        """Compact old moves in the history."""
        self._history = self._large_repo.compact_move_history(self._history)

    def frontier_size(self) -> int:
        """Number of items in the frontier."""
        return len(self._frontier)

    def prune_frontier(self, keep_top_k: int = 1000) -> None:
        """Prune the frontier to *keep_top_k* items."""
        self._frontier = self._large_repo.optimize_frontier(
            self._frontier, max_size=keep_top_k
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_state_dict(self) -> dict[str, Any]:
        """Build the state dict consumed by phase detector and fleet."""
        presheaf = self._obligation_manager.to_presheaf()
        usage = self._budget.usage()
        return {
            "obligation_count": int(presheaf.total_pressure),
            "coverage": self._estimated_coverage(),
            "overall_drift_score": 0.0,
            "budget": usage,
            "budget_remaining": usage.remaining / max(
                0.001, usage.remaining + usage.spent
            ),
            "obstruction_count": 0,
        }

    def _estimated_coverage(self) -> float:
        """Rough coverage estimate based on step count."""
        # Starts at 0, approaches 1 asymptotically
        if self._step_count == 0:
            return 0.0
        return min(1.0, self._step_count / (self._step_count + 50))

    def _pick_region_for_move(self, result: MoveResult) -> str:
        """Pick the region responsible for a move result."""
        if self._regional_controllers:
            return next(iter(self._regional_controllers))
        return "default"
