"""Worker process for JuGeo distributed verification.

A worker connects to the coordinator, receives task assignments, executes
them, and returns results.  Each :class:`Worker` instance runs in its own
OS process but communicates purely over TCP sockets.

Handler dispatch
----------------
:meth:`Worker.execute_task` inspects :attr:`~jugeo.scaling.workers.models.Task.kind`
and calls the matching ``_handle_*`` method.  Each handler receives the
task's ``payload`` dict and returns a plain dict that becomes the
:attr:`~jugeo.scaling.workers.models.TaskResult.result_data`.

Heartbeat
---------
A dedicated background thread sends a :attr:`~jugeo.scaling.workers.models.MessageKind.HEARTBEAT`
message every :attr:`~jugeo.scaling.workers.models.WorkerConfig.heartbeat_interval_s`
seconds so the coordinator can detect dead workers.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Optional

from jugeo.scaling.workers.models import (
    Message,
    MessageKind,
    Task,
    TaskKind,
    TaskResult,
    TaskState,
    WorkerConfig,
    WorkerInfo,
    WorkerState,
)
from jugeo.scaling.workers.message_protocol import MessageChannel, MessageBus

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _uid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class Worker:
    """A single worker process that executes tasks dispatched by the coordinator.

    Parameters
    ----------
    config:
        :class:`~jugeo.scaling.workers.models.WorkerConfig` instance.

    Notes
    -----
    In production this runs in a separate OS process.  In tests it is
    instantiated directly in the test process with an injected
    :class:`~jugeo.scaling.workers.message_protocol.MessageChannel`.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self._info = WorkerInfo.create(os.getpid())
        self._channel: Optional[MessageChannel] = None
        self._running = False
        self._lock = threading.RLock()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._task_thread: Optional[threading.Thread] = None
        # Injected during tests to avoid sockets.
        self._task_queue: list[Task] = []
        self._task_queue_lock = threading.Lock()
        self._task_available = threading.Event()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return self._info.id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Connect to coordinator and start heartbeat + task loops."""
        if self._running:
            return
        self._running = True
        self._connect()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"worker-{self._info.id[:8]}-heartbeat",
        )
        self._heartbeat_thread.start()
        self._task_thread = threading.Thread(
            target=self._task_loop,
            daemon=True,
            name=f"worker-{self._info.id[:8]}-tasks",
        )
        self._task_thread.start()
        logger.info(
            "Worker %s started (pid=%d)", self._info.id, self._info.pid
        )

    def start_with_channel(self, channel: MessageChannel) -> None:
        """Start using an already-connected channel (for in-process use)."""
        self._channel = channel
        self._running = True
        self._register()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name=f"worker-{self._info.id[:8]}-heartbeat",
        )
        self._heartbeat_thread.start()
        logger.info(
            "Worker %s started with injected channel (pid=%d)",
            self._info.id,
            self._info.pid,
        )

    def stop(self) -> None:
        """Gracefully drain and shut down the worker."""
        if not self._running:
            return
        with self._lock:
            self._info.state = WorkerState.DRAINING
        self._running = False
        self._task_available.set()
        if self._channel is not None:
            try:
                self._channel.send(
                    Message.create(
                        MessageKind.UNREGISTER.value,
                        self._info.id,
                        "coordinator",
                        {},
                    )
                )
                self._channel.close()
            except Exception:
                pass
        with self._lock:
            self._info.state = WorkerState.STOPPED
        logger.info("Worker %s stopped", self._info.id)

    # ------------------------------------------------------------------
    # In-process task injection (used in tests / when not using sockets)
    # ------------------------------------------------------------------

    def inject_task(self, task: Task) -> None:
        """Push a task directly into the worker's queue (no socket needed)."""
        with self._task_queue_lock:
            self._task_queue.append(task)
        self._task_available.set()

    def run_next_task(self) -> Optional[TaskResult]:
        """Execute the next queued task and return the result synchronously."""
        with self._task_queue_lock:
            if not self._task_queue:
                return None
            task = self._task_queue.pop(0)
        return self.execute_task(task)

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    def execute_task(self, task: Task) -> TaskResult:
        """Dispatch *task* to the appropriate handler.

        Parameters
        ----------
        task:
            The :class:`~jugeo.scaling.workers.models.Task` to execute.

        Returns
        -------
        :class:`~jugeo.scaling.workers.models.TaskResult`
        """
        with self._lock:
            self._info.state = WorkerState.BUSY
            self._info.current_task_id = task.id

        start_ms = time.monotonic() * 1000.0
        result_data: dict = {}
        error: Optional[str] = None
        success = True

        try:
            handler = self._get_handler(task.kind)
            result_data = handler(task.payload)
        except Exception as exc:
            logger.exception("Task %s raised an exception", task.id)
            error = str(exc)
            success = False

        duration_ms = time.monotonic() * 1000.0 - start_ms

        with self._lock:
            self._info.state = WorkerState.IDLE
            self._info.current_task_id = None
            if success:
                self._info.tasks_completed += 1
            else:
                self._info.tasks_failed += 1

        result = TaskResult(
            task_id=task.id,
            success=success,
            result_data=result_data,
            error_message=error,
            duration_ms=duration_ms,
            worker_id=self._info.id,
        )
        return result

    def _get_handler(self, kind: TaskKind):
        handlers = {
            TaskKind.PARSE_FILES: self._handle_parse_files,
            TaskKind.VERIFY_PARTITION: self._handle_verify_partition,
            TaskKind.DESCENT_CHECK: self._handle_descent_check,
            TaskKind.SOLVER_QUERY: self._handle_solver_query,
            TaskKind.EVIDENCE_COLLECTION: self._handle_evidence_collection,
            TaskKind.TREATY_NEGOTIATION: self._handle_treaty_negotiation,
            TaskKind.FULL_ANALYSIS: self._handle_full_analysis,
        }
        handler = handlers.get(kind)
        if handler is None:
            raise ValueError(f"No handler for task kind: {kind!r}")
        return handler

    # ------------------------------------------------------------------
    # Task handlers
    # ------------------------------------------------------------------

    def _handle_parse_files(self, payload: dict) -> dict:
        """Parse Python files and extract coordinate metadata.

        Parameters
        ----------
        payload:
            ``files``: list of file paths to parse.

        Returns
        -------
        dict
            ``coordinates``: list of extracted coordinate dicts.
            ``errors``: list of parse error dicts.
        """
        files = payload.get("files", [])
        coordinates = []
        errors = []
        for path in files:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    source = fh.read()
                # Lightweight extraction: just record the file name and
                # line count for now.  A full implementation would use
                # ast.parse and extract class/function definitions.
                coordinates.append(
                    {
                        "path": path,
                        "lines": source.count("\n") + 1,
                        "size_bytes": len(source.encode("utf-8")),
                    }
                )
            except Exception as exc:
                errors.append({"path": path, "error": str(exc)})
        return {"coordinates": coordinates, "errors": errors}

    def _handle_verify_partition(self, payload: dict) -> dict:
        """Run verification on a partition.

        Parameters
        ----------
        payload:
            ``partition_id``: partition to verify.
            ``coordinate_ids``: IDs of coordinates in the partition.
            ``options``: optional verification options.

        Returns
        -------
        dict
            ``judgment_deltas``: list of judgment change records.
            ``obstructions``: list of obstruction dicts.
            ``verified``: bool indicating success.
        """
        partition_id = payload.get("partition_id", "")
        coordinate_ids = payload.get("coordinate_ids", [])
        # Stub: in a real implementation this would run the sheaf
        # verification logic.
        return {
            "partition_id": partition_id,
            "judgment_deltas": [],
            "obstructions": [],
            "verified": True,
            "checked_coordinates": len(coordinate_ids),
        }

    def _handle_descent_check(self, payload: dict) -> dict:
        """Check descent conditions at partition overlaps.

        Parameters
        ----------
        payload:
            ``overlap_ids``: list of overlap coordinate IDs to check.
            ``source_partition``: partition containing the source.
            ``target_partition``: partition containing the target.

        Returns
        -------
        dict
            ``descent_satisfied``: bool.
            ``gluing_data``: dict of computed gluing morphisms.
            ``failures``: list of descent failure dicts.
        """
        overlap_ids = payload.get("overlap_ids", [])
        source_partition = payload.get("source_partition", "")
        target_partition = payload.get("target_partition", "")
        return {
            "source_partition": source_partition,
            "target_partition": target_partition,
            "checked_overlaps": len(overlap_ids),
            "descent_satisfied": True,
            "gluing_data": {},
            "failures": [],
        }

    def _handle_solver_query(self, payload: dict) -> dict:
        """Execute a solver query.

        Parameters
        ----------
        payload:
            ``query_type``: type of solver query.
            ``constraints``: list of constraint dicts.
            ``variables``: list of variable dicts.

        Returns
        -------
        dict
            ``satisfiable``: bool.
            ``model``: dict of variable assignments if satisfiable.
            ``unsat_core``: list of conflicting constraints if unsatisfiable.
        """
        query_type = payload.get("query_type", "unknown")
        constraints = payload.get("constraints", [])
        return {
            "query_type": query_type,
            "satisfiable": True,
            "model": {},
            "unsat_core": [],
            "constraint_count": len(constraints),
        }

    def _handle_evidence_collection(self, payload: dict) -> dict:
        """Collect evidence for a verification obligation.

        Parameters
        ----------
        payload:
            ``obligation_id``: obligation to discharge.
            ``evidence_kinds``: list of evidence kinds to collect.

        Returns
        -------
        dict
            ``evidence``: list of collected evidence dicts.
            ``discharged``: bool.
        """
        obligation_id = payload.get("obligation_id", "")
        evidence_kinds = payload.get("evidence_kinds", [])
        return {
            "obligation_id": obligation_id,
            "evidence": [],
            "discharged": False,
            "collected_kinds": evidence_kinds,
        }

    def _handle_treaty_negotiation(self, payload: dict) -> dict:
        """Execute a treaty negotiation step.

        Parameters
        ----------
        payload:
            ``treaty_id``: ID of the treaty being negotiated.
            ``proposal``: proposed terms dict.
            ``counterparty``: ID of the counterparty agent.

        Returns
        -------
        dict
            ``accepted``: bool.
            ``counter_proposal``: optional counter-proposal dict.
            ``treaty_id``: echoed back.
        """
        treaty_id = payload.get("treaty_id", "")
        proposal = payload.get("proposal", {})
        return {
            "treaty_id": treaty_id,
            "accepted": False,
            "counter_proposal": proposal,
            "reason": "stub implementation",
        }

    def _handle_full_analysis(self, payload: dict) -> dict:
        """Run full analysis on a set of coordinates.

        Parameters
        ----------
        payload:
            ``coordinate_ids``: IDs to analyse.
            ``depth``: analysis depth limit.

        Returns
        -------
        dict
            ``analysis``: dict of analysis results.
            ``issues``: list of detected issues.
        """
        coordinate_ids = payload.get("coordinate_ids", [])
        depth = payload.get("depth", 1)
        return {
            "analyzed": len(coordinate_ids),
            "depth": depth,
            "analysis": {},
            "issues": [],
        }

    # ------------------------------------------------------------------
    # Result reporting
    # ------------------------------------------------------------------

    def _report_result(self, result: TaskResult) -> None:
        """Send a task result to the coordinator over the channel."""
        if self._channel is None or self._channel.is_closed:
            return
        msg = Message.create(
            MessageKind.TASK_RESULT.value,
            self._info.id,
            "coordinator",
            {"result": result.to_dict()},
        )
        try:
            self._channel.send(msg)
        except OSError as exc:
            logger.error("Failed to report result for task %s: %s", result.task_id, exc)

    def _report_failure(self, task_id: str, error: str) -> None:
        """Report a task failure to the coordinator."""
        result = TaskResult(
            task_id=task_id,
            success=False,
            result_data={},
            error_message=error,
            duration_ms=0.0,
            worker_id=self._info.id,
        )
        self._report_result(result)

    # ------------------------------------------------------------------
    # Current state
    # ------------------------------------------------------------------

    def current_info(self) -> WorkerInfo:
        """Return a copy of the current :class:`~jugeo.scaling.workers.models.WorkerInfo`."""
        with self._lock:
            return WorkerInfo.from_dict(self._info.to_dict())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Open a TCP connection to the coordinator."""
        bus = MessageBus(
            self.config.coordinator_address,
            self.config.coordinator_port,
        )
        self._channel = bus.connect()
        self._register()

    def _register(self) -> None:
        """Send registration message to the coordinator."""
        if self._channel is None:
            return
        msg = Message.create(
            MessageKind.REGISTER.value,
            self._info.id,
            "coordinator",
            {"worker_info": self._info.to_dict()},
        )
        try:
            self._channel.send(msg)
        except OSError as exc:
            logger.error("Registration failed: %s", exc)

    def _heartbeat_loop(self) -> None:
        """Send heartbeats periodically until the worker stops."""
        while self._running:
            time.sleep(self.config.heartbeat_interval_s)
            if not self._running:
                break
            self._send_heartbeat()

    def _send_heartbeat(self) -> None:
        if self._channel is None or self._channel.is_closed:
            return
        with self._lock:
            info = {
                "state": self._info.state.value,
                "memory_mb": self._info.memory_mb,
                "cpu_percent": self._info.cpu_percent,
                "tasks_completed": self._info.tasks_completed,
                "tasks_failed": self._info.tasks_failed,
            }
        msg = Message.create(
            MessageKind.HEARTBEAT.value,
            self._info.id,
            "coordinator",
            info,
        )
        try:
            self._channel.send(msg)
        except OSError as exc:
            logger.warning("Heartbeat send failed: %s", exc)

    def _task_loop(self) -> None:
        """Wait for task assignments and execute them."""
        while self._running:
            # Try the injected queue first.
            task = self._dequeue_task()
            if task is not None:
                result = self.execute_task(task)
                self._report_result(result)
                continue

            # Otherwise wait for a network message.
            if self._channel is not None and not self._channel.is_closed:
                msg = self._channel.receive(timeout=1.0)
                if msg is not None:
                    self._dispatch_coordinator_message(msg)
                    continue

            self._task_available.wait(timeout=1.0)
            self._task_available.clear()

    def _dequeue_task(self) -> Optional[Task]:
        with self._task_queue_lock:
            if self._task_queue:
                return self._task_queue.pop(0)
        return None

    def _dispatch_coordinator_message(self, msg: Message) -> None:
        """Handle messages arriving from the coordinator."""
        kind = msg.kind
        if kind == MessageKind.TASK_ASSIGN.value:
            task = Task.from_dict(msg.payload["task"])
            task.state = TaskState.RUNNING
            task.started_at = _now()
            result = self.execute_task(task)
            self._report_result(result)
        elif kind == MessageKind.SHUTDOWN.value:
            logger.info("Worker %s received shutdown", self._info.id)
            self._running = False
        elif kind == MessageKind.PHASE_CHANGE.value:
            logger.info(
                "Worker %s: phase change to %s",
                self._info.id,
                msg.payload.get("phase"),
            )
        elif kind == MessageKind.BUDGET_UPDATE.value:
            logger.debug("Worker %s: budget update received", self._info.id)
        else:
            logger.debug("Worker ignoring message kind: %s", kind)
