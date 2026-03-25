from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .models import CheckpointConfig, Snapshot, WALEntry


class SnapshotManager:
    """Manages point-in-time snapshots of system state."""

    def __init__(self, config: CheckpointConfig) -> None:
        self._config = config
        self._snapshots: Dict[str, Snapshot] = {}

        if config.snapshot_dir:
            os.makedirs(config.snapshot_dir, exist_ok=True)

        self._load_all_from_disk()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_snapshot(self, state: dict, wal_sequence: int) -> Snapshot:
        snapshot_id = str(uuid.uuid4())
        raw = json.dumps(state, sort_keys=True).encode("utf-8")
        checksum = hashlib.sha256(raw).hexdigest()
        size_bytes = len(raw)

        snapshot = Snapshot(
            id=snapshot_id,
            wal_sequence=wal_sequence,
            data=copy.deepcopy(state),
            created_at=time.time(),
            size_bytes=size_bytes,
            checksum=checksum,
        )
        self._snapshots[snapshot_id] = snapshot
        self._save_to_disk(snapshot)
        return snapshot

    def restore_snapshot(self, snapshot_id: str) -> Tuple[dict, int]:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            snapshot = self._load_from_disk(snapshot_id)

        if snapshot is None:
            raise KeyError(f"Snapshot {snapshot_id} not found")

        if not self._verify_snapshot(snapshot):
            raise ValueError(f"Snapshot {snapshot_id} checksum mismatch")

        return copy.deepcopy(snapshot.data), snapshot.wal_sequence

    def latest_snapshot(self) -> Optional[Snapshot]:
        if not self._snapshots:
            return None
        return max(self._snapshots.values(), key=lambda s: s.wal_sequence)

    def list_snapshots(self) -> List[Snapshot]:
        return sorted(self._snapshots.values(), key=lambda s: s.created_at)

    def prune_old_snapshots(self, keep: int = 5) -> None:
        snapshots = sorted(self._snapshots.values(), key=lambda s: s.created_at)
        if len(snapshots) <= keep:
            return
        to_remove = snapshots[: len(snapshots) - keep]
        for snap in to_remove:
            self._delete_from_disk(snap.id)
            del self._snapshots[snap.id]

    def incremental_snapshot(
        self, base_snapshot: Snapshot, wal_entries: List[WALEntry]
    ) -> Snapshot:
        state = copy.deepcopy(base_snapshot.data)
        for entry in wal_entries:
            self._apply_entry(state, entry)

        new_seq = base_snapshot.wal_sequence
        if wal_entries:
            new_seq = wal_entries[-1].sequence_number

        return self.create_snapshot(state, new_seq)

    def total_size_bytes(self) -> int:
        return sum(s.size_bytes for s in self._snapshots.values())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_entry(state: dict, entry: WALEntry) -> None:
        kind_str = entry.kind.value if hasattr(entry.kind, "value") else str(entry.kind)

        if kind_str in ("JUDGMENT_ADD", "JUDGMENT_UPDATE"):
            state.setdefault("judgments", {})[entry.entity_id] = entry.data
        elif kind_str == "EVIDENCE_ADD":
            state.setdefault("evidence", {})[entry.entity_id] = entry.data
        elif kind_str in ("OBLIGATION_ADD", "OBLIGATION_UPDATE"):
            state.setdefault("obligations", {})[entry.entity_id] = entry.data
        elif kind_str == "OBSTRUCTION_ADD":
            state.setdefault("obstructions", []).append(
                {"entity_id": entry.entity_id, **entry.data}
            )
        elif kind_str == "OBSTRUCTION_RESOLVE":
            obs = state.get("obstructions", [])
            for o in obs:
                if o.get("entity_id") == entry.entity_id:
                    o["resolved"] = True
        elif kind_str in ("TREATY_ADD", "TREATY_UPDATE"):
            state.setdefault("treaties", {})[entry.entity_id] = entry.data
        elif kind_str == "CERTIFICATE_ADD":
            state.setdefault("certificates", {})[entry.entity_id] = entry.data
        elif kind_str == "EPOCH_ADVANCE":
            state["epoch"] = entry.data.get("epoch", 0)

    def _save_to_disk(self, snapshot: Snapshot) -> None:
        if not self._config.snapshot_dir:
            return
        path = os.path.join(self._config.snapshot_dir, f"{snapshot.id}.json")
        with open(path, "w") as fh:
            json.dump(snapshot.to_dict(), fh)

    def _load_from_disk(self, snapshot_id: str) -> Optional[Snapshot]:
        if not self._config.snapshot_dir:
            return None
        path = os.path.join(self._config.snapshot_dir, f"{snapshot_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r") as fh:
            data = json.load(fh)
        snapshot = Snapshot.from_dict(data)
        self._snapshots[snapshot.id] = snapshot
        return snapshot

    def _load_all_from_disk(self) -> None:
        if not self._config.snapshot_dir or not os.path.isdir(self._config.snapshot_dir):
            return
        for fname in os.listdir(self._config.snapshot_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._config.snapshot_dir, fname)
            try:
                with open(path, "r") as fh:
                    data = json.load(fh)
                snapshot = Snapshot.from_dict(data)
                self._snapshots[snapshot.id] = snapshot
            except (json.JSONDecodeError, KeyError):
                continue

    def _delete_from_disk(self, snapshot_id: str) -> None:
        if not self._config.snapshot_dir:
            return
        path = os.path.join(self._config.snapshot_dir, f"{snapshot_id}.json")
        if os.path.exists(path):
            os.remove(path)

    def _verify_snapshot(self, snapshot: Snapshot) -> bool:
        raw = json.dumps(snapshot.data, sort_keys=True).encode("utf-8")
        expected = hashlib.sha256(raw).hexdigest()
        return expected == snapshot.checksum
