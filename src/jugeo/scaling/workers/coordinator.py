"""Coordinator process for JuGeo worker architecture.

The coordinator is the central orchestrator for distributed verification:

- Accepts worker registrations.
- Maintains a task queue and dispatches tasks to idle workers.
- Monitors worker health via heartbeats and reassigns tasks from dead workers.
- Broadcasts phase-change and budget-update signals.
- Tracks task results and exposes a synchronous ``wait_for_task`` API.

The coordinator does **not** perform any verification logic itself; it is
purely a scheduling and routing layer.

Architecture
------------
All network I/O runs in a single background thread (``_io_thread``).
A second background thread (``_monitor_thread``) periodically checks for
dead workers and timed-out tasks.  The public API is thread-safe: all
mutations go through ``_lock``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Dict, List, Optional

from jugeo.scaling.workers.models import (
    ClusterStatus,
    CoordinatorConfig,
    Message,
    MessageKind,
    Task,
    TaskResult,
    TaskState,
    WorkerInfo,
    WorkerState,
)
from jugeo.scaling.workers.message_protocol import MessageBus, MessageChannel

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _uid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

class Coordinator:
    """Central task coordinator for the JuGeo worker cluster.

    Parameters
    ----------
    config:
        :class:`~jugeo.scaling.workers.models.CoordinatorConfig` instance.

    Examples
    --------
    In-process usage (no actual sockets required):

    >>> from jugeo.scaling.workers.models import CoordinatorConfig, Task, TaskKind
    >>> from jugeo.scaling.workers.coordinator import Coordinator
    >>> cfg = CoordinatorConfig(max_workers=2)
    >>> coord = Coordinator(cfg)
    >>> coord.start()
    >>> task = Task.create(TaskKind.PARSE_FILES, {"files": []})
    >>> task_id = coord.submit_task(task)
    >>> coord.stop()
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: CoordinatorConfig) -> None:
        self.config = config
        self._id = "coordinator-" + _uid()

        # Worker registry: worker_id -> WorkerInfo
        self._workers: Dict[str, WorkerInfo] = {}
        # Worker channels: worker_id -> MessageChannel
        self._channels: Dict[str, MessageChannel] = {}

        # Task state
        self._tasks: Dict[str, Task] = {}
        # Retry counts: task_id -> retries_so_far
        self._retries: Dict[str, int] = {}
        # Results: task_id -> TaskResult
        self._results: Dict[str, TaskResult] = {}
        # Events for callers waiting on a specific task
        self._task_events: Dict[str, threading.Event] = {}

        # Counters
        self._completed_count = 0
        self._failed_count = 0

        # Partition tracking
        self._total_partitions = 0
        self._verified_partitions = 0

        # Synchronisation
        self._lock = threading.RLock()
        self._schedule_event = threading.Event()

        # Networking (optional — not used in in-process tests)
        self._bus: Optional[MessageBus] = None

        # Background threads
        self._running = False
        self._io_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the coordinator (background threads only; no socket binding)."""
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="coordinator-monitor",
        )
        self._monitor_thread.start()
        logger.info("Coordinator %s started", self._id)

    def start_with_network(self, address: str = "localhost", port: int = 9876) -> None:
        """Start the coordinator with a network socket for real multi-process use."""
        self.start()
        self._bus = MessageBus(address, port)
        self._bus.start_server()
        self._io_thread = threading.Thread(
            target=self._io_loop,
            daemon=True,
            name="coordinator-io",
        )
        self._io_thread.start()

    def stop(self) -> None:
        """Gracefully shut down the coordinator."""
        if not self._running:
            return
        self._running = False
        self._schedule_event.set()

        # Send shutdown to all connected workers.
        with self._lock:
            worker_ids = list(self._channels.keys())
        for worker_id in worker_ids:
            self._send_to_worker(
                worker_id,
                Message.create(
                    MessageKind.SHUTDOWN.value,
                    self._id,
                    worker_id,
                    {},
                ),
            )
        if self._bus is not None:
            self._bus.close()
        logger.info("Coordinator %s stopped", self._id)

    # ------------------------------------------------------------------
    # Worker management
    # ------------------------------------------------------------------

    def register_worker(
        self,
        worker_info: WorkerInfo,
        channel: Optional[MessageChannel] = None,
    ) -> None:
        """Register a worker with the coordinator.

        Parameters
        ----------
        worker_info:
            :class:`~jugeo.scaling.workers.models.WorkerInfo` snapshot.
        channel:
            Optional :class:`~jugeo.scaling.workers.message_protocol.MessageChannel`
            for socket-based deployments.
        """
        with self._lock:
            self._workers[worker_info.id] = worker_info
            if channel is not None:
                self._channels[worker_info.id] = channel
        logger.info("Registered worker %s (pid=%d)", worker_info.id, worker_info.pid)
        self._schedule_event.set()

    def unregister_worker(self, worker_id: str) -> None:
        """Remove a worker from the registry."""
        with self._lock:
            self._workers.pop(worker_id, None)
            ch = self._channels.pop(worker_id, None)
        if ch is not None:
            try:
                ch.close()
            except Exception:
                pass
        logger.info("Unregistered worker %s", worker_id)

    # ------------------------------------------------------------------
    # Task submission
    # ------------------------------------------------------------------

    def submit_task(self, task: Task) -> str:
        """Submit a single task and return its ID."""
        with self._lock:
            self._tasks[task.id] = task
            self._task_events[task.id] = threading.Event()
            self._retries[task.id] = 0
        self._schedule_event.set()
        logger.debug("Task %s submitted (kind=%s)", task.id, task.kind.value)
        return task.id

    def submit_batch(self, tasks: List[Task]) -> List[str]:
        """Submit multiple tasks atomically and return their IDs."""
        ids = []
        with self._lock:
            for task in tasks:
                self._tasks[task.id] = task
                self._task_events[task.id] = threading.Event()
                self._retries[task.id] = 0
                ids.append(task.id)
        self._schedule_event.set()
        logger.debug("Batch of %d tasks submitted", len(tasks))
        return ids

    def cancel_task(self, task_id: str) -> None:
        """Cancel a pending or running task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if task.state in (TaskState.PENDING, TaskState.ASSIGNED):
                task.state = TaskState.CANCELLED
                ev = self._task_events.get(task_id)
                if ev is not None:
                    ev.set()
            elif task.state == TaskState.RUNNING and task.assigned_worker:
                # Tell the worker to drop it.
                self._send_to_worker(
                    task.assigned_worker,
                    Message.create(
                        "cancel_task",
                        self._id,
                        task.assigned_worker,
                        {"task_id": task_id},
                    ),
                )

    # ------------------------------------------------------------------
    # Result retrieval
    # ------------------------------------------------------------------

    def get_task_result(self, task_id: str) -> Optional[TaskResult]:
        """Return the :class:`~jugeo.scaling.workers.models.TaskResult` if available."""
        with self._lock:
            return self._results.get(task_id)

    def wait_for_task(
        self,
        task_id: str,
        timeout: Optional[float] = None,
    ) -> Optional[TaskResult]:
        """Block until the task completes (or *timeout* expires).

        Returns ``None`` if the timeout expires before the task finishes.
        """
        ev = None
        with self._lock:
            ev = self._task_events.get(task_id)
            # If the task is already terminal, return immediately.
            task = self._tasks.get(task_id)
            if task is not None and task.is_terminal():
                return self._results.get(task_id)
        if ev is None:
            return None
        ev.wait(timeout=timeout)
        return self.get_task_result(task_id)

    def wait_for_all(
        self,
        task_ids: List[str],
        timeout: Optional[float] = None,
    ) -> List[Optional[TaskResult]]:
        """Block until all tasks complete (or *timeout* expires).

        Returns a list of results in the same order as *task_ids*.
        Individual entries are ``None`` if the task did not complete
        before the timeout.
        """
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        results = []
        for task_id in task_ids:
            remaining: Optional[float]
            if deadline is not None:
                remaining = max(0.0, deadline - time.monotonic())
            else:
                remaining = None
            results.append(self.wait_for_task(task_id, timeout=remaining))
        return results

    # ------------------------------------------------------------------
    # Partition assignment
    # ------------------------------------------------------------------

    def assign_partitions(
        self,
        partitions: list,
        worker_ids: List[str],
    ) -> Dict[str, str]:
        """Assign partitions to workers.

        Returns a mapping ``{partition_id: worker_id}``.
        """
        assignment: Dict[str, str] = {}
        if not worker_ids or not partitions:
            return assignment
        for i, partition in enumerate(partitions):
            worker_id = worker_ids[i % len(worker_ids)]
            assignment[partition.id] = worker_id
            with self._lock:
                worker = self._workers.get(worker_id)
                if worker is not None:
                    worker.partition_id = partition.id
        self._total_partitions = len(partitions)
        return assignment

    # ------------------------------------------------------------------
    # Scheduling (internal)
    # ------------------------------------------------------------------

    def _schedule_tasks(self) -> None:
        """Assign pending tasks to idle workers."""
        with self._lock:
            idle_workers = [
                w
                for w in self._workers.values()
                if w.state == WorkerState.IDLE
            ]
            # Collect completed task IDs for dependency resolution.
            completed_ids = {
                tid
                for tid, t in self._tasks.items()
                if t.state == TaskState.COMPLETED
            }
            # Sort pending tasks by priority (descending) then creation time.
            pending = sorted(
                [
                    t
                    for t in self._tasks.values()
                    if t.state == TaskState.PENDING
                    and t.is_ready(completed_ids)
                ],
                key=lambda t: (-t.priority, t.created_at),
            )
            for task in pending:
                if not idle_workers:
                    break
                worker = idle_workers.pop(0)
                task.state = TaskState.ASSIGNED
                task.assigned_worker = worker.id
                worker.state = WorkerState.BUSY
                worker.current_task_id = task.id
                logger.debug(
                    "Assigned task %s to worker %s", task.id, worker.id
                )
                self._send_to_worker_locked(
                    worker.id,
                    Message.create(
                        MessageKind.TASK_ASSIGN.value,
                        self._id,
                        worker.id,
                        {"task": task.to_dict()},
                    ),
                )

    def _schedule_loop(self) -> None:
        """Background thread: wait for schedule events and call _schedule_tasks."""
        while self._running:
            self._schedule_event.wait(timeout=1.0)
            self._schedule_event.clear()
            if not self._running:
                break
            self._schedule_tasks()

    # ------------------------------------------------------------------
    # Heartbeat / dead-worker detection
    # ------------------------------------------------------------------

    def _handle_heartbeat(self, worker_id: str, info: dict) -> None:
        """Process a heartbeat from *worker_id*."""
        with self._lock:
            worker = self._workers.get(worker_id)
            if worker is None:
                return
            worker.last_heartbeat = _now()
            worker.memory_mb = float(info.get("memory_mb", worker.memory_mb))
            worker.cpu_percent = float(info.get("cpu_percent", worker.cpu_percent))
            new_state_str = info.get("state")
            if new_state_str:
                try:
                    worker.state = WorkerState(new_state_str)
                except ValueError:
                    pass

    def _detect_dead_workers(self) -> List[str]:
        """Return IDs of workers that have missed two heartbeat intervals."""
        threshold = 2 * self.config.heartbeat_interval_s
        now = _now()
        dead = []
        with self._lock:
            for wid, w in self._workers.items():
                if w.state == WorkerState.STOPPED:
                    continue
                if now - w.last_heartbeat > threshold:
                    dead.append(wid)
        return dead

    def _reassign_tasks(self, dead_worker_id: str) -> None:
        """Reassign all tasks belonging to *dead_worker_id* back to pending."""
        with self._lock:
            for task in self._tasks.values():
                if task.assigned_worker != dead_worker_id:
                    continue
                if task.is_terminal():
                    continue
                retry_count = self._retries.get(task.id, 0)
                if retry_count >= self.config.max_retries:
                    logger.warning(
                        "Task %s exhausted retries; marking FAILED", task.id
                    )
                    task.state = TaskState.FAILED
                    task.error = f"Worker {dead_worker_id} died; retries exhausted"
                    task.assigned_worker = None
                    self._failed_count += 1
                    ev = self._task_events.get(task.id)
                    if ev is not None:
                        ev.set()
                else:
                    logger.info(
                        "Re-queuing task %s (retry %d)", task.id, retry_count + 1
                    )
                    self._retries[task.id] = retry_count + 1
                    task.state = TaskState.PENDING
                    task.assigned_worker = None
            # Mark the dead worker.
            worker = self._workers.get(dead_worker_id)
            if worker is not None:
                worker.state = WorkerState.FAILED
        self._schedule_event.set()

    # ------------------------------------------------------------------
    # Result handling
    # ------------------------------------------------------------------

    def _handle_task_result(self, result: TaskResult) -> None:
        """Store a completed task result and update worker state."""
        with self._lock:
            task = self._tasks.get(result.task_id)
            if task is None:
                logger.warning(
                    "Received result for unknown task %s", result.task_id
                )
                return
            now = _now()
            if result.success:
                task.state = TaskState.COMPLETED
                task.result = result.result_data
                task.completed_at = now
                self._completed_count += 1
            else:
                retry_count = self._retries.get(result.task_id, 0)
                if retry_count < self.config.max_retries:
                    self._retries[result.task_id] = retry_count + 1
                    task.state = TaskState.PENDING
                    task.assigned_worker = None
                    task.error = result.error_message
                    self._schedule_event.set()
                    return
                task.state = TaskState.FAILED
                task.error = result.error_message
                task.completed_at = now
                self._failed_count += 1

            # Clear worker busy status.
            if task.assigned_worker:
                worker = self._workers.get(task.assigned_worker)
                if worker is not None:
                    worker.state = WorkerState.IDLE
                    worker.current_task_id = None
                    if result.success:
                        worker.tasks_completed += 1
                    else:
                        worker.tasks_failed += 1
            task.assigned_worker = None

            self._results[result.task_id] = result
            ev = self._task_events.get(result.task_id)
            if ev is not None:
                ev.set()
        self._schedule_event.set()

    # ------------------------------------------------------------------
    # Cluster status
    # ------------------------------------------------------------------

    def cluster_status(self) -> ClusterStatus:
        """Return a point-in-time :class:`~jugeo.scaling.workers.models.ClusterStatus`."""
        with self._lock:
            workers = [
                WorkerInfo.from_dict(w.to_dict()) for w in self._workers.values()
            ]
            pending = sum(
                1 for t in self._tasks.values() if t.state == TaskState.PENDING
            )
            running = sum(
                1
                for t in self._tasks.values()
                if t.state in (TaskState.ASSIGNED, TaskState.RUNNING)
            )
            return ClusterStatus(
                coordinator_alive=self._running,
                workers=workers,
                pending_tasks=pending,
                running_tasks=running,
                completed_tasks=self._completed_count,
                failed_tasks=self._failed_count,
                total_partitions=self._total_partitions,
                verified_partitions=self._verified_partitions,
            )

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    def phase_change(self, new_phase: str) -> None:
        """Broadcast a phase-change message to all connected workers."""
        msg = Message.create(
            MessageKind.PHASE_CHANGE.value,
            self._id,
            "*",
            {"phase": new_phase},
        )
        self._broadcast(msg)

    def budget_update(self, budget: dict) -> None:
        """Broadcast an updated budget to all connected workers."""
        msg = Message.create(
            MessageKind.BUDGET_UPDATE.value,
            self._id,
            "*",
            {"budget": budget},
        )
        self._broadcast(msg)

    # ------------------------------------------------------------------
    # Internal networking
    # ------------------------------------------------------------------

    def _broadcast(self, message: Message) -> None:
        with self._lock:
            channels = list(self._channels.values())
        for ch in channels:
            try:
                ch.send(message)
            except OSError as exc:
                logger.warning("Broadcast failed: %s", exc)

    def _send_to_worker(self, worker_id: str, message: Message) -> None:
        with self._lock:
            self._send_to_worker_locked(worker_id, message)

    def _send_to_worker_locked(self, worker_id: str, message: Message) -> None:
        """Must be called with self._lock held."""
        ch = self._channels.get(worker_id)
        if ch is None:
            return
        try:
            ch.send(message)
        except OSError as exc:
            logger.warning("Send to worker %s failed: %s", worker_id, exc)

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _io_loop(self) -> None:
        """Accept new worker connections and read incoming messages."""
        if self._bus is None:
            return
        while self._running:
            ch = self._bus.accept(timeout=1.0)
            if ch is None:
                continue
            # Spawn a reader thread for this channel.
            t = threading.Thread(
                target=self._channel_reader,
                args=(ch,),
                daemon=True,
            )
            t.start()

    def _channel_reader(self, channel: MessageChannel) -> None:
        """Read messages from a single worker channel until it closes."""
        while self._running:
            msg = channel.receive(timeout=2.0)
            if msg is None:
                if channel.is_closed:
                    break
                continue
            self._dispatch_message(msg, channel)

    def _dispatch_message(self, msg: Message, channel: MessageChannel) -> None:
        """Route an incoming message to the appropriate handler."""
        kind = msg.kind
        if kind == MessageKind.REGISTER.value:
            info = WorkerInfo.from_dict(msg.payload["worker_info"])
            self.register_worker(info, channel)
        elif kind == MessageKind.HEARTBEAT.value:
            self._handle_heartbeat(msg.sender, msg.payload)
        elif kind == MessageKind.TASK_RESULT.value:
            result = TaskResult.from_dict(msg.payload["result"])
            self._handle_task_result(result)
        elif kind == MessageKind.UNREGISTER.value:
            self.unregister_worker(msg.sender)
        else:
            logger.debug("Unhandled message kind: %s", kind)

    def _monitor_loop(self) -> None:
        """Periodically check for dead workers and timed-out tasks."""
        while self._running:
            time.sleep(self.config.heartbeat_interval_s)
            if not self._running:
                break
            # Dead workers.
            for dead_id in self._detect_dead_workers():
                logger.warning("Worker %s appears dead; reassigning tasks", dead_id)
                self._reassign_tasks(dead_id)
                self.unregister_worker(dead_id)
            # Timed-out tasks.
            self._check_task_timeouts()
            # Schedule any newly-ready tasks.
            self._schedule_tasks()

    def _check_task_timeouts(self) -> None:
        """Mark running tasks that have exceeded their timeout."""
        now = _now()
        with self._lock:
            for task in self._tasks.values():
                if task.state not in (TaskState.ASSIGNED, TaskState.RUNNING):
                    continue
                if task.started_at is None:
                    # Use assigned time as approximation if start not recorded.
                    continue
                if now - task.started_at > task.timeout_s:
                    logger.warning("Task %s timed out", task.id)
                    task.state = TaskState.TIMEOUT
                    task.error = "Task exceeded timeout"
                    if task.assigned_worker:
                        worker = self._workers.get(task.assigned_worker)
                        if worker:
                            worker.state = WorkerState.IDLE
                            worker.current_task_id = None
                    task.assigned_worker = None
                    self._failed_count += 1
                    ev = self._task_events.get(task.id)
                    if ev is not None:
                        ev.set()
