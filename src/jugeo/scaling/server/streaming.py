from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .models import EventKind, TaskStatus, VerificationProgress


class SSEStream:
    """Server-Sent Events stream manager.

    Allows multiple subscribers to receive events via long-polling.
    """

    def __init__(self, event_bus: Any) -> None:
        self._event_bus = event_bus
        self._streams: Dict[str, _StreamState] = {}
        # Register ourselves as a subscriber on the event bus
        self._event_bus.subscribe(self._on_event)

    def subscribe(self, event_kinds: Optional[list[str]] = None) -> str:
        stream_id = str(uuid.uuid4())
        self._streams[stream_id] = _StreamState(
            stream_id=stream_id,
            event_kinds=set(event_kinds) if event_kinds else None,
            buffer=[],
            created_at=time.time(),
        )
        return stream_id

    def unsubscribe(self, stream_id: str) -> None:
        self._streams.pop(stream_id, None)

    async def poll(self, stream_id: str, timeout: float = 30.0) -> list[dict]:
        state = self._streams.get(stream_id)
        if state is None:
            return []

        deadline = time.time() + timeout
        while time.time() < deadline:
            if state.buffer:
                events = list(state.buffer)
                state.buffer.clear()
                return events
            await asyncio.sleep(0.05)

        # Return whatever accumulated (may be empty)
        events = list(state.buffer)
        state.buffer.clear()
        return events

    def format_sse(self, event_kind: str, data: dict) -> str:
        payload = json.dumps(data)
        return f"event: {event_kind}\ndata: {payload}\n\n"

    # ------------------------------------------------------------------

    def _on_event(self, event_kind: EventKind, data: dict) -> None:
        kind_str = event_kind.value if isinstance(event_kind, EventKind) else str(event_kind)
        event_record = {
            "event_kind": kind_str,
            "data": data,
            "timestamp": time.time(),
            "event_id": str(uuid.uuid4()),
        }
        for state in self._streams.values():
            if state.event_kinds is None or kind_str in state.event_kinds:
                state.buffer.append(event_record)


@dataclass
class _StreamState:
    stream_id: str
    event_kinds: Optional[set[str]]
    buffer: list
    created_at: float


class ProgressTracker:
    """Tracks verification progress for multiple concurrent tasks."""

    def __init__(self) -> None:
        self._tasks: Dict[str, _ProgressState] = {}

    def start_tracking(self, task_id: str, total: int) -> None:
        self._tasks[task_id] = _ProgressState(
            task_id=task_id,
            total=total,
            verified=0,
            phase="started",
            started_at=time.time(),
            status=TaskStatus.RUNNING,
        )

    def update(self, task_id: str, increment: int = 1, phase: Optional[str] = None) -> None:
        state = self._tasks.get(task_id)
        if state is None:
            return
        state.verified = min(state.verified + increment, state.total)
        if phase is not None:
            state.phase = phase

    def get_progress(self, task_id: str) -> Optional[VerificationProgress]:
        state = self._tasks.get(task_id)
        if state is None:
            return None
        pct = (state.verified / state.total * 100.0) if state.total > 0 else 0.0
        remaining = self.estimate_remaining(task_id)
        return VerificationProgress(
            task_id=state.task_id,
            status=state.status,
            progress_pct=pct,
            coordinates_verified=state.verified,
            total_coordinates=state.total,
            obstructions_found=0,
            current_phase=state.phase,
            started_at=state.started_at,
            estimated_remaining_s=remaining,
        )

    def estimate_remaining(self, task_id: str) -> float:
        state = self._tasks.get(task_id)
        if state is None:
            return 0.0
        elapsed = time.time() - state.started_at
        if state.verified <= 0 or elapsed <= 0:
            return 0.0
        rate = state.verified / elapsed
        remaining_coords = state.total - state.verified
        return max(0.0, remaining_coords / rate)

    def complete(self, task_id: str) -> None:
        state = self._tasks.get(task_id)
        if state is None:
            return
        state.status = TaskStatus.COMPLETED
        state.verified = state.total

    def fail(self, task_id: str) -> None:
        state = self._tasks.get(task_id)
        if state is None:
            return
        state.status = TaskStatus.FAILED


@dataclass
class _ProgressState:
    task_id: str
    total: int
    verified: int
    phase: str
    started_at: float
    status: TaskStatus
