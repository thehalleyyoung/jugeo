"""Comprehensive tests for jugeo.orchestration.semantic_control.s01_state_management.

Covers:
- StateSnapshot: construction, age(), to_dict(), summary(), matches()
- StateEventBus: subscribe(), publish(), unsubscribe(), history(), clear_history()
- StateValidator: add_rule(), validate(), is_valid(), validate_transition(), default_rules()
- StateProjector: project(), project_covers(), project_obligations(), project_budget(), weighted_projection()
- StateAggregator: aggregate(), coverage_union(), obligation_intersection(), weighted_attainability()
- StateDeltaComputer: compute(), apply_delta(), compose_deltas(), is_reversible()
- StateManager: initialize(), transition(), rollback(), take_snapshot(), get_snapshot(),
                list_snapshots(), diff(), reset(), status(), export_history()
- Integration tests with upstream controller, trust, and fleet modules
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = next(
    parent for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import time
import uuid
from typing import Any

import pytest

# ── Module under test ────────────────────────────────────────────────────────

from jugeo.orchestration.semantic_control.s01_state_management import (
    StateSnapshot,
    StateEventBus,
    StateEventKind,
    StateEvent,
    StateValidator,
    StateProjector,
    StateAggregator,
    StateDeltaComputer,
    StateManager,
    make_default_state_manager,
    make_state_projector,
    DEFAULT_MAX_HISTORY,
    DEFAULT_AGGREGATION_STRATEGY,
    VERSION,
)

# ── Upstream models ──────────────────────────────────────────────────────────

from jugeo.orchestration.semantic_control.models import (
    SemanticControlState,
    StateDelta,
    AdmissibleMove,
    StateHealthStatus,
)

# ── Optional upstream imports ─────────────────────────────────────────────────

try:
    from jugeo.orchestration.controller import (
        OrchestratorState,
        MoveKind,
        GreedyControl,
    )
    HAS_CONTROLLER = True
except Exception:
    HAS_CONTROLLER = False

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember, CompetitiveSearch
    HAS_FLEET = True
except Exception:
    HAS_FLEET = False

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel, TrustTier
    HAS_TRUST = True
except Exception:
    HAS_TRUST = False


# ── State construction helpers ────────────────────────────────────────────────

def _make_state(
    cover_ids=None,
    section_ids=None,
    obligation_ids=None,
    budget=None,
    treaty_ids=None,
    channel_ids=None,
    context_ids=None,
    metadata=None,
    state_id=None,
):
    return SemanticControlState(
        state_id=state_id or str(uuid.uuid4()),
        cover_ids=list(cover_ids or []),
        context_ids=list(context_ids or []),
        section_ids=list(section_ids or []),
        treaty_ids=list(treaty_ids or []),
        obligation_ids=list(obligation_ids or []),
        channel_ids=list(channel_ids or []),
        budget=dict(budget or {"default": 100.0}),
        timestamp=time.time(),
        metadata=dict(metadata or {}),
    )


def _make_event(kind=None, state_id=None, payload=None):
    return StateEvent(
        event_id=str(uuid.uuid4()),
        kind=kind or StateEventKind.CREATED,
        state_id=state_id or str(uuid.uuid4()),
        payload=payload or {},
        timestamp=time.time(),
    )


def _valid_next_state(prev, extra_cover="c-new"):
    """Return a state that passes validate_transition from prev (adds cover, stable budget)."""
    new_covers = list(prev.cover_ids) + [extra_cover]
    return _make_state(
        cover_ids=new_covers,
        section_ids=list(prev.section_ids),
        obligation_ids=list(prev.obligation_ids),
        budget=dict(prev.budget),
        treaty_ids=list(prev.treaty_ids),
        channel_ids=list(prev.channel_ids),
        context_ids=list(prev.context_ids),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def basic_state():
    """A minimal valid SemanticControlState for reuse across tests."""
    return _make_state(
        cover_ids=["cover-1", "cover-2"],
        section_ids=["sec-A", "sec-B", "sec-C"],
        obligation_ids=["obl-1"],
        budget={"default": 50.0, "trust": 10.0},
    )


@pytest.fixture
def basic_move():
    """A minimal valid AdmissibleMove for reuse across tests."""
    _kind = MoveKind.VERIFY if HAS_CONTROLLER else "VERIFY"
    return AdmissibleMove(
        move_id=str(uuid.uuid4()),
        kind=_kind,
        preconditions=["cover-1"],
        postconditions=["cover-3"],
        cost=5.0,
        priority=1.0,
        expected_gain=10.0,
        trust_requirement="low",
        metadata={},
    )


@pytest.fixture
def populated_manager():
    """A StateManager with a few validated transitions already applied."""
    mgr = make_default_state_manager()
    s0 = _make_state(
        cover_ids=["c1"],
        section_ids=["s1", "s2"],
        obligation_ids=["o1", "o2"],
        budget={"default": 100.0},
    )
    mgr.initialize(s0)
    s1 = _make_state(
        cover_ids=["c1", "c2"],
        section_ids=["s1", "s2"],
        obligation_ids=["o1", "o2"],
        budget={"default": 100.0},
    )
    mgr.transition(s1)
    s2 = _make_state(
        cover_ids=["c1", "c2", "c3"],
        section_ids=["s1", "s2"],
        obligation_ids=["o2"],
        budget={"default": 100.0},
    )
    mgr.transition(s2)
    return mgr


# ════════════════════════════════════════════════════════════════════════════
# StateSnapshot
# ════════════════════════════════════════════════════════════════════════════

class TestStateSnapshot:

    def test_construction_stores_all_fields(self, basic_state):
        snap = StateSnapshot(
            snapshot_id="snap-001",
            state=basic_state,
            taken_at=1_700_000_000.0,
            label="test-label",
            metadata={"tag": "v1"},
        )
        assert snap.snapshot_id == "snap-001"
        assert snap.state is basic_state
        assert snap.taken_at == 1_700_000_000.0
        assert snap.label == "test-label"
        assert snap.metadata["tag"] == "v1"

    def test_age_is_nonnegative_float(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={},
        )
        age = snap.age()
        assert isinstance(age, float)
        assert age >= 0.0

    def test_age_increases_for_old_snapshot(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time() - 10.0,
            label="",
            metadata={},
        )
        assert snap.age() >= 10.0

    def test_age_close_to_zero_for_fresh_snapshot(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={},
        )
        assert snap.age() < 2.0

    def test_to_dict_has_required_keys(self, basic_state):
        snap = StateSnapshot(
            snapshot_id="snap-x",
            state=basic_state,
            taken_at=1_234_567_890.0,
            label="checkpoint",
            metadata={"key": "val"},
        )
        d = snap.to_dict()
        for key in ("snapshot_id", "state", "taken_at", "label", "metadata"):
            assert key in d

    def test_to_dict_snapshot_id_matches(self, basic_state):
        snap_id = str(uuid.uuid4())
        snap = StateSnapshot(
            snapshot_id=snap_id,
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={},
        )
        assert snap.to_dict()["snapshot_id"] == snap_id

    def test_to_dict_state_is_dict(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={},
        )
        assert isinstance(snap.to_dict()["state"], dict)

    def test_to_dict_taken_at_preserved(self, basic_state):
        t = 1_700_123_456.789
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=t,
            label="",
            metadata={},
        )
        assert abs(snap.to_dict()["taken_at"] - t) < 1e-6

    def test_summary_returns_nonempty_string(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="my-label",
            metadata={},
        )
        s = snap.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summary_contains_label_when_set(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="special-label",
            metadata={},
        )
        assert "special-label" in snap.summary()

    def test_summary_contains_snapshot_id_prefix(self, basic_state):
        snap_id = "abcdef12-0000-0000-0000-000000000000"
        snap = StateSnapshot(
            snapshot_id=snap_id,
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={},
        )
        assert snap_id[:8] in snap.summary()

    def test_matches_empty_query_always_true(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="lbl",
            metadata={},
        )
        assert snap.matches({}) is True

    def test_matches_label_key_correct_value(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="target-label",
            metadata={},
        )
        assert snap.matches({"label": "target-label"}) is True

    def test_matches_label_key_wrong_value(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="target-label",
            metadata={},
        )
        assert snap.matches({"label": "wrong-label"}) is False

    def test_matches_metadata_key_correct(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={"phase": "pre-refine"},
        )
        assert snap.matches({"phase": "pre-refine"}) is True

    def test_matches_metadata_key_wrong(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={"phase": "pre-refine"},
        )
        assert snap.matches({"phase": "post-refine"}) is False

    def test_matches_state_attribute_correct(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={},
        )
        assert snap.matches({"state_id": basic_state.state_id}) is True

    def test_matches_state_attribute_wrong(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={},
        )
        assert snap.matches({"state_id": "nonexistent-id"}) is False

    def test_matches_nonexistent_key_returns_false(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={},
        )
        assert snap.matches({"totally_unknown_key": 42}) is False

    def test_matches_combined_query_all_must_pass(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="multi-match",
            metadata={"env": "test"},
        )
        assert snap.matches({"label": "multi-match", "env": "test"}) is True
        assert snap.matches({"label": "multi-match", "env": "prod"}) is False
        assert snap.matches({"label": "wrong", "env": "test"}) is False

    def test_matches_metadata_overrides_state_attr_lookup(self, basic_state):
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=basic_state,
            taken_at=time.time(),
            label="",
            metadata={"cover_ids": "metadata-value"},
        )
        assert snap.matches({"cover_ids": "metadata-value"}) is True


# ════════════════════════════════════════════════════════════════════════════
# StateEventBus
# ════════════════════════════════════════════════════════════════════════════

class TestStateEventBus:

    def test_subscribe_returns_str(self):
        bus = StateEventBus()
        sub_id = bus.subscribe(StateEventKind.CREATED, lambda e: None)
        assert isinstance(sub_id, str) and len(sub_id) > 0

    def test_subscribe_returns_unique_ids(self):
        bus = StateEventBus()
        ids = {bus.subscribe(StateEventKind.CREATED, lambda e: None) for _ in range(20)}
        assert len(ids) == 20

    def test_publish_calls_subscriber(self):
        bus = StateEventBus()
        received = []
        bus.subscribe(StateEventKind.CREATED, received.append)
        event = _make_event(StateEventKind.CREATED)
        bus.publish(event)
        assert len(received) == 1
        assert received[0] is event

    def test_publish_calls_multiple_subscribers(self):
        bus = StateEventBus()
        calls_a, calls_b = [], []
        bus.subscribe(StateEventKind.TRANSITION, calls_a.append)
        bus.subscribe(StateEventKind.TRANSITION, calls_b.append)
        bus.publish(_make_event(StateEventKind.TRANSITION))
        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_publish_does_not_call_different_kind_subscribers(self):
        bus = StateEventBus()
        received = []
        bus.subscribe(StateEventKind.VALIDATED, received.append)
        bus.publish(_make_event(StateEventKind.CREATED))
        assert received == []

    def test_publish_appends_to_history(self):
        bus = StateEventBus()
        bus.publish(_make_event(StateEventKind.CREATED))
        bus.publish(_make_event(StateEventKind.TRANSITION))
        assert len(bus.history()) == 2

    def test_subscriber_exception_is_swallowed(self):
        bus = StateEventBus()
        good_calls = []

        def bad_cb(e):
            raise RuntimeError("intentional boom")

        bus.subscribe(StateEventKind.CREATED, bad_cb)
        bus.subscribe(StateEventKind.CREATED, good_calls.append)
        bus.publish(_make_event(StateEventKind.CREATED))
        assert len(good_calls) == 1

    def test_unsubscribe_stops_future_callbacks(self):
        bus = StateEventBus()
        received = []
        sub_id = bus.subscribe(StateEventKind.RESET, received.append)
        bus.publish(_make_event(StateEventKind.RESET))
        assert len(received) == 1
        assert bus.unsubscribe(sub_id) is True
        bus.publish(_make_event(StateEventKind.RESET))
        assert len(received) == 1

    def test_unsubscribe_unknown_id_returns_false(self):
        bus = StateEventBus()
        assert bus.unsubscribe("nonexistent-sub-id") is False

    def test_unsubscribe_same_id_twice_second_is_false(self):
        bus = StateEventBus()
        sub_id = bus.subscribe(StateEventKind.CREATED, lambda e: None)
        assert bus.unsubscribe(sub_id) is True
        assert bus.unsubscribe(sub_id) is False

    def test_history_no_filter_returns_all(self):
        bus = StateEventBus()
        for k in [StateEventKind.CREATED, StateEventKind.TRANSITION, StateEventKind.RESET]:
            bus.publish(_make_event(k))
        assert len(bus.history()) == 3

    def test_history_filtered_by_kind(self):
        bus = StateEventBus()
        bus.publish(_make_event(StateEventKind.CREATED))
        bus.publish(_make_event(StateEventKind.TRANSITION))
        bus.publish(_make_event(StateEventKind.CREATED))
        created = bus.history(StateEventKind.CREATED)
        assert len(created) == 2
        assert all(e.kind == StateEventKind.CREATED for e in created)

    def test_history_filter_empty_when_kind_not_seen(self):
        bus = StateEventBus()
        bus.publish(_make_event(StateEventKind.CREATED))
        assert bus.history(StateEventKind.RESET) == []

    def test_history_preserves_insertion_order(self):
        bus = StateEventBus()
        kinds = [StateEventKind.CREATED, StateEventKind.TRANSITION, StateEventKind.SNAPSHOT_TAKEN]
        for k in kinds:
            bus.publish(_make_event(k))
        assert [e.kind for e in bus.history()] == kinds

    def test_history_returns_copy_not_live_list(self):
        bus = StateEventBus()
        bus.publish(_make_event(StateEventKind.CREATED))
        h = bus.history()
        h.clear()
        assert len(bus.history()) == 1

    def test_clear_history_removes_all_events(self):
        bus = StateEventBus()
        for _ in range(5):
            bus.publish(_make_event(StateEventKind.CREATED))
        bus.clear_history()
        assert bus.history() == []

    def test_clear_history_does_not_affect_subscribers(self):
        bus = StateEventBus()
        received = []
        bus.subscribe(StateEventKind.CREATED, received.append)
        bus.publish(_make_event(StateEventKind.CREATED))
        bus.clear_history()
        bus.publish(_make_event(StateEventKind.CREATED))
        assert len(received) == 2

    def test_subscribe_and_publish_multiple_kinds(self):
        bus = StateEventBus()
        events_seen = []
        bus.subscribe(StateEventKind.CREATED, lambda e: events_seen.append(("created", e)))
        bus.subscribe(StateEventKind.TRANSITION, lambda e: events_seen.append(("transition", e)))
        bus.publish(_make_event(StateEventKind.CREATED))
        bus.publish(_make_event(StateEventKind.TRANSITION))
        bus.publish(_make_event(StateEventKind.RESET))
        assert len(events_seen) == 2


# ════════════════════════════════════════════════════════════════════════════
# StateValidator
# ════════════════════════════════════════════════════════════════════════════

class TestStateValidator:

    def test_empty_rules_always_valid(self, basic_state):
        v = StateValidator(rules=[], strict=False)
        assert v.validate(basic_state) == []
        assert v.is_valid(basic_state) is True

    def test_add_rule_appends_to_list(self):
        v = StateValidator(rules=[], strict=False)
        assert len(v.rules) == 0
        v.add_rule(lambda s: None)
        assert len(v.rules) == 1
        v.add_rule(lambda s: "violation")
        assert len(v.rules) == 2

    def test_passing_rule_no_violations(self, basic_state):
        v = StateValidator(rules=[lambda s: None], strict=False)
        assert v.validate(basic_state) == []

    def test_failing_rule_returns_message(self, basic_state):
        v = StateValidator(rules=[lambda s: "bad state"], strict=False)
        violations = v.validate(basic_state)
        assert len(violations) == 1
        assert "bad state" in violations[0]

    def test_multiple_violations_all_collected_non_strict(self, basic_state):
        v = StateValidator(rules=[lambda s: "v1", lambda s: "v2", lambda s: None], strict=False)
        violations = v.validate(basic_state)
        assert len(violations) == 2

    def test_strict_mode_stops_at_first_violation(self, basic_state):
        v = StateValidator(rules=[lambda s: "first", lambda s: "second"], strict=True)
        violations = v.validate(basic_state)
        assert len(violations) == 1
        assert violations[0] == "first"

    def test_is_valid_true_when_no_violations(self, basic_state):
        v = StateValidator(rules=[lambda s: None], strict=False)
        assert v.is_valid(basic_state) is True

    def test_is_valid_false_when_violations(self, basic_state):
        v = StateValidator(rules=[lambda s: "problem"], strict=False)
        assert v.is_valid(basic_state) is False

    def test_is_valid_does_not_mutate_strict_mode(self, basic_state):
        v = StateValidator(rules=[lambda s: None], strict=True)
        v.is_valid(basic_state)
        assert v.strict is True

    def test_default_rules_returns_nonempty_list(self):
        rules = StateValidator.default_rules()
        assert isinstance(rules, list)
        assert len(rules) >= 4

    def test_default_rules_pass_valid_state(self):
        rules = StateValidator.default_rules()
        v = StateValidator(rules=rules, strict=False)
        state = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 10.0})
        assert v.is_valid(state) is True

    def test_default_rules_fail_negative_budget(self):
        rules = StateValidator.default_rules()
        v = StateValidator(rules=rules, strict=False)
        state = _make_state(budget={"channel": -5.0})
        assert v.is_valid(state) is False

    def test_rule_exception_caught_and_reported(self, basic_state):
        def bad_rule(s):
            raise ValueError("explode!")

        v = StateValidator(rules=[bad_rule], strict=False)
        violations = v.validate(basic_state)
        assert len(violations) == 1
        assert "explode!" in violations[0]

    def test_validate_transition_valid_cover_gain(self):
        v = StateValidator(rules=StateValidator.default_rules(), strict=False)
        from_s = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=["o1"], budget={"ch": 100.0})
        to_s = _make_state(cover_ids=["c1", "c2"], section_ids=["s1"], obligation_ids=["o1"], budget={"ch": 100.0})
        assert v.validate_transition(from_s, to_s) == []

    def test_validate_transition_valid_obligation_resolved(self):
        v = StateValidator(rules=StateValidator.default_rules(), strict=False)
        from_s = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=["o1", "o2"], budget={"ch": 100.0})
        to_s = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=["o2"], budget={"ch": 100.0})
        assert v.validate_transition(from_s, to_s) == []

    def test_validate_transition_catches_budget_regression(self):
        v = StateValidator(rules=StateValidator.default_rules(), strict=False)
        from_s = _make_state(budget={"ch": 100.0})
        to_s = _make_state(cover_ids=["c-new"], section_ids=["s1"], obligation_ids=[], budget={"ch": 50.0})
        violations = v.validate_transition(from_s, to_s)
        assert any("budget" in msg.lower() or "regression" in msg.lower() for msg in violations)

    def test_validate_transition_catches_non_productive(self):
        v = StateValidator(rules=StateValidator.default_rules(), strict=False)
        from_s = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=["o1"], budget={"ch": 100.0})
        to_s = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=["o1"], budget={"ch": 100.0})
        violations = v.validate_transition(from_s, to_s)
        assert any("productive" in msg.lower() for msg in violations)

    def test_validate_returns_list_type(self, basic_state):
        v = StateValidator(rules=StateValidator.default_rules(), strict=False)
        assert isinstance(v.validate(basic_state), list)

    def test_add_rule_then_validate(self, basic_state):
        v = StateValidator(rules=[], strict=False)
        v.add_rule(lambda s: "always bad")
        violations = v.validate(basic_state)
        assert "always bad" in violations[0]


# ════════════════════════════════════════════════════════════════════════════
# StateProjector
# ════════════════════════════════════════════════════════════════════════════

class TestStateProjector:

    def test_project_returns_dict(self, basic_state):
        p = StateProjector(dimensions=["covers", "budget"], weights={})
        assert isinstance(p.project(basic_state), dict)

    def test_project_covers_dimension(self, basic_state):
        p = StateProjector(dimensions=["covers"], weights={})
        result = p.project(basic_state)
        assert "covers" in result
        assert result["covers"] == list(basic_state.cover_ids)

    def test_project_obligations_dimension(self, basic_state):
        p = StateProjector(dimensions=["obligations"], weights={})
        result = p.project(basic_state)
        assert "obligations" in result
        assert isinstance(result["obligations"], list)

    def test_project_budget_dimension(self, basic_state):
        p = StateProjector(dimensions=["budget"], weights={})
        result = p.project(basic_state)
        assert "budget" in result
        assert isinstance(result["budget"], dict)

    def test_project_sections_dimension(self, basic_state):
        p = StateProjector(dimensions=["sections"], weights={})
        result = p.project(basic_state)
        assert "sections" in result
        assert set(result["sections"]) == set(basic_state.section_ids)

    def test_project_score_is_float(self, basic_state):
        p = StateProjector(dimensions=["score"], weights={})
        result = p.project(basic_state)
        assert "score" in result
        assert isinstance(result["score"], float)

    def test_project_unknown_dimension_silently_skipped(self, basic_state):
        p = StateProjector(dimensions=["covers", "NONEXISTENT_DIM_XYZ"], weights={})
        result = p.project(basic_state)
        assert "covers" in result
        assert "NONEXISTENT_DIM_XYZ" not in result

    def test_project_all_default_dimensions(self, basic_state):
        p = make_state_projector()
        result = p.project(basic_state)
        for dim in ["covers", "obligations", "budget", "sections", "score"]:
            assert dim in result

    def test_project_covers_returns_copy(self, basic_state):
        p = StateProjector(dimensions=["covers"], weights={})
        covers = p.project_covers(basic_state)
        covers.append("injected-cover")
        assert "injected-cover" not in basic_state.cover_ids

    def test_project_obligations_returns_list(self, basic_state):
        p = StateProjector(dimensions=["obligations"], weights={})
        obs = p.project_obligations(basic_state)
        assert isinstance(obs, list)
        assert obs == list(basic_state.obligation_ids)

    def test_project_budget_returns_dict_copy(self, basic_state):
        p = StateProjector(dimensions=["budget"], weights={})
        budget = p.project_budget(basic_state)
        assert isinstance(budget, dict)
        assert budget == basic_state.budget
        budget["injected"] = 999.0
        assert "injected" not in basic_state.budget

    def test_weighted_projection_returns_dict_of_numerics(self, basic_state):
        p = StateProjector(dimensions=["covers", "obligations", "budget", "score"], weights={"covers": 2.0})
        result = p.weighted_projection(basic_state)
        assert isinstance(result, dict)
        for v in result.values():
            assert isinstance(v, (int, float))

    def test_weighted_projection_scales_by_weight(self):
        state = _make_state(cover_ids=["c1", "c2"])
        p_unit = StateProjector(dimensions=["covers"], weights={})
        p_scaled = StateProjector(dimensions=["covers"], weights={"covers": 4.0})
        unit_val = p_unit.weighted_projection(state)["covers"]
        scaled_val = p_scaled.weighted_projection(state)["covers"]
        assert abs(scaled_val - 4.0 * unit_val) < 1e-9

    def test_weighted_projection_obligations_non_positive(self):
        state = _make_state(obligation_ids=["o1", "o2", "o3"])
        p = StateProjector(dimensions=["obligations"], weights={})
        result = p.weighted_projection(state)
        assert result["obligations"] <= 0.0

    def test_weighted_projection_empty_dimensions(self, basic_state):
        p = StateProjector(dimensions=[], weights={})
        assert p.weighted_projection(basic_state) == {}

    def test_make_state_projector_factory_default(self):
        p = make_state_projector()
        assert isinstance(p, StateProjector)
        assert len(p.dimensions) > 0

    def test_make_state_projector_custom_args(self):
        p = make_state_projector(dimensions=["covers", "score"], weights={"score": 2.0})
        assert p.dimensions == ["covers", "score"]
        assert p.weights["score"] == 2.0

    def test_project_budget_content_matches_state(self):
        state = _make_state(budget={"alpha": 10.0, "beta": 20.0})
        p = StateProjector(dimensions=["budget"], weights={})
        b = p.project_budget(state)
        assert b["alpha"] == 10.0
        assert b["beta"] == 20.0


# ════════════════════════════════════════════════════════════════════════════
# StateAggregator
# ════════════════════════════════════════════════════════════════════════════

class TestStateAggregator:

    def _agg(self, strategy="union", weights=None):
        return StateAggregator(aggregation_strategy=strategy, weights=weights or {})

    def test_aggregate_single_state(self):
        agg = self._agg()
        s = _make_state(cover_ids=["c1"], obligation_ids=["o1"])
        result = agg.aggregate([s])
        assert isinstance(result, SemanticControlState)
        assert "c1" in result.cover_ids

    def test_aggregate_empty_list_raises_value_error(self):
        agg = self._agg()
        with pytest.raises(ValueError):
            agg.aggregate([])

    def test_aggregate_union_covers(self):
        agg = self._agg(strategy="union")
        s1 = _make_state(cover_ids=["c1", "c2"])
        s2 = _make_state(cover_ids=["c2", "c3"])
        result = agg.aggregate([s1, s2])
        assert set(result.cover_ids) >= {"c1", "c2", "c3"}

    def test_aggregate_intersection_covers(self):
        agg = self._agg(strategy="intersection")
        s1 = _make_state(cover_ids=["c1", "c2"])
        s2 = _make_state(cover_ids=["c2", "c3"])
        result = agg.aggregate([s1, s2])
        assert "c2" in result.cover_ids
        assert "c1" not in result.cover_ids
        assert "c3" not in result.cover_ids

    def test_aggregate_returns_new_state_id(self):
        agg = self._agg()
        s1 = _make_state()
        s2 = _make_state()
        result = agg.aggregate([s1, s2])
        assert result.state_id not in {s1.state_id, s2.state_id}

    def test_aggregate_metadata_source_ids(self):
        agg = self._agg()
        s1 = _make_state()
        s2 = _make_state()
        result = agg.aggregate([s1, s2])
        assert "source_state_ids" in result.metadata
        for sid in [s1.state_id, s2.state_id]:
            assert sid in result.metadata["source_state_ids"]

    def test_aggregate_metadata_strategy(self):
        agg = self._agg(strategy="union")
        result = agg.aggregate([_make_state()])
        assert result.metadata.get("aggregation_strategy") == "union"

    def test_aggregate_merges_sections(self):
        agg = self._agg()
        s1 = _make_state(section_ids=["sec-1"])
        s2 = _make_state(section_ids=["sec-2"])
        result = agg.aggregate([s1, s2])
        assert "sec-1" in result.section_ids
        assert "sec-2" in result.section_ids

    def test_coverage_union_all_covers(self):
        agg = self._agg()
        s1 = _make_state(cover_ids=["a", "b"])
        s2 = _make_state(cover_ids=["b", "c"])
        s3 = _make_state(cover_ids=["d"])
        assert set(agg.coverage_union([s1, s2, s3])) == {"a", "b", "c", "d"}

    def test_coverage_union_deduplicates(self):
        agg = self._agg()
        s1 = _make_state(cover_ids=["x", "y"])
        s2 = _make_state(cover_ids=["x"])
        union = agg.coverage_union([s1, s2])
        assert union.count("x") == 1

    def test_coverage_union_empty_input(self):
        agg = self._agg()
        assert agg.coverage_union([]) == []

    def test_obligation_intersection_common_only(self):
        agg = self._agg()
        s1 = _make_state(obligation_ids=["o1", "o2"])
        s2 = _make_state(obligation_ids=["o2", "o3"])
        assert set(agg.obligation_intersection([s1, s2])) == {"o2"}

    def test_obligation_intersection_empty_when_disjoint(self):
        agg = self._agg()
        s1 = _make_state(obligation_ids=["o1"])
        s2 = _make_state(obligation_ids=["o2"])
        assert agg.obligation_intersection([s1, s2]) == []

    def test_obligation_intersection_empty_input(self):
        agg = self._agg()
        assert agg.obligation_intersection([]) == []

    def test_obligation_intersection_single_state_all_retained(self):
        agg = self._agg()
        s = _make_state(obligation_ids=["o1", "o2"])
        assert set(agg.obligation_intersection([s])) == {"o1", "o2"}

    def test_weighted_attainability_in_unit_interval(self):
        agg = self._agg()
        states = [
            _make_state(cover_ids=["c1"], section_ids=["s1"]),
            _make_state(cover_ids=["c1", "c2"], section_ids=["s1", "s2"]),
        ]
        score = agg.weighted_attainability(states)
        assert 0.0 <= score <= 1.0

    def test_weighted_attainability_empty_returns_zero(self):
        agg = self._agg()
        assert agg.weighted_attainability([]) == 0.0

    def test_weighted_attainability_shifts_with_weights(self):
        s_high = _make_state(cover_ids=["c1", "c2"], section_ids=["c1", "c2"])
        s_low = _make_state(cover_ids=[], section_ids=["s1", "s2"])
        agg_eq = self._agg(weights={})
        agg_hi = self._agg(weights={s_high.state_id: 10.0, s_low.state_id: 1.0})
        assert agg_hi.weighted_attainability([s_high, s_low]) >= \
               agg_eq.weighted_attainability([s_high, s_low])

    def test_aggregate_budgets_union_uses_mean(self):
        agg = self._agg(strategy="union")
        s1 = _make_state(budget={"ch": 100.0})
        s2 = _make_state(budget={"ch": 60.0})
        budget = agg.aggregate_budgets([s1, s2])
        assert abs(budget["ch"] - 80.0) < 1e-9

    def test_aggregate_budgets_intersection_uses_min(self):
        agg = self._agg(strategy="intersection")
        s1 = _make_state(budget={"ch": 100.0})
        s2 = _make_state(budget={"ch": 60.0})
        budget = agg.aggregate_budgets([s1, s2])
        assert abs(budget["ch"] - 60.0) < 1e-9

    def test_aggregate_budgets_missing_channel_defaults_zero(self):
        agg = self._agg(strategy="union")
        s1 = _make_state(budget={"ch_a": 100.0})
        s2 = _make_state(budget={"ch_b": 80.0})
        budget = agg.aggregate_budgets([s1, s2])
        assert "ch_a" in budget
        assert "ch_b" in budget


# ════════════════════════════════════════════════════════════════════════════
# StateDeltaComputer
# ════════════════════════════════════════════════════════════════════════════

class TestStateDeltaComputer:

    def test_compute_returns_state_delta(self):
        dc = StateDeltaComputer()
        s1 = _make_state(cover_ids=["c1"])
        s2 = _make_state(cover_ids=["c1", "c2"])
        assert isinstance(dc.compute(s1, s2), StateDelta)

    def test_compute_added_covers(self):
        dc = StateDeltaComputer()
        s1 = _make_state(cover_ids=["c1"])
        s2 = _make_state(cover_ids=["c1", "c2", "c3"])
        delta = dc.compute(s1, s2)
        assert "c2" in delta.added_covers
        assert "c3" in delta.added_covers
        assert "c1" not in delta.added_covers

    def test_compute_removed_covers(self):
        dc = StateDeltaComputer()
        s1 = _make_state(cover_ids=["c1", "c2"])
        s2 = _make_state(cover_ids=["c1"])
        delta = dc.compute(s1, s2)
        assert "c2" in delta.removed_covers
        assert "c1" not in delta.removed_covers

    def test_compute_resolved_obligations(self):
        dc = StateDeltaComputer()
        s1 = _make_state(obligation_ids=["o1", "o2"])
        s2 = _make_state(cover_ids=["c-new"], obligation_ids=["o2"])
        delta = dc.compute(s1, s2)
        assert "o1" in delta.resolved_obligations

    def test_compute_added_obligations(self):
        dc = StateDeltaComputer()
        s1 = _make_state(obligation_ids=["o1"])
        s2 = _make_state(cover_ids=["c-new"], obligation_ids=["o1", "o2"])
        delta = dc.compute(s1, s2)
        assert "o2" in delta.added_obligations

    def test_compute_budget_delta_decrease(self):
        dc = StateDeltaComputer()
        s1 = _make_state(budget={"ch": 100.0})
        s2 = _make_state(cover_ids=["c-new"], budget={"ch": 90.0})
        delta = dc.compute(s1, s2)
        assert abs(delta.budget_delta["ch"] - (-10.0)) < 1e-9

    def test_compute_budget_delta_new_channel(self):
        dc = StateDeltaComputer()
        s1 = _make_state(budget={"ch": 100.0})
        s2 = _make_state(cover_ids=["c-new"], budget={"ch": 100.0, "new_ch": 50.0})
        delta = dc.compute(s1, s2)
        assert abs(delta.budget_delta["new_ch"] - 50.0) < 1e-9

    def test_compute_positive_score_delta_on_cover_gain(self):
        dc = StateDeltaComputer()
        s1 = _make_state(cover_ids=[], section_ids=["s1"])
        s2 = _make_state(cover_ids=["s1"], section_ids=["s1"])
        delta = dc.compute(s1, s2)
        assert delta.score_delta > 0.0

    def test_compute_identical_states_zero_delta(self):
        dc = StateDeltaComputer()
        s = _make_state(cover_ids=["c1"], section_ids=["s1"])
        delta = dc.compute(s, s)
        assert len(delta.added_covers) == 0
        assert len(delta.removed_covers) == 0
        assert abs(delta.score_delta) < 1e-9

    def test_is_reversible_clean_delta(self):
        dc = StateDeltaComputer()
        delta = StateDelta(
            added_covers=("c2",), removed_covers=("c1",), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.1,
        )
        assert dc.is_reversible(delta) is True

    def test_is_reversible_overlap_covers_false(self):
        dc = StateDeltaComputer()
        delta = StateDelta(
            added_covers=("c1",), removed_covers=("c1",), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.0,
        )
        assert dc.is_reversible(delta) is False

    def test_is_reversible_overlap_sections_false(self):
        dc = StateDeltaComputer()
        delta = StateDelta(
            added_covers=(), removed_covers=(), added_sections=("s1",),
            removed_sections=("s1",), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.0,
        )
        assert dc.is_reversible(delta) is False

    def test_is_reversible_overlap_obligations_false(self):
        dc = StateDeltaComputer()
        delta = StateDelta(
            added_covers=(), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=("o1",), resolved_obligations=("o1",),
            budget_delta={}, score_delta=0.0,
        )
        assert dc.is_reversible(delta) is False

    def test_compose_deltas_single_element(self):
        dc = StateDeltaComputer()
        d = StateDelta(
            added_covers=("c2",), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={"ch": -5.0}, score_delta=0.1,
        )
        composed = dc.compose_deltas([d])
        assert "c2" in composed.added_covers
        assert abs(composed.budget_delta.get("ch", 0) - (-5.0)) < 1e-9

    def test_compose_deltas_two_elements(self):
        dc = StateDeltaComputer()
        d1 = StateDelta(
            added_covers=("c1",), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={"ch": -5.0}, score_delta=0.1,
        )
        d2 = StateDelta(
            added_covers=("c2",), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={"ch": -3.0}, score_delta=0.2,
        )
        composed = dc.compose_deltas([d1, d2])
        assert "c1" in composed.added_covers
        assert "c2" in composed.added_covers
        assert abs(composed.budget_delta.get("ch", 0) - (-8.0)) < 1e-9
        assert abs(composed.score_delta - 0.3) < 1e-9

    def test_compose_deltas_add_then_remove_cancels(self):
        dc = StateDeltaComputer()
        d1 = StateDelta(
            added_covers=("c1",), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.0,
        )
        d2 = StateDelta(
            added_covers=(), removed_covers=("c1",), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.0,
        )
        composed = dc.compose_deltas([d1, d2])
        assert "c1" not in composed.added_covers

    def test_compose_deltas_empty_raises_value_error(self):
        dc = StateDeltaComputer()
        with pytest.raises(ValueError):
            dc.compose_deltas([])

    def test_apply_delta_adds_covers(self):
        dc = StateDeltaComputer()
        s = _make_state(cover_ids=["c1"])
        delta = StateDelta(
            added_covers=("c2",), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.0,
        )
        new_s = dc.apply_delta(s, delta)
        assert "c1" in new_s.cover_ids
        assert "c2" in new_s.cover_ids

    def test_apply_delta_removes_covers(self):
        dc = StateDeltaComputer()
        s = _make_state(cover_ids=["c1", "c2"])
        delta = StateDelta(
            added_covers=(), removed_covers=("c1",), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.0,
        )
        new_s = dc.apply_delta(s, delta)
        assert "c1" not in new_s.cover_ids
        assert "c2" in new_s.cover_ids

    def test_apply_delta_does_not_mutate_source(self):
        dc = StateDeltaComputer()
        s = _make_state(cover_ids=["c1"])
        original_covers = list(s.cover_ids)
        delta = StateDelta(
            added_covers=("c2",), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.0,
        )
        dc.apply_delta(s, delta)
        assert list(s.cover_ids) == original_covers

    def test_apply_delta_adjusts_budget(self):
        dc = StateDeltaComputer()
        s = _make_state(budget={"ch": 100.0})
        delta = StateDelta(
            added_covers=(), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={"ch": -20.0}, score_delta=0.0,
        )
        new_s = dc.apply_delta(s, delta)
        assert abs(new_s.budget["ch"] - 80.0) < 1e-9

    def test_apply_delta_returns_new_state_id(self):
        dc = StateDeltaComputer()
        s = _make_state(cover_ids=["c1"])
        delta = StateDelta(
            added_covers=("c2",), removed_covers=(), added_sections=(),
            removed_sections=(), added_obligations=(), resolved_obligations=(),
            budget_delta={}, score_delta=0.0,
        )
        new_s = dc.apply_delta(s, delta)
        assert new_s.state_id != s.state_id


# ════════════════════════════════════════════════════════════════════════════
# StateEvent
# ════════════════════════════════════════════════════════════════════════════

class TestStateEvent:

    def test_to_dict_correct_values(self):
        event = StateEvent(
            event_id="evt-1", kind=StateEventKind.TRANSITION,
            state_id="st-1", payload={"info": "value"}, timestamp=1_700_000_000.0,
        )
        d = event.to_dict()
        assert d["event_id"] == "evt-1"
        assert d["kind"] == StateEventKind.TRANSITION.value
        assert d["state_id"] == "st-1"
        assert d["payload"] == {"info": "value"}
        assert d["timestamp"] == 1_700_000_000.0

    def test_event_is_immutable(self):
        event = StateEvent(event_id="e1", kind=StateEventKind.CREATED,
                           state_id="s1", payload={}, timestamp=1.0)
        with pytest.raises((AttributeError, TypeError)):
            event.event_id = "modified"  # type: ignore[misc]

    def test_state_event_kind_complete(self):
        expected = {"CREATED", "UPDATED", "SNAPSHOT_TAKEN", "TRANSITION",
                    "VALIDATED", "PROJECTED", "AGGREGATED", "RESET"}
        assert expected <= {k.name for k in StateEventKind}

    def test_state_event_kind_values_are_strings(self):
        for kind in StateEventKind:
            assert isinstance(kind.value, str)


# ════════════════════════════════════════════════════════════════════════════
# StateManager
# ════════════════════════════════════════════════════════════════════════════

class TestStateManager:

    def test_make_default_state_manager_returns_instance(self):
        mgr = make_default_state_manager()
        assert isinstance(mgr, StateManager)
        assert mgr.current_state is None
        assert mgr.history == []

    def test_initialize_sets_current_state(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        assert mgr.current_state is basic_state

    def test_initialize_creates_at_least_one_snapshot(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        assert len(mgr.history) >= 1

    def test_initialize_emits_created_event(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        assert len(mgr.event_bus.history(StateEventKind.CREATED)) >= 1

    def test_initialize_snapshot_labelled_initial(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        assert any(s.label == "initial" for s in mgr.history)

    def test_transition_valid_returns_true(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0})
        mgr.initialize(s0)
        assert mgr.transition(_valid_next_state(s0)) is True

    def test_transition_valid_updates_current_state(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0})
        mgr.initialize(s0)
        s1 = _valid_next_state(s0)
        mgr.transition(s1)
        assert mgr.current_state.state_id == s1.state_id

    def test_transition_valid_grows_history(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0})
        mgr.initialize(s0)
        before = len(mgr.history)
        mgr.transition(_valid_next_state(s0))
        assert len(mgr.history) > before

    def test_transition_valid_emits_transition_event(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0})
        mgr.initialize(s0)
        mgr.transition(_valid_next_state(s0))
        assert len(mgr.event_bus.history(StateEventKind.TRANSITION)) >= 1

    def test_transition_invalid_budget_returns_false(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0})
        mgr.initialize(s0)
        s_bad = _make_state(cover_ids=["c1", "c2"], section_ids=["s1"], obligation_ids=[], budget={"ch": 50.0})
        assert mgr.transition(s_bad) is False

    def test_transition_invalid_does_not_update_state(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0})
        mgr.initialize(s0)
        mgr.transition(_make_state(budget={"ch": -5.0}))
        assert mgr.current_state.state_id == s0.state_id

    def test_transition_invalid_emits_validated_event(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0})
        mgr.initialize(s0)
        mgr.transition(_make_state(budget={"ch": -1.0}))
        validated = mgr.event_bus.history(StateEventKind.VALIDATED)
        assert any(not e.payload.get("accepted", True) for e in validated)

    def test_rollback_one_step_returns_state(self, populated_manager):
        restored = populated_manager.rollback(steps=1)
        assert restored is not None
        assert isinstance(restored, SemanticControlState)

    def test_rollback_updates_current_state(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c1"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0})
        mgr.initialize(s0)
        mgr.transition(_valid_next_state(s0))
        before = mgr.current_state.state_id
        mgr.rollback(steps=1)
        assert isinstance(mgr.current_state, SemanticControlState)

    def test_rollback_insufficient_history_returns_none(self):
        mgr = make_default_state_manager()
        mgr.initialize(_make_state())
        assert mgr.rollback(steps=9999) is None

    def test_rollback_zero_steps_returns_none(self):
        mgr = make_default_state_manager()
        mgr.initialize(_make_state())
        assert mgr.rollback(steps=0) is None

    def test_take_snapshot_before_initialize_raises(self):
        mgr = make_default_state_manager()
        with pytest.raises(RuntimeError):
            mgr.take_snapshot()

    def test_take_snapshot_returns_state_snapshot(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        snap = mgr.take_snapshot(label="manual")
        assert isinstance(snap, StateSnapshot)
        assert snap.label == "manual"

    def test_take_snapshot_emits_event(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        mgr.event_bus.clear_history()
        mgr.take_snapshot(label="test")
        assert len(mgr.event_bus.history(StateEventKind.SNAPSHOT_TAKEN)) >= 1

    def test_get_snapshot_by_id(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        snap = mgr.take_snapshot(label="findable")
        retrieved = mgr.get_snapshot(snap.snapshot_id)
        assert retrieved is not None
        assert retrieved.snapshot_id == snap.snapshot_id

    def test_get_snapshot_missing_returns_none(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        assert mgr.get_snapshot("nonexistent-snapshot-id") is None

    def test_list_snapshots_returns_list(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        snaps = mgr.list_snapshots()
        assert isinstance(snaps, list)
        assert len(snaps) >= 1

    def test_list_snapshots_returns_copy(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        snaps = mgr.list_snapshots()
        snaps.clear()
        assert len(mgr.list_snapshots()) >= 1

    def test_diff_returns_state_delta(self, populated_manager):
        snaps = populated_manager.list_snapshots()
        assert len(snaps) >= 2
        delta = populated_manager.diff(snaps[0].snapshot_id, snaps[-1].snapshot_id)
        assert isinstance(delta, StateDelta)

    def test_diff_missing_snapshot_returns_none(self, populated_manager):
        snaps = populated_manager.list_snapshots()
        assert populated_manager.diff(snaps[0].snapshot_id, "fake-snap-id-xyz") is None

    def test_diff_both_missing_returns_none(self, populated_manager):
        assert populated_manager.diff("fake-a", "fake-b") is None

    def test_reset_sets_new_current_state(self, populated_manager):
        new_state = _make_state(cover_ids=["fresh-c"], budget={"ch": 50.0})
        populated_manager.reset(new_state)
        assert populated_manager.current_state.state_id == new_state.state_id

    def test_reset_emits_reset_event(self, populated_manager):
        populated_manager.reset(_make_state())
        assert len(populated_manager.event_bus.history(StateEventKind.RESET)) >= 1

    def test_status_uninitialized(self):
        mgr = make_default_state_manager()
        s = mgr.status()
        assert s["current_state_id"] is None
        assert s["health"] == "uninitialized"

    def test_status_after_initialize_has_required_keys(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        s = mgr.status()
        for key in ("current_state_id", "health", "history_length", "event_count",
                    "coverage_ratio", "attainability_score"):
            assert key in s

    def test_status_current_state_id_matches(self, basic_state):
        mgr = make_default_state_manager()
        mgr.initialize(basic_state)
        assert mgr.status()["current_state_id"] == basic_state.state_id

    def test_export_history_returns_list_of_dicts(self, populated_manager):
        history = populated_manager.export_history()
        assert isinstance(history, list)
        assert len(history) > 0
        for entry in history:
            assert isinstance(entry, dict)
            assert "snapshot_id" in entry

    def test_max_history_evicts_oldest(self):
        mgr = make_default_state_manager(max_history=3)
        s0 = _make_state(cover_ids=["c0"], section_ids=["s1"], budget={"ch": 100.0})
        mgr.initialize(s0)
        prev = s0
        for i in range(1, 12):
            si = _valid_next_state(prev, extra_cover=f"c{i}")
            mgr.transition(si)
            prev = si
        assert len(mgr.history) <= 3

    def test_version_is_string(self):
        assert isinstance(VERSION, str) and len(VERSION) > 0

    def test_default_max_history_positive_int(self):
        assert isinstance(DEFAULT_MAX_HISTORY, int) and DEFAULT_MAX_HISTORY > 0

    def test_default_aggregation_strategy_str(self):
        assert isinstance(DEFAULT_AGGREGATION_STRATEGY, str) and len(DEFAULT_AGGREGATION_STRATEGY) > 0

    def test_multiple_transitions_history_length(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c0"], section_ids=["s1"], budget={"ch": 100.0})
        mgr.initialize(s0)
        prev = s0
        n = 5
        for i in range(1, n + 1):
            si = _valid_next_state(prev, extra_cover=f"c{i}")
            mgr.transition(si)
            prev = si
        assert len(mgr.history) >= n + 1


# ════════════════════════════════════════════════════════════════════════════
# Integration tests
# ════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_CONTROLLER, reason="jugeo.orchestration.controller not available")
class TestIntegrationWithController:

    def test_state_manager_with_move_kind_metadata(self):
        mgr = make_default_state_manager()
        s0 = SemanticControlState(
            state_id=str(uuid.uuid4()),
            cover_ids=["cover-A"],
            context_ids=[],
            section_ids=["sec-1"],
            treaty_ids=[],
            obligation_ids=["obl-1"],
            channel_ids=[],
            budget={"trust": 100.0},
            timestamp=time.time(),
            metadata={"last_move_kind": MoveKind.VERIFY.value},
        )
        mgr.initialize(s0)
        assert mgr.status()["current_state_id"] == s0.state_id

    def test_greedy_control_name_stored_in_metadata(self):
        law = GreedyControl()
        s0 = _make_state(metadata={"control_law": law.name()})
        mgr = make_default_state_manager()
        mgr.initialize(s0)
        assert isinstance(mgr.current_state.metadata["control_law"], str)

    def test_all_move_kinds_serialisable_in_metadata(self):
        mgr = make_default_state_manager()
        for kind in MoveKind:
            s = _make_state(metadata={"move_kind": kind.value})
            mgr.reset(s)
            assert mgr.current_state.metadata["move_kind"] == kind.value

    def test_transition_chain_with_move_kind_tracking(self):
        mgr = make_default_state_manager()
        s0 = _make_state(cover_ids=["c0"], section_ids=["s1"], obligation_ids=[], budget={"ch": 100.0},
                         metadata={"move_kind": MoveKind.VERIFY.value})
        mgr.initialize(s0)
        s1 = SemanticControlState(
            state_id=str(uuid.uuid4()),
            cover_ids=["c0", "c1"],
            context_ids=[],
            section_ids=["s1"],
            treaty_ids=[],
            obligation_ids=[],
            channel_ids=[],
            budget={"ch": 100.0},
            timestamp=time.time(),
            metadata={"move_kind": MoveKind.CONSTRUCT.value},
        )
        assert mgr.transition(s1) is True
        assert mgr.current_state.metadata["move_kind"] == MoveKind.CONSTRUCT.value


@pytest.mark.skipif(not HAS_TRUST, reason="jugeo.evidence.trust not available")
class TestIntegrationWithTrust:

    def test_custom_validator_rule_checks_trust_level(self):
        valid_names = {lvl.name for lvl in TrustLevel}

        def trust_level_rule(state):
            level_name = state.metadata.get("trust_level")
            if level_name is not None and level_name not in valid_names:
                return f"Unknown trust_level: {level_name!r}"
            return None

        v = StateValidator(rules=[trust_level_rule], strict=False)
        bad_state = _make_state(metadata={"trust_level": "TOTALLY_INVALID_LEVEL_XYZ"})
        assert v.is_valid(bad_state) is False
        if valid_names:
            good_state = _make_state(metadata={"trust_level": next(iter(valid_names))})
            assert v.is_valid(good_state) is True

    def test_trust_tier_stored_in_state_metadata(self):
        mgr = make_default_state_manager()
        tier = TrustTier.DIRECT
        s0 = _make_state(metadata={"trust_tier": tier.name})
        mgr.initialize(s0)
        assert mgr.current_state.metadata["trust_tier"] == tier.name

    def test_trust_algebra_composition_in_metadata(self):
        algebra = TrustAlgebra()
        levels = list(TrustLevel)
        if len(levels) >= 2:
            composed = algebra.compose(levels[0], levels[1])
            s = _make_state(metadata={"composed_trust": composed.name})
            mgr = make_default_state_manager()
            mgr.initialize(s)
            assert mgr.current_state.metadata["composed_trust"] == composed.name


@pytest.mark.skipif(not HAS_FLEET, reason="jugeo.orchestration.fleet not available")
class TestIntegrationWithFleet:

    def test_aggregator_merges_fleet_member_states(self):
        agg = StateAggregator(aggregation_strategy="union", weights={})
        member_states = [
            _make_state(
                cover_ids=[f"cover-{i}"],
                section_ids=["sec-1"],
                obligation_ids=["obl-shared"],
                budget={"default": 50.0 + 10.0 * i},
            )
            for i in range(4)
        ]
        result = agg.aggregate(member_states)
        assert isinstance(result, SemanticControlState)
        for i in range(4):
            assert f"cover-{i}" in result.cover_ids

    def test_weighted_attainability_fleet_scenario(self):
        s_leader = _make_state(cover_ids=["c1", "c2", "c3"], section_ids=["s1", "s2", "s3"])
        followers = [_make_state(cover_ids=["c1"], section_ids=["s1", "s2", "s3"]) for _ in range(3)]
        weights = {s_leader.state_id: 5.0}
        agg = StateAggregator(aggregation_strategy="union", weights=weights)
        score = agg.weighted_attainability([s_leader] + followers)
        assert 0.0 <= score <= 1.0

    def test_state_manager_reset_for_fleet_rebalance(self):
        mgr = make_default_state_manager()
        mgr.initialize(_make_state(cover_ids=["c-fleet"], budget={"default": 100.0}))
        new_state = _make_state(cover_ids=["c-new-fleet"], budget={"default": 200.0})
        mgr.reset(new_state)
        assert mgr.current_state.state_id == new_state.state_id
        assert len(mgr.event_bus.history(StateEventKind.RESET)) >= 1
