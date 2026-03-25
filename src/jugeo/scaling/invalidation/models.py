from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class InvalidationStrategy(str, Enum):
    """Strategy for invalidating dependent coordinates."""

    FULL_CASCADE = "FULL_CASCADE"
    CONTRACT_BOUNDED = "CONTRACT_BOUNDED"
    TIERED = "TIERED"
    PROBABILISTIC = "PROBABILISTIC"
    SEMANTIC = "SEMANTIC"


@dataclass
class ContractBoundary:
    """A coordinate acting as a contract firewall."""

    coordinate_id: str
    contract_hash: str
    verified_at: float
    trust_level: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_id": self.coordinate_id,
            "contract_hash": self.contract_hash,
            "verified_at": self.verified_at,
            "trust_level": self.trust_level,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ContractBoundary:
        return cls(
            coordinate_id=data["coordinate_id"],
            contract_hash=data["contract_hash"],
            verified_at=data["verified_at"],
            trust_level=data["trust_level"],
        )


@dataclass
class DampeningConfig:
    """Configuration for invalidation dampening."""

    max_depth: int = 10
    contract_firewalls: bool = True
    tiered_depths: List[int] = field(default_factory=lambda: [1, 3, 10])
    probabilistic_sample_rate: float = 0.1
    semantic_threshold: float = 0.8

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_depth": self.max_depth,
            "contract_firewalls": self.contract_firewalls,
            "tiered_depths": self.tiered_depths,
            "probabilistic_sample_rate": self.probabilistic_sample_rate,
            "semantic_threshold": self.semantic_threshold,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DampeningConfig:
        return cls(
            max_depth=data.get("max_depth", 10),
            contract_firewalls=data.get("contract_firewalls", True),
            tiered_depths=data.get("tiered_depths", [1, 3, 10]),
            probabilistic_sample_rate=data.get("probabilistic_sample_rate", 0.1),
            semantic_threshold=data.get("semantic_threshold", 0.8),
        )
