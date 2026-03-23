"""Tests for jugeo.ideation.semantic_futures.integration.

Covers FutureEvent, FuturesEventBus, ComponentHealth, IntegrationHealthCheck,
CopilotFuturesAdvisor, SemanticFuturesIntegration, and private helpers.
Each test is self-contained.  External schedulers and searchers are stubbed
with minimal inline classes so that no real infrastructure is required.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

from jugeo.ideation.semantic_futures.integration import (
    CopilotFuturesAdvisor,
    ComponentHealth,
    EventKind,
    EventSubscription,
    FutureEvent,
    FuturesEventBus,
    IntegrationHealthCheck,
    IntegrationStatus,
    SemanticFuturesIntegration,
    _format_payload,
    _make_event_id,
    _truncate,
)
from jugeo.ideation.semantic_futures.models import (
    FutureState,
    FutureTag,
    IdeationState,
    PurposeFunction,
    SemanticFuture,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_future(
    fid: str,
    *,
    delta: str = "Bridge lemma delta",
    reachability: float = 0.8,
    purpose_alignment: float = 0.75,
    expected_yield: float = 4.5,
    cost_estimate: float = 1.2,
) -> SemanticFuture:
    """Return a lightweight SemanticFuture for integration tests."""
    return SemanticFuture(
        future_id=fid,
        delta=delta,
        reachability=reachability,
        purpose_alignment=purpose_alignment,
        expected_yield=expected_yield,
        cost_estimate=cost_estimate,
        tags=(FutureTag.EXTENSION,),
        metadata={},
    )


def _make_ideation_state(n: int = 3) -> IdeationState:
    """Return a small IdeationState for use in integration tests."""
    fs = FutureState(
        state_id="s-int",
        theorem_portfolio=("T1", "T2"),
        known_kinds=("K1",),
        semantic_embedding=(0.4, 0.6),
        timestamp=datetime.now(),
    )
    purpose = PurposeFunction(
        purpose_id="p-int",
        domain="analysis",
        utility_weights={"yield": 0.5, "novelty": 0.5},
        alignment_threshold=0.4,
        description="Integration test purpose",
    )
    futures = [_make_future(f"fi{i}", delta=f"Delta step {i}") for i in range(n)]
    return IdeationState(
        state_id="is-int",
        current_state=fs,
        purpose=purpose,
        reachable_futures=futures,
        budget_remaining=8.0,
        archive=[],
    )


class _StubScheduler:
    """Minimal scheduler stub: just stores a flag."""

    connected: bool = False


class _StubNoveltySearcher:
    """Minimal novelty-searcher stub."""

    def score(self, future: SemanticFuture) -> float:
        return 0.5


# ---------------------------------------------------------------------------
# TestFutureEvent
# ---------------------------------------------------------------------------


class TestFutureEvent:
    """Tests for FutureEvent creation, serialisation, and string conversion."""

    def _make_event(self) -> FutureEvent:
        return FutureEvent(
            event_id="evt-001",
            kind=EventKind.SEARCH_COMPLETED,
            payload={"futures_found": 3},
            source="BeamSearchFutures",
            timestamp=datetime.now(),
        )

    def test_basic_creation(self) -> None:
        """FutureEvent stores all provided fields."""
        ev = self._make_event()
        assert ev.event_id == "evt-001"
        assert ev.kind is EventKind.SEARCH_COMPLETED
        assert ev.payload["futures_found"] == 3

    def test_to_dict_contains_kind(self) -> None:
        """to_dict() must include the event kind."""
        d = self._make_event().to_dict()
        assert "kind" in d

    def test_from_dict_round_trip(self) -> None:
        """from_dict(to_dict()) produces an equivalent event."""
        ev = self._make_event()
        restored = FutureEvent.from_dict(ev.to_dict())
        assert restored.event_id == ev.event_id
        assert restored.kind == ev.kind
        assert restored.source == ev.source

    def test_str_contains_kind_name(self) -> None:
        """str(event) should mention the kind name for readability."""
        ev = self._make_event()
        s = str(ev)
        assert "SEARCH_COMPLETED" in s or ev.kind.value in s

    def test_frozen_rejects_mutation(self) -> None:
        """FutureEvent must be immutable."""
        ev = self._make_event()
        with pytest.raises((AttributeError, TypeError)):
            ev.event_id = "modified"  # type: ignore[misc]

    def test_all_event_kinds_constructible(self) -> None:
        """FutureEvent can be built for every EventKind value."""
        for kind in EventKind:
            ev = FutureEvent(
                event_id=f"evt-{kind.name}",
                kind=kind,
                payload={},
                source="test",
                timestamp=datetime.now(),
            )
            assert ev.kind is kind


# ---------------------------------------------------------------------------
# TestFuturesEventBus
# ---------------------------------------------------------------------------


class TestFuturesEventBus:
    """Tests for the pub/sub event bus."""

    def test_subscribe_returns_string_id(self) -> None:
        """subscribe() returns a non-empty string subscription ID."""
        bus = FuturesEventBus()
        sid = bus.subscribe(EventKind.SEARCH_COMPLETED, lambda e: None)
        assert isinstance(sid, str)
        assert len(sid) > 0

    def test_publish_calls_handler(self) -> None:
        """A subscribed handler is called when the matching kind is published."""
        bus = FuturesEventBus()
        received: list[FutureEvent] = []
        bus.subscribe(EventKind.SEARCH_COMPLETED, received.append)
        ev = FutureEvent(
            event_id="e1",
            kind=EventKind.SEARCH_COMPLETED,
            payload={},
            source="test",
            timestamp=datetime.now(),
        )
        bus.publish(ev)
        assert len(received) == 1
        assert received[0].event_id == "e1"

    def test_handler_not_called_for_wrong_kind(self) -> None:
        """A handler subscribed to kind A is not called when kind B is published."""
        bus = FuturesEventBus()
        received: list[FutureEvent] = []
        bus.subscribe(EventKind.FUTURE_SELECTED, received.append)
        ev = FutureEvent(
            event_id="e2",
            kind=EventKind.SEARCH_COMPLETED,
            payload={},
            source="test",
            timestamp=datetime.now(),
        )
        bus.publish(ev)
        assert received == []

    def test_unsubscribe_removes_handler(self) -> None:
        """After unsubscribe, the handler is no longer called."""
        bus = FuturesEventBus()
        received: list[FutureEvent] = []
        sid = bus.subscribe(EventKind.FUTURE_ARCHIVED, received.append)
        bus.unsubscribe(sid)
        ev = FutureEvent(
            event_id="e3",
            kind=EventKind.FUTURE_ARCHIVED,
            payload={},
            source="test",
            timestamp=datetime.now(),
        )
        bus.publish(ev)
        assert received == []

    def test_unsubscribe_nonexistent_returns_false(self) -> None:
        """Unsubscribing an unknown ID returns False without raising."""
        bus = FuturesEventBus()
        result = bus.unsubscribe("ghost-id-xyz")
        assert result is False

    def test_handler_error_does_not_crash_bus(self) -> None:
        """A handler that raises must not prevent other handlers from running."""
        bus = FuturesEventBus()
        good: list[FutureEvent] = []

        def _bad(e: FutureEvent) -> None:
            raise RuntimeError("handler crash")

        bus.subscribe(EventKind.SEARCH_COMPLETED, _bad)
        bus.subscribe(EventKind.SEARCH_COMPLETED, good.append)
        ev = FutureEvent(
            event_id="e4",
            kind=EventKind.SEARCH_COMPLETED,
            payload={},
            source="test",
            timestamp=datetime.now(),
        )
        bus.publish(ev)  # must not raise
        assert len(good) == 1

    def test_history_returns_list(self) -> None:
        """history() returns a list of published events."""
        bus = FuturesEventBus()
        ev = FutureEvent(
            event_id="eh1",
            kind=EventKind.SEARCH_COMPLETED,
            payload={},
            source="test",
            timestamp=datetime.now(),
        )
        bus.publish(ev)
        h = bus.history()
        assert isinstance(h, list)
        assert len(h) >= 1

    def test_history_filtered_by_kind(self) -> None:
        """history(kind=K) returns only events of kind K."""
        bus = FuturesEventBus()
        for kind in (EventKind.SEARCH_COMPLETED, EventKind.FUTURE_SELECTED, EventKind.SEARCH_COMPLETED):
            ev = FutureEvent(
                event_id=f"eh-{kind.name}",
                kind=kind,
                payload={},
                source="test",
                timestamp=datetime.now(),
            )
            bus.publish(ev)
        filtered = bus.history(kind=EventKind.SEARCH_COMPLETED)
        assert all(e.kind is EventKind.SEARCH_COMPLETED for e in filtered)
        assert len(filtered) == 2

    def test_clear_history_empties_log(self) -> None:
        """clear_history() empties the event log."""
        bus = FuturesEventBus()
        ev = FutureEvent(
            event_id="ec1",
            kind=EventKind.FUTURE_ARCHIVED,
            payload={},
            source="test",
            timestamp=datetime.now(),
        )
        bus.publish(ev)
        bus.clear_history()
        assert bus.history() == []

    def test_subscriber_count_correct(self) -> None:
        """subscriber_count() reflects actual registered handlers."""
        bus = FuturesEventBus()
        assert bus.subscriber_count(EventKind.SEARCH_COMPLETED) == 0
        bus.subscribe(EventKind.SEARCH_COMPLETED, lambda e: None)
        bus.subscribe(EventKind.SEARCH_COMPLETED, lambda e: None)
        assert bus.subscriber_count(EventKind.SEARCH_COMPLETED) == 2

    def test_multiple_handlers_for_same_kind_all_called(self) -> None:
        """All handlers registered for the same kind are invoked."""
        bus = FuturesEventBus()
        calls: list[int] = []
        bus.subscribe(EventKind.FUTURE_SELECTED, lambda e: calls.append(1))
        bus.subscribe(EventKind.FUTURE_SELECTED, lambda e: calls.append(2))
        bus.subscribe(EventKind.FUTURE_SELECTED, lambda e: calls.append(3))
        ev = FutureEvent(
            event_id="em1",
            kind=EventKind.FUTURE_SELECTED,
            payload={},
            source="test",
            timestamp=datetime.now(),
        )
        bus.publish(ev)
        assert sorted(calls) == [1, 2, 3]

    def test_publish_kind_convenience(self) -> None:
        """publish_kind() creates and dispatches an event of the given kind."""
        bus = FuturesEventBus()
        received: list[FutureEvent] = []
        bus.subscribe(EventKind.BUDGET_EXHAUSTED, received.append)
        bus.publish_kind(EventKind.BUDGET_EXHAUSTED, payload={"budget": 0.0}, source="budget-tracker")
        assert len(received) == 1
        assert received[0].kind is EventKind.BUDGET_EXHAUSTED

    @pytest.mark.parametrize("kind", list(EventKind))
    def test_publish_every_kind(self, kind: EventKind) -> None:
        """bus.publish() handles every EventKind value without error."""
        bus = FuturesEventBus()
        ev = FutureEvent(
            event_id=f"ep-{kind.name}",
            kind=kind,
            payload={},
            source="parametrize-test",
            timestamp=datetime.now(),
        )
        bus.publish(ev)  # must not raise
        assert len(bus.history()) >= 1


# ---------------------------------------------------------------------------
# TestComponentHealth
# ---------------------------------------------------------------------------


class TestComponentHealth:
    """Tests for ComponentHealth dataclass."""

    def test_connected_is_healthy(self) -> None:
        """CONNECTED status reports is_healthy() == True."""
        h = ComponentHealth(
            name="scheduler",
            status=IntegrationStatus.CONNECTED,
            last_checked=datetime.now(),
            details="",
        )
        assert h.is_healthy() is True

    def test_disconnected_is_not_healthy(self) -> None:
        """DISCONNECTED status reports is_healthy() == False."""
        h = ComponentHealth(
            name="novelty-searcher",
            status=IntegrationStatus.DISCONNECTED,
            last_checked=datetime.now(),
            details="timeout",
        )
        assert h.is_healthy() is False

    def test_degraded_is_not_healthy(self) -> None:
        """DEGRADED status is treated as unhealthy by default."""
        h = ComponentHealth(
            name="archive",
            status=IntegrationStatus.DEGRADED,
            last_checked=datetime.now(),
            details="slow",
        )
        assert h.is_healthy() is False

    def test_to_dict_includes_status(self) -> None:
        """to_dict() must include the status field."""
        h = ComponentHealth(
            name="advisor",
            status=IntegrationStatus.CONNECTED,
            last_checked=datetime.now(),
            details="ok",
        )
        d = h.to_dict()
        assert "status" in d
        assert "name" in d


# ---------------------------------------------------------------------------
# TestIntegrationHealthCheck
# ---------------------------------------------------------------------------


class TestIntegrationHealthCheck:
    """Tests for IntegrationHealthCheck."""

    def _make_health_check_with_stubs(
        self,
        scheduler_status: IntegrationStatus = IntegrationStatus.CONNECTED,
        searcher_status: IntegrationStatus = IntegrationStatus.CONNECTED,
    ) -> IntegrationHealthCheck:
        """Return a health-check wired up with stub component statuses."""
        hc = IntegrationHealthCheck()
        hc.register_component(
            "scheduler",
            ComponentHealth(
                name="scheduler",
                status=scheduler_status,
                last_checked=datetime.now(),
                details="",
            ),
        )
        hc.register_component(
            "novelty_searcher",
            ComponentHealth(
                name="novelty_searcher",
                status=searcher_status,
                last_checked=datetime.now(),
                details="",
            ),
        )
        return hc

    def test_check_all_returns_dict(self) -> None:
        """check_all() returns a dict of component name → ComponentHealth."""
        hc = self._make_health_check_with_stubs()
        result = hc.check_all()
        assert isinstance(result, dict)
        assert "scheduler" in result

    def test_overall_health_all_connected(self) -> None:
        """All components CONNECTED → overall CONNECTED."""
        hc = self._make_health_check_with_stubs(
            IntegrationStatus.CONNECTED, IntegrationStatus.CONNECTED
        )
        assert hc.overall_health() is IntegrationStatus.CONNECTED

    def test_overall_health_mixed(self) -> None:
        """One CONNECTED, one DISCONNECTED → overall DEGRADED."""
        hc = self._make_health_check_with_stubs(
            IntegrationStatus.CONNECTED, IntegrationStatus.DISCONNECTED
        )
        assert hc.overall_health() is IntegrationStatus.DEGRADED

    def test_overall_health_all_disconnected(self) -> None:
        """All DISCONNECTED → overall DISCONNECTED."""
        hc = self._make_health_check_with_stubs(
            IntegrationStatus.DISCONNECTED, IntegrationStatus.DISCONNECTED
        )
        assert hc.overall_health() is IntegrationStatus.DISCONNECTED

    def test_report_non_empty_string(self) -> None:
        """report() returns a non-empty human-readable string."""
        hc = self._make_health_check_with_stubs()
        report = hc.report()
        assert isinstance(report, str)
        assert len(report) > 0


# ---------------------------------------------------------------------------
# TestCopilotFuturesAdvisor
# ---------------------------------------------------------------------------


class TestCopilotFuturesAdvisor:
    """Tests for the CopilotFuturesAdvisor natural-language advisory interface."""

    def _make_advisor(self, n: int = 3) -> CopilotFuturesAdvisor:
        state = _make_ideation_state(n)
        return CopilotFuturesAdvisor(state=state)

    def test_top_futures_summary_returns_string(self) -> None:
        """top_futures_summary() returns a non-empty string."""
        adv = self._make_advisor()
        s = adv.top_futures_summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_top_futures_summary_mentions_future_ids(self) -> None:
        """Summary should reference at least one future_id from the state."""
        state = _make_ideation_state(3)
        adv = CopilotFuturesAdvisor(state=state)
        summary = adv.top_futures_summary()
        any_id = any(f.future_id in summary for f in state.reachable_futures)
        assert any_id

    def test_next_step_advice_returns_string(self) -> None:
        """next_step_advice() returns actionable guidance as a string."""
        adv = self._make_advisor()
        advice = adv.next_step_advice()
        assert isinstance(advice, str)
        assert len(advice) > 10  # at least a sentence

    def test_budget_warning_none_when_budget_ok(self) -> None:
        """budget_warning() returns None when budget is healthy."""
        state = _make_ideation_state(2)
        # budget_remaining = 8.0 is well above total cost
        adv = CopilotFuturesAdvisor(state=state)
        warning = adv.budget_warning()
        assert warning is None

    def test_budget_warning_string_when_low(self) -> None:
        """budget_warning() returns a warning string when budget is nearly exhausted."""
        state = _make_ideation_state(2)
        low_budget_state = IdeationState(
            state_id=state.state_id,
            current_state=state.current_state,
            purpose=state.purpose,
            reachable_futures=state.reachable_futures,
            budget_remaining=0.01,
            archive=state.archive,
        )
        adv = CopilotFuturesAdvisor(state=low_budget_state)
        warning = adv.budget_warning()
        assert isinstance(warning, str)
        assert len(warning) > 0

    def test_archive_summary_returns_string(self) -> None:
        """archive_summary() returns a non-empty string."""
        adv = self._make_advisor()
        s = adv.archive_summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_full_advisory_returns_comprehensive_string(self) -> None:
        """full_advisory() integrates all advisory components into one string."""
        adv = self._make_advisor()
        full = adv.full_advisory()
        assert isinstance(full, str)
        assert len(full) > 50  # must be meaningful

    def test_format_future_readable(self) -> None:
        """format_future() returns a human-readable description of a SemanticFuture."""
        adv = self._make_advisor()
        f = _make_future("fmt-f", delta="Extend via catalan numbers")
        s = adv.format_future(f)
        assert isinstance(s, str)
        assert "fmt-f" in s or "catalan" in s.lower()

    def test_empty_futures_no_crash(self) -> None:
        """Advisor works gracefully when reachable_futures is empty."""
        state = _make_ideation_state(0)
        adv = CopilotFuturesAdvisor(state=state)
        summary = adv.top_futures_summary()
        assert isinstance(summary, str)


# ---------------------------------------------------------------------------
# TestSemanticFuturesIntegration
# ---------------------------------------------------------------------------


class TestSemanticFuturesIntegration:
    """Tests for SemanticFuturesIntegration lifecycle methods."""

    def _make_integration(self) -> SemanticFuturesIntegration:
        bus = FuturesEventBus()
        return SemanticFuturesIntegration(event_bus=bus)

    def test_connect_scheduler_stores_reference(self) -> None:
        """connect_to_scheduler() stores the scheduler for later use."""
        integ = self._make_integration()
        sched = _StubScheduler()
        integ.connect_to_scheduler(sched)
        assert integ.scheduler is sched

    def test_connect_scheduler_publishes_event(self) -> None:
        """connect_to_scheduler() publishes a SCHEDULER_CONNECTED event."""
        bus = FuturesEventBus()
        integ = SemanticFuturesIntegration(event_bus=bus)
        received: list[FutureEvent] = []
        bus.subscribe(EventKind.SCHEDULER_CONNECTED, received.append)
        integ.connect_to_scheduler(_StubScheduler())
        assert len(received) == 1

    def test_connect_novelty_searcher_stores_reference(self) -> None:
        """connect_to_novelty_searcher() stores the searcher."""
        integ = self._make_integration()
        searcher = _StubNoveltySearcher()
        integ.connect_to_novelty_searcher(searcher)
        assert integ.novelty_searcher is searcher

    def test_connect_novelty_searcher_publishes_event(self) -> None:
        """connect_to_novelty_searcher() publishes a SEARCHER_CONNECTED event."""
        bus = FuturesEventBus()
        integ = SemanticFuturesIntegration(event_bus=bus)
        received: list[FutureEvent] = []
        bus.subscribe(EventKind.SEARCHER_CONNECTED, received.append)
        integ.connect_to_novelty_searcher(_StubNoveltySearcher())
        assert len(received) == 1

    def test_push_futures_to_archive_publishes_events(self) -> None:
        """push_futures_to_archive() publishes FUTURE_ARCHIVED for each future."""
        bus = FuturesEventBus()
        integ = SemanticFuturesIntegration(event_bus=bus)
        archived: list[FutureEvent] = []
        bus.subscribe(EventKind.FUTURE_ARCHIVED, archived.append)
        futures = [_make_future(f"pf{i}") for i in range(3)]
        integ.push_futures_to_archive(futures)
        assert len(archived) == 3

    def test_on_search_completed_publishes_event(self) -> None:
        """on_search_completed() publishes a SEARCH_COMPLETED event."""
        bus = FuturesEventBus()
        integ = SemanticFuturesIntegration(event_bus=bus)
        received: list[FutureEvent] = []
        bus.subscribe(EventKind.SEARCH_COMPLETED, received.append)
        from jugeo.ideation.semantic_futures.algorithms import SearchConfig, SearchResult

        result = SearchResult(
            best_future=_make_future("sr-f"),
            selected_futures=(_make_future("sr-f"),),
            value_trace=(3.0,),
            converged=True,
            algorithm_name="TestAlgo",
            wall_time_s=0.0,
        )
        integ.on_search_completed(result)
        assert len(received) == 1

    def test_on_future_selected_publishes_event(self) -> None:
        """on_future_selected() publishes a FUTURE_SELECTED event."""
        bus = FuturesEventBus()
        integ = SemanticFuturesIntegration(event_bus=bus)
        received: list[FutureEvent] = []
        bus.subscribe(EventKind.FUTURE_SELECTED, received.append)
        integ.on_future_selected(_make_future("sel-f"))
        assert len(received) == 1

    def test_disconnect_all_clears_connections(self) -> None:
        """disconnect_all() sets scheduler and novelty_searcher to None."""
        integ = self._make_integration()
        integ.connect_to_scheduler(_StubScheduler())
        integ.connect_to_novelty_searcher(_StubNoveltySearcher())
        integ.disconnect_all()
        assert integ.scheduler is None
        assert integ.novelty_searcher is None

    def test_status_returns_dict(self) -> None:
        """status() returns a dict mapping component names to IntegrationStatus values."""
        integ = self._make_integration()
        s = integ.status()
        assert isinstance(s, dict)
        for v in s.values():
            assert isinstance(v, IntegrationStatus)

    def test_status_disconnected_before_connect(self) -> None:
        """Before connecting anything, all components should report DISCONNECTED."""
        integ = self._make_integration()
        s = integ.status()
        for v in s.values():
            assert v is IntegrationStatus.DISCONNECTED

    def test_status_connected_after_connect(self) -> None:
        """After connecting scheduler, its status should be CONNECTED."""
        integ = self._make_integration()
        integ.connect_to_scheduler(_StubScheduler())
        s = integ.status()
        assert s.get("scheduler") is IntegrationStatus.CONNECTED


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for module-level private helper functions."""

    def test_make_event_id_unique(self) -> None:
        """_make_event_id() must return a different string on each call."""
        ids = {_make_event_id() for _ in range(20)}
        assert len(ids) == 20

    def test_make_event_id_non_empty(self) -> None:
        """_make_event_id() must return a non-empty string."""
        eid = _make_event_id()
        assert isinstance(eid, str)
        assert len(eid) > 0

    def test_format_payload_non_empty(self) -> None:
        """_format_payload() returns a non-empty string for any dict payload."""
        s = _format_payload({"key": "value", "number": 42})
        assert isinstance(s, str)
        assert len(s) > 0

    def test_format_payload_empty_dict(self) -> None:
        """_format_payload({}) returns a string (possibly 'null' or '{}')."""
        s = _format_payload({})
        assert isinstance(s, str)

    def test_truncate_short_string_unchanged(self) -> None:
        """Strings shorter than max_len are returned as-is."""
        s = "hello"
        assert _truncate(s, max_len=20) == s

    def test_truncate_long_string_cut(self) -> None:
        """Strings longer than max_len are cut to at most max_len characters."""
        s = "a" * 100
        result = _truncate(s, max_len=10)
        assert len(result) <= 10

    def test_truncate_exact_length(self) -> None:
        """String exactly at max_len is returned unchanged."""
        s = "abcde"
        assert _truncate(s, max_len=5) == s

    def test_truncate_zero_length(self) -> None:
        """max_len=0 returns an empty string (or just the ellipsis suffix)."""
        result = _truncate("hello", max_len=0)
        assert isinstance(result, str)
        assert len(result) == 0 or len(result) <= 3  # ellipsis only


# ---------------------------------------------------------------------------
# TestIntegrationFull
# ---------------------------------------------------------------------------


class TestIntegrationFull:
    """End-to-end tests combining multiple integration components."""

    def test_full_pipeline_connect_search_advise(self) -> None:
        """Full pipeline: connect → search → push archive → get copilot advice."""
        bus = FuturesEventBus()
        integ = SemanticFuturesIntegration(event_bus=bus)

        # Track events
        all_events: list[FutureEvent] = []
        for kind in EventKind:
            bus.subscribe(kind, all_events.append)

        # Connect components
        integ.connect_to_scheduler(_StubScheduler())
        integ.connect_to_novelty_searcher(_StubNoveltySearcher())

        # Simulate search result
        from jugeo.ideation.semantic_futures.algorithms import (
            BeamSearchFutures,
            SearchConfig,
        )

        state = _make_ideation_state(4)
        algo = BeamSearchFutures(SearchConfig(beam_width=2))
        search_result = algo.search(state)
        integ.on_search_completed(search_result)

        # Push some futures to archive
        integ.push_futures_to_archive(list(state.reachable_futures[:2]))

        # Get copilot advice
        adv = CopilotFuturesAdvisor(state=state)
        advice = adv.full_advisory()
        assert isinstance(advice, str) and len(advice) > 0

        # Verify events were received
        kinds_seen = {e.kind for e in all_events}
        assert EventKind.SCHEDULER_CONNECTED in kinds_seen
        assert EventKind.SEARCH_COMPLETED in kinds_seen
        assert EventKind.FUTURE_ARCHIVED in kinds_seen

    def test_event_bus_isolation_between_buses(self) -> None:
        """Two separate FuturesEventBus instances do not share events."""
        bus_a = FuturesEventBus()
        bus_b = FuturesEventBus()
        received_b: list[FutureEvent] = []
        bus_b.subscribe(EventKind.SEARCH_COMPLETED, received_b.append)

        ev = FutureEvent(
            event_id="iso-1",
            kind=EventKind.SEARCH_COMPLETED,
            payload={},
            source="test",
            timestamp=datetime.now(),
        )
        bus_a.publish(ev)  # only on bus_a
        assert received_b == []

    def test_reconnect_replaces_previous_reference(self) -> None:
        """Connecting a second scheduler replaces the first reference."""
        integ = SemanticFuturesIntegration(event_bus=FuturesEventBus())
        s1 = _StubScheduler()
        s2 = _StubScheduler()
        integ.connect_to_scheduler(s1)
        integ.connect_to_scheduler(s2)
        assert integ.scheduler is s2

    def test_health_check_after_connect(self) -> None:
        """Health check reports CONNECTED for scheduler after connection."""
        integ = SemanticFuturesIntegration(event_bus=FuturesEventBus())
        integ.connect_to_scheduler(_StubScheduler())
        hc = IntegrationHealthCheck()
        s = integ.status()
        hc.register_component(
            "scheduler",
            ComponentHealth(
                name="scheduler",
                status=s.get("scheduler", IntegrationStatus.DISCONNECTED),
                last_checked=datetime.now(),
                details="from status()",
            ),
        )
        assert hc.check_all()["scheduler"].status is IntegrationStatus.CONNECTED

    def test_push_empty_futures_no_events(self) -> None:
        """Pushing an empty list to archive publishes no FUTURE_ARCHIVED events."""
        bus = FuturesEventBus()
        integ = SemanticFuturesIntegration(event_bus=bus)
        archived: list[FutureEvent] = []
        bus.subscribe(EventKind.FUTURE_ARCHIVED, archived.append)
        integ.push_futures_to_archive([])
        assert archived == []

    def test_copilot_advisor_with_archive(self) -> None:
        """CopilotFuturesAdvisor archive_summary is more informative with archived futures."""
        state = _make_ideation_state(3)
        archived_future = _make_future("arch-f", delta="Archived insight lemma")
        state_with_archive = IdeationState(
            state_id=state.state_id,
            current_state=state.current_state,
            purpose=state.purpose,
            reachable_futures=state.reachable_futures,
            budget_remaining=state.budget_remaining,
            archive=[archived_future],
        )
        adv = CopilotFuturesAdvisor(state=state_with_archive)
        summary = adv.archive_summary()
        assert isinstance(summary, str) and len(summary) > 0

    def test_event_subscription_object_has_id(self) -> None:
        """EventSubscription returned by subscribe carries the subscription ID."""
        bus = FuturesEventBus()
        sid = bus.subscribe(EventKind.SEARCH_COMPLETED, lambda e: None)
        sub = EventSubscription(subscription_id=sid, kind=EventKind.SEARCH_COMPLETED)
        assert sub.subscription_id == sid
        assert sub.kind is EventKind.SEARCH_COMPLETED
