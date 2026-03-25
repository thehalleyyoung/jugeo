"""Tests for CoEvolutionEngine."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from jugeo.orchestration.large_scale.models import (
    CoEvolutionState,
    DriftEdge,
    MoveCategory,
    Surface,
    SurfaceState,
)
from jugeo.orchestration.large_scale.co_evolution import CoEvolutionEngine


# ---------------------------------------------------------------------------
# Surface management
# ---------------------------------------------------------------------------

class TestAddSurface:
    def test_add_surface(self) -> None:
        engine = CoEvolutionEngine()
        engine.add_surface(Surface.CODE, ["c1", "c2", "c3"])
        state = engine._surfaces[Surface.CODE]
        assert state.surface == Surface.CODE
        assert state.coordinate_ids == ["c1", "c2", "c3"]
        assert state.version == 1

    def test_add_surface_updates_existing(self) -> None:
        engine = CoEvolutionEngine(surfaces=[Surface.CODE])
        engine.add_surface(Surface.CODE, ["c1", "c2"])
        state = engine._surfaces[Surface.CODE]
        assert state.version == 1
        assert state.coordinate_ids == ["c1", "c2"]


class TestAddDriftEdge:
    def test_add_drift_edge(self) -> None:
        engine = CoEvolutionEngine()
        engine.add_drift_edge(Surface.SPECIFICATION, Surface.CODE, ["c1"])
        assert len(engine._drift_edges_config) == 1
        sa, sb, overlap = engine._drift_edges_config[0]
        assert sa == Surface.SPECIFICATION
        assert sb == Surface.CODE
        assert overlap == ["c1"]


# ---------------------------------------------------------------------------
# Drift computation
# ---------------------------------------------------------------------------

class TestComputeDrift:
    def test_compute_drift_no_overlap(self) -> None:
        engine = CoEvolutionEngine()
        assert engine.compute_drift({"a": 1}, {"b": 2}, []) == 0.0

    def test_compute_drift_full_drift(self) -> None:
        engine = CoEvolutionEngine()
        sections_a = {"c1": 1, "c2": 2}
        sections_b: dict = {}
        drift = engine.compute_drift(sections_a, sections_b, ["c1", "c2"])
        assert drift == 1.0

    def test_compute_drift_partial(self) -> None:
        engine = CoEvolutionEngine()
        sections_a = {"c1": 1, "c2": 2}
        sections_b = {"c1": 1}
        drift = engine.compute_drift(sections_a, sections_b, ["c1", "c2"])
        assert drift == 0.5

    def test_compute_drift_no_drift(self) -> None:
        engine = CoEvolutionEngine()
        sections_a = {"c1": 1, "c2": 2}
        sections_b = {"c1": 1, "c2": 2}
        drift = engine.compute_drift(sections_a, sections_b, ["c1", "c2"])
        assert drift == 0.0

    def test_compute_drift_value_mismatch(self) -> None:
        engine = CoEvolutionEngine()
        sections_a = {"c1": 1}
        sections_b = {"c1": 99}
        drift = engine.compute_drift(sections_a, sections_b, ["c1"])
        assert drift == 1.0


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

class TestFullDriftAnalysis:
    def test_full_drift_analysis_empty(self) -> None:
        engine = CoEvolutionEngine()
        state = engine.full_drift_analysis()
        assert isinstance(state, CoEvolutionState)
        assert state.drift_edges == []
        assert state.overall_drift_score == 0.0
        assert state.is_synchronized is True

    def test_full_drift_analysis_with_surfaces(self) -> None:
        engine = CoEvolutionEngine()
        engine.add_surface(Surface.SPECIFICATION, ["c1", "c2", "c3"])
        engine.add_surface(Surface.CODE, ["c1", "c2"])
        engine.add_drift_edge(Surface.SPECIFICATION, Surface.CODE, ["c1", "c2"])
        state = engine.full_drift_analysis()
        assert len(state.drift_edges) == 1
        # c1 and c2 present in both → different versions but same key
        # Both surfaces have the coordinate, so drift depends on version equality
        assert isinstance(state.overall_drift_score, float)


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

class TestDetectSpecificationDrift:
    def test_above_threshold(self) -> None:
        edge = DriftEdge(
            surface_a=Surface.SPECIFICATION,
            surface_b=Surface.CODE,
            overlap_coordinates=["c1"],
            drift_score=0.5,
        )
        state = CoEvolutionState(drift_edges=[edge])
        engine = CoEvolutionEngine()
        result = engine.detect_specification_drift(state)
        assert len(result) == 1
        assert result[0].drift_score == 0.5

    def test_below_threshold(self) -> None:
        edge = DriftEdge(
            surface_a=Surface.SPECIFICATION,
            surface_b=Surface.CODE,
            overlap_coordinates=["c1"],
            drift_score=0.1,
        )
        state = CoEvolutionState(drift_edges=[edge])
        engine = CoEvolutionEngine()
        result = engine.detect_specification_drift(state)
        assert result == []


class TestDetectStaleSurfaces:
    def test_detect_stale_surfaces(self) -> None:
        old_time = time.time() - 200000.0
        ss = SurfaceState(
            surface=Surface.CODE,
            last_modified_at=old_time,
        )
        state = CoEvolutionState(surfaces={Surface.CODE.value: ss})
        engine = CoEvolutionEngine()
        stale = engine.detect_stale_surfaces(state, max_age_s=86400.0)
        assert Surface.CODE in stale

    def test_detect_fresh_surfaces(self) -> None:
        ss = SurfaceState(
            surface=Surface.CODE,
            last_modified_at=time.time(),
        )
        state = CoEvolutionState(surfaces={Surface.CODE.value: ss})
        engine = CoEvolutionEngine()
        stale = engine.detect_stale_surfaces(state, max_age_s=86400.0)
        assert stale == []


class TestDetectTrustViolations:
    def test_detect_trust_violations(self) -> None:
        ss_a = SurfaceState(surface=Surface.SPECIFICATION, trust_floor="proved")
        ss_b = SurfaceState(surface=Surface.CODE, trust_floor="conjecture")
        edge = DriftEdge(
            surface_a=Surface.SPECIFICATION,
            surface_b=Surface.CODE,
            overlap_coordinates=["c1"],
            drift_score=0.5,
        )
        state = CoEvolutionState(
            surfaces={
                Surface.SPECIFICATION.value: ss_a,
                Surface.CODE.value: ss_b,
            },
            drift_edges=[edge],
        )
        engine = CoEvolutionEngine()
        violations = engine.detect_trust_violations(state)
        assert len(violations) >= 1
        assert "trust floor mismatch" in violations[0]["issue"]


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------

class TestSynchronizationPlan:
    def test_synchronization_plan_generates_moves(self) -> None:
        edge = DriftEdge(
            surface_a=Surface.SPECIFICATION,
            surface_b=Surface.CODE,
            overlap_coordinates=["c1", "c2"],
            drift_score=0.6,
        )
        state = CoEvolutionState(drift_edges=[edge])
        engine = CoEvolutionEngine()
        moves = engine.synchronization_plan(state)
        assert len(moves) == 1
        assert moves[0].category == MoveCategory.GROUNDING


class TestIsSynchronized:
    def test_is_synchronized_true(self) -> None:
        edge = DriftEdge(
            surface_a=Surface.SPECIFICATION,
            surface_b=Surface.CODE,
            drift_score=0.05,
        )
        state = CoEvolutionState(drift_edges=[edge])
        engine = CoEvolutionEngine()
        assert engine.is_synchronized(state) is True

    def test_is_synchronized_false(self) -> None:
        edge = DriftEdge(
            surface_a=Surface.SPECIFICATION,
            surface_b=Surface.CODE,
            drift_score=0.5,
        )
        state = CoEvolutionState(drift_edges=[edge])
        engine = CoEvolutionEngine()
        assert engine.is_synchronized(state) is False


# ---------------------------------------------------------------------------
# Domain agnosticism
# ---------------------------------------------------------------------------

class TestDomainAgnostic:
    def test_domain_agnostic_web_app_surfaces(self) -> None:
        surfaces = [Surface.SPECIFICATION, Surface.CODE, Surface.DEPLOYMENT, Surface.DOCUMENTATION]
        engine = CoEvolutionEngine(surfaces=surfaces)
        assert len(engine._surfaces) == 4

    def test_domain_agnostic_library_surfaces(self) -> None:
        surfaces = [Surface.SPECIFICATION, Surface.CODE, Surface.BENCHMARKS, Surface.DOCUMENTATION]
        engine = CoEvolutionEngine(surfaces=surfaces)
        assert len(engine._surfaces) == 4
        engine.add_surface(Surface.BENCHMARKS, ["bench-1", "bench-2"])
        assert engine._surfaces[Surface.BENCHMARKS].coordinate_ids == ["bench-1", "bench-2"]
