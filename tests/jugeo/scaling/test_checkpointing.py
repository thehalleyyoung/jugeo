from __future__ import annotations

import os
import time

import pytest

from src.jugeo.scaling.checkpointing.models import (
    CheckpointConfig,
    WALEntry,
    WALEntryKind,
    WALSegment,
    Snapshot,
)
from src.jugeo.scaling.checkpointing.wal import WriteAheadLog
from src.jugeo.scaling.checkpointing.snapshots import SnapshotManager
from src.jugeo.scaling.checkpointing.recovery import RecoveryManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def wal_config(tmp_path) -> CheckpointConfig:
    return CheckpointConfig(
        wal_dir=str(tmp_path / "wal"),
        snapshot_dir=str(tmp_path / "snapshots"),
        max_wal_entries=10000,
        compaction_threshold=50,
        max_snapshots_retained=5,
    )


@pytest.fixture
def wal(wal_config) -> WriteAheadLog:
    return WriteAheadLog(wal_config)


@pytest.fixture
def snap_mgr(wal_config) -> SnapshotManager:
    return SnapshotManager(wal_config)


# ===================================================================
# WriteAheadLog tests
# ===================================================================


class TestWriteAheadLog:
    def test_append_returns_sequence(self, wal: WriteAheadLog) -> None:
        seq = wal.append(WALEntryKind.JUDGMENT_ADD, "j1", {"name": "test"})
        assert seq == 0

    def test_append_increments_sequence(self, wal: WriteAheadLog) -> None:
        s0 = wal.append(WALEntryKind.JUDGMENT_ADD, "j1", {"a": 1})
        s1 = wal.append(WALEntryKind.EVIDENCE_ADD, "e1", {"b": 2})
        s2 = wal.append(WALEntryKind.TREATY_ADD, "t1", {"c": 3})
        assert s0 < s1 < s2

    def test_read_range(self, wal: WriteAheadLog) -> None:
        for i in range(10):
            wal.append(WALEntryKind.JUDGMENT_ADD, f"j{i}", {"i": i})
        entries = wal.read(3, 7)
        assert len(entries) == 5
        assert entries[0].sequence_number == 3
        assert entries[-1].sequence_number == 7

    def test_replay_from_start(self, wal: WriteAheadLog) -> None:
        for i in range(5):
            wal.append(WALEntryKind.EVIDENCE_ADD, f"e{i}", {"i": i})
        entries = wal.replay(0)
        assert len(entries) == 5

    def test_checksum_integrity(self, wal: WriteAheadLog) -> None:
        wal.append(WALEntryKind.JUDGMENT_ADD, "j1", {"hello": "world"})
        entries = wal.read(0)
        assert len(entries) == 1
        assert wal._verify_integrity(entries[0]) is True

    def test_compact_removes_old_segments(self, wal: WriteAheadLog) -> None:
        # Append >1000 entries to trigger a segment rotation
        for i in range(1001):
            wal.append(WALEntryKind.JUDGMENT_ADD, f"j{i}", {"i": i})
        segs_before = len(wal.segments())
        assert segs_before >= 2
        # Compact everything up to seq 999
        wal.compact(999)
        segs_after = len(wal.segments())
        assert segs_after < segs_before

    def test_flush_writes_to_disk(self, wal: WriteAheadLog, wal_config: CheckpointConfig) -> None:
        wal.append(WALEntryKind.JUDGMENT_ADD, "j1", {"x": 1})
        wal.flush()
        files = os.listdir(wal_config.wal_dir)
        json_files = [f for f in files if f.endswith(".json")]
        assert len(json_files) >= 1

    def test_batch_append(self, wal: WriteAheadLog) -> None:
        batch = [
            (WALEntryKind.JUDGMENT_ADD, "j1", {"a": 1}),
            (WALEntryKind.EVIDENCE_ADD, "e1", {"b": 2}),
            (WALEntryKind.TREATY_ADD, "t1", {"c": 3}),
        ]
        seqs = wal.append_batch(batch)
        assert len(seqs) == 3
        assert seqs == [0, 1, 2]

    def test_statistics(self, wal: WriteAheadLog) -> None:
        wal.append(WALEntryKind.JUDGMENT_ADD, "j1", {"x": 1})
        stats = wal.statistics()
        assert stats["total_entries"] == 1
        assert stats["current_sequence"] == 1


# ===================================================================
# SnapshotManager tests
# ===================================================================


class TestSnapshotManager:
    def test_create_snapshot(self, snap_mgr: SnapshotManager) -> None:
        snap = snap_mgr.create_snapshot({"judgments": {"j1": {}}}, 10)
        assert snap.id is not None
        assert snap.wal_sequence == 10

    def test_restore_snapshot(self, snap_mgr: SnapshotManager) -> None:
        state = {"judgments": {"j1": {"name": "test"}}, "epoch": 1}
        snap = snap_mgr.create_snapshot(state, 20)
        restored_state, seq = snap_mgr.restore_snapshot(snap.id)
        assert seq == 20
        assert restored_state["judgments"]["j1"]["name"] == "test"

    def test_latest_snapshot(self, snap_mgr: SnapshotManager) -> None:
        snap_mgr.create_snapshot({"a": 1}, 5)
        snap_mgr.create_snapshot({"b": 2}, 15)
        latest = snap_mgr.latest_snapshot()
        assert latest is not None
        assert latest.wal_sequence == 15

    def test_prune_old_snapshots(self, snap_mgr: SnapshotManager) -> None:
        for i in range(7):
            snap_mgr.create_snapshot({"i": i}, i * 10)
        snap_mgr.prune_old_snapshots(keep=5)
        assert len(snap_mgr.list_snapshots()) == 5

    def test_incremental_snapshot(self, snap_mgr: SnapshotManager) -> None:
        base = snap_mgr.create_snapshot({"judgments": {}}, 0)
        entry = WALEntry(
            sequence_number=1,
            kind=WALEntryKind.JUDGMENT_ADD,
            entity_id="j1",
            data={"name": "new"},
            timestamp=time.time(),
            checksum="abc",
        )
        incremental = snap_mgr.incremental_snapshot(base, [entry])
        restored, seq = snap_mgr.restore_snapshot(incremental.id)
        assert "j1" in restored["judgments"]

    def test_total_size_bytes(self, snap_mgr: SnapshotManager) -> None:
        snap_mgr.create_snapshot({"data": "x" * 100}, 5)
        assert snap_mgr.total_size_bytes() > 0


# ===================================================================
# RecoveryManager tests
# ===================================================================


class TestRecoveryManager:
    def test_recover_empty(self, wal: WriteAheadLog, snap_mgr: SnapshotManager) -> None:
        rm = RecoveryManager(wal, snap_mgr)
        state, seq = rm.recover()
        assert isinstance(state, dict)
        assert "judgments" in state

    def test_recover_from_wal(self, wal: WriteAheadLog, snap_mgr: SnapshotManager) -> None:
        wal.append(WALEntryKind.JUDGMENT_ADD, "j1", {"name": "first"})
        wal.append(WALEntryKind.EVIDENCE_ADD, "e1", {"kind": "proof"})
        rm = RecoveryManager(wal, snap_mgr)
        state, seq = rm.recover()
        assert "j1" in state["judgments"]
        assert "e1" in state["evidence"]

    def test_recover_from_snapshot_and_wal(
        self, wal: WriteAheadLog, snap_mgr: SnapshotManager
    ) -> None:
        # Create some WAL entries, then snapshot, then more WAL entries
        wal.append(WALEntryKind.JUDGMENT_ADD, "j_old", {"name": "old"})
        base_state = {
            "judgments": {"j_old": {"name": "old"}},
            "evidence": {},
            "obligations": {},
            "obstructions": [],
            "treaties": {},
            "certificates": {},
            "epoch": 0,
        }
        snap_mgr.create_snapshot(base_state, wal.current_sequence() - 1)
        wal.append(WALEntryKind.JUDGMENT_ADD, "j_new", {"name": "new"})

        rm = RecoveryManager(wal, snap_mgr)
        state, seq = rm.recover()
        assert "j_old" in state["judgments"]
        assert "j_new" in state["judgments"]

    def test_create_checkpoint(
        self, wal: WriteAheadLog, snap_mgr: SnapshotManager
    ) -> None:
        for i in range(10):
            wal.append(WALEntryKind.JUDGMENT_ADD, f"j{i}", {"i": i})
        rm = RecoveryManager(wal, snap_mgr)
        state, _ = rm.recover()
        rm.create_checkpoint(state)
        assert snap_mgr.latest_snapshot() is not None

    def test_verify_recovery(
        self, wal: WriteAheadLog, snap_mgr: SnapshotManager
    ) -> None:
        rm = RecoveryManager(wal, snap_mgr)
        state, _ = rm.recover()
        assert rm.verify_recovery(state) is True
        assert rm.verify_recovery({"only_one_key": True}) is False

    def test_auto_checkpoint(
        self, wal: WriteAheadLog, snap_mgr: SnapshotManager
    ) -> None:
        # compaction_threshold is 50 in our fixture
        for i in range(60):
            wal.append(WALEntryKind.JUDGMENT_ADD, f"j{i}", {"i": i})
        rm = RecoveryManager(wal, snap_mgr)
        state, _ = rm.recover()
        rm.auto_checkpoint(state)
        assert snap_mgr.latest_snapshot() is not None
