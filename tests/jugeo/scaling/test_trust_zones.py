from __future__ import annotations

import time

import pytest

from src.jugeo.scaling.trust_zones.models import (
    PropagationPolicy,
    TrustChangeEvent,
    TrustPropagation,
    TrustZone,
    ZoneBoundary,
    ZoneReport,
)
from src.jugeo.scaling.trust_zones.algorithms import TrustZoneManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(coord: str, zone_id: str = "", old: str = "STANDARD", new: str = "WEAK") -> TrustChangeEvent:
    return TrustChangeEvent(
        coordinate_id=coord,
        old_level=old,
        new_level=new,
        reason="test",
        timestamp=time.time(),
        zone_id=zone_id,
    )


# ===================================================================
# TrustZoneManager tests
# ===================================================================


class TestTrustZoneManager:
    def test_create_zone(self) -> None:
        mgr = TrustZoneManager()
        zone = mgr.create_zone("auth", ["auth.*"], "WEAK", "FULL")
        assert zone.name == "auth"
        assert zone.trust_floor == "WEAK"
        assert zone.trust_ceiling == "FULL"
        assert zone.id in [z.id for z in mgr.all_zones()]

    def test_resolve_zone(self) -> None:
        mgr = TrustZoneManager()
        zone = mgr.create_zone("auth", ["auth.*"], "WEAK", "FULL")
        mgr.register_coordinate("auth.login")
        resolved = mgr.resolve_zone("auth.login")
        assert resolved is not None
        assert resolved.id == zone.id

    def test_resolve_zone_no_match(self) -> None:
        mgr = TrustZoneManager()
        mgr.create_zone("auth", ["auth.*"], "WEAK", "FULL")
        resolved = mgr.resolve_zone("db.query")
        assert resolved is None

    def test_add_boundary(self) -> None:
        mgr = TrustZoneManager()
        z1 = mgr.create_zone("zone_a", ["a.*"], "WEAK", "FULL")
        z2 = mgr.create_zone("zone_b", ["b.*"], "WEAK", "FULL")
        boundary = mgr.add_boundary(z1.id, z2.id, ["a.edge", "b.edge"], PropagationPolicy.BLOCK.value)
        assert boundary.zone_a_id == z1.id
        assert boundary.zone_b_id == z2.id

    def test_propagate_within_zone(self) -> None:
        mgr = TrustZoneManager()
        zone = mgr.create_zone("auth", ["auth.*"], "WEAK", "FULL")
        mgr.register_coordinate("auth.login")
        mgr.register_coordinate("auth.logout")
        mgr.register_coordinate("auth.token")
        event = _make_event("auth.login", zone.id)
        prop = mgr.propagate_trust_change(event)
        assert isinstance(prop, TrustPropagation)
        # auth.logout and auth.token should be affected
        assert "auth.logout" in prop.affected_coordinates
        assert "auth.token" in prop.affected_coordinates

    def test_boundary_blocks_propagation(self) -> None:
        mgr = TrustZoneManager()
        z1 = mgr.create_zone("zone_a", ["a.*"], "WEAK", "FULL")
        z2 = mgr.create_zone("zone_b", ["b.*"], "WEAK", "FULL")
        mgr.add_boundary(z1.id, z2.id, ["b.edge"], PropagationPolicy.BLOCK.value)
        mgr.register_coordinate("a.source")
        mgr.register_coordinate("b.edge")
        event = _make_event("a.source", z1.id)
        prop = mgr.propagate_trust_change(event)
        # b.edge should NOT be in affected (BLOCK stops propagation)
        assert "b.edge" not in prop.affected_coordinates
        assert len(prop.stopped_at_boundaries) > 0

    def test_boundary_attenuates_propagation(self) -> None:
        mgr = TrustZoneManager()
        z1 = mgr.create_zone("zone_a", ["a.*"], "WEAK", "FULL")
        z2 = mgr.create_zone("zone_b", ["b.*"], "WEAK", "FULL")
        mgr.add_boundary(z1.id, z2.id, ["b.edge"], PropagationPolicy.ATTENUATE.value, "PARTIAL")
        mgr.register_coordinate("a.source")
        mgr.register_coordinate("b.edge")
        event = _make_event("a.source", z1.id)
        prop = mgr.propagate_trust_change(event)
        # ATTENUATE allows propagation to boundary_coordinates
        assert "b.edge" in prop.affected_coordinates

    def test_boundary_passes_propagation(self) -> None:
        mgr = TrustZoneManager()
        z1 = mgr.create_zone("zone_a", ["a.*"], "WEAK", "FULL")
        z2 = mgr.create_zone("zone_b", ["b.*"], "WEAK", "FULL")
        mgr.add_boundary(z1.id, z2.id, ["b.edge"], PropagationPolicy.PASS.value)
        mgr.register_coordinate("a.source")
        mgr.register_coordinate("b.edge")
        event = _make_event("a.source", z1.id)
        prop = mgr.propagate_trust_change(event)
        assert "b.edge" in prop.affected_coordinates

    def test_lazy_mark(self) -> None:
        mgr = TrustZoneManager()
        z1 = mgr.create_zone("zone_a", ["a.*"], "WEAK", "FULL")
        z2 = mgr.create_zone("zone_b", ["b.*"], "WEAK", "FULL")
        mgr.add_boundary(z1.id, z2.id, ["b.edge"], PropagationPolicy.LAZY.value)
        mgr.register_coordinate("a.source")
        mgr.register_coordinate("b.edge")
        event = _make_event("a.source", z1.id)
        prop = mgr.propagate_trust_change(event)
        # LAZY doesn't propagate but marks coordinates as dirty
        assert "b.edge" not in prop.affected_coordinates
        assert "b.edge" in mgr._dirty_coordinates

    def test_challenge(self) -> None:
        mgr = TrustZoneManager()
        mgr.create_zone("auth", ["auth.*"], "WEAK", "FULL")
        mgr.register_coordinate("auth.login")
        mgr.register_coordinate("auth.logout")
        prop = mgr.challenge("auth.login", "suspected compromise")
        assert isinstance(prop, TrustPropagation)
        assert prop.source_event_id == "auth.login"

    def test_zone_report(self) -> None:
        mgr = TrustZoneManager()
        zone = mgr.create_zone("auth", ["auth.*"], "WEAK", "FULL")
        mgr.register_coordinate("auth.login")
        mgr.register_coordinate("auth.logout")
        report = mgr.zone_report(zone.id)
        assert report.zone_id == zone.id
        assert report.coordinate_count == 2
        assert report.trust_floor == "WEAK"

    def test_batch_propagate(self) -> None:
        mgr = TrustZoneManager()
        zone = mgr.create_zone("auth", ["auth.*"], "WEAK", "FULL")
        mgr.register_coordinate("auth.a")
        mgr.register_coordinate("auth.b")
        mgr.register_coordinate("auth.c")
        events = [
            _make_event("auth.a", zone.id),
            _make_event("auth.b", zone.id),
        ]
        results = mgr.batch_propagate(events)
        assert len(results) == 2
        # All results should be TrustPropagation instances
        for r in results:
            assert isinstance(r, TrustPropagation)
