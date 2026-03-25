from __future__ import annotations

import fnmatch
import time
import uuid
from typing import Any, Dict, List, Optional

from .models import (
    PropagationPolicy,
    TrustChangeEvent,
    TrustPropagation,
    TrustZone,
    ZoneBoundary,
    ZoneReport,
)

TRUST_LEVELS = ["NONE", "WEAK", "PARTIAL", "STANDARD", "STRONG", "FULL"]


def _trust_index(level: str) -> int:
    try:
        return TRUST_LEVELS.index(level)
    except ValueError:
        return 0


def _attenuate(level: str, attenuation: str) -> str:
    idx = min(_trust_index(level), _trust_index(attenuation))
    return TRUST_LEVELS[idx]


class TrustZoneManager:
    """Manages trust zones, boundaries, and trust-change propagation."""

    def __init__(self) -> None:
        self._zones: Dict[str, TrustZone] = {}
        self._boundaries: Dict[str, ZoneBoundary] = {}
        self._coordinate_to_zone: Dict[str, str] = {}
        self._dirty_coordinates: set[str] = set()
        self._propagation_history: List[TrustPropagation] = []

    # ------------------------------------------------------------------
    # Zone management
    # ------------------------------------------------------------------

    def create_zone(
        self,
        name: str,
        patterns: List[str],
        floor: str,
        ceiling: str,
        parent_zone_id: Optional[str] = None,
    ) -> TrustZone:
        zone_id = str(uuid.uuid4())
        zone = TrustZone(
            id=zone_id,
            name=name,
            coordinate_patterns=patterns,
            trust_floor=floor,
            trust_ceiling=ceiling,
            boundary_coordinates=[],
            parent_zone_id=parent_zone_id,
        )
        self._zones[zone_id] = zone
        return zone

    def add_boundary(
        self,
        zone_a_id: str,
        zone_b_id: str,
        boundary_coords: list,
        policy: str,
        attenuation_level: Optional[str] = None,
    ) -> ZoneBoundary:
        key = f"{zone_a_id}:{zone_b_id}"
        boundary = ZoneBoundary(
            zone_a_id=zone_a_id,
            zone_b_id=zone_b_id,
            boundary_coordinates=boundary_coords,
            propagation_policy=policy,
            attenuation_level=attenuation_level,
        )
        self._boundaries[key] = boundary
        return boundary

    # ------------------------------------------------------------------
    # Coordinate resolution
    # ------------------------------------------------------------------

    def resolve_zone(self, coordinate_id: str) -> Optional[TrustZone]:
        cached_zone_id = self._coordinate_to_zone.get(coordinate_id)
        if cached_zone_id is not None:
            return self._zones.get(cached_zone_id)

        for zone in self._zones.values():
            for pattern in zone.coordinate_patterns:
                if fnmatch.fnmatch(coordinate_id, pattern):
                    self._coordinate_to_zone[coordinate_id] = zone.id
                    return zone
        return None

    def register_coordinate(self, coordinate_id: str) -> None:
        self.resolve_zone(coordinate_id)

    # ------------------------------------------------------------------
    # Propagation
    # ------------------------------------------------------------------

    def propagate_trust_change(self, event: TrustChangeEvent) -> TrustPropagation:
        zone = self.resolve_zone(event.coordinate_id)
        affected: List[str] = []
        stopped_at: List[str] = []

        if zone is not None:
            within = self._within_zone_propagation(event, zone)
            affected.extend(within)

            for key, boundary in self._boundaries.items():
                if boundary.zone_a_id == zone.id or boundary.zone_b_id == zone.id:
                    cross = self._cross_boundary_propagation(event, boundary)
                    if cross:
                        affected.extend(cross)
                    else:
                        policy = boundary.propagation_policy
                        if policy in (PropagationPolicy.BLOCK.value, PropagationPolicy.BLOCK):
                            stopped_at.append(key)
                        elif policy in (PropagationPolicy.LAZY.value, PropagationPolicy.LAZY):
                            stopped_at.append(key)

        propagation = TrustPropagation(
            source_event_id=event.coordinate_id,
            affected_coordinates=affected,
            propagation_depth=1,
            stopped_at_boundaries=stopped_at,
        )
        self._propagation_history.append(propagation)
        return propagation

    def _within_zone_propagation(
        self, event: TrustChangeEvent, zone: TrustZone
    ) -> List[str]:
        coords_in_zone = [
            cid
            for cid, zid in self._coordinate_to_zone.items()
            if zid == zone.id and cid != event.coordinate_id
        ]
        return coords_in_zone

    def _cross_boundary_propagation(
        self, event: TrustChangeEvent, boundary: ZoneBoundary
    ) -> List[str]:
        policy = boundary.propagation_policy
        if isinstance(policy, PropagationPolicy):
            policy = policy.value

        if policy == PropagationPolicy.BLOCK.value:
            return []
        elif policy == PropagationPolicy.PASS.value:
            return list(boundary.boundary_coordinates)
        elif policy == PropagationPolicy.ATTENUATE.value:
            return list(boundary.boundary_coordinates)
        elif policy == PropagationPolicy.LAZY.value:
            self._lazy_mark(boundary.boundary_coordinates)
            return []
        return []

    def _lazy_mark(self, coordinates: list) -> None:
        for c in coordinates:
            self._dirty_coordinates.add(c)

    # ------------------------------------------------------------------
    # Batch & challenge
    # ------------------------------------------------------------------

    def batch_propagate(
        self, events: List[TrustChangeEvent]
    ) -> List[TrustPropagation]:
        results: List[TrustPropagation] = []
        seen: set[str] = set()
        for event in events:
            prop = self.propagate_trust_change(event)
            deduped = [c for c in prop.affected_coordinates if c not in seen]
            seen.update(deduped)
            prop.affected_coordinates = deduped
            results.append(prop)
        return results

    def challenge(self, coordinate_id: str, reason: str) -> TrustPropagation:
        zone = self.resolve_zone(coordinate_id)
        zone_id = zone.id if zone else ""
        event = TrustChangeEvent(
            coordinate_id=coordinate_id,
            old_level="STANDARD",
            new_level="NONE",
            reason=reason,
            timestamp=time.time(),
            zone_id=zone_id,
        )
        return self.propagate_trust_change(event)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def zone_report(self, zone_id: str) -> ZoneReport:
        zone = self._zones.get(zone_id)
        if zone is None:
            raise KeyError(f"Zone {zone_id} not found")

        coord_count = sum(
            1 for zid in self._coordinate_to_zone.values() if zid == zone_id
        )
        dirty_in_zone = sum(
            1
            for c in self._dirty_coordinates
            if self._coordinate_to_zone.get(c) == zone_id
        )
        return ZoneReport(
            zone_id=zone_id,
            coordinate_count=coord_count,
            trust_floor=zone.trust_floor,
            trust_ceiling=zone.trust_ceiling,
            pending_propagations=dirty_in_zone,
            boundary_violations=0,
        )

    def all_zones(self) -> List[TrustZone]:
        return list(self._zones.values())
