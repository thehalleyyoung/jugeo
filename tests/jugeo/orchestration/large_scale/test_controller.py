"""Tests for LargeScaleController."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from jugeo.orchestration.large_scale.models import (
    ControllerLevel,
    MoveHistory,
    MoveResult,
    Phase,
    Surface,
)
from jugeo.orchestration.large_scale.controller import LargeScaleController


def _coords(n: int) -> list[str]:
    return [f"coord-{i}" for i in range(n)]


def _morphisms(n: int) -> list[str]:
    return [f"morph-{i}" for i in range(n)]


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_init_default_config(self) -> None:
        ctrl = LargeScaleController()
        assert ctrl._surfaces == list(Surface)

    def test_init_custom_surfaces(self) -> None:
        ctrl = LargeScaleController(config={
            "surfaces": ["specification", "code", "evidence", "claims"],
        })
        assert len(ctrl._surfaces) == 4
        assert Surface.SPECIFICATION in ctrl._surfaces
        assert Surface.CODE in ctrl._surfaces


class TestInitializeFromSite:
    def test_initialize_from_site_small(self) -> None:
        ctrl = LargeScaleController()
        ctrl.initialize_from_site(
            coordinates=_coords(10),
            morphisms=_morphisms(5),
            covers=["c1"],
        )
        assert len(ctrl._local_controllers) >= 1
        assert len(ctrl._regional_controllers) >= 1
        assert ctrl._global_controller is not None

    def test_initialize_from_site_with_partitions(self) -> None:
        ctrl = LargeScaleController()
        parts = [_coords(5), [f"coord-extra-{i}" for i in range(5)]]
        all_coords = parts[0] + parts[1]
        ctrl.initialize_from_site(
            coordinates=all_coords,
            morphisms=[],
            covers=[],
            partitions=parts,
        )
        assert len(ctrl._local_controllers) == 2


# ---------------------------------------------------------------------------
# Step / run
# ---------------------------------------------------------------------------

class TestStep:
    def test_step_executes_move(self) -> None:
        ctrl = LargeScaleController(config={"budget": 100.0})
        ctrl.initialize_from_site(_coords(10), _morphisms(3), [])
        result = ctrl.step()
        assert isinstance(result, MoveResult)
        assert result.success is True

    def test_run_max_steps(self) -> None:
        ctrl = LargeScaleController(config={"budget": 500.0})
        ctrl.initialize_from_site(_coords(10), _morphisms(3), [])
        summary = ctrl.run(max_steps=5)
        assert summary["steps"] <= 5

    def test_run_convergence_detection(self) -> None:
        ctrl = LargeScaleController(config={"budget": 10000.0})
        ctrl.initialize_from_site(_coords(5), [], [])
        summary = ctrl.run(max_steps=10)
        assert "converged" in summary


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------

class TestHierarchy:
    def test_hierarchical_controllers_created(self) -> None:
        ctrl = LargeScaleController()
        ctrl.initialize_from_site(_coords(50), [], [])
        assert len(ctrl._local_controllers) >= 1
        assert len(ctrl._regional_controllers) >= 1
        assert ctrl._global_controller is not None

    def test_local_controller_scope(self) -> None:
        ctrl = LargeScaleController()
        ctrl.initialize_from_site(_coords(30), [], [])
        for lc in ctrl._local_controllers.values():
            assert lc.scope.startswith("partition-")
            assert lc.state.level == ControllerLevel.LOCAL

    def test_regional_controller_groups_locals(self) -> None:
        ctrl = LargeScaleController()
        ctrl.initialize_from_site(_coords(100), [], [])
        for rc in ctrl._regional_controllers.values():
            assert isinstance(rc.local_controllers, list)
            assert rc.state.level == ControllerLevel.REGIONAL

    def test_global_controller_has_regionals(self) -> None:
        ctrl = LargeScaleController()
        ctrl.initialize_from_site(_coords(100), [], [])
        gc = ctrl._global_controller
        assert gc is not None
        assert len(gc.regional_controllers) == len(ctrl._regional_controllers)


# ---------------------------------------------------------------------------
# Domain agnosticism
# ---------------------------------------------------------------------------

class TestDomainAgnostic:
    def test_research_software_surfaces(self) -> None:
        ctrl = LargeScaleController(config={
            "surfaces": ["specification", "code", "evidence", "claims"],
        })
        assert len(ctrl._surfaces) == 4

    def test_web_app_surfaces(self) -> None:
        ctrl = LargeScaleController(config={
            "surfaces": ["specification", "code", "deployment", "documentation"],
        })
        assert len(ctrl._surfaces) == 4

    def test_library_surfaces(self) -> None:
        ctrl = LargeScaleController(config={
            "surfaces": ["specification", "code", "benchmarks", "documentation"],
        })
        assert len(ctrl._surfaces) == 4

    def test_infrastructure_surfaces(self) -> None:
        ctrl = LargeScaleController(config={
            "surfaces": ["specification", "code", "deployment", "evidence"],
        })
        assert len(ctrl._surfaces) == 4

    def test_comet_h_special_case(self) -> None:
        """Comet-H is a special case with 4 surfaces."""
        ctrl = LargeScaleController(config={
            "surfaces": ["specification", "code", "evidence", "claims"],
        })
        ctrl.initialize_from_site(_coords(20), [], [])
        result = ctrl.step()
        assert result.success is True


# ---------------------------------------------------------------------------
# Status / history / frontier
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_returns_dict(self) -> None:
        ctrl = LargeScaleController()
        ctrl.initialize_from_site(_coords(10), [], [])
        status = ctrl.status()
        assert isinstance(status, dict)
        assert "step_count" in status
        assert "phase" in status
        assert "surfaces" in status

    def test_move_history(self) -> None:
        ctrl = LargeScaleController(config={"budget": 100.0})
        ctrl.initialize_from_site(_coords(10), [], [])
        ctrl.step()
        history = ctrl.move_history()
        assert isinstance(history, MoveHistory)
        assert history.total_moves >= 1

    def test_compact_history(self) -> None:
        ctrl = LargeScaleController(config={"budget": 1000.0})
        ctrl.initialize_from_site(_coords(10), [], [])
        for _ in range(5):
            ctrl.step()
        ctrl.compact_history()
        # History should still be a valid MoveHistory
        assert isinstance(ctrl._history, MoveHistory)

    def test_frontier_size(self) -> None:
        ctrl = LargeScaleController()
        ctrl.initialize_from_site(_coords(10), [], [])
        assert ctrl.frontier_size() >= 0

    def test_prune_frontier(self) -> None:
        ctrl = LargeScaleController()
        ctrl.initialize_from_site(_coords(10), [], [])
        ctrl.prune_frontier(keep_top_k=5)
        assert ctrl.frontier_size() <= 10  # originally ≤10 items
