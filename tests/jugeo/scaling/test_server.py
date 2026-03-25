from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from src.jugeo.scaling.server.models import (
    EventKind,
    ServerConfig,
    TaskStatus,
    VerificationProgress,
    VerificationRequest,
    VerificationResult,
)
from src.jugeo.scaling.server.async_server import EventBus, TaskQueue
from src.jugeo.scaling.server.streaming import ProgressTracker, SSEStream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(**overrides) -> VerificationRequest:
    defaults = {
        "id": str(uuid.uuid4()),
        "target_path": "src/main.py",
        "scope": "LOCAL",
        "trust_target": "STANDARD",
        "incremental": True,
        "callback_url": None,
    }
    defaults.update(overrides)
    return VerificationRequest(**defaults)


# ===================================================================
# TaskQueue tests
# ===================================================================


class TestTaskQueue:
    def test_submit_creates_task(self) -> None:
        async def _run() -> None:
            q = TaskQueue(max_concurrent=2)
            req = _make_request()
            task_id = await q.submit(req)
            assert task_id is not None
            progress = await q.get_progress(task_id)
            assert progress is not None
            assert progress.status in (TaskStatus.PENDING, TaskStatus.RUNNING)
            await q.stop_workers()

        asyncio.run(_run())

    def test_cancel_task(self) -> None:
        async def _run() -> None:
            q = TaskQueue(max_concurrent=1)
            req = _make_request()
            task_id = await q.submit(req)
            ok = await q.cancel(task_id)
            assert ok is True
            progress = await q.get_progress(task_id)
            assert progress is not None
            assert progress.status == TaskStatus.CANCELLED
            await q.stop_workers()

        asyncio.run(_run())

    def test_get_result(self) -> None:
        async def _run() -> None:
            q = TaskQueue(max_concurrent=2)
            req = _make_request()
            task_id = await q.submit(req)
            # Wait for completion
            for _ in range(200):
                result = await q.get_result(task_id)
                if result is not None:
                    break
                await asyncio.sleep(0.05)
            assert result is not None
            assert result.success is True
            assert result.task_id == task_id
            await q.stop_workers()

        asyncio.run(_run())

    def test_concurrent_tasks(self) -> None:
        async def _run() -> None:
            q = TaskQueue(max_concurrent=4)
            ids = []
            for _ in range(3):
                req = _make_request()
                ids.append(await q.submit(req))
            # Wait for all
            for _ in range(300):
                results = [await q.get_result(tid) for tid in ids]
                if all(r is not None for r in results):
                    break
                await asyncio.sleep(0.05)
            for tid in ids:
                r = await q.get_result(tid)
                assert r is not None
                assert r.success is True
            await q.stop_workers()

        asyncio.run(_run())


# ===================================================================
# EventBus tests
# ===================================================================


class TestEventBus:
    def test_subscribe_and_publish(self) -> None:
        async def _run() -> None:
            bus = EventBus()
            received: list = []
            bus.subscribe(lambda kind, data: received.append((kind, data)))
            await bus.publish(EventKind.JUDGMENT_ADDED, {"x": 1})
            assert len(received) == 1
            assert received[0][0] == EventKind.JUDGMENT_ADDED

        asyncio.run(_run())

    def test_unsubscribe(self) -> None:
        async def _run() -> None:
            bus = EventBus()
            received: list = []
            cb = lambda kind, data: received.append((kind, data))
            bus.subscribe(cb)
            bus.unsubscribe(cb)
            await bus.publish(EventKind.EVIDENCE_ADDED, {"y": 2})
            assert len(received) == 0

        asyncio.run(_run())

    def test_recent_events(self) -> None:
        async def _run() -> None:
            bus = EventBus()
            for i in range(5):
                await bus.publish(EventKind.TRUST_CHANGED, {"i": i})
            events = bus.recent_events(10)
            assert len(events) == 5

        asyncio.run(_run())


# ===================================================================
# SSEStream tests
# ===================================================================


class TestSSEStream:
    def test_subscribe_returns_stream_id(self) -> None:
        bus = EventBus()
        stream = SSEStream(bus)
        sid = stream.subscribe()
        assert isinstance(sid, str)
        assert len(sid) > 0

    def test_format_sse(self) -> None:
        bus = EventBus()
        stream = SSEStream(bus)
        text = stream.format_sse("test_event", {"key": "val"})
        assert text.startswith("event: test_event\n")
        assert "data:" in text
        assert text.endswith("\n\n")

    def test_poll_returns_events(self) -> None:
        async def _run() -> None:
            bus = EventBus()
            stream = SSEStream(bus)
            sid = stream.subscribe()
            # Push event directly
            stream._on_event(EventKind.TASK_COMPLETED, {"task": "abc"})
            events = await stream.poll(sid, timeout=1.0)
            assert len(events) >= 1
            assert events[0]["event_kind"] == "TASK_COMPLETED"

        asyncio.run(_run())


# ===================================================================
# ProgressTracker tests
# ===================================================================


class TestProgressTracker:
    def test_start_and_update(self) -> None:
        tracker = ProgressTracker()
        tracker.start_tracking("t1", total=100)
        tracker.update("t1", increment=25, phase="phase1")
        progress = tracker.get_progress("t1")
        assert progress is not None
        assert progress.progress_pct == pytest.approx(25.0, abs=0.1)
        assert progress.coordinates_verified == 25

    def test_estimate_remaining(self) -> None:
        tracker = ProgressTracker()
        tracker.start_tracking("t2", total=100)
        tracker.update("t2", increment=50)
        remaining = tracker.estimate_remaining("t2")
        assert isinstance(remaining, float)
        assert remaining >= 0.0

    def test_complete(self) -> None:
        tracker = ProgressTracker()
        tracker.start_tracking("t3", total=100)
        tracker.complete("t3")
        progress = tracker.get_progress("t3")
        assert progress is not None
        assert progress.status == TaskStatus.COMPLETED

    def test_fail(self) -> None:
        tracker = ProgressTracker()
        tracker.start_tracking("t4", total=100)
        tracker.fail("t4")
        progress = tracker.get_progress("t4")
        assert progress is not None
        assert progress.status == TaskStatus.FAILED
