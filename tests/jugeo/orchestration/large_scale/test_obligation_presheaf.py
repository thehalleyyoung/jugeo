"""Tests for ObligationManager."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from jugeo.orchestration.large_scale.models import (
    MoveResult,
    ObligationKind,
    ObligationPresheaf,
    SupportAwareDecay,
    TypedObligation,
)
from jugeo.orchestration.large_scale.obligation_presheaf import ObligationManager


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

class TestCreateObligation:
    def test_create_obligation_defaults(self) -> None:
        mgr = ObligationManager()
        ob = mgr.create_obligation(
            kind=ObligationKind.VERIFICATION,
            coordinate_id="coord-1",
            proposition="Verify correctness",
        )
        assert ob.kind == ObligationKind.VERIFICATION
        assert ob.coordinate_id == "coord-1"
        assert ob.status == "PENDING"
        assert ob.priority == 1.0
        assert ob.deadline is None

    def test_create_obligation_with_deadline(self) -> None:
        mgr = ObligationManager()
        deadline = time.time() + 3600
        ob = mgr.create_obligation(
            kind=ObligationKind.GROUNDING,
            coordinate_id="coord-2",
            proposition="Ground after change",
            deadline=deadline,
        )
        assert ob.deadline == deadline


# ---------------------------------------------------------------------------
# Discharge / fail / expire
# ---------------------------------------------------------------------------

class TestDischarge:
    def test_discharge_obligation(self) -> None:
        mgr = ObligationManager()
        ob = mgr.create_obligation(
            kind=ObligationKind.VERIFICATION,
            coordinate_id="coord-1",
            proposition="Verify",
        )
        assert mgr.discharge(ob.id, evidence_id="ev-1") is True
        assert ob.status == "DISCHARGED"
        assert ob.discharge_evidence_id == "ev-1"

    def test_discharge_nonexistent(self) -> None:
        mgr = ObligationManager()
        assert mgr.discharge("nonexistent", "ev-1") is False


class TestFail:
    def test_fail_obligation(self) -> None:
        mgr = ObligationManager()
        ob = mgr.create_obligation(
            kind=ObligationKind.AUDIT,
            coordinate_id="coord-3",
            proposition="Audit claims",
        )
        assert mgr.fail(ob.id, reason="Evidence contradicts") is True
        assert ob.status == "FAILED"
        assert mgr._failure_reasons[ob.id] == "Evidence contradicts"

    def test_fail_already_discharged(self) -> None:
        mgr = ObligationManager()
        ob = mgr.create_obligation(
            kind=ObligationKind.AUDIT,
            coordinate_id="c1",
            proposition="x",
        )
        mgr.discharge(ob.id, "ev-1")
        assert mgr.fail(ob.id, "too late") is False


class TestExpireOverdue:
    def test_expire_overdue(self) -> None:
        mgr = ObligationManager()
        ob = mgr.create_obligation(
            kind=ObligationKind.TESTING,
            coordinate_id="coord-4",
            proposition="Run tests",
            deadline=time.time() - 100,  # already past
        )
        expired = mgr.expire_overdue()
        assert ob.id in expired
        assert ob.status == "EXPIRED"

    def test_expire_overdue_none_past(self) -> None:
        mgr = ObligationManager()
        mgr.create_obligation(
            kind=ObligationKind.TESTING,
            coordinate_id="c1",
            proposition="x",
            deadline=time.time() + 99999,
        )
        assert mgr.expire_overdue() == []


# ---------------------------------------------------------------------------
# Pressure
# ---------------------------------------------------------------------------

class TestPressure:
    def test_compute_pressure_empty(self) -> None:
        mgr = ObligationManager()
        assert mgr.compute_pressure() == 0.0

    def test_compute_pressure_with_obligations(self) -> None:
        mgr = ObligationManager()
        mgr.create_obligation(ObligationKind.VERIFICATION, "c1", "p1", priority=2.0)
        mgr.create_obligation(ObligationKind.GROUNDING, "c2", "p2", priority=3.0)
        assert mgr.compute_pressure() == 5.0

    def test_pressure_by_kind(self) -> None:
        mgr = ObligationManager()
        mgr.create_obligation(ObligationKind.VERIFICATION, "c1", "p1", priority=2.0)
        mgr.create_obligation(ObligationKind.VERIFICATION, "c2", "p2", priority=1.0)
        mgr.create_obligation(ObligationKind.GROUNDING, "c3", "p3", priority=4.0)
        pbk = mgr.pressure_by_kind()
        assert pbk[ObligationKind.VERIFICATION] == 3.0
        assert pbk[ObligationKind.GROUNDING] == 4.0

    def test_pressure_by_coordinate(self) -> None:
        mgr = ObligationManager()
        mgr.create_obligation(ObligationKind.VERIFICATION, "c1", "p1", priority=2.0)
        mgr.create_obligation(ObligationKind.GROUNDING, "c1", "p2", priority=3.0)
        assert mgr.pressure_by_coordinate("c1") == 5.0
        assert mgr.pressure_by_coordinate("c99") == 0.0


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

class TestSupportAwareStaleness:
    def test_stale(self) -> None:
        """Code changed AFTER obligation was created → stale."""
        mgr = ObligationManager()
        base = time.time() - 1000
        ob = mgr.create_obligation(
            ObligationKind.VERIFICATION, "c1", "verify",
        )
        # Pretend the obligation was created earlier
        ob.created_at = base
        code_times = {"c1": base + 500}  # code changed after evidence
        results = mgr.support_aware_staleness(code_times)
        assert len(results) == 1
        assert results[0].is_stale is True
        assert results[0].staleness_days > 0

    def test_fresh(self) -> None:
        """Code NOT changed since obligation → fresh."""
        mgr = ObligationManager()
        base = time.time()
        ob = mgr.create_obligation(
            ObligationKind.VERIFICATION, "c1", "verify",
        )
        ob.created_at = base
        code_times = {"c1": base - 500}  # code changed BEFORE evidence
        results = mgr.support_aware_staleness(code_times)
        assert len(results) == 1
        assert results[0].is_stale is False

    def test_no_magic_decay_constant(self) -> None:
        """Staleness uses code_change_times, not time.time()."""
        mgr = ObligationManager()
        ob = mgr.create_obligation(ObligationKind.VERIFICATION, "c1", "v")
        ob.created_at = 1000.0
        # Code changed at 1500 → stale regardless of current wall clock
        results = mgr.support_aware_staleness({"c1": 1500.0})
        assert results[0].is_stale is True
        assert results[0].staleness_days == pytest.approx(500.0 / 86400.0, abs=0.001)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

class TestQueries:
    def test_pending_for_coordinate(self) -> None:
        mgr = ObligationManager()
        mgr.create_obligation(ObligationKind.VERIFICATION, "c1", "p1")
        mgr.create_obligation(ObligationKind.GROUNDING, "c1", "p2")
        mgr.create_obligation(ObligationKind.AUDIT, "c2", "p3")
        result = mgr.pending_for_coordinate("c1")
        assert len(result) == 2

    def test_pending_by_kind(self) -> None:
        mgr = ObligationManager()
        mgr.create_obligation(ObligationKind.VERIFICATION, "c1", "p1")
        ob2 = mgr.create_obligation(ObligationKind.VERIFICATION, "c2", "p2")
        mgr.discharge(ob2.id, "ev-1")
        result = mgr.pending_by_kind(ObligationKind.VERIFICATION)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class TestGeneration:
    def test_generate_from_move(self) -> None:
        mgr = ObligationManager()
        move_result = MoveResult(
            move_id="m1",
            success=True,
            sections_modified=["s1", "s2"],
            obligations_generated=["verification", "grounding"],
        )
        created = mgr.generate_from_move(move_result)
        # 2 kinds × 2 sections = 4
        assert len(created) == 4

    def test_generate_grounding_obligations(self) -> None:
        mgr = ObligationManager()
        created = mgr.generate_grounding_obligations(["c1", "c2", "c3"])
        assert len(created) == 3
        assert all(o.kind == ObligationKind.GROUNDING for o in created)


# ---------------------------------------------------------------------------
# Presheaf snapshot
# ---------------------------------------------------------------------------

class TestPresheaf:
    def test_to_presheaf(self) -> None:
        mgr = ObligationManager()
        mgr.create_obligation(ObligationKind.VERIFICATION, "c1", "p1", priority=2.0)
        mgr.create_obligation(ObligationKind.GROUNDING, "c2", "p2", priority=3.0)
        ps = mgr.to_presheaf()
        assert isinstance(ps, ObligationPresheaf)
        assert len(ps.obligations) == 2
        assert ps.total_pressure == 5.0
        assert "verification" in ps.pressure_by_kind
        assert "grounding" in ps.pressure_by_kind


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestStatistics:
    def test_statistics(self) -> None:
        mgr = ObligationManager()
        mgr.create_obligation(ObligationKind.VERIFICATION, "c1", "p1")
        ob2 = mgr.create_obligation(ObligationKind.GROUNDING, "c2", "p2")
        mgr.discharge(ob2.id, "ev-1")
        stats = mgr.statistics()
        assert stats["total"] == 2
        assert stats["pending"] == 1
        assert stats["discharged"] == 1
