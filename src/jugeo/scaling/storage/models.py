"""Persistent storage models for JuGeo scaling infrastructure.

Each model is a plain :func:`~dataclasses.dataclass` that represents one row
in the SQLite backend.  All models provide ``to_dict`` / ``from_dict``
round-trips so they can be serialised to JSON and written to an audit log.

Column naming follows the pattern ``<field>_json`` for any value that is
stored as a JSON blob in the database, and ``<field>_id`` for foreign-key
references.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _uid() -> str:
    return uuid.uuid4().hex


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def _loads(s: str | None) -> Any:
    if s is None:
        return None
    return json.loads(s)


# ---------------------------------------------------------------------------
# Status / priority enums used inside models
# ---------------------------------------------------------------------------

class ObligationStatus(str, Enum):
    """Lifecycle states for a residual obligation."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    DISCHARGED = "discharged"
    FAILED = "failed"
    EXPIRED = "expired"


class JudgmentStatus(str, Enum):
    """High-level status of a stored judgment."""

    OPEN = "open"
    CLOSED = "closed"
    RETRACTED = "retracted"
    SUPERSEDED = "superseded"


class TreatyStatus(str, Enum):
    """Lifecycle state of an inter-agent treaty."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    BREACHED = "breached"
    DISSOLVED = "dissolved"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# StoredCoordinate
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredCoordinate:
    """Persistent representation of a sheaf coordinate.

    Parameters
    ----------
    id:
        Unique hex identifier.
    name:
        Human-readable coordinate name (e.g. ``jugeo.geometry.site``).
    kind:
        CoordinateKind value serialised as a string.
    depth:
        Nesting depth within the package tree.
    package:
        Top-level package name.
    module:
        Fully-qualified module path.
    components_json:
        JSON-encoded tuple of path components.
    metadata_json:
        JSON-encoded arbitrary metadata dict.
    created_at:
        Unix timestamp of insertion.
    """

    id: str
    name: str
    kind: str
    depth: int
    package: str
    module: str
    components_json: str
    metadata_json: str
    created_at: float = field(default_factory=_now)

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        name: str,
        kind: str,
        depth: int,
        package: str,
        module: str,
        components: tuple[str, ...] | list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        coord_id: str | None = None,
    ) -> StoredCoordinate:
        return cls(
            id=coord_id or _uid(),
            name=name,
            kind=kind,
            depth=depth,
            package=package,
            module=module,
            components_json=_dumps(list(components or [])),
            metadata_json=_dumps(metadata or {}),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "depth": self.depth,
            "package": self.package,
            "module": self.module,
            "components": _loads(self.components_json),
            "metadata": _loads(self.metadata_json),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredCoordinate:
        return cls(
            id=d["id"],
            name=d["name"],
            kind=d["kind"],
            depth=d["depth"],
            package=d["package"],
            module=d["module"],
            components_json=_dumps(d.get("components", [])),
            metadata_json=_dumps(d.get("metadata", {})),
            created_at=d.get("created_at", _now()),
        )


# ---------------------------------------------------------------------------
# StoredMorphism
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredMorphism:
    """Persistent representation of a sheaf morphism."""

    id: str
    source_id: str
    target_id: str
    kind: str
    label: str
    created_at: float = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        source_id: str,
        target_id: str,
        kind: str,
        label: str,
        morph_id: str | None = None,
    ) -> StoredMorphism:
        return cls(
            id=morph_id or _uid(),
            source_id=source_id,
            target_id=target_id,
            kind=kind,
            label=label,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "label": self.label,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredMorphism:
        return cls(
            id=d["id"],
            source_id=d["source_id"],
            target_id=d["target_id"],
            kind=d["kind"],
            label=d["label"],
            created_at=d.get("created_at", _now()),
        )


# ---------------------------------------------------------------------------
# StoredJudgment
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredJudgment:
    """Persistent representation of a verification judgment.

    A judgment binds a proposition to a coordinate and records the current
    trust tier, status, and accumulated evidence/obligations/obstructions.
    """

    id: str
    coordinate_id: str
    proposition: str
    trust_level: str
    status: str
    carrier_json: str
    evidence_json: str
    obligations_json: str
    obstructions_json: str
    provenance_json: str
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        coordinate_id: str,
        proposition: str,
        trust_level: str,
        status: str = JudgmentStatus.OPEN.value,
        carrier: dict[str, Any] | None = None,
        evidence: list[Any] | None = None,
        obligations: list[str] | None = None,
        obstructions: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        judgment_id: str | None = None,
    ) -> StoredJudgment:
        now = _now()
        return cls(
            id=judgment_id or _uid(),
            coordinate_id=coordinate_id,
            proposition=proposition,
            trust_level=trust_level,
            status=status,
            carrier_json=_dumps(carrier or {}),
            evidence_json=_dumps(evidence or []),
            obligations_json=_dumps(obligations or []),
            obstructions_json=_dumps(obstructions or []),
            provenance_json=_dumps(provenance or {}),
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "coordinate_id": self.coordinate_id,
            "proposition": self.proposition,
            "trust_level": self.trust_level,
            "status": self.status,
            "carrier": _loads(self.carrier_json),
            "evidence": _loads(self.evidence_json),
            "obligations": _loads(self.obligations_json),
            "obstructions": _loads(self.obstructions_json),
            "provenance": _loads(self.provenance_json),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredJudgment:
        return cls(
            id=d["id"],
            coordinate_id=d["coordinate_id"],
            proposition=d["proposition"],
            trust_level=d["trust_level"],
            status=d["status"],
            carrier_json=_dumps(d.get("carrier", {})),
            evidence_json=_dumps(d.get("evidence", [])),
            obligations_json=_dumps(d.get("obligations", [])),
            obstructions_json=_dumps(d.get("obstructions", [])),
            provenance_json=_dumps(d.get("provenance", {})),
            created_at=d.get("created_at", _now()),
            updated_at=d.get("updated_at", _now()),
        )


# ---------------------------------------------------------------------------
# StoredEvidence
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredEvidence:
    """Persistent evidence record anchored to a judgment and coordinate."""

    id: str
    judgment_id: str
    channel: str
    trust_level: str
    claim: str
    payload_json: str
    coordinate_id: str
    timestamp: float
    record_id: str
    support_json: str
    provenance_json: str

    @classmethod
    def create(
        cls,
        judgment_id: str,
        channel: str,
        trust_level: str,
        claim: str,
        coordinate_id: str,
        record_id: str | None = None,
        payload: dict[str, Any] | None = None,
        support: list[Any] | None = None,
        provenance: dict[str, Any] | None = None,
        evidence_id: str | None = None,
        timestamp: float | None = None,
    ) -> StoredEvidence:
        return cls(
            id=evidence_id or _uid(),
            judgment_id=judgment_id,
            channel=channel,
            trust_level=trust_level,
            claim=claim,
            payload_json=_dumps(payload or {}),
            coordinate_id=coordinate_id,
            timestamp=timestamp if timestamp is not None else _now(),
            record_id=record_id or _uid(),
            support_json=_dumps(support or []),
            provenance_json=_dumps(provenance or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "judgment_id": self.judgment_id,
            "channel": self.channel,
            "trust_level": self.trust_level,
            "claim": self.claim,
            "payload": _loads(self.payload_json),
            "coordinate_id": self.coordinate_id,
            "timestamp": self.timestamp,
            "record_id": self.record_id,
            "support": _loads(self.support_json),
            "provenance": _loads(self.provenance_json),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredEvidence:
        return cls(
            id=d["id"],
            judgment_id=d["judgment_id"],
            channel=d["channel"],
            trust_level=d["trust_level"],
            claim=d["claim"],
            payload_json=_dumps(d.get("payload", {})),
            coordinate_id=d["coordinate_id"],
            timestamp=d.get("timestamp", _now()),
            record_id=d.get("record_id", _uid()),
            support_json=_dumps(d.get("support", [])),
            provenance_json=_dumps(d.get("provenance", {})),
        )


# ---------------------------------------------------------------------------
# StoredObligation
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredObligation:
    """Persistent residual obligation attached to a judgment."""

    id: str
    judgment_id: str
    coordinate_id: str
    proposition: str
    status: str
    priority: int
    created_at: float
    deadline: Optional[float]
    assigned_to: Optional[str]
    support_json: str

    @classmethod
    def create(
        cls,
        judgment_id: str,
        coordinate_id: str,
        proposition: str,
        priority: int = 2,
        status: str = ObligationStatus.PENDING.value,
        deadline: float | None = None,
        assigned_to: str | None = None,
        support: list[Any] | None = None,
        obligation_id: str | None = None,
    ) -> StoredObligation:
        return cls(
            id=obligation_id or _uid(),
            judgment_id=judgment_id,
            coordinate_id=coordinate_id,
            proposition=proposition,
            status=status,
            priority=priority,
            created_at=_now(),
            deadline=deadline,
            assigned_to=assigned_to,
            support_json=_dumps(support or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "judgment_id": self.judgment_id,
            "coordinate_id": self.coordinate_id,
            "proposition": self.proposition,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at,
            "deadline": self.deadline,
            "assigned_to": self.assigned_to,
            "support": _loads(self.support_json),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredObligation:
        return cls(
            id=d["id"],
            judgment_id=d["judgment_id"],
            coordinate_id=d["coordinate_id"],
            proposition=d["proposition"],
            status=d["status"],
            priority=d["priority"],
            created_at=d.get("created_at", _now()),
            deadline=d.get("deadline"),
            assigned_to=d.get("assigned_to"),
            support_json=_dumps(d.get("support", [])),
        )


# ---------------------------------------------------------------------------
# StoredObstruction
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredObstruction:
    """Persistent obstruction record (sheaf cohomology witness)."""

    id: str
    coordinate_id: str
    kind: str
    proposition: str
    cohomology_class: str
    repair_frontier_json: str
    blast_radius: int
    countermodel_json: str
    severity: float
    created_at: float
    resolved_at: Optional[float]

    @classmethod
    def create(
        cls,
        coordinate_id: str,
        kind: str,
        proposition: str,
        cohomology_class: str = "",
        repair_frontier: list[str] | None = None,
        blast_radius: int = 0,
        countermodel: dict[str, Any] | None = None,
        severity: float = 0.5,
        obstruction_id: str | None = None,
    ) -> StoredObstruction:
        return cls(
            id=obstruction_id or _uid(),
            coordinate_id=coordinate_id,
            kind=kind,
            proposition=proposition,
            cohomology_class=cohomology_class,
            repair_frontier_json=_dumps(repair_frontier or []),
            blast_radius=blast_radius,
            countermodel_json=_dumps(countermodel or {}),
            severity=severity,
            created_at=_now(),
            resolved_at=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "coordinate_id": self.coordinate_id,
            "kind": self.kind,
            "proposition": self.proposition,
            "cohomology_class": self.cohomology_class,
            "repair_frontier": _loads(self.repair_frontier_json),
            "blast_radius": self.blast_radius,
            "countermodel": _loads(self.countermodel_json),
            "severity": self.severity,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredObstruction:
        return cls(
            id=d["id"],
            coordinate_id=d["coordinate_id"],
            kind=d["kind"],
            proposition=d["proposition"],
            cohomology_class=d.get("cohomology_class", ""),
            repair_frontier_json=_dumps(d.get("repair_frontier", [])),
            blast_radius=d.get("blast_radius", 0),
            countermodel_json=_dumps(d.get("countermodel", {})),
            severity=d.get("severity", 0.5),
            created_at=d.get("created_at", _now()),
            resolved_at=d.get("resolved_at"),
        )


# ---------------------------------------------------------------------------
# StoredTreaty
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredTreaty:
    """Persistent inter-agent treaty record."""

    id: str
    parties_json: str
    overlap_coordinates_json: str
    propositions_json: str
    status: str
    trust_floor: str
    created_at: float
    updated_at: float

    @classmethod
    def create(
        cls,
        parties: list[str],
        overlap_coordinates: list[str],
        propositions: list[str],
        trust_floor: str,
        status: str = TreatyStatus.PROPOSED.value,
        treaty_id: str | None = None,
    ) -> StoredTreaty:
        now = _now()
        return cls(
            id=treaty_id or _uid(),
            parties_json=_dumps(parties),
            overlap_coordinates_json=_dumps(overlap_coordinates),
            propositions_json=_dumps(propositions),
            status=status,
            trust_floor=trust_floor,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parties": _loads(self.parties_json),
            "overlap_coordinates": _loads(self.overlap_coordinates_json),
            "propositions": _loads(self.propositions_json),
            "status": self.status,
            "trust_floor": self.trust_floor,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredTreaty:
        now = _now()
        return cls(
            id=d["id"],
            parties_json=_dumps(d.get("parties", [])),
            overlap_coordinates_json=_dumps(d.get("overlap_coordinates", [])),
            propositions_json=_dumps(d.get("propositions", [])),
            status=d["status"],
            trust_floor=d["trust_floor"],
            created_at=d.get("created_at", now),
            updated_at=d.get("updated_at", now),
        )


# ---------------------------------------------------------------------------
# StoredCertificate
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StoredCertificate:
    """Persistent settlement certificate."""

    id: str
    judgment_id: str
    coordinate_id: str
    trust_level: str
    evidence_chain_json: str
    version: int
    issued_at: float
    expires_at: Optional[float]
    issuer: str

    @classmethod
    def create(
        cls,
        judgment_id: str,
        coordinate_id: str,
        trust_level: str,
        issuer: str,
        evidence_chain: list[str] | None = None,
        version: int = 1,
        expires_at: float | None = None,
        cert_id: str | None = None,
    ) -> StoredCertificate:
        return cls(
            id=cert_id or _uid(),
            judgment_id=judgment_id,
            coordinate_id=coordinate_id,
            trust_level=trust_level,
            evidence_chain_json=_dumps(evidence_chain or []),
            version=version,
            issued_at=_now(),
            expires_at=expires_at,
            issuer=issuer,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "judgment_id": self.judgment_id,
            "coordinate_id": self.coordinate_id,
            "trust_level": self.trust_level,
            "evidence_chain": _loads(self.evidence_chain_json),
            "version": self.version,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "issuer": self.issuer,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoredCertificate:
        return cls(
            id=d["id"],
            judgment_id=d["judgment_id"],
            coordinate_id=d["coordinate_id"],
            trust_level=d["trust_level"],
            evidence_chain_json=_dumps(d.get("evidence_chain", [])),
            version=d.get("version", 1),
            issued_at=d.get("issued_at", _now()),
            expires_at=d.get("expires_at"),
            issuer=d.get("issuer", ""),
        )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "ObligationStatus",
    "JudgmentStatus",
    "TreatyStatus",
    "StoredCoordinate",
    "StoredMorphism",
    "StoredJudgment",
    "StoredEvidence",
    "StoredObligation",
    "StoredObstruction",
    "StoredTreaty",
    "StoredCertificate",
]
