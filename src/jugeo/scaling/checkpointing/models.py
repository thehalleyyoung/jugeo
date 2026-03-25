from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class WALEntryKind(str, Enum):
    """Kinds of entries that can appear in the write-ahead log."""

    JUDGMENT_ADD = "JUDGMENT_ADD"
    JUDGMENT_UPDATE = "JUDGMENT_UPDATE"
    EVIDENCE_ADD = "EVIDENCE_ADD"
    OBLIGATION_ADD = "OBLIGATION_ADD"
    OBLIGATION_UPDATE = "OBLIGATION_UPDATE"
    OBSTRUCTION_ADD = "OBSTRUCTION_ADD"
    OBSTRUCTION_RESOLVE = "OBSTRUCTION_RESOLVE"
    TREATY_ADD = "TREATY_ADD"
    TREATY_UPDATE = "TREATY_UPDATE"
    CERTIFICATE_ADD = "CERTIFICATE_ADD"
    EPOCH_ADVANCE = "EPOCH_ADVANCE"


@dataclass
class WALEntry:
    """A single entry in the write-ahead log."""

    sequence_number: int
    kind: WALEntryKind
    entity_id: str
    data: dict
    timestamp: float
    checksum: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence_number": self.sequence_number,
            "kind": self.kind.value if isinstance(self.kind, WALEntryKind) else self.kind,
            "entity_id": self.entity_id,
            "data": self.data,
            "timestamp": self.timestamp,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WALEntry:
        kind_val = data["kind"]
        if isinstance(kind_val, str):
            kind_val = WALEntryKind(kind_val)
        return cls(
            sequence_number=data["sequence_number"],
            kind=kind_val,
            entity_id=data["entity_id"],
            data=data["data"],
            timestamp=data["timestamp"],
            checksum=data["checksum"],
        )


@dataclass
class WALSegment:
    """A contiguous segment of WAL entries."""

    id: str
    entries: List[WALEntry]
    start_seq: int
    end_seq: int
    created_at: float
    size_bytes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "entries": [e.to_dict() for e in self.entries],
            "start_seq": self.start_seq,
            "end_seq": self.end_seq,
            "created_at": self.created_at,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WALSegment:
        entries = [WALEntry.from_dict(e) for e in data.get("entries", [])]
        return cls(
            id=data["id"],
            entries=entries,
            start_seq=data["start_seq"],
            end_seq=data["end_seq"],
            created_at=data["created_at"],
            size_bytes=data["size_bytes"],
        )


@dataclass
class Snapshot:
    """A point-in-time snapshot of system state."""

    id: str
    wal_sequence: int
    data: dict
    created_at: float
    size_bytes: int
    checksum: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "wal_sequence": self.wal_sequence,
            "data": self.data,
            "created_at": self.created_at,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Snapshot:
        return cls(
            id=data["id"],
            wal_sequence=data["wal_sequence"],
            data=data["data"],
            created_at=data["created_at"],
            size_bytes=data["size_bytes"],
            checksum=data["checksum"],
        )


@dataclass
class CheckpointConfig:
    """Configuration for WAL and snapshot management."""

    wal_dir: str = ".jugeo_wal"
    snapshot_dir: str = ".jugeo_snapshots"
    max_wal_entries: int = 10000
    compaction_threshold: int = 5000
    auto_snapshot_interval_s: float = 300
    max_snapshots_retained: int = 5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "wal_dir": self.wal_dir,
            "snapshot_dir": self.snapshot_dir,
            "max_wal_entries": self.max_wal_entries,
            "compaction_threshold": self.compaction_threshold,
            "auto_snapshot_interval_s": self.auto_snapshot_interval_s,
            "max_snapshots_retained": self.max_snapshots_retained,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CheckpointConfig:
        return cls(
            wal_dir=data.get("wal_dir", ".jugeo_wal"),
            snapshot_dir=data.get("snapshot_dir", ".jugeo_snapshots"),
            max_wal_entries=data.get("max_wal_entries", 10000),
            compaction_threshold=data.get("compaction_threshold", 5000),
            auto_snapshot_interval_s=data.get("auto_snapshot_interval_s", 300),
            max_snapshots_retained=data.get("max_snapshots_retained", 5),
        )
