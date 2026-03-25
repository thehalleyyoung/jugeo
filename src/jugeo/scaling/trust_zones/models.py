from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class PropagationPolicy(str, Enum):
    """Policy for trust propagation across zone boundaries."""

    BLOCK = "BLOCK"
    ATTENUATE = "ATTENUATE"
    PASS = "PASS"
    LAZY = "LAZY"


@dataclass
class TrustZone:
    """A zone grouping coordinates under shared trust constraints."""

    id: str
    name: str
    coordinate_patterns: List[str]
    trust_floor: str
    trust_ceiling: str
    boundary_coordinates: list
    parent_zone_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "coordinate_patterns": self.coordinate_patterns,
            "trust_floor": self.trust_floor,
            "trust_ceiling": self.trust_ceiling,
            "boundary_coordinates": self.boundary_coordinates,
            "parent_zone_id": self.parent_zone_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TrustZone:
        return cls(
            id=data["id"],
            name=data["name"],
            coordinate_patterns=data["coordinate_patterns"],
            trust_floor=data["trust_floor"],
            trust_ceiling=data["trust_ceiling"],
            boundary_coordinates=data.get("boundary_coordinates", []),
            parent_zone_id=data.get("parent_zone_id"),
        )


@dataclass
class ZoneBoundary:
    """Describes how trust propagates between two zones."""

    zone_a_id: str
    zone_b_id: str
    boundary_coordinates: list
    propagation_policy: str
    attenuation_level: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_a_id": self.zone_a_id,
            "zone_b_id": self.zone_b_id,
            "boundary_coordinates": self.boundary_coordinates,
            "propagation_policy": self.propagation_policy,
            "attenuation_level": self.attenuation_level,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ZoneBoundary:
        return cls(
            zone_a_id=data["zone_a_id"],
            zone_b_id=data["zone_b_id"],
            boundary_coordinates=data.get("boundary_coordinates", []),
            propagation_policy=data["propagation_policy"],
            attenuation_level=data.get("attenuation_level"),
        )


@dataclass
class TrustChangeEvent:
    """Records a trust level change for a coordinate."""

    coordinate_id: str
    old_level: str
    new_level: str
    reason: str
    timestamp: float
    zone_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_id": self.coordinate_id,
            "old_level": self.old_level,
            "new_level": self.new_level,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "zone_id": self.zone_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TrustChangeEvent:
        return cls(
            coordinate_id=data["coordinate_id"],
            old_level=data["old_level"],
            new_level=data["new_level"],
            reason=data["reason"],
            timestamp=data["timestamp"],
            zone_id=data["zone_id"],
        )


@dataclass
class TrustPropagation:
    """Result of propagating a trust change through zones."""

    source_event_id: str
    affected_coordinates: list
    propagation_depth: int
    stopped_at_boundaries: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_event_id": self.source_event_id,
            "affected_coordinates": self.affected_coordinates,
            "propagation_depth": self.propagation_depth,
            "stopped_at_boundaries": self.stopped_at_boundaries,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TrustPropagation:
        return cls(
            source_event_id=data["source_event_id"],
            affected_coordinates=data.get("affected_coordinates", []),
            propagation_depth=data.get("propagation_depth", 0),
            stopped_at_boundaries=data.get("stopped_at_boundaries", []),
        )


@dataclass
class ZoneReport:
    """Summary report for a single trust zone."""

    zone_id: str
    coordinate_count: int
    trust_floor: str
    trust_ceiling: str
    pending_propagations: int
    boundary_violations: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "coordinate_count": self.coordinate_count,
            "trust_floor": self.trust_floor,
            "trust_ceiling": self.trust_ceiling,
            "pending_propagations": self.pending_propagations,
            "boundary_violations": self.boundary_violations,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ZoneReport:
        return cls(
            zone_id=data["zone_id"],
            coordinate_count=data["coordinate_count"],
            trust_floor=data["trust_floor"],
            trust_ceiling=data["trust_ceiling"],
            pending_propagations=data.get("pending_propagations", 0),
            boundary_violations=data.get("boundary_violations", 0),
        )
