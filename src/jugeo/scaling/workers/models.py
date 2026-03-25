"""Data models for JuGeo worker architecture.

Covers worker lifecycle, task lifecycle, inter-process messages,
partition definitions, and cluster configuration.  All models are plain
:func:`~dataclasses.dataclass` objects with ``to_dict`` / ``from_dict``
round-trips so they can be serialised to JSON and sent over sockets.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
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
# Enumerations
# ---------------------------------------------------------------------------

class WorkerState(str, Enum):
    """Lifecycle states for a worker process."""

    IDLE = "idle"
    BUSY = "busy"
    DRAINING = "draining"
    STOPPED = "stopped"
    FAILED = "failed"


class TaskState(str, Enum):
    """Lifecycle states for a task."""

    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class TaskKind(str, Enum):
    """Kinds of work that can be dispatched to a worker."""

    PARSE_FILES = "parse_files"
    VERIFY_PARTITION = "verify_partition"
    DESCENT_CHECK = "descent_check"
    SOLVER_QUERY = "solver_query"
    EVIDENCE_COLLECTION = "evidence_collection"
    TREATY_NEGOTIATION = "treaty_negotiation"
    FULL_ANALYSIS = "full_analysis"


# ---------------------------------------------------------------------------
# WorkerInfo
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class WorkerInfo:
    """Runtime snapshot of a single worker process.

    Parameters
    ----------
    id:
        Unique worker identifier (hex UUID).
    pid:
        OS process ID.
    state:
        Current :class:`WorkerState`.
    partition_id:
        ID of the partition this worker is currently responsible for,
        or ``None`` if unassigned.
    current_task_id:
        ID of the task currently executing, or ``None``.
    tasks_completed:
        Cumulative completed-task count since start.
    tasks_failed:
        Cumulative failed-task count since start.
    started_at:
        Unix timestamp when the worker started.
    last_heartbeat:
        Unix timestamp of the most recent heartbeat.
    memory_mb:
        Resident set size in megabytes.
    cpu_percent:
        CPU utilisation percentage at last measurement.
    """

    id: str
    pid: int
    state: WorkerState
    partition_id: Optional[str] = None
    current_task_id: Optional[str] = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    started_at: float = field(default_factory=_now)
    last_heartbeat: float = field(default_factory=_now)
    memory_mb: float = 0.0
    cpu_percent: float = 0.0

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, pid: int) -> WorkerInfo:
        """Create a fresh IDLE worker record."""
        now = _now()
        return cls(
            id=_uid(),
            pid=pid,
            state=WorkerState.IDLE,
            started_at=now,
            last_heartbeat=now,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pid": self.pid,
            "state": self.state.value,
            "partition_id": self.partition_id,
            "current_task_id": self.current_task_id,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "started_at": self.started_at,
            "last_heartbeat": self.last_heartbeat,
            "memory_mb": self.memory_mb,
            "cpu_percent": self.cpu_percent,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorkerInfo:
        return cls(
            id=d["id"],
            pid=d["pid"],
            state=WorkerState(d.get("state", WorkerState.IDLE.value)),
            partition_id=d.get("partition_id"),
            current_task_id=d.get("current_task_id"),
            tasks_completed=int(d.get("tasks_completed", 0)),
            tasks_failed=int(d.get("tasks_failed", 0)),
            started_at=float(d.get("started_at", 0.0)),
            last_heartbeat=float(d.get("last_heartbeat", 0.0)),
            memory_mb=float(d.get("memory_mb", 0.0)),
            cpu_percent=float(d.get("cpu_percent", 0.0)),
        )


# ---------------------------------------------------------------------------
# PartitionDef
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PartitionDef:
    """Definition of a verification partition.

    Parameters
    ----------
    id:
        Unique partition identifier.
    coordinate_ids:
        IDs of the coordinates assigned to this partition.
    morphism_ids:
        IDs of the morphisms assigned to this partition.
    estimated_cost:
        Heuristic cost score used for load balancing.
    level:
        Optional :class:`~jugeo.sheaf_types.SiteLevel` name (e.g.
        ``"function"``, ``"module"``).
    package:
        Optional top-level package name for package-scoped partitions.
    """

    id: str
    coordinate_ids: list[str]
    morphism_ids: list[str]
    estimated_cost: float = 1.0
    level: Optional[str] = None
    package: Optional[str] = None

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        coordinate_ids: list[str],
        morphism_ids: list[str],
        estimated_cost: float = 1.0,
        level: Optional[str] = None,
        package: Optional[str] = None,
    ) -> PartitionDef:
        return cls(
            id=_uid(),
            coordinate_ids=list(coordinate_ids),
            morphism_ids=list(morphism_ids),
            estimated_cost=estimated_cost,
            level=level,
            package=package,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "coordinate_ids": list(self.coordinate_ids),
            "morphism_ids": list(self.morphism_ids),
            "estimated_cost": self.estimated_cost,
            "level": self.level,
            "package": self.package,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PartitionDef:
        return cls(
            id=d["id"],
            coordinate_ids=list(d.get("coordinate_ids", [])),
            morphism_ids=list(d.get("morphism_ids", [])),
            estimated_cost=float(d.get("estimated_cost", 1.0)),
            level=d.get("level"),
            package=d.get("package"),
        )


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Task:
    """A unit of work dispatched through the coordinator.

    Parameters
    ----------
    id:
        Unique task identifier.
    kind:
        :class:`TaskKind` describing the type of work.
    partition_id:
        Partition this task operates on, if any.
    payload:
        Arbitrary key-value data consumed by the worker handler.
    priority:
        Scheduling priority; higher values are dispatched first.
    timeout_s:
        Maximum execution time before the task is marked
        :attr:`~TaskState.TIMEOUT`.
    depends_on:
        List of task IDs that must complete before this task runs.
    state:
        Current :class:`TaskState`.
    assigned_worker:
        ID of the worker this task is assigned to, or ``None``.
    result:
        Result payload once the task is :attr:`~TaskState.COMPLETED`.
    error:
        Error message if the task :attr:`~TaskState.FAILED`.
    created_at:
        Unix timestamp of creation.
    started_at:
        Unix timestamp when execution began.
    completed_at:
        Unix timestamp when execution finished.
    """

    id: str
    kind: TaskKind
    partition_id: Optional[str] = None
    payload: dict = field(default_factory=dict)
    priority: float = 1.0
    timeout_s: float = 300.0
    depends_on: list[str] = field(default_factory=list)
    state: TaskState = TaskState.PENDING
    assigned_worker: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=_now)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        kind: TaskKind,
        payload: dict,
        partition_id: Optional[str] = None,
        priority: float = 1.0,
        timeout_s: float = 300.0,
        depends_on: Optional[list[str]] = None,
    ) -> Task:
        return cls(
            id=_uid(),
            kind=kind,
            partition_id=partition_id,
            payload=dict(payload),
            priority=priority,
            timeout_s=timeout_s,
            depends_on=list(depends_on or []),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_terminal(self) -> bool:
        """Return True if the task is in a terminal state."""
        return self.state in (
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.TIMEOUT,
        )

    def is_ready(self, completed_ids: set[str]) -> bool:
        """Return True if all dependencies have completed."""
        return all(dep in completed_ids for dep in self.depends_on)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "partition_id": self.partition_id,
            "payload": self.payload,
            "priority": self.priority,
            "timeout_s": self.timeout_s,
            "depends_on": list(self.depends_on),
            "state": self.state.value,
            "assigned_worker": self.assigned_worker,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        return cls(
            id=d["id"],
            kind=TaskKind(d["kind"]),
            partition_id=d.get("partition_id"),
            payload=dict(d.get("payload", {})),
            priority=float(d.get("priority", 1.0)),
            timeout_s=float(d.get("timeout_s", 300.0)),
            depends_on=list(d.get("depends_on", [])),
            state=TaskState(d.get("state", TaskState.PENDING.value)),
            assigned_worker=d.get("assigned_worker"),
            result=d.get("result"),
            error=d.get("error"),
            created_at=float(d.get("created_at", 0.0)),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
        )


# ---------------------------------------------------------------------------
# TaskResult
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TaskResult:
    """Result of a completed (or failed) task.

    Parameters
    ----------
    task_id:
        ID of the originating :class:`Task`.
    success:
        ``True`` if the task completed without error.
    result_data:
        Structured result (judgment deltas, evidence, obstructions, …).
    error_message:
        Human-readable error if ``success`` is ``False``.
    duration_ms:
        Wall-clock execution time in milliseconds.
    worker_id:
        ID of the worker that executed the task.
    """

    task_id: str
    success: bool
    result_data: dict
    error_message: Optional[str] = None
    duration_ms: float = 0.0
    worker_id: str = ""

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "result_data": self.result_data,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
            "worker_id": self.worker_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TaskResult:
        return cls(
            task_id=d["task_id"],
            success=bool(d.get("success", False)),
            result_data=dict(d.get("result_data", {})),
            error_message=d.get("error_message"),
            duration_ms=float(d.get("duration_ms", 0.0)),
            worker_id=d.get("worker_id", ""),
        )


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class MessageKind(str, Enum):
    """Kinds of messages exchanged between coordinator and workers."""

    TASK_ASSIGN = "task_assign"
    TASK_RESULT = "task_result"
    HEARTBEAT = "heartbeat"
    PHASE_CHANGE = "phase_change"
    BUDGET_UPDATE = "budget_update"
    TREATY_PROPOSAL = "treaty_proposal"
    SHUTDOWN = "shutdown"
    REGISTER = "register"
    UNREGISTER = "unregister"
    ACK = "ack"


@dataclass(slots=True)
class Message:
    """An inter-process message exchanged over a :class:`MessageChannel`.

    Parameters
    ----------
    id:
        Unique message identifier.
    kind:
        :class:`MessageKind` (or any string for extensibility).
    sender:
        ID of the sending entity.
    receiver:
        ID of the receiving entity (``"*"`` for broadcast).
    payload:
        Arbitrary key-value data.
    timestamp:
        Unix timestamp of message creation.
    """

    id: str
    kind: str
    sender: str
    receiver: str
    payload: dict
    timestamp: float = field(default_factory=_now)

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        kind: str,
        sender: str,
        receiver: str,
        payload: Optional[dict] = None,
    ) -> Message:
        return cls(
            id=_uid(),
            kind=kind,
            sender=sender,
            receiver=receiver,
            payload=dict(payload or {}),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "sender": self.sender,
            "receiver": self.receiver,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        return cls(
            id=d["id"],
            kind=d["kind"],
            sender=d["sender"],
            receiver=d["receiver"],
            payload=dict(d.get("payload", {})),
            timestamp=float(d.get("timestamp", 0.0)),
        )


# ---------------------------------------------------------------------------
# CoordinatorConfig
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CoordinatorConfig:
    """Configuration for the :class:`~jugeo.scaling.workers.coordinator.Coordinator`.

    Parameters
    ----------
    max_workers:
        Maximum number of concurrent worker processes.
    heartbeat_interval_s:
        Expected heartbeat interval; workers missing two intervals are
        considered dead.
    task_timeout_s:
        Default task execution timeout.
    max_retries:
        Number of times a failed task is retried before marking it
        permanently failed.
    partition_strategy:
        Partition strategy passed to :class:`~jugeo.scaling.workers.partition_manager.PartitionManager`.
        One of ``"scc"``, ``"level"``, ``"balanced"``.
    balance_factor:
        Load-balance threshold; workers are considered imbalanced when
        the cost ratio between the busiest and quietest worker exceeds
        this value.
    """

    max_workers: int = 4
    heartbeat_interval_s: float = 5.0
    task_timeout_s: float = 300.0
    max_retries: int = 2
    partition_strategy: str = "scc"
    balance_factor: float = 0.8

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "max_workers": self.max_workers,
            "heartbeat_interval_s": self.heartbeat_interval_s,
            "task_timeout_s": self.task_timeout_s,
            "max_retries": self.max_retries,
            "partition_strategy": self.partition_strategy,
            "balance_factor": self.balance_factor,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CoordinatorConfig:
        return cls(
            max_workers=int(d.get("max_workers", 4)),
            heartbeat_interval_s=float(d.get("heartbeat_interval_s", 5.0)),
            task_timeout_s=float(d.get("task_timeout_s", 300.0)),
            max_retries=int(d.get("max_retries", 2)),
            partition_strategy=d.get("partition_strategy", "scc"),
            balance_factor=float(d.get("balance_factor", 0.8)),
        )


# ---------------------------------------------------------------------------
# WorkerConfig
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class WorkerConfig:
    """Configuration for a :class:`~jugeo.scaling.workers.worker.Worker` process.

    Parameters
    ----------
    coordinator_address:
        Hostname or IP of the coordinator.
    coordinator_port:
        TCP port the coordinator listens on.
    max_memory_mb:
        RSS limit in megabytes before the worker starts draining.
    heartbeat_interval_s:
        How often the worker sends heartbeats to the coordinator.
    """

    coordinator_address: str = "localhost"
    coordinator_port: int = 9876
    max_memory_mb: int = 4096
    heartbeat_interval_s: float = 5.0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "coordinator_address": self.coordinator_address,
            "coordinator_port": self.coordinator_port,
            "max_memory_mb": self.max_memory_mb,
            "heartbeat_interval_s": self.heartbeat_interval_s,
        }

    @classmethod
    def from_dict(cls, d: dict) -> WorkerConfig:
        return cls(
            coordinator_address=d.get("coordinator_address", "localhost"),
            coordinator_port=int(d.get("coordinator_port", 9876)),
            max_memory_mb=int(d.get("max_memory_mb", 4096)),
            heartbeat_interval_s=float(d.get("heartbeat_interval_s", 5.0)),
        )


# ---------------------------------------------------------------------------
# ClusterStatus
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ClusterStatus:
    """Point-in-time snapshot of the entire cluster.

    Parameters
    ----------
    coordinator_alive:
        Whether the coordinator process is responsive.
    workers:
        List of :class:`WorkerInfo` snapshots.
    pending_tasks:
        Number of tasks waiting to be dispatched.
    running_tasks:
        Number of tasks currently executing.
    completed_tasks:
        Cumulative completed-task count.
    failed_tasks:
        Cumulative failed-task count.
    total_partitions:
        Total number of partitions defined.
    verified_partitions:
        Number of partitions for which all tasks have completed
        successfully.
    """

    coordinator_alive: bool
    workers: list[WorkerInfo]
    pending_tasks: int
    running_tasks: int
    completed_tasks: int
    failed_tasks: int
    total_partitions: int
    verified_partitions: int

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def idle_workers(self) -> int:
        return sum(1 for w in self.workers if w.state == WorkerState.IDLE)

    @property
    def busy_workers(self) -> int:
        return sum(1 for w in self.workers if w.state == WorkerState.BUSY)

    @property
    def total_workers(self) -> int:
        return len(self.workers)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "coordinator_alive": self.coordinator_alive,
            "workers": [w.to_dict() for w in self.workers],
            "pending_tasks": self.pending_tasks,
            "running_tasks": self.running_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "total_partitions": self.total_partitions,
            "verified_partitions": self.verified_partitions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ClusterStatus:
        return cls(
            coordinator_alive=bool(d.get("coordinator_alive", False)),
            workers=[WorkerInfo.from_dict(w) for w in d.get("workers", [])],
            pending_tasks=int(d.get("pending_tasks", 0)),
            running_tasks=int(d.get("running_tasks", 0)),
            completed_tasks=int(d.get("completed_tasks", 0)),
            failed_tasks=int(d.get("failed_tasks", 0)),
            total_partitions=int(d.get("total_partitions", 0)),
            verified_partitions=int(d.get("verified_partitions", 0)),
        )
