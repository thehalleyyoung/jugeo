from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .models import (
    EventKind,
    ServerConfig,
    TaskStatus,
    VerificationProgress,
    VerificationRequest,
    VerificationResult,
)

logger = logging.getLogger(__name__)

VERIFICATION_PHASES = [
    "analyzing",
    "resolving_dependencies",
    "verifying_coordinates",
    "checking_obstructions",
    "finalizing",
]


class TaskQueue:
    """Manages submission, execution and tracking of verification tasks."""

    def __init__(self, max_concurrent: int = 4) -> None:
        self._max_concurrent = max_concurrent
        self._pending: asyncio.Queue[VerificationRequest] = asyncio.Queue()
        self._progress: Dict[str, VerificationProgress] = {}
        self._results: Dict[str, VerificationResult] = {}
        self._cancelled: set[str] = set()
        self._active: set[str] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._started = False

    async def start_workers(self) -> None:
        if self._started:
            return
        self._started = True
        for _ in range(self._max_concurrent):
            self._workers.append(asyncio.ensure_future(self._worker_loop()))

    async def stop_workers(self) -> None:
        self._started = False
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    async def submit(self, request: VerificationRequest) -> str:
        task_id = request.id or str(uuid.uuid4())
        request = VerificationRequest(
            id=task_id,
            target_path=request.target_path,
            scope=request.scope,
            trust_target=request.trust_target,
            incremental=request.incremental,
            callback_url=request.callback_url,
        )
        self._progress[task_id] = VerificationProgress(
            task_id=task_id,
            status=TaskStatus.PENDING,
            progress_pct=0.0,
            coordinates_verified=0,
            total_coordinates=0,
            obstructions_found=0,
            current_phase="queued",
            started_at=time.time(),
            estimated_remaining_s=0.0,
        )
        await self._pending.put(request)
        if not self._started:
            await self.start_workers()
        return task_id

    async def cancel(self, task_id: str) -> bool:
        if task_id in self._results:
            return False
        self._cancelled.add(task_id)
        if task_id in self._progress:
            self._progress[task_id].status = TaskStatus.CANCELLED
        return True

    async def get_progress(self, task_id: str) -> Optional[VerificationProgress]:
        return self._progress.get(task_id)

    async def get_result(self, task_id: str) -> Optional[VerificationResult]:
        return self._results.get(task_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _worker_loop(self) -> None:
        while self._started:
            try:
                request = await asyncio.wait_for(self._pending.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if request.id in self._cancelled:
                continue

            self._active.add(request.id)
            try:
                result = await self._execute_task(request)
                self._results[request.id] = result
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Task %s failed: %s", request.id, exc)
                self._results[request.id] = VerificationResult(
                    task_id=request.id,
                    success=False,
                    trust_achieved="NONE",
                    coverage=0.0,
                    obstructions=[str(exc)],
                    duration_s=0.0,
                )
                if request.id in self._progress:
                    self._progress[request.id].status = TaskStatus.FAILED
            finally:
                self._active.discard(request.id)

    async def _execute_task(self, request: VerificationRequest) -> VerificationResult:
        task_id = request.id
        start_time = time.time()
        total_coordinates = 50  # simulated

        progress = self._progress.get(task_id)
        if progress is None:
            progress = VerificationProgress(
                task_id=task_id,
                status=TaskStatus.RUNNING,
                progress_pct=0.0,
                coordinates_verified=0,
                total_coordinates=total_coordinates,
                obstructions_found=0,
                current_phase="starting",
                started_at=start_time,
                estimated_remaining_s=0.0,
            )
            self._progress[task_id] = progress

        progress.status = TaskStatus.RUNNING
        progress.total_coordinates = total_coordinates

        coords_per_phase = total_coordinates // len(VERIFICATION_PHASES)
        verified = 0

        for phase_idx, phase in enumerate(VERIFICATION_PHASES):
            if task_id in self._cancelled:
                progress.status = TaskStatus.CANCELLED
                return VerificationResult(
                    task_id=task_id,
                    success=False,
                    trust_achieved="NONE",
                    coverage=0.0,
                    obstructions=[],
                    duration_s=time.time() - start_time,
                )

            progress.current_phase = phase
            chunk = coords_per_phase if phase_idx < len(VERIFICATION_PHASES) - 1 else (total_coordinates - verified)

            for _ in range(chunk):
                await asyncio.sleep(0.001)
                verified += 1
                progress.coordinates_verified = verified
                progress.progress_pct = (verified / total_coordinates) * 100.0
                elapsed = time.time() - start_time
                if verified > 0:
                    progress.estimated_remaining_s = max(
                        0.0,
                        (elapsed / verified) * (total_coordinates - verified),
                    )

        duration = time.time() - start_time
        progress.status = TaskStatus.COMPLETED
        progress.progress_pct = 100.0
        progress.coordinates_verified = total_coordinates
        progress.current_phase = "completed"
        progress.estimated_remaining_s = 0.0

        result = VerificationResult(
            task_id=task_id,
            success=True,
            trust_achieved=request.trust_target,
            coverage=100.0,
            obstructions=[],
            certificate_id=str(uuid.uuid4()),
            duration_s=duration,
        )
        return result

    def active_tasks(self) -> list[str]:
        return list(self._active)

    def pending_count(self) -> int:
        return self._pending.qsize()


class EventBus:
    """Simple publish/subscribe event bus."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[..., Any]] = []
        self._history: list[Dict[str, Any]] = []

    def subscribe(self, callback: Callable[..., Any]) -> None:
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[..., Any]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def publish(self, event_kind: EventKind, data: dict) -> None:
        event = {
            "event_kind": event_kind.value if isinstance(event_kind, EventKind) else event_kind,
            "data": data,
            "timestamp": time.time(),
            "event_id": str(uuid.uuid4()),
        }
        self._history.append(event)
        for cb in list(self._subscribers):
            try:
                result = cb(event_kind, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception("EventBus subscriber raised")

    def recent_events(self, count: int = 50) -> list[Dict[str, Any]]:
        return list(self._history[-count:])


class AsyncVerificationServer:
    """Minimal async HTTP/JSON server over TCP using asyncio."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.task_queue = TaskQueue(max_concurrent=config.max_concurrent_tasks)
        self.event_bus = EventBus()
        self._server: Optional[asyncio.AbstractServer] = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self.task_queue.start_workers()
        self._server = await asyncio.start_server(
            self._handle_client,
            self.config.host,
            self.config.port,
        )
        logger.info("Server listening on %s:%s", self.config.host, self.config.port)

    async def stop(self) -> None:
        self._running = False
        await self.task_queue.stop_workers()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------------
    # HTTP handling
    # ------------------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.read(65536), timeout=30.0)
            if not raw:
                writer.close()
                return

            text = raw.decode("utf-8", errors="replace")
            method, path, body = self._parse_http(text)
            status_code, response_body = await self._handle_request(method, path, body)

            response_json = json.dumps(response_body)
            http_response = (
                f"HTTP/1.1 {status_code} OK\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(response_json)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
                f"{response_json}"
            )
            writer.write(http_response.encode("utf-8"))
            await writer.drain()
        except Exception:  # noqa: BLE001
            logger.exception("Error handling client")
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _parse_http(text: str) -> tuple[str, str, dict]:
        parts = text.split("\r\n\r\n", 1)
        header_section = parts[0]
        body_text = parts[1] if len(parts) > 1 else ""

        first_line = header_section.split("\r\n")[0]
        tokens = first_line.split(" ")
        method = tokens[0] if tokens else "GET"
        path = tokens[1] if len(tokens) > 1 else "/"

        body: dict = {}
        if body_text.strip():
            try:
                body = json.loads(body_text.strip())
            except json.JSONDecodeError:
                body = {}
        return method, path, body

    async def _handle_request(self, method: str, path: str, body: dict) -> tuple[int, dict]:
        path = path.rstrip("/")
        if path == "/health" or path == "":
            return await self._route_health()
        if path == "/stats":
            return await self._route_stats()
        if path == "/events":
            return await self._route_events()
        if path == "/submit" and method == "POST":
            return await self._route_submit(body)
        if path.startswith("/status/"):
            task_id = path.split("/status/", 1)[1]
            return await self._route_status(task_id)
        if path.startswith("/cancel/"):
            task_id = path.split("/cancel/", 1)[1]
            return await self._route_cancel(task_id)
        return 404, {"error": "not found", "path": path}

    async def _route_submit(self, body: dict) -> tuple[int, dict]:
        try:
            req = VerificationRequest.from_dict(body)
        except (KeyError, TypeError) as exc:
            return 400, {"error": f"invalid request: {exc}"}

        task_id = await self.task_queue.submit(req)
        await self.event_bus.publish(EventKind.JUDGMENT_ADDED, {"task_id": task_id})
        return 200, {"task_id": task_id, "status": "submitted"}

    async def _route_status(self, task_id: str) -> tuple[int, dict]:
        result = await self.task_queue.get_result(task_id)
        if result is not None:
            return 200, result.to_dict()

        progress = await self.task_queue.get_progress(task_id)
        if progress is not None:
            return 200, progress.to_dict()

        return 404, {"error": "task not found", "task_id": task_id}

    async def _route_cancel(self, task_id: str) -> tuple[int, dict]:
        ok = await self.task_queue.cancel(task_id)
        if ok:
            return 200, {"task_id": task_id, "cancelled": True}
        return 400, {"task_id": task_id, "cancelled": False, "reason": "task already completed or not found"}

    async def _route_events(self) -> tuple[int, dict]:
        events = self.event_bus.recent_events(50)
        return 200, {"events": events}

    async def _route_health(self) -> tuple[int, dict]:
        return 200, {
            "status": "healthy",
            "active_tasks": len(self.task_queue.active_tasks()),
            "pending": self.task_queue.pending_count(),
        }

    async def _route_stats(self) -> tuple[int, dict]:
        return 200, {
            "active_tasks": self.task_queue.active_tasks(),
            "pending_count": self.task_queue.pending_count(),
            "completed_count": len(self.task_queue._results),
        }
