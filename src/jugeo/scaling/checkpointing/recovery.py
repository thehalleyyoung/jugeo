from __future__ import annotations

import copy
import time
from typing import Any, Dict, Optional, Tuple

from .models import CheckpointConfig, Snapshot, WALEntry, WALEntryKind
from .snapshots import SnapshotManager
from .wal import WriteAheadLog


_EXPECTED_STATE_KEYS = [
    "judgments",
    "evidence",
    "obligations",
    "obstructions",
    "treaties",
    "certificates",
    "epoch",
]


class RecoveryManager:
    """Recovers system state from WAL + snapshots."""

    def __init__(self, wal: WriteAheadLog, snapshots: SnapshotManager) -> None:
        self._wal = wal
        self._snapshots = snapshots

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recover(self) -> Tuple[dict, int]:
        snapshot, start_seq = self._find_recovery_point()

        if snapshot is not None:
            state = copy.deepcopy(snapshot.data)
        else:
            state = self._empty_state()

        state = self._replay_wal(state, start_seq)
        final_seq = self._wal.current_sequence()
        return state, final_seq

    def verify_recovery(self, recovered_state: dict) -> bool:
        for key in _EXPECTED_STATE_KEYS:
            if key not in recovered_state:
                return False
        return True

    def create_checkpoint(self, state: dict) -> None:
        seq = self._wal.current_sequence()
        snapshot = self._snapshots.create_snapshot(state, seq)
        self._wal.compact(snapshot.wal_sequence)
        self._snapshots.prune_old_snapshots(
            keep=self._snapshots._config.max_snapshots_retained
        )

    def auto_checkpoint(self, state: dict, force: bool = False) -> None:
        stats = self._wal.statistics()
        if force or stats["total_entries"] >= self._wal._config.compaction_threshold:
            self.create_checkpoint(state)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_recovery_point(self) -> Tuple[Optional[Snapshot], int]:
        latest = self._snapshots.latest_snapshot()
        if latest is not None:
            return latest, latest.wal_sequence + 1
        return None, 0

    def _replay_wal(self, state: dict, from_seq: int) -> dict:
        entries = self._wal.replay(from_seq)
        for entry in entries:
            state = self._apply_entry(state, entry)
        return state

    def _apply_entry(self, state: dict, entry: WALEntry) -> dict:
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

        return state

    @staticmethod
    def _empty_state() -> dict:
        return {
            "judgments": {},
            "evidence": {},
            "obligations": {},
            "obstructions": [],
            "treaties": {},
            "certificates": {},
            "epoch": 0,
        }
