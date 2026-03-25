from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BridgeKind(str, Enum):
    """The kind of bridge connecting two packs."""

    TYPE_SAFETY = "TYPE_SAFETY"
    RESOURCE_SAFETY = "RESOURCE_SAFETY"
    CONCURRENCY = "CONCURRENCY"
    ENCODING = "ENCODING"
    PROTOCOL = "PROTOCOL"
    GENERIC = "GENERIC"


@dataclass
class Bridge:
    """A bridge that transports judgments between packs."""

    id: str
    source_pack: str
    target_pack: str
    kind: BridgeKind
    proposition_pattern: str
    transport_function_name: str
    trust_attenuation: str
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_pack": self.source_pack,
            "target_pack": self.target_pack,
            "kind": self.kind.value if isinstance(self.kind, BridgeKind) else self.kind,
            "proposition_pattern": self.proposition_pattern,
            "transport_function_name": self.transport_function_name,
            "trust_attenuation": self.trust_attenuation,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Bridge:
        kind_val = data["kind"]
        if isinstance(kind_val, str):
            kind_val = BridgeKind(kind_val)
        return cls(
            id=data["id"],
            source_pack=data["source_pack"],
            target_pack=data["target_pack"],
            kind=kind_val,
            proposition_pattern=data["proposition_pattern"],
            transport_function_name=data["transport_function_name"],
            trust_attenuation=data["trust_attenuation"],
            created_at=data["created_at"],
        )


@dataclass
class BridgeIndex:
    """Multi-index over bridges for fast lookup."""

    bridges_by_pack_pair: dict
    bridges_by_kind: dict
    bridges_by_pattern: dict

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bridges_by_pack_pair": self.bridges_by_pack_pair,
            "bridges_by_kind": self.bridges_by_kind,
            "bridges_by_pattern": self.bridges_by_pattern,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BridgeIndex:
        return cls(
            bridges_by_pack_pair=data.get("bridges_by_pack_pair", {}),
            bridges_by_kind=data.get("bridges_by_kind", {}),
            bridges_by_pattern=data.get("bridges_by_pattern", {}),
        )


@dataclass
class FederationQuery:
    """A query to transport a judgment across packs."""

    source_judgment_id: str
    source_pack: str
    target_pack: str
    proposition: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_judgment_id": self.source_judgment_id,
            "source_pack": self.source_pack,
            "target_pack": self.target_pack,
            "proposition": self.proposition,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FederationQuery:
        return cls(
            source_judgment_id=data["source_judgment_id"],
            source_pack=data["source_pack"],
            target_pack=data["target_pack"],
            proposition=data["proposition"],
        )


@dataclass
class FederationResult:
    """Result of a federation query."""

    query_id: str
    bridge_id: str
    transported_judgment: dict
    trust_level: str
    cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query_id": self.query_id,
            "bridge_id": self.bridge_id,
            "transported_judgment": self.transported_judgment,
            "trust_level": self.trust_level,
            "cached": self.cached,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FederationResult:
        return cls(
            query_id=data["query_id"],
            bridge_id=data["bridge_id"],
            transported_judgment=data.get("transported_judgment", {}),
            trust_level=data["trust_level"],
            cached=data.get("cached", False),
        )


@dataclass
class ComposedBridge:
    """A bridge composed of multiple hops."""

    id: str
    bridges: List[str]
    composition_trust: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "bridges": self.bridges,
            "composition_trust": self.composition_trust,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ComposedBridge:
        return cls(
            id=data["id"],
            bridges=data.get("bridges", []),
            composition_trust=data["composition_trust"],
        )


@dataclass
class FederationStatistics:
    """Aggregated statistics for federation operations."""

    total_queries: int = 0
    cache_hits: int = 0
    bridge_applications: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)
    avg_transport_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_queries": self.total_queries,
            "cache_hits": self.cache_hits,
            "bridge_applications": self.bridge_applications,
            "by_kind": self.by_kind,
            "avg_transport_ms": self.avg_transport_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FederationStatistics:
        return cls(
            total_queries=data.get("total_queries", 0),
            cache_hits=data.get("cache_hits", 0),
            bridge_applications=data.get("bridge_applications", 0),
            by_kind=data.get("by_kind", {}),
            avg_transport_ms=data.get("avg_transport_ms", 0.0),
        )
