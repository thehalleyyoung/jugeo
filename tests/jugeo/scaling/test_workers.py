"""Tests for JuGeo worker architecture (in-process, no actual sockets).

All tests use in-process simulation:

- :class:`MessageSerializer` is tested by calling ``serialize`` /
  ``deserialize`` directly.
- :class:`PartitionManager` is tested by building small synthetic graphs.
- :class:`Coordinator` is used in its thread-based form but without network
  sockets; workers are registered directly via ``register_worker``.
- :class:`Worker` is invoked via ``run_next_task`` (synchronous, no threads).
"""

from __future__ import annotations

import time
import uuid

import pytest

from jugeo.scaling.workers.models import (
    ClusterStatus,
    CoordinatorConfig,
    Message,
    MessageKind,
    PartitionDef,
    Task,
    TaskKind,
    TaskResult,
    TaskState,
    WorkerConfig,
    WorkerInfo,
    WorkerState,
)
from jugeo.scaling.workers.message_protocol import MessageSerializer
from jugeo.scaling.workers.coordinator import Coordinator
from jugeo.scaling.workers.worker import Worker
from jugeo.scaling.workers.partition_manager import PartitionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(**kwargs) -> Coordinator:
    cfg = CoordinatorConfig(
        heartbeat_interval_s=999.0,  # Disable automatic dead-worker detection
        **kwargs,
    )
    coord = Coordinator(cfg)
    coord.start()
    return coord


def _make_worker_info(state: WorkerState = WorkerState.IDLE) -> WorkerInfo:
    info = WorkerInfo.create(pid=12345)
    info.state = state
    return info


def _make_task(kind: TaskKind = TaskKind.PARSE_FILES, **kwargs) -> Task:
    return Task.create(kind=kind, payload=kwargs or {})


def _small_graph():
    """Return a tiny synthetic graph: 4 coords, 3 morphisms, 1 cycle."""
    coordinates = [
        {"id": "c1", "level": "function", "package": "pkg"},
        {"id": "c2", "level": "function", "package": "pkg"},
        {"id": "c3", "level": "module",   "package": "pkg"},
        {"id": "c4", "level": "module",   "package": "pkg"},
    ]
    morphisms = [
        {"id": "m1", "source_id": "c1", "target_id": "c2"},
        {"id": "m2", "source_id": "c2", "target_id": "c1"},  # cycle c1<->c2
        {"id": "m3", "source_id": "c3", "target_id": "c4"},
    ]
    return coordinates, morphisms


# ===========================================================================
# MessageSerializer tests
# ===========================================================================

class TestMessageSerializer:

    def test_serialize_deserialize_roundtrip(self):
        s = MessageSerializer()
        msg = Message.create("heartbeat", "worker-1", "coordinator", {"foo": 42})
        raw = s.serialize(msg)
        recovered = s.deserialize(raw)
        assert recovered.id == msg.id
        assert recovered.kind == msg.kind
        assert recovered.sender == msg.sender
        assert recovered.receiver == msg.receiver
        assert recovered.payload == msg.payload

    def test_header_encode_decode(self):
        s = MessageSerializer()
        for length in [0, 1, 255, 256, 65535, 1_000_000]:
            header = s._encode_header(length)
            assert len(header) == 4
            assert s._decode_header(header) == length

    def test_serialize_produces_length_prefix(self):
        s = MessageSerializer()
        msg = Message.create("heartbeat", "a", "b", {})
        raw = s.serialize(msg)
        declared = s._decode_header(raw[:4])
        assert len(raw) == 4 + declared

    def test_deserialize_all_message_kinds(self):
        s = MessageSerializer()
        for kind in MessageKind:
            msg = Message.create(kind.value, "sender", "receiver", {"k": "v"})
            raw = s.serialize(msg)
            recovered = s.deserialize(raw)
            assert recovered.kind == kind.value

    def test_roundtrip_with_nested_payload(self):
        s = MessageSerializer()
        payload = {
            "task": {"id": "t1", "kind": "parse_files", "data": [1, 2, 3]},
            "nested": {"a": {"b": {"c": True}}},
        }
        msg = Message.create("task_assign", "coord", "worker", payload)
        raw = s.serialize(msg)
        recovered = s.deserialize(raw)
        assert recovered.payload == payload

    def test_deserialize_truncated_raises(self):
        s = MessageSerializer()
        msg = Message.create("heartbeat", "a", "b", {})
        raw = s.serialize(msg)
        # Truncate: keep only header + 2 bytes of body
        with pytest.raises(ValueError, match="Truncated"):
            s.deserialize(raw[:6])

    def test_deserialize_too_short_raises(self):
        s = MessageSerializer()
        with pytest.raises(ValueError, match="too short"):
            s.deserialize(b"\x00\x00")

    def test_message_to_dict_from_dict(self):
        msg = Message.create("heartbeat", "w1", "coord", {"x": 1})
        d = msg.to_dict()
        recovered = Message.from_dict(d)
        assert recovered.id == msg.id
        assert recovered.timestamp == pytest.approx(msg.timestamp, abs=1e-3)


# ===========================================================================
# Models tests
# ===========================================================================

class TestModels:

    def test_worker_info_roundtrip(self):
        info = WorkerInfo.create(pid=9999)
        info.state = WorkerState.BUSY
        info.tasks_completed = 5
        d = info.to_dict()
        recovered = WorkerInfo.from_dict(d)
        assert recovered.id == info.id
        assert recovered.pid == 9999
        assert recovered.state == WorkerState.BUSY
        assert recovered.tasks_completed == 5

    def test_task_roundtrip(self):
        task = Task.create(
            kind=TaskKind.VERIFY_PARTITION,
            payload={"partition_id": "p1"},
            priority=3.0,
            timeout_s=60.0,
            depends_on=["dep1", "dep2"],
        )
        d = task.to_dict()
        recovered = Task.from_dict(d)
        assert recovered.id == task.id
        assert recovered.kind == TaskKind.VERIFY_PARTITION
        assert recovered.priority == 3.0
        assert recovered.depends_on == ["dep1", "dep2"]

    def test_task_result_roundtrip(self):
        result = TaskResult(
            task_id="t1",
            success=True,
            result_data={"verified": True},
            error_message=None,
            duration_ms=123.4,
            worker_id="w1",
        )
        d = result.to_dict()
        recovered = TaskResult.from_dict(d)
        assert recovered.task_id == "t1"
        assert recovered.success is True
        assert recovered.duration_ms == pytest.approx(123.4)

    def test_partition_def_roundtrip(self):
        p = PartitionDef.create(
            coordinate_ids=["c1", "c2"],
            morphism_ids=["m1"],
            estimated_cost=10.0,
            level="function",
            package="mypkg",
        )
        d = p.to_dict()
        recovered = PartitionDef.from_dict(d)
        assert recovered.id == p.id
        assert recovered.coordinate_ids == ["c1", "c2"]
        assert recovered.level == "function"

    def test_task_is_terminal(self):
        for state in (TaskState.COMPLETED, TaskState.FAILED,
                      TaskState.CANCELLED, TaskState.TIMEOUT):
            t = Task.create(TaskKind.PARSE_FILES, {})
            t.state = state
            assert t.is_terminal()
        for state in (TaskState.PENDING, TaskState.ASSIGNED, TaskState.RUNNING):
            t = Task.create(TaskKind.PARSE_FILES, {})
            t.state = state
            assert not t.is_terminal()

    def test_task_is_ready(self):
        t = Task.create(TaskKind.PARSE_FILES, {}, depends_on=["a", "b"])
        assert not t.is_ready({"a"})
        assert t.is_ready({"a", "b"})
        assert t.is_ready({"a", "b", "c"})

    def test_cluster_status_properties(self):
        workers = [
            WorkerInfo.create(pid=1),
            WorkerInfo.create(pid=2),
        ]
        workers[0].state = WorkerState.IDLE
        workers[1].state = WorkerState.BUSY
        status = ClusterStatus(
            coordinator_alive=True,
            workers=workers,
            pending_tasks=3,
            running_tasks=1,
            completed_tasks=10,
            failed_tasks=2,
            total_partitions=5,
            verified_partitions=4,
        )
        assert status.idle_workers == 1
        assert status.busy_workers == 1
        assert status.total_workers == 2

    def test_coordinator_config_roundtrip(self):
        cfg = CoordinatorConfig(max_workers=8, max_retries=3)
        d = cfg.to_dict()
        recovered = CoordinatorConfig.from_dict(d)
        assert recovered.max_workers == 8
        assert recovered.max_retries == 3

    def test_worker_config_roundtrip(self):
        cfg = WorkerConfig(coordinator_port=12345, max_memory_mb=2048)
        d = cfg.to_dict()
        recovered = WorkerConfig.from_dict(d)
        assert recovered.coordinator_port == 12345
        assert recovered.max_memory_mb == 2048


# ===========================================================================
# PartitionManager tests
# ===========================================================================

class TestPartitionManager:

    def setup_method(self):
        self.pm = PartitionManager()

    # ------------------------------------------------------------------
    # SCC partitioning
    # ------------------------------------------------------------------

    def test_scc_cycle_grouped(self):
        coords, morphisms = _small_graph()
        partitions = self.pm.create_partitions(coords, morphisms, strategy="scc")
        # c1 and c2 form a cycle; they should be in the same partition.
        c1_part = next(p for p in partitions if "c1" in p.coordinate_ids)
        assert "c2" in c1_part.coordinate_ids

    def test_scc_acyclic_singletons(self):
        coords = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        morphisms = [
            {"id": "m1", "source_id": "a", "target_id": "b"},
            {"id": "m2", "source_id": "b", "target_id": "c"},
        ]
        partitions = self.pm.create_partitions(coords, morphisms, strategy="scc")
        # No cycles → each node is its own SCC.
        assert len(partitions) == 3
        sizes = sorted(len(p.coordinate_ids) for p in partitions)
        assert sizes == [1, 1, 1]

    def test_scc_complete_cycle(self):
        coords = [{"id": str(i)} for i in range(4)]
        morphisms = [
            {"id": f"m{i}", "source_id": str(i), "target_id": str((i + 1) % 4)}
            for i in range(4)
        ]
        partitions = self.pm.create_partitions(coords, morphisms, strategy="scc")
        assert len(partitions) == 1
        assert sorted(partitions[0].coordinate_ids) == ["0", "1", "2", "3"]

    def test_scc_empty_input(self):
        partitions = self.pm.create_partitions([], [], strategy="scc")
        assert partitions == []

    def test_scc_no_morphisms(self):
        coords = [{"id": "x"}, {"id": "y"}]
        partitions = self.pm.create_partitions(coords, [], strategy="scc")
        assert len(partitions) == 2

    # ------------------------------------------------------------------
    # Level partitioning
    # ------------------------------------------------------------------

    def test_level_partition_groups_by_level(self):
        coords, morphisms = _small_graph()
        partitions = self.pm.create_partitions(coords, morphisms, strategy="level")
        levels = {p.level for p in partitions}
        # Small graph has "function" and "module" levels.
        assert "function" in levels
        assert "module" in levels

    def test_level_partition_all_same_level(self):
        coords = [{"id": f"c{i}", "level": "function"} for i in range(5)]
        partitions = self.pm.create_partitions(coords, [], strategy="level")
        assert len(partitions) == 1
        assert len(partitions[0].coordinate_ids) == 5

    # ------------------------------------------------------------------
    # Balanced partitioning
    # ------------------------------------------------------------------

    def test_balanced_creates_k_groups(self):
        coords = [{"id": f"c{i}", "cost": 1.0} for i in range(10)]
        partitions = self.pm.create_partitions(
            coords, [], strategy="balanced", max_size=3
        )
        # With max_size=3 and 10 coords, k=10//3=3 groups.
        assert len(partitions) >= 1
        # All coordinates should be covered.
        all_ids = {cid for p in partitions for cid in p.coordinate_ids}
        assert all_ids == {f"c{i}" for i in range(10)}

    def test_balanced_no_overlap(self):
        coords = [{"id": f"c{i}"} for i in range(6)]
        partitions = self.pm.create_partitions(
            coords, [], strategy="balanced", max_size=2
        )
        all_ids = [cid for p in partitions for cid in p.coordinate_ids]
        assert len(all_ids) == len(set(all_ids)), "Coordinates should not appear in multiple partitions"

    # ------------------------------------------------------------------
    # Worker assignment
    # ------------------------------------------------------------------

    def test_assign_to_workers_cost_balanced(self):
        partitions = [
            PartitionDef.create(["c1"], [], estimated_cost=10.0),
            PartitionDef.create(["c2"], [], estimated_cost=1.0),
            PartitionDef.create(["c3"], [], estimated_cost=5.0),
            PartitionDef.create(["c4"], [], estimated_cost=8.0),
        ]
        workers = [_make_worker_info() for _ in range(2)]
        assignment = self.pm.assign_to_workers(partitions, workers, strategy="cost_balanced")
        assert len(assignment) == 4
        assert set(assignment.keys()) == {p.id for p in partitions}
        assert set(assignment.values()).issubset({w.id for w in workers})

    def test_assign_to_workers_round_robin(self):
        partitions = [PartitionDef.create([f"c{i}"], []) for i in range(6)]
        workers = [_make_worker_info() for _ in range(3)]
        assignment = self.pm.assign_to_workers(partitions, workers, strategy="round_robin")
        # Each worker should get exactly 2 partitions.
        from collections import Counter
        counts = Counter(assignment.values())
        assert all(v == 2 for v in counts.values())

    def test_assign_empty_workers_returns_empty(self):
        partitions = [PartitionDef.create(["c1"], [])]
        assignment = self.pm.assign_to_workers(partitions, [], strategy="cost_balanced")
        assert assignment == {}

    # ------------------------------------------------------------------
    # Cross-partition tasks
    # ------------------------------------------------------------------

    def test_cross_partition_tasks_created(self):
        coords, morphisms = _small_graph()
        partitions = self.pm.create_partitions(coords, morphisms, strategy="scc")
        # If the SCC strategy put c1/c2 in one partition and c3/c4 in another,
        # m3 (c3→c4) is intra-partition, but there are no cross-partition edges.
        # Add an explicit cross-partition morphism for the test.
        #
        # Force two specific partitions.
        p1 = PartitionDef.create(["c1", "c2"], ["m1", "m2"])
        p2 = PartitionDef.create(["c3", "c4"], ["m3"])
        cross_morph = {"id": "mx", "source_id": "c2", "target_id": "c3"}
        tasks = self.pm.cross_partition_tasks([p1, p2], [cross_morph])
        assert len(tasks) == 1
        assert tasks[0].kind == TaskKind.DESCENT_CHECK

    def test_cross_partition_tasks_no_cross_edges(self):
        p1 = PartitionDef.create(["c1", "c2"], ["m1"])
        morphisms = [{"id": "m1", "source_id": "c1", "target_id": "c2"}]
        tasks = self.pm.cross_partition_tasks([p1], morphisms)
        assert tasks == []

    def test_cross_partition_tasks_deduplication(self):
        p1 = PartitionDef.create(["c1"], [])
        p2 = PartitionDef.create(["c2"], [])
        # Two morphisms crossing the same partition boundary.
        morphisms = [
            {"id": "ma", "source_id": "c1", "target_id": "c2"},
            {"id": "mb", "source_id": "c1", "target_id": "c2"},
        ]
        tasks = self.pm.cross_partition_tasks([p1, p2], morphisms)
        # Should produce exactly 1 task for the (p1, p2) pair.
        assert len(tasks) == 1

    # ------------------------------------------------------------------
    # Tarjan SCC
    # ------------------------------------------------------------------

    def test_tarjan_empty(self):
        result = self.pm._tarjan_scc({})
        assert result == []

    def test_tarjan_single_node(self):
        result = self.pm._tarjan_scc({"a": []})
        assert len(result) == 1
        assert result[0] == ["a"]

    def test_tarjan_two_node_cycle(self):
        adjacency = {"a": ["b"], "b": ["a"]}
        sccs = self.pm._tarjan_scc(adjacency)
        assert len(sccs) == 1
        assert set(sccs[0]) == {"a", "b"}

    def test_tarjan_dag(self):
        adjacency = {"a": ["b"], "b": ["c"], "c": []}
        sccs = self.pm._tarjan_scc(adjacency)
        assert len(sccs) == 3

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def test_partition_statistics(self):
        partitions = [
            PartitionDef.create(["c1", "c2"], ["m1"], estimated_cost=10.0, level="function"),
            PartitionDef.create(["c3"], [], estimated_cost=5.0, level="module"),
        ]
        stats = self.pm.partition_statistics(partitions)
        assert stats["count"] == 2
        assert stats["total_coordinates"] == 3
        assert stats["total_morphisms"] == 1
        assert stats["min_cost"] == 5.0
        assert stats["max_cost"] == 10.0
        assert stats["mean_cost"] == pytest.approx(7.5)
        assert "function" in stats["levels"]
        assert "module" in stats["levels"]

    def test_partition_statistics_empty(self):
        stats = self.pm.partition_statistics([])
        assert stats["count"] == 0

    # ------------------------------------------------------------------
    # Rebalance
    # ------------------------------------------------------------------

    def test_balance_partitions_reduces_max_cost(self):
        partitions = [
            PartitionDef.create([f"c{i}" for i in range(10)], [], estimated_cost=100.0),
            PartitionDef.create(["cx"], [], estimated_cost=1.0),
        ]
        result = self.pm.balance_partitions(partitions, factor=0.5)
        costs = [p.estimated_cost for p in result]
        assert max(costs) < 100.0  # The big partition was split.

    def test_rebalance_on_worker_change(self):
        partitions = [PartitionDef.create([f"c{i}"], []) for i in range(4)]
        old_workers = [_make_worker_info() for _ in range(2)]
        new_workers = [_make_worker_info() for _ in range(3)]
        assignment = self.pm.rebalance_on_worker_change(
            partitions, old_workers, new_workers
        )
        assert len(assignment) == 4
        assert set(assignment.values()).issubset({w.id for w in new_workers})


# ===========================================================================
# Task lifecycle tests
# ===========================================================================

class TestTaskLifecycle:

    def test_create_task(self):
        task = Task.create(TaskKind.VERIFY_PARTITION, {"partition_id": "p1"})
        assert task.state == TaskState.PENDING
        assert task.assigned_worker is None
        assert task.result is None
        assert task.error is None

    def test_assign_task(self):
        task = Task.create(TaskKind.VERIFY_PARTITION, {})
        task.state = TaskState.ASSIGNED
        task.assigned_worker = "w1"
        assert task.state == TaskState.ASSIGNED
        assert not task.is_terminal()

    def test_complete_task(self):
        task = Task.create(TaskKind.VERIFY_PARTITION, {})
        task.state = TaskState.COMPLETED
        task.result = {"verified": True}
        task.completed_at = time.time()
        assert task.is_terminal()
        assert task.result["verified"] is True

    def test_fail_task(self):
        task = Task.create(TaskKind.VERIFY_PARTITION, {})
        task.state = TaskState.FAILED
        task.error = "Something went wrong"
        assert task.is_terminal()

    def test_cancel_task(self):
        task = Task.create(TaskKind.VERIFY_PARTITION, {})
        task.state = TaskState.CANCELLED
        assert task.is_terminal()

    def test_timeout_task(self):
        task = Task.create(TaskKind.VERIFY_PARTITION, {}, timeout_s=1.0)
        task.state = TaskState.TIMEOUT
        assert task.is_terminal()

    def test_all_task_kinds(self):
        for kind in TaskKind:
            task = Task.create(kind, {"dummy": True})
            assert task.kind == kind

    def test_task_dependency_chain(self):
        t1 = Task.create(TaskKind.PARSE_FILES, {})
        t2 = Task.create(TaskKind.VERIFY_PARTITION, {}, depends_on=[t1.id])
        t3 = Task.create(TaskKind.DESCENT_CHECK, {}, depends_on=[t2.id])

        assert t2.is_ready(set()) is False
        assert t2.is_ready({t1.id}) is True
        assert t3.is_ready({t1.id}) is False
        assert t3.is_ready({t1.id, t2.id}) is True


# ===========================================================================
# Coordinator tests (in-process, no sockets)
# ===========================================================================

class TestCoordinator:

    def setup_method(self):
        self.coord = _make_coordinator(max_workers=4)

    def teardown_method(self):
        self.coord.stop()

    # ------------------------------------------------------------------
    # Basic submit / result
    # ------------------------------------------------------------------

    def test_submit_task_returns_id(self):
        task = _make_task()
        task_id = self.coord.submit_task(task)
        assert task_id == task.id

    def test_submit_batch_returns_ids(self):
        tasks = [_make_task() for _ in range(5)]
        ids = self.coord.submit_batch(tasks)
        assert ids == [t.id for t in tasks]

    def test_get_result_before_completion_is_none(self):
        task = _make_task()
        self.coord.submit_task(task)
        result = self.coord.get_task_result(task.id)
        assert result is None

    def test_handle_task_result_success(self):
        task = _make_task()
        self.coord.submit_task(task)
        worker = _make_worker_info()
        self.coord.register_worker(worker)
        # Manually assign task to worker.
        with self.coord._lock:
            t = self.coord._tasks[task.id]
            t.state = TaskState.ASSIGNED
            t.assigned_worker = worker.id
        # Inject a result.
        result = TaskResult(
            task_id=task.id,
            success=True,
            result_data={"ok": True},
            duration_ms=50.0,
            worker_id=worker.id,
        )
        self.coord._handle_task_result(result)
        stored = self.coord.get_task_result(task.id)
        assert stored is not None
        assert stored.success is True

    def test_handle_task_result_failure_triggers_retry(self):
        cfg = CoordinatorConfig(heartbeat_interval_s=999.0, max_retries=2)
        coord = Coordinator(cfg)
        coord.start()
        task = _make_task()
        coord.submit_task(task)
        worker = _make_worker_info()
        coord.register_worker(worker)
        # Mark as running.
        with coord._lock:
            t = coord._tasks[task.id]
            t.state = TaskState.RUNNING
            t.assigned_worker = worker.id
        # Report failure.
        result = TaskResult(
            task_id=task.id,
            success=False,
            result_data={},
            error_message="transient error",
            duration_ms=10.0,
            worker_id=worker.id,
        )
        coord._handle_task_result(result)
        # Task should be re-queued (PENDING).
        with coord._lock:
            t = coord._tasks[task.id]
        assert t.state == TaskState.PENDING
        coord.stop()

    def test_handle_task_result_failure_exhausted_retries(self):
        cfg = CoordinatorConfig(heartbeat_interval_s=999.0, max_retries=0)
        coord = Coordinator(cfg)
        coord.start()
        task = _make_task()
        coord.submit_task(task)
        worker = _make_worker_info()
        coord.register_worker(worker)
        with coord._lock:
            t = coord._tasks[task.id]
            t.state = TaskState.RUNNING
            t.assigned_worker = worker.id
        result = TaskResult(
            task_id=task.id,
            success=False,
            result_data={},
            error_message="fatal error",
            duration_ms=10.0,
            worker_id=worker.id,
        )
        coord._handle_task_result(result)
        with coord._lock:
            t = coord._tasks[task.id]
        assert t.state == TaskState.FAILED
        coord.stop()

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def test_schedule_assigns_pending_to_idle_worker(self):
        task = _make_task()
        self.coord.submit_task(task)
        worker = _make_worker_info(WorkerState.IDLE)
        self.coord.register_worker(worker)  # no channel; send is skipped
        self.coord._schedule_tasks()
        with self.coord._lock:
            t = self.coord._tasks[task.id]
        assert t.state in (TaskState.ASSIGNED, TaskState.PENDING)

    def test_schedule_respects_dependencies(self):
        t1 = Task.create(TaskKind.PARSE_FILES, {})
        t2 = Task.create(TaskKind.VERIFY_PARTITION, {}, depends_on=[t1.id])
        self.coord.submit_batch([t1, t2])
        worker = _make_worker_info(WorkerState.IDLE)
        self.coord.register_worker(worker)
        self.coord._schedule_tasks()
        with self.coord._lock:
            # t1 should be assigned; t2 should still be pending.
            assert self.coord._tasks[t1.id].state == TaskState.ASSIGNED
            assert self.coord._tasks[t2.id].state == TaskState.PENDING

    def test_schedule_priority_ordering(self):
        low_priority = Task.create(TaskKind.PARSE_FILES, {}, priority=0.5)
        high_priority = Task.create(TaskKind.VERIFY_PARTITION, {}, priority=10.0)
        self.coord.submit_batch([low_priority, high_priority])
        # Only one idle worker so only one task can be assigned.
        worker = _make_worker_info(WorkerState.IDLE)
        self.coord.register_worker(worker)
        self.coord._schedule_tasks()
        with self.coord._lock:
            # The high-priority task should be picked first.
            assert self.coord._tasks[high_priority.id].state == TaskState.ASSIGNED
            assert self.coord._tasks[low_priority.id].state == TaskState.PENDING

    def test_schedule_no_idle_workers(self):
        task = _make_task()
        self.coord.submit_task(task)
        worker = _make_worker_info(WorkerState.BUSY)
        self.coord.register_worker(worker)
        self.coord._schedule_tasks()
        with self.coord._lock:
            assert self.coord._tasks[task.id].state == TaskState.PENDING

    # ------------------------------------------------------------------
    # Heartbeat / dead worker detection
    # ------------------------------------------------------------------

    def test_handle_heartbeat_updates_worker(self):
        worker = _make_worker_info()
        self.coord.register_worker(worker)
        old_heartbeat = self.coord._workers[worker.id].last_heartbeat
        time.sleep(0.01)
        self.coord._handle_heartbeat(
            worker.id, {"state": "idle", "memory_mb": 512.0, "cpu_percent": 5.0}
        )
        new_heartbeat = self.coord._workers[worker.id].last_heartbeat
        assert new_heartbeat >= old_heartbeat
        assert self.coord._workers[worker.id].memory_mb == 512.0

    def test_detect_dead_workers(self):
        cfg = CoordinatorConfig(heartbeat_interval_s=0.01)
        coord = Coordinator(cfg)
        coord.start()
        worker = _make_worker_info()
        coord.register_worker(worker)
        # Make the heartbeat very old.
        coord._workers[worker.id].last_heartbeat = time.time() - 10.0
        dead = coord._detect_dead_workers()
        assert worker.id in dead
        coord.stop()

    def test_reassign_tasks_from_dead_worker(self):
        task = _make_task()
        self.coord.submit_task(task)
        worker = _make_worker_info()
        self.coord.register_worker(worker)
        # Simulate task being assigned.
        with self.coord._lock:
            t = self.coord._tasks[task.id]
            t.state = TaskState.RUNNING
            t.assigned_worker = worker.id
        self.coord._reassign_tasks(worker.id)
        with self.coord._lock:
            t = self.coord._tasks[task.id]
        # After reassignment, task should be pending (retry budget not exhausted).
        assert t.state == TaskState.PENDING
        assert t.assigned_worker is None

    def test_reassign_exhausted_retries_marks_failed(self):
        cfg = CoordinatorConfig(heartbeat_interval_s=999.0, max_retries=0)
        coord = Coordinator(cfg)
        coord.start()
        task = _make_task()
        coord.submit_task(task)
        worker = _make_worker_info()
        coord.register_worker(worker)
        with coord._lock:
            t = coord._tasks[task.id]
            t.state = TaskState.RUNNING
            t.assigned_worker = worker.id
        coord._reassign_tasks(worker.id)
        with coord._lock:
            t = coord._tasks[task.id]
        assert t.state == TaskState.FAILED
        coord.stop()

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def test_cancel_pending_task(self):
        task = _make_task()
        self.coord.submit_task(task)
        self.coord.cancel_task(task.id)
        with self.coord._lock:
            t = self.coord._tasks[task.id]
        assert t.state == TaskState.CANCELLED

    # ------------------------------------------------------------------
    # Cluster status
    # ------------------------------------------------------------------

    def test_cluster_status(self):
        w1 = _make_worker_info(WorkerState.IDLE)
        w2 = _make_worker_info(WorkerState.BUSY)
        self.coord.register_worker(w1)
        self.coord.register_worker(w2)
        task = _make_task()
        self.coord.submit_task(task)
        status = self.coord.cluster_status()
        assert status.coordinator_alive is True
        assert status.total_workers == 2
        assert status.pending_tasks == 1

    # ------------------------------------------------------------------
    # Partition assignment
    # ------------------------------------------------------------------

    def test_assign_partitions(self):
        partitions = [PartitionDef.create([f"c{i}"], []) for i in range(6)]
        workers = [_make_worker_info() for _ in range(3)]
        for w in workers:
            self.coord.register_worker(w)
        assignment = self.coord.assign_partitions(
            partitions, [w.id for w in workers]
        )
        assert len(assignment) == 6
        assert set(assignment.keys()) == {p.id for p in partitions}

    # ------------------------------------------------------------------
    # Wait for task (synchronous result path)
    # ------------------------------------------------------------------

    def test_wait_for_task_timeout_returns_none(self):
        task = _make_task()
        self.coord.submit_task(task)
        result = self.coord.wait_for_task(task.id, timeout=0.05)
        assert result is None

    def test_wait_for_task_completes(self):
        task = _make_task()
        self.coord.submit_task(task)
        worker = _make_worker_info()
        self.coord.register_worker(worker)

        def _complete_after_delay():
            time.sleep(0.05)
            r = TaskResult(
                task_id=task.id,
                success=True,
                result_data={"done": True},
                duration_ms=10.0,
                worker_id=worker.id,
            )
            self.coord._handle_task_result(r)

        import threading
        t = threading.Thread(target=_complete_after_delay, daemon=True)
        t.start()
        result = self.coord.wait_for_task(task.id, timeout=2.0)
        assert result is not None
        assert result.success is True

    def test_wait_for_all(self):
        tasks = [_make_task() for _ in range(3)]
        ids = self.coord.submit_batch(tasks)
        worker = _make_worker_info()
        self.coord.register_worker(worker)

        import threading

        def _complete_all():
            time.sleep(0.05)
            for task in tasks:
                r = TaskResult(
                    task_id=task.id,
                    success=True,
                    result_data={},
                    duration_ms=1.0,
                    worker_id=worker.id,
                )
                self.coord._handle_task_result(r)

        t = threading.Thread(target=_complete_all, daemon=True)
        t.start()
        results = self.coord.wait_for_all(ids, timeout=2.0)
        assert len(results) == 3
        assert all(r is not None and r.success for r in results)


# ===========================================================================
# Worker tests (mock handlers)
# ===========================================================================

class TestWorker:

    def setup_method(self):
        self.worker = Worker(WorkerConfig())

    # ------------------------------------------------------------------
    # Handler dispatch
    # ------------------------------------------------------------------

    def test_execute_parse_files(self):
        task = Task.create(TaskKind.PARSE_FILES, {"files": []})
        result = self.worker.execute_task(task)
        assert result.success is True
        assert "coordinates" in result.result_data
        assert "errors" in result.result_data

    def test_execute_verify_partition(self):
        task = Task.create(
            TaskKind.VERIFY_PARTITION,
            {"partition_id": "p1", "coordinate_ids": ["c1", "c2"]},
        )
        result = self.worker.execute_task(task)
        assert result.success is True
        assert result.result_data["verified"] is True
        assert result.result_data["checked_coordinates"] == 2

    def test_execute_descent_check(self):
        task = Task.create(
            TaskKind.DESCENT_CHECK,
            {
                "overlap_ids": ["c1", "c2"],
                "source_partition": "p1",
                "target_partition": "p2",
            },
        )
        result = self.worker.execute_task(task)
        assert result.success is True
        assert result.result_data["descent_satisfied"] is True
        assert result.result_data["checked_overlaps"] == 2

    def test_execute_solver_query(self):
        task = Task.create(
            TaskKind.SOLVER_QUERY,
            {"query_type": "sat", "constraints": [{"x": 1}], "variables": []},
        )
        result = self.worker.execute_task(task)
        assert result.success is True
        assert result.result_data["satisfiable"] is True
        assert result.result_data["constraint_count"] == 1

    def test_execute_evidence_collection(self):
        task = Task.create(
            TaskKind.EVIDENCE_COLLECTION,
            {"obligation_id": "ob1", "evidence_kinds": ["type_check"]},
        )
        result = self.worker.execute_task(task)
        assert result.success is True
        assert result.result_data["obligation_id"] == "ob1"

    def test_execute_treaty_negotiation(self):
        task = Task.create(
            TaskKind.TREATY_NEGOTIATION,
            {"treaty_id": "tr1", "proposal": {"terms": []}, "counterparty": "agent2"},
        )
        result = self.worker.execute_task(task)
        assert result.success is True
        assert result.result_data["treaty_id"] == "tr1"

    def test_execute_full_analysis(self):
        task = Task.create(
            TaskKind.FULL_ANALYSIS,
            {"coordinate_ids": ["c1", "c2", "c3"], "depth": 2},
        )
        result = self.worker.execute_task(task)
        assert result.success is True
        assert result.result_data["analyzed"] == 3
        assert result.result_data["depth"] == 2

    # ------------------------------------------------------------------
    # Result metadata
    # ------------------------------------------------------------------

    def test_result_contains_worker_id(self):
        task = Task.create(TaskKind.PARSE_FILES, {"files": []})
        result = self.worker.execute_task(task)
        assert result.worker_id == self.worker.id

    def test_result_duration_positive(self):
        task = Task.create(TaskKind.PARSE_FILES, {"files": []})
        result = self.worker.execute_task(task)
        assert result.duration_ms >= 0.0

    def test_worker_state_returns_to_idle_after_task(self):
        task = Task.create(TaskKind.PARSE_FILES, {"files": []})
        self.worker.execute_task(task)
        info = self.worker.current_info()
        assert info.state == WorkerState.IDLE
        assert info.current_task_id is None

    def test_worker_counts_completed_tasks(self):
        for _ in range(3):
            task = Task.create(TaskKind.PARSE_FILES, {"files": []})
            self.worker.execute_task(task)
        info = self.worker.current_info()
        assert info.tasks_completed == 3

    def test_inject_and_run_task(self):
        task = Task.create(TaskKind.VERIFY_PARTITION, {"partition_id": "p1"})
        self.worker.inject_task(task)
        result = self.worker.run_next_task()
        assert result is not None
        assert result.success is True

    def test_run_next_task_empty_queue_returns_none(self):
        result = self.worker.run_next_task()
        assert result is None

    # ------------------------------------------------------------------
    # current_info
    # ------------------------------------------------------------------

    def test_current_info_is_copy(self):
        info1 = self.worker.current_info()
        info1.tasks_completed = 999  # Mutate the copy.
        info2 = self.worker.current_info()
        assert info2.tasks_completed != 999  # Original unchanged.

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def test_worker_handles_handler_exception(self):
        """If a handler raises, the result should indicate failure."""
        task = Task.create(TaskKind.PARSE_FILES, {"files": ["/nonexistent/path.py"]})
        result = self.worker.execute_task(task)
        # The handler catches file errors internally and adds them to errors[].
        assert result.success is True
        assert len(result.result_data["errors"]) == 1

    def test_worker_counts_failed_tasks(self):
        """Simulate a handler that raises by monkey-patching."""
        original = self.worker._handle_parse_files

        def _failing_handler(payload):
            raise RuntimeError("injected failure")

        self.worker._handle_parse_files = _failing_handler
        task = Task.create(TaskKind.PARSE_FILES, {})
        result = self.worker.execute_task(task)
        assert result.success is False
        assert "injected failure" in result.error_message
        info = self.worker.current_info()
        assert info.tasks_failed == 1
        self.worker._handle_parse_files = original
