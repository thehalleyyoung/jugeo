"""Comprehensive tests for jugeo.orchestration.semantic_control.models.

Covers all public classes and methods:
  - ControlLawKind, StateHealthStatus, ConvergenceMode enumerations
  - SemanticControlState construction, metrics, delta, snapshot, to_dict, health_status
  - StateDelta is_improving, magnitude, summary, to_dict
  - AdmissibleMove construction, validate, is_applicable, apply, net_value, to_dict
  - ControlLaw construction, select_move, evaluate, adapt, to_dict
  - ConvergenceCertificate construction, is_valid, is_expired, summary, to_dict
  - SemanticTrajectory construction, append, length, is_converging, export, replay,
    latest_state, score_history, try_issue_certificate
  - Integration tests with upstream modules (skipped when unavailable)

All integration-test imports are guarded with try/except; the corresponding test
functions use ``pytest.importorskip`` or ``pytest.mark.skipif`` so the suite
remains green even in minimal environments.
"""

from __future__ import annotations

import math
import time
import uuid
from pathlib import Path
import sys

# ---------------------------------------------------------------------------
# Canonical sys.path bootstrap (must appear at the top of every test file)
# ---------------------------------------------------------------------------

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---------------------------------------------------------------------------
# Primary import under test
# ---------------------------------------------------------------------------

import pytest

from jugeo.orchestration.semantic_control.models import (
    CERTIFICATE_TTL,
    CONVERGENCE_THRESHOLD,
    MIN_TRAJECTORY_LENGTH,
    OBLIGATION_STALL_THRESHOLD,
    STRONG_CONVERGENCE_WINDOW,
    AdmissibleMove,
    ControlLaw,
    ControlLawKind,
    ConvergenceCertificate,
    ConvergenceMode,
    MoveKind,
    SemanticControlState,
    SemanticTrajectory,
    StateDelta,
    StateHealthStatus,
)

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.controller import (
        MoveKind as ControllerMoveKind,
        GreedyControl,
        OrchestratorState,
    )
    _HAS_CONTROLLER = True
except Exception:
    _HAS_CONTROLLER = False

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember, CompetitiveSearch
    _HAS_FLEET = True
except Exception:
    _HAS_FLEET = False

try:
    from jugeo.geometry.descent import DescentEngine, GluingData, DescentResult, GlobalSection
    _HAS_DESCENT = True
except Exception:
    _HAS_DESCENT = False


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def basic_state() -> SemanticControlState:
    """A SemanticControlState with a variety of non-empty fields."""
    return SemanticControlState(
        state_id="test-state-001",
        cover_ids=["cover-A", "cover-B", "cover-C"],
        context_ids=["ctx-1", "ctx-2"],
        section_ids=["sec-1", "sec-2", "sec-3", "sec-4", "sec-5"],
        treaty_ids=["treaty-alpha"],
        obligation_ids=["obl-1"],
        channel_ids=["ch-solver", "ch-runtime"],
        budget={"solver": 200.0, "runtime": 100.0, "copilot": 50.0},
        metadata={"source": "fixture", "version": "1"},
    )


@pytest.fixture
def converged_state() -> SemanticControlState:
    """A state whose coverage_ratio >= CONVERGENCE_THRESHOLD and no obligations."""
    # section_ids=9, cover_ids=10, obligation_ids=0  →  9/10 = 0.90 ≥ threshold
    return SemanticControlState(
        state_id="converged-001",
        cover_ids=[f"c{i}" for i in range(10)],
        context_ids=[],
        section_ids=[f"s{i}" for i in range(9)],
        treaty_ids=[],
        obligation_ids=[],
        channel_ids=[],
        budget={"solver": 100.0},
    )


@pytest.fixture
def healthy_state() -> SemanticControlState:
    """A state with attainability_score ≥ 0.7, below CONVERGED threshold."""
    # cov=0.5, no obligations, no treaties → score=0.75
    return SemanticControlState(
        state_id="healthy-001",
        cover_ids=[f"c{i}" for i in range(10)],
        context_ids=[],
        section_ids=[f"s{i}" for i in range(5)],
        treaty_ids=[],
        obligation_ids=[],
        channel_ids=[],
        budget={},
    )


@pytest.fixture
def degraded_state() -> SemanticControlState:
    """A state with attainability_score in [0.4, 0.7)."""
    # cov=2/6≈0.333, treaty_health=0.5 (no treaties + obligations),
    # ob_deficit=exp(-0.1)≈0.905  →  score≈0.497
    return SemanticControlState(
        state_id="degraded-001",
        cover_ids=["c1", "c2", "c3", "c4", "c5"],
        context_ids=[],
        section_ids=["s1", "s2"],
        treaty_ids=[],
        obligation_ids=["o1"],
        channel_ids=[],
        budget={},
    )


@pytest.fixture
def stalled_state() -> SemanticControlState:
    """A state with attainability_score < 0.4 (no sections, multiple obligations)."""
    # cov=0, treaty_health=0.5, ob_deficit=exp(-0.2)≈0.819 → score≈0.314
    return SemanticControlState(
        state_id="stalled-001",
        cover_ids=["c1", "c2", "c3"],
        context_ids=[],
        section_ids=[],
        treaty_ids=[],
        obligation_ids=["o1", "o2"],
        channel_ids=[],
        budget={},
    )


@pytest.fixture
def basic_move() -> AdmissibleMove:
    """An AdmissibleMove with no preconditions (always applicable)."""
    return AdmissibleMove(
        move_id="move-0001",
        kind=next(iter(MoveKind)),
        preconditions=[],
        postconditions=["new-cover-X"],
        cost=1.5,
        priority=0.8,
        expected_gain=4.0,
        trust_requirement="PROVISIONAL",
        metadata={"test": True},
    )


@pytest.fixture
def guarded_move(basic_state: SemanticControlState) -> AdmissibleMove:
    """An AdmissibleMove whose precondition IS satisfied by basic_state."""
    return AdmissibleMove(
        move_id="move-guarded",
        kind=next(iter(MoveKind)),
        preconditions=["cover-A"],   # present in basic_state.cover_ids
        postconditions=["new-cover-Y"],
        cost=1.0,
        priority=0.5,
        expected_gain=3.0,
        trust_requirement="PROVISIONAL",
    )


@pytest.fixture
def blocked_move() -> AdmissibleMove:
    """An AdmissibleMove whose precondition is NOT satisfied by any default state."""
    return AdmissibleMove(
        move_id="move-blocked",
        kind=next(iter(MoveKind)),
        preconditions=["cover-NONEXISTENT-XYZ"],
        postconditions=[],
        cost=0.5,
        priority=0.5,
        expected_gain=2.0,
        trust_requirement="PROVISIONAL",
    )


@pytest.fixture
def basic_law() -> ControlLaw:
    """A ControlLaw with GREEDY kind and a default weight parameter."""
    return ControlLaw(
        law_id="law-greedy-001",
        name="test-greedy",
        kind=ControlLawKind.GREEDY,
        parameters={"weight": 0.7},
    )


@pytest.fixture
def sample_trajectory() -> SemanticTrajectory:
    """A SemanticTrajectory with 4 states showing an improving score trend."""
    traj = SemanticTrajectory(trajectory_id="traj-test-001")

    # Build 4 states with strictly improving attainability scores.
    states = [
        # State 0: very low score
        SemanticControlState(
            state_id="ts-0",
            cover_ids=["c1", "c2", "c3"],
            section_ids=[],
            obligation_ids=["o1", "o2", "o3"],
            budget={"solver": 100.0},
        ),
        # State 1: slightly better
        SemanticControlState(
            state_id="ts-1",
            cover_ids=["c1", "c2", "c3"],
            section_ids=["s1"],
            obligation_ids=["o1", "o2"],
            budget={"solver": 90.0},
        ),
        # State 2: noticeably better
        SemanticControlState(
            state_id="ts-2",
            cover_ids=["c1", "c2", "c3"],
            section_ids=["s1", "s2"],
            obligation_ids=["o1"],
            budget={"solver": 80.0},
        ),
        # State 3: fully resolved
        SemanticControlState(
            state_id="ts-3",
            cover_ids=["c1", "c2", "c3"],
            section_ids=["s1", "s2", "s3"],
            obligation_ids=[],
            budget={"solver": 70.0},
        ),
    ]
    moves = [
        AdmissibleMove(
            move_id=f"tm-{i}",
            kind=next(iter(MoveKind)),
            preconditions=[],
            postconditions=[],
            cost=float(i + 1),
            priority=0.5,
            expected_gain=float(i + 2),
        )
        for i in range(3)
    ]

    traj.append(states[0], None)
    for state, move in zip(states[1:], moves):
        traj.append(state, move)

    return traj


# ===========================================================================
# Section 1 — Enum tests
# ===========================================================================


class TestControlLawKind:
    def test_has_greedy(self) -> None:
        assert ControlLawKind.GREEDY.value == "greedy"

    def test_has_lookahead(self) -> None:
        assert ControlLawKind.LOOKAHEAD.value == "lookahead"

    def test_has_balanced(self) -> None:
        assert ControlLawKind.BALANCED.value == "balanced"

    def test_has_adaptive(self) -> None:
        assert ControlLawKind.ADAPTIVE.value == "adaptive"

    def test_has_custom(self) -> None:
        assert ControlLawKind.CUSTOM.value == "custom"

    def test_all_values_distinct(self) -> None:
        values = [k.value for k in ControlLawKind]
        assert len(values) == len(set(values))

    def test_is_enum(self) -> None:
        import enum
        assert issubclass(ControlLawKind, enum.Enum)


class TestStateHealthStatus:
    def test_has_healthy(self) -> None:
        assert StateHealthStatus.HEALTHY.value == "healthy"

    def test_has_degraded(self) -> None:
        assert StateHealthStatus.DEGRADED.value == "degraded"

    def test_has_stalled(self) -> None:
        assert StateHealthStatus.STALLED.value == "stalled"

    def test_has_diverged(self) -> None:
        assert StateHealthStatus.DIVERGED.value == "diverged"

    def test_has_converged(self) -> None:
        assert StateHealthStatus.CONVERGED.value == "converged"

    def test_all_values_distinct(self) -> None:
        values = [s.value for s in StateHealthStatus]
        assert len(values) == len(set(values))


class TestConvergenceMode:
    def test_has_strong(self) -> None:
        assert ConvergenceMode.STRONG.value == "strong"

    def test_has_weak(self) -> None:
        assert ConvergenceMode.WEAK.value == "weak"

    def test_has_approximate(self) -> None:
        assert ConvergenceMode.APPROXIMATE.value == "approximate"

    def test_all_values_distinct(self) -> None:
        values = [m.value for m in ConvergenceMode]
        assert len(values) == len(set(values))


# ===========================================================================
# Section 2 — SemanticControlState construction
# ===========================================================================


class TestSemanticControlStateConstruction:
    def test_default_construction(self) -> None:
        """A state created with no arguments should have sensible defaults."""
        state = SemanticControlState()
        assert isinstance(state.state_id, str)
        assert state.state_id != ""
        assert state.cover_ids == []
        assert state.context_ids == []
        assert state.section_ids == []
        assert state.treaty_ids == []
        assert state.obligation_ids == []
        assert state.channel_ids == []
        assert isinstance(state.budget, dict)
        assert isinstance(state.timestamp, float)
        assert state.timestamp > 0

    def test_default_state_id_is_uuid(self) -> None:
        state = SemanticControlState()
        parsed = uuid.UUID(state.state_id)
        assert str(parsed) == state.state_id

    def test_two_defaults_have_distinct_ids(self) -> None:
        s1, s2 = SemanticControlState(), SemanticControlState()
        assert s1.state_id != s2.state_id

    def test_custom_fields(self, basic_state: SemanticControlState) -> None:
        assert basic_state.state_id == "test-state-001"
        assert basic_state.cover_ids == ["cover-A", "cover-B", "cover-C"]
        assert basic_state.section_ids == ["sec-1", "sec-2", "sec-3", "sec-4", "sec-5"]
        assert basic_state.treaty_ids == ["treaty-alpha"]
        assert basic_state.obligation_ids == ["obl-1"]
        assert basic_state.channel_ids == ["ch-solver", "ch-runtime"]
        assert basic_state.metadata["source"] == "fixture"


# ===========================================================================
# Section 3 — SemanticControlState.coverage_ratio
# ===========================================================================


class TestCoverageRatio:
    """coverage_ratio = len(section_ids) / max(1, len(cover_ids) + len(obligation_ids))"""

    def test_all_empty_returns_zero(self) -> None:
        state = SemanticControlState()
        assert state.coverage_ratio() == 0.0

    def test_single_section_single_cover(self) -> None:
        state = SemanticControlState(
            section_ids=["s1"],
            cover_ids=["c1"],
        )
        # 1 / max(1, 1+0) = 1.0
        assert state.coverage_ratio() == pytest.approx(1.0)

    def test_more_sections_than_denominator_capped_at_one(self) -> None:
        state = SemanticControlState(
            section_ids=["s1", "s2", "s3", "s4", "s5"],
            cover_ids=["c1"],
            obligation_ids=[],
        )
        # 5 / 1 = 5 → capped at 1.0
        assert state.coverage_ratio() == pytest.approx(1.0)

    def test_obligations_increase_denominator(self) -> None:
        state = SemanticControlState(
            section_ids=["s1", "s2"],
            cover_ids=["c1", "c2", "c3"],
            obligation_ids=["o1"],
        )
        # 2 / max(1, 3+1) = 2/4 = 0.5
        assert state.coverage_ratio() == pytest.approx(0.5)

    def test_nine_sections_ten_covers(self) -> None:
        state = SemanticControlState(
            section_ids=[f"s{i}" for i in range(9)],
            cover_ids=[f"c{i}" for i in range(10)],
            obligation_ids=[],
        )
        # 9 / 10 = 0.9 == CONVERGENCE_THRESHOLD
        assert state.coverage_ratio() == pytest.approx(CONVERGENCE_THRESHOLD)

    def test_returns_float(self) -> None:
        state = SemanticControlState(section_ids=["s1"], cover_ids=["c1"])
        assert isinstance(state.coverage_ratio(), float)

    def test_value_never_exceeds_one(self) -> None:
        for n_sections in range(1, 20):
            state = SemanticControlState(
                section_ids=[f"s{i}" for i in range(n_sections)],
                cover_ids=["c1"],
            )
            assert state.coverage_ratio() <= 1.0


# ===========================================================================
# Section 4 — SemanticControlState.attainability_score
# ===========================================================================


class TestAttainabilityScore:
    def test_always_in_unit_interval(self, basic_state: SemanticControlState) -> None:
        score = basic_state.attainability_score()
        assert 0.0 <= score <= 1.0

    def test_returns_float(self, basic_state: SemanticControlState) -> None:
        assert isinstance(basic_state.attainability_score(), float)

    def test_no_obligations_no_treaties_high_coverage_gives_high_score(self) -> None:
        state = SemanticControlState(
            section_ids=[f"s{i}" for i in range(10)],
            cover_ids=["c1"],
            obligation_ids=[],
            treaty_ids=[],
        )
        assert state.attainability_score() >= 0.7

    def test_many_obligations_reduces_score(self) -> None:
        low_obs = SemanticControlState(
            section_ids=["s1", "s2"],
            cover_ids=["c1"],
            obligation_ids=["o1"],
        )
        high_obs = SemanticControlState(
            section_ids=["s1", "s2"],
            cover_ids=["c1"],
            obligation_ids=[f"o{i}" for i in range(20)],
        )
        assert high_obs.attainability_score() < low_obs.attainability_score()

    def test_empty_state_score_is_in_range(self) -> None:
        state = SemanticControlState()
        assert 0.0 <= state.attainability_score() <= 1.0

    def test_converged_state_has_high_score(
        self, converged_state: SemanticControlState
    ) -> None:
        # A converged state has coverage ≥ 0.9 and no obligations.
        assert converged_state.attainability_score() >= 0.5


# ===========================================================================
# Section 5 — SemanticControlState.is_admissible
# ===========================================================================


class TestIsAdmissible:
    def test_empty_preconditions_always_admissible(
        self, basic_state: SemanticControlState, basic_move: AdmissibleMove
    ) -> None:
        assert basic_state.is_admissible(basic_move)

    def test_satisfied_cover_id_precondition(
        self, basic_state: SemanticControlState, guarded_move: AdmissibleMove
    ) -> None:
        # "cover-A" IS in basic_state.cover_ids
        assert basic_state.is_admissible(guarded_move)

    def test_unsatisfied_precondition_returns_false(
        self, basic_state: SemanticControlState, blocked_move: AdmissibleMove
    ) -> None:
        assert not basic_state.is_admissible(blocked_move)

    def test_precondition_in_context_ids_is_satisfied(self) -> None:
        state = SemanticControlState(context_ids=["ctx-X"])
        move = AdmissibleMove(
            move_id="m1",
            kind=next(iter(MoveKind)),
            preconditions=["ctx-X"],
        )
        assert state.is_admissible(move)

    def test_precondition_in_section_ids_is_satisfied(self) -> None:
        state = SemanticControlState(section_ids=["sec-Y"])
        move = AdmissibleMove(
            move_id="m2",
            kind=next(iter(MoveKind)),
            preconditions=["sec-Y"],
        )
        assert state.is_admissible(move)

    def test_precondition_in_treaty_ids_is_satisfied(self) -> None:
        state = SemanticControlState(treaty_ids=["treaty-Z"])
        move = AdmissibleMove(
            move_id="m3",
            kind=next(iter(MoveKind)),
            preconditions=["treaty-Z"],
        )
        assert state.is_admissible(move)

    def test_precondition_in_obligation_ids_is_satisfied(self) -> None:
        state = SemanticControlState(obligation_ids=["obl-W"])
        move = AdmissibleMove(
            move_id="m4",
            kind=next(iter(MoveKind)),
            preconditions=["obl-W"],
        )
        assert state.is_admissible(move)

    def test_multiple_preconditions_all_must_be_satisfied(self) -> None:
        state = SemanticControlState(
            cover_ids=["c1"],
            section_ids=["s1"],
        )
        move_all_ok = AdmissibleMove(
            move_id="m-ok",
            kind=next(iter(MoveKind)),
            preconditions=["c1", "s1"],
        )
        move_one_missing = AdmissibleMove(
            move_id="m-fail",
            kind=next(iter(MoveKind)),
            preconditions=["c1", "missing-id"],
        )
        assert state.is_admissible(move_all_ok)
        assert not state.is_admissible(move_one_missing)


# ===========================================================================
# Section 6 — SemanticControlState.delta_from
# ===========================================================================


class TestDeltaFrom:
    def test_returns_state_delta_type(self, basic_state: SemanticControlState) -> None:
        other = basic_state.snapshot()
        delta = basic_state.delta_from(other)
        assert isinstance(delta, StateDelta)

    def test_identical_states_produce_empty_delta(
        self, basic_state: SemanticControlState
    ) -> None:
        other = basic_state.snapshot()
        delta = basic_state.delta_from(other)
        assert delta.added_covers == ()
        assert delta.removed_covers == ()
        assert delta.added_sections == ()
        assert delta.resolved_obligations == ()

    def test_added_covers_detected(self) -> None:
        old = SemanticControlState(cover_ids=["c1"])
        new = SemanticControlState(cover_ids=["c1", "c2"])
        delta = new.delta_from(old)
        assert "c2" in delta.added_covers
        assert "c1" not in delta.added_covers

    def test_removed_covers_detected(self) -> None:
        old = SemanticControlState(cover_ids=["c1", "c2"])
        new = SemanticControlState(cover_ids=["c1"])
        delta = new.delta_from(old)
        assert "c2" in delta.removed_covers

    def test_resolved_obligations_detected(self) -> None:
        old = SemanticControlState(obligation_ids=["o1", "o2"])
        new = SemanticControlState(obligation_ids=["o1"])
        delta = new.delta_from(old)
        assert "o2" in delta.resolved_obligations

    def test_score_delta_is_float(self, basic_state: SemanticControlState) -> None:
        other = basic_state.snapshot()
        delta = basic_state.delta_from(other)
        assert isinstance(delta.score_delta, float)

    def test_improving_transition_has_positive_score_delta(self) -> None:
        low = SemanticControlState(
            cover_ids=["c1", "c2", "c3"],
            section_ids=[],
            obligation_ids=["o1", "o2"],
        )
        high = SemanticControlState(
            cover_ids=["c1", "c2", "c3"],
            section_ids=["s1", "s2", "s3"],
            obligation_ids=[],
        )
        delta = high.delta_from(low)
        assert delta.score_delta > 0.0


# ===========================================================================
# Section 7 — SemanticControlState.snapshot
# ===========================================================================


class TestSnapshot:
    def test_returns_new_object(self, basic_state: SemanticControlState) -> None:
        snap = basic_state.snapshot()
        assert snap is not basic_state

    def test_state_id_preserved(self, basic_state: SemanticControlState) -> None:
        assert basic_state.snapshot().state_id == basic_state.state_id

    def test_cover_ids_independent(self, basic_state: SemanticControlState) -> None:
        snap = basic_state.snapshot()
        snap.cover_ids.append("new-cover")
        assert "new-cover" not in basic_state.cover_ids

    def test_budget_deep_copy(self, basic_state: SemanticControlState) -> None:
        snap = basic_state.snapshot()
        snap.budget["solver"] = 999.0
        assert basic_state.budget["solver"] != 999.0

    def test_metadata_deep_copy(self, basic_state: SemanticControlState) -> None:
        snap = basic_state.snapshot()
        snap.metadata["injected"] = True
        assert "injected" not in basic_state.metadata

    def test_all_list_fields_are_copies(self, basic_state: SemanticControlState) -> None:
        snap = basic_state.snapshot()
        for field_name in (
            "cover_ids", "context_ids", "section_ids",
            "treaty_ids", "obligation_ids", "channel_ids",
        ):
            orig = getattr(basic_state, field_name)
            copy_ = getattr(snap, field_name)
            assert orig == copy_
            assert orig is not copy_


# ===========================================================================
# Section 8 — SemanticControlState.to_dict
# ===========================================================================


class TestStateToDice:
    _EXPECTED_KEYS = {
        "state_id", "cover_ids", "context_ids", "section_ids",
        "treaty_ids", "obligation_ids", "channel_ids",
        "budget", "timestamp", "metadata",
    }

    def test_returns_dict(self, basic_state: SemanticControlState) -> None:
        assert isinstance(basic_state.to_dict(), dict)

    def test_contains_all_expected_keys(self, basic_state: SemanticControlState) -> None:
        d = basic_state.to_dict()
        assert self._EXPECTED_KEYS.issubset(d.keys())

    def test_state_id_value(self, basic_state: SemanticControlState) -> None:
        assert basic_state.to_dict()["state_id"] == basic_state.state_id

    def test_cover_ids_value(self, basic_state: SemanticControlState) -> None:
        assert basic_state.to_dict()["cover_ids"] == basic_state.cover_ids

    def test_budget_value(self, basic_state: SemanticControlState) -> None:
        assert basic_state.to_dict()["budget"] == basic_state.budget


# ===========================================================================
# Section 9 — SemanticControlState.health_status
# ===========================================================================


class TestHealthStatus:
    def test_converged_state_returns_converged(
        self, converged_state: SemanticControlState
    ) -> None:
        assert converged_state.health_status() == StateHealthStatus.CONVERGED

    def test_healthy_state_returns_healthy(
        self, healthy_state: SemanticControlState
    ) -> None:
        assert healthy_state.health_status() == StateHealthStatus.HEALTHY

    def test_degraded_state_returns_degraded(
        self, degraded_state: SemanticControlState
    ) -> None:
        assert degraded_state.health_status() == StateHealthStatus.DEGRADED

    def test_stalled_state_returns_stalled(
        self, stalled_state: SemanticControlState
    ) -> None:
        assert stalled_state.health_status() == StateHealthStatus.STALLED

    def test_too_many_obligations_returns_stalled(self) -> None:
        state = SemanticControlState(
            section_ids=["s1"],
            cover_ids=["c1"],
            obligation_ids=[f"o{i}" for i in range(OBLIGATION_STALL_THRESHOLD + 1)],
        )
        assert state.health_status() == StateHealthStatus.STALLED

    def test_returns_state_health_status_enum(
        self, basic_state: SemanticControlState
    ) -> None:
        result = basic_state.health_status()
        assert isinstance(result, StateHealthStatus)

    def test_high_coverage_with_obligations_is_not_converged(self) -> None:
        state = SemanticControlState(
            section_ids=[f"s{i}" for i in range(9)],
            cover_ids=[f"c{i}" for i in range(10)],
            obligation_ids=["o1"],  # ← prevents CONVERGED
        )
        assert state.health_status() != StateHealthStatus.CONVERGED


# ===========================================================================
# Section 10 — StateDelta
# ===========================================================================


class TestStateDelta:
    def _make_delta(
        self,
        *,
        score_delta: float = 0.0,
        added_covers: tuple[str, ...] = (),
        removed_covers: tuple[str, ...] = (),
        resolved_obligations: tuple[str, ...] = (),
        added_obligations: tuple[str, ...] = (),
    ) -> StateDelta:
        return StateDelta(
            added_covers=added_covers,
            removed_covers=removed_covers,
            added_sections=(),
            removed_sections=(),
            added_obligations=added_obligations,
            resolved_obligations=resolved_obligations,
            budget_delta={},
            score_delta=score_delta,
        )

    def test_is_improving_positive_score_delta(self) -> None:
        delta = self._make_delta(score_delta=0.1)
        assert delta.is_improving() is True

    def test_is_not_improving_zero_score_delta(self) -> None:
        delta = self._make_delta(score_delta=0.0)
        assert delta.is_improving() is False

    def test_is_not_improving_negative_score_delta(self) -> None:
        delta = self._make_delta(score_delta=-0.5)
        assert delta.is_improving() is False

    def test_magnitude_nonzero_for_changes(self) -> None:
        delta = self._make_delta(added_covers=("c1",), score_delta=0.1)
        assert delta.magnitude() > 0.0

    def test_magnitude_zero_for_empty_delta(self) -> None:
        delta = self._make_delta()
        assert delta.magnitude() == pytest.approx(0.0, abs=1e-9)

    def test_magnitude_in_open_unit_interval(self) -> None:
        delta = self._make_delta(
            added_covers=("c1", "c2"),
            resolved_obligations=("o1",),
            score_delta=0.3,
        )
        assert 0.0 < delta.magnitude() <= 1.0

    def test_summary_returns_string(self) -> None:
        delta = self._make_delta(score_delta=0.2)
        assert isinstance(delta.summary(), str)

    def test_summary_contains_direction_arrow(self) -> None:
        improving = self._make_delta(score_delta=0.1)
        not_improving = self._make_delta(score_delta=-0.1)
        assert "↑" in improving.summary()
        assert "↓" in not_improving.summary()

    def test_to_dict_keys(self) -> None:
        delta = self._make_delta(score_delta=0.1)
        d = delta.to_dict()
        for key in (
            "added_covers", "removed_covers", "added_sections",
            "removed_sections", "added_obligations", "resolved_obligations",
            "budget_delta", "score_delta",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_score_delta_value(self) -> None:
        delta = self._make_delta(score_delta=0.42)
        assert delta.to_dict()["score_delta"] == pytest.approx(0.42)

    def test_delta_is_frozen(self) -> None:
        """Assigning to a frozen dataclass attribute must raise."""
        delta = self._make_delta()
        with pytest.raises((TypeError, AttributeError)):
            delta.score_delta = 99.9  # type: ignore[misc]

    def test_delta_from_state_pair_is_improving(self) -> None:
        low = SemanticControlState(
            section_ids=[], cover_ids=["c1"], obligation_ids=["o1"]
        )
        high = SemanticControlState(
            section_ids=["s1"], cover_ids=["c1"], obligation_ids=[]
        )
        delta = high.delta_from(low)
        assert delta.is_improving()


# ===========================================================================
# Section 11 — AdmissibleMove construction and validation
# ===========================================================================


class TestAdmissibleMoveConstruction:
    def test_default_move_has_uuid_id(self) -> None:
        m = AdmissibleMove()
        parsed = uuid.UUID(m.move_id)
        assert str(parsed) == m.move_id

    def test_custom_move_id(self, basic_move: AdmissibleMove) -> None:
        assert basic_move.move_id == "move-0001"

    def test_cost_field(self, basic_move: AdmissibleMove) -> None:
        assert basic_move.cost == pytest.approx(1.5)

    def test_expected_gain_field(self, basic_move: AdmissibleMove) -> None:
        assert basic_move.expected_gain == pytest.approx(4.0)

    def test_trust_requirement_field(self, basic_move: AdmissibleMove) -> None:
        assert basic_move.trust_requirement == "PROVISIONAL"


class TestAdmissibleMoveValidate:
    def test_valid_move_returns_no_errors(self, basic_move: AdmissibleMove) -> None:
        assert basic_move.validate() == []

    def test_negative_cost_is_invalid(self) -> None:
        m = AdmissibleMove(move_id="m", kind=next(iter(MoveKind)), cost=-0.1)
        errors = m.validate()
        assert any("cost" in e for e in errors)

    def test_priority_above_one_is_invalid(self) -> None:
        m = AdmissibleMove(
            move_id="m", kind=next(iter(MoveKind)), priority=1.5
        )
        errors = m.validate()
        assert any("priority" in e for e in errors)

    def test_priority_below_zero_is_invalid(self) -> None:
        m = AdmissibleMove(
            move_id="m", kind=next(iter(MoveKind)), priority=-0.1
        )
        errors = m.validate()
        assert any("priority" in e for e in errors)

    def test_infinite_expected_gain_is_invalid(self) -> None:
        m = AdmissibleMove(
            move_id="m", kind=next(iter(MoveKind)), expected_gain=math.inf
        )
        errors = m.validate()
        assert any("expected_gain" in e for e in errors)

    def test_nan_expected_gain_is_invalid(self) -> None:
        m = AdmissibleMove(
            move_id="m", kind=next(iter(MoveKind)), expected_gain=math.nan
        )
        errors = m.validate()
        assert any("expected_gain" in e for e in errors)

    def test_empty_move_id_is_invalid(self) -> None:
        m = AdmissibleMove(move_id="", kind=next(iter(MoveKind)))
        errors = m.validate()
        assert any("move_id" in e for e in errors)

    def test_validate_returns_list(self, basic_move: AdmissibleMove) -> None:
        assert isinstance(basic_move.validate(), list)


# ===========================================================================
# Section 12 — AdmissibleMove.is_applicable
# ===========================================================================


class TestAdmissibleMoveIsApplicable:
    def test_no_preconditions_always_applicable(
        self, basic_state: SemanticControlState, basic_move: AdmissibleMove
    ) -> None:
        assert basic_move.is_applicable(basic_state)

    def test_satisfied_precondition_applicable(
        self, basic_state: SemanticControlState, guarded_move: AdmissibleMove
    ) -> None:
        assert guarded_move.is_applicable(basic_state)

    def test_unsatisfied_precondition_not_applicable(
        self, basic_state: SemanticControlState, blocked_move: AdmissibleMove
    ) -> None:
        assert not blocked_move.is_applicable(basic_state)


# ===========================================================================
# Section 13 — AdmissibleMove.apply
# ===========================================================================


class TestAdmissibleMoveApply:
    def test_extend_cover_kind_appends_postconditions_to_cover_ids(self) -> None:
        """kind=extend_cover → postconditions land in cover_ids."""
        state = SemanticControlState(cover_ids=["c1"])
        move = AdmissibleMove(
            move_id="m",
            kind=MoveKind.EXTEND_COVER,
            preconditions=[],
            postconditions=["c2", "c3"],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        successor = move.apply(state)
        assert "c2" in successor.cover_ids
        assert "c3" in successor.cover_ids

    def test_discharge_obligation_removes_from_obligation_ids(self) -> None:
        state = SemanticControlState(obligation_ids=["o1", "o2"])
        move = AdmissibleMove(
            move_id="m",
            kind=MoveKind.DISCHARGE_OBLIGATION,
            preconditions=[],
            postconditions=["o1"],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        successor = move.apply(state)
        assert "o1" not in successor.obligation_ids
        assert "o2" in successor.obligation_ids

    def test_bind_treaty_appends_to_treaty_ids(self) -> None:
        state = SemanticControlState(treaty_ids=[])
        move = AdmissibleMove(
            move_id="m",
            kind=MoveKind.BIND_TREATY,
            preconditions=[],
            postconditions=["treaty-new"],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        successor = move.apply(state)
        assert "treaty-new" in successor.treaty_ids

    def test_open_channel_appends_to_channel_ids(self) -> None:
        state = SemanticControlState(channel_ids=[])
        move = AdmissibleMove(
            move_id="m",
            kind=MoveKind.OPEN_CHANNEL,
            preconditions=[],
            postconditions=["ch-new"],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        successor = move.apply(state)
        assert "ch-new" in successor.channel_ids

    def test_lift_section_appends_to_section_ids(self) -> None:
        state = SemanticControlState(section_ids=[])
        move = AdmissibleMove(
            move_id="m",
            kind=MoveKind.LIFT_SECTION,
            preconditions=[],
            postconditions=["sec-new"],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        successor = move.apply(state)
        assert "sec-new" in successor.section_ids

    def test_apply_does_not_mutate_source_state(self) -> None:
        state = SemanticControlState(cover_ids=["c1"])
        original_covers = list(state.cover_ids)
        move = AdmissibleMove(
            move_id="m",
            kind=MoveKind.EXTEND_COVER,
            preconditions=[],
            postconditions=["c2"],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        _ = move.apply(state)
        assert state.cover_ids == original_covers

    def test_apply_raises_if_not_applicable(self) -> None:
        state = SemanticControlState()
        move = AdmissibleMove(
            move_id="m",
            kind=next(iter(MoveKind)),
            preconditions=["MISSING"],
            postconditions=[],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        with pytest.raises(ValueError, match="not applicable"):
            move.apply(state)

    def test_apply_sets_last_move_metadata(self) -> None:
        state = SemanticControlState()
        move = AdmissibleMove(
            move_id="meta-test-move",
            kind=next(iter(MoveKind)),
            preconditions=[],
            postconditions=[],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        successor = move.apply(state)
        assert successor.metadata.get("last_move_id") == "meta-test-move"

    def test_apply_returns_semantic_control_state(self) -> None:
        state = SemanticControlState()
        move = AdmissibleMove(
            move_id="m",
            kind=next(iter(MoveKind)),
            preconditions=[],
            postconditions=[],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        assert isinstance(move.apply(state), SemanticControlState)


# ===========================================================================
# Section 14 — AdmissibleMove.net_value and to_dict
# ===========================================================================


class TestAdmissibleMoveNetValue:
    def test_net_value_positive(self) -> None:
        m = AdmissibleMove(
            move_id="m", kind=next(iter(MoveKind)), cost=1.0, expected_gain=3.0
        )
        assert m.net_value() == pytest.approx(2.0)

    def test_net_value_negative(self) -> None:
        m = AdmissibleMove(
            move_id="m", kind=next(iter(MoveKind)), cost=5.0, expected_gain=1.0
        )
        assert m.net_value() == pytest.approx(-4.0)

    def test_net_value_zero(self) -> None:
        m = AdmissibleMove(
            move_id="m", kind=next(iter(MoveKind)), cost=2.0, expected_gain=2.0
        )
        assert m.net_value() == pytest.approx(0.0)


class TestAdmissibleMoveToDice:
    _EXPECTED_KEYS = {
        "move_id", "kind", "preconditions", "postconditions",
        "cost", "priority", "expected_gain", "net_value",
        "trust_requirement", "metadata",
    }

    def test_returns_dict(self, basic_move: AdmissibleMove) -> None:
        assert isinstance(basic_move.to_dict(), dict)

    def test_contains_all_expected_keys(self, basic_move: AdmissibleMove) -> None:
        d = basic_move.to_dict()
        assert self._EXPECTED_KEYS.issubset(d.keys())

    def test_net_value_in_dict(self, basic_move: AdmissibleMove) -> None:
        d = basic_move.to_dict()
        assert d["net_value"] == pytest.approx(basic_move.net_value())

    def test_kind_is_string_in_dict(self, basic_move: AdmissibleMove) -> None:
        d = basic_move.to_dict()
        assert isinstance(d["kind"], str)


# ===========================================================================
# Section 15 — ControlLaw
# ===========================================================================


class TestControlLawConstruction:
    def test_default_construction(self) -> None:
        law = ControlLaw()
        assert isinstance(law.law_id, str)
        assert law.name == "default"
        assert law.kind == ControlLawKind.GREEDY
        assert isinstance(law.parameters, dict)

    def test_custom_fields(self, basic_law: ControlLaw) -> None:
        assert basic_law.law_id == "law-greedy-001"
        assert basic_law.name == "test-greedy"
        assert basic_law.kind == ControlLawKind.GREEDY
        assert basic_law.parameters["weight"] == pytest.approx(0.7)


class TestControlLawSelectMove:
    def test_empty_candidates_returns_none(
        self, basic_state: SemanticControlState, basic_law: ControlLaw
    ) -> None:
        assert basic_law.select_move(basic_state, []) is None

    def test_all_inapplicable_returns_none(
        self, basic_state: SemanticControlState,
        basic_law: ControlLaw,
        blocked_move: AdmissibleMove,
    ) -> None:
        assert basic_law.select_move(basic_state, [blocked_move]) is None

    def test_greedy_picks_highest_net_value(
        self, basic_state: SemanticControlState
    ) -> None:
        law = ControlLaw(kind=ControlLawKind.GREEDY)
        moves = [
            AdmissibleMove(
                move_id=f"m{i}",
                kind=next(iter(MoveKind)),
                preconditions=[],
                postconditions=[],
                cost=1.0,
                priority=0.5,
                expected_gain=float(i + 1),
            )
            for i in range(5)
        ]
        selected = law.select_move(basic_state, moves)
        assert selected is not None
        assert selected.move_id == "m4"  # highest expected_gain=5, cost=1 → net=4

    def test_balanced_law_returns_admissible_move(
        self, basic_state: SemanticControlState
    ) -> None:
        law = ControlLaw(kind=ControlLawKind.BALANCED, parameters={"alpha": 0.6})
        moves = [
            AdmissibleMove(
                move_id=f"bm{i}",
                kind=next(iter(MoveKind)),
                preconditions=[],
                postconditions=[],
                cost=float(i + 1),
                priority=0.5,
                expected_gain=float(i + 2),
            )
            for i in range(3)
        ]
        result = law.select_move(basic_state, moves)
        assert isinstance(result, AdmissibleMove)

    def test_lookahead_law_returns_admissible_move(
        self, basic_state: SemanticControlState
    ) -> None:
        law = ControlLaw(
            kind=ControlLawKind.LOOKAHEAD, parameters={"depth": 2, "beam_width": 2}
        )
        moves = [
            AdmissibleMove(
                move_id=f"lm{i}",
                kind=next(iter(MoveKind)),
                preconditions=[],
                postconditions=[],
                cost=1.0,
                priority=0.5,
                expected_gain=float(i + 1),
            )
            for i in range(3)
        ]
        result = law.select_move(basic_state, moves)
        assert isinstance(result, AdmissibleMove)

    def test_adaptive_law_returns_admissible_move(
        self, basic_state: SemanticControlState
    ) -> None:
        law = ControlLaw(kind=ControlLawKind.ADAPTIVE, parameters={"alpha": 0.5})
        moves = [
            AdmissibleMove(
                move_id="am1",
                kind=next(iter(MoveKind)),
                preconditions=[],
                postconditions=[],
                cost=1.0,
                priority=0.5,
                expected_gain=2.0,
            )
        ]
        result = law.select_move(basic_state, moves)
        assert isinstance(result, AdmissibleMove)

    def test_custom_law_with_callable_selector(
        self, basic_state: SemanticControlState
    ) -> None:
        chosen = AdmissibleMove(
            move_id="custom-chosen",
            kind=next(iter(MoveKind)),
            preconditions=[],
            postconditions=[],
            cost=1.0,
            priority=0.5,
            expected_gain=1.0,
        )

        def my_selector(state, applicable):
            return chosen

        law = ControlLaw(
            kind=ControlLawKind.CUSTOM,
            parameters={"selector": my_selector},
        )
        result = law.select_move(basic_state, [chosen])
        assert result is chosen


class TestControlLawEvaluate:
    def test_evaluate_returns_float(
        self, basic_state: SemanticControlState, basic_law: ControlLaw
    ) -> None:
        result = basic_law.evaluate(basic_state)
        assert isinstance(result, float)

    def test_evaluate_in_unit_interval(
        self, basic_state: SemanticControlState, basic_law: ControlLaw
    ) -> None:
        result = basic_law.evaluate(basic_state)
        assert 0.0 <= result <= 1.0


class TestControlLawAdapt:
    def test_adapt_adds_parameter(self, basic_law: ControlLaw) -> None:
        basic_law.adapt({"new_param": 42})
        assert basic_law.parameters["new_param"] == 42

    def test_adapt_overwrites_parameter(self, basic_law: ControlLaw) -> None:
        basic_law.adapt({"weight": 0.99})
        assert basic_law.parameters["weight"] == pytest.approx(0.99)

    def test_adapt_with_empty_dict_is_noop(self, basic_law: ControlLaw) -> None:
        original = dict(basic_law.parameters)
        basic_law.adapt({})
        assert basic_law.parameters == original


class TestControlLawToDice:
    def test_returns_dict(self, basic_law: ControlLaw) -> None:
        assert isinstance(basic_law.to_dict(), dict)

    def test_expected_keys(self, basic_law: ControlLaw) -> None:
        d = basic_law.to_dict()
        for key in ("law_id", "name", "kind", "parameters"):
            assert key in d

    def test_kind_is_string(self, basic_law: ControlLaw) -> None:
        assert isinstance(basic_law.to_dict()["kind"], str)


# ===========================================================================
# Section 16 — ConvergenceCertificate
# ===========================================================================


class TestConvergenceCertificate:
    def _make_cert(
        self,
        *,
        coverage_ratio: float = 0.95,
        obligation_count: int = 0,
        issued_at: float | None = None,
        valid_for: float = CERTIFICATE_TTL,
    ) -> ConvergenceCertificate:
        return ConvergenceCertificate(
            cert_id=str(uuid.uuid4()),
            state_id="state-x",
            coverage_ratio=coverage_ratio,
            obligation_count=obligation_count,
            issued_at=issued_at if issued_at is not None else time.time(),
            valid_for=valid_for,
            evidence={"test": True},
        )

    def test_construction(self) -> None:
        cert = self._make_cert()
        assert isinstance(cert.cert_id, str)
        assert cert.state_id == "state-x"

    def test_is_valid_full_coverage_no_obligations(self) -> None:
        cert = self._make_cert(coverage_ratio=CONVERGENCE_THRESHOLD, obligation_count=0)
        assert cert.is_valid()

    def test_is_valid_returns_false_partial_coverage(self) -> None:
        cert = self._make_cert(coverage_ratio=0.5, obligation_count=0)
        assert not cert.is_valid()

    def test_is_valid_returns_false_outstanding_obligations(self) -> None:
        cert = self._make_cert(coverage_ratio=0.95, obligation_count=1)
        assert not cert.is_valid()

    def test_is_not_expired_freshly_created(self) -> None:
        cert = self._make_cert(issued_at=time.time(), valid_for=CERTIFICATE_TTL)
        assert not cert.is_expired()

    def test_is_expired_past_valid_window(self) -> None:
        cert = self._make_cert(
            issued_at=time.time() - CERTIFICATE_TTL - 10.0,
            valid_for=CERTIFICATE_TTL,
        )
        assert cert.is_expired()

    def test_summary_returns_string(self) -> None:
        assert isinstance(self._make_cert().summary(), str)

    def test_summary_contains_valid_for_valid_cert(self) -> None:
        cert = self._make_cert(coverage_ratio=0.95, obligation_count=0)
        assert "VALID" in cert.summary()

    def test_summary_contains_invalid_for_invalid_cert(self) -> None:
        cert = self._make_cert(coverage_ratio=0.5, obligation_count=0)
        assert "INVALID" in cert.summary()

    def test_to_dict_keys(self) -> None:
        cert = self._make_cert()
        d = cert.to_dict()
        for key in (
            "cert_id", "state_id", "coverage_ratio",
            "obligation_count", "issued_at", "valid_for", "evidence",
        ):
            assert key in d

    def test_to_dict_coverage_ratio_value(self) -> None:
        cert = self._make_cert(coverage_ratio=0.92)
        assert cert.to_dict()["coverage_ratio"] == pytest.approx(0.92)

    def test_cert_is_frozen(self) -> None:
        cert = self._make_cert()
        with pytest.raises((TypeError, AttributeError)):
            cert.coverage_ratio = 0.0  # type: ignore[misc]


# ===========================================================================
# Section 17 — SemanticTrajectory
# ===========================================================================


class TestSemanticTrajectoryConstruction:
    def test_default_construction(self) -> None:
        traj = SemanticTrajectory()
        assert isinstance(traj.trajectory_id, str)
        assert traj.states == []
        assert traj.moves == []
        assert traj.timestamps == []

    def test_custom_trajectory_id(self) -> None:
        traj = SemanticTrajectory(trajectory_id="custom-id-42")
        assert traj.trajectory_id == "custom-id-42"


class TestSemanticTrajectoryAppendAndLength:
    def test_length_zero_initially(self) -> None:
        assert SemanticTrajectory().length() == 0

    def test_append_increases_length(self) -> None:
        traj = SemanticTrajectory()
        traj.append(SemanticControlState(), None)
        assert traj.length() == 1

    def test_multiple_appends(self) -> None:
        traj = SemanticTrajectory()
        for i in range(5):
            traj.append(SemanticControlState(state_id=str(i)))
        assert traj.length() == 5

    def test_append_with_move(self, basic_move: AdmissibleMove) -> None:
        traj = SemanticTrajectory()
        traj.append(SemanticControlState(), basic_move)
        assert traj.moves[0] is basic_move

    def test_timestamps_grow_with_appends(self) -> None:
        traj = SemanticTrajectory()
        traj.append(SemanticControlState())
        traj.append(SemanticControlState())
        assert len(traj.timestamps) == 2


class TestSemanticTrajectoryIsConverging:
    def test_empty_trajectory_not_converging(self) -> None:
        assert not SemanticTrajectory().is_converging()

    def test_too_short_not_converging(self) -> None:
        traj = SemanticTrajectory()
        for _ in range(MIN_TRAJECTORY_LENGTH - 1):
            traj.append(SemanticControlState())
        assert not traj.is_converging()

    def test_flat_scores_not_converging(self) -> None:
        # All states identical → slope = 0 → not converging
        traj = SemanticTrajectory()
        state = SemanticControlState(
            section_ids=["s1"], cover_ids=["c1"], obligation_ids=[]
        )
        for _ in range(MIN_TRAJECTORY_LENGTH + 1):
            traj.append(state.snapshot())
        assert not traj.is_converging()

    def test_strictly_increasing_scores_converging(
        self, sample_trajectory: SemanticTrajectory
    ) -> None:
        # sample_trajectory fixture has 4 states with strictly improving scores
        assert sample_trajectory.is_converging()

    def test_strictly_decreasing_not_converging(self) -> None:
        traj = SemanticTrajectory()
        # States with decreasing section coverage
        for n_sections in range(5, 0, -1):
            traj.append(
                SemanticControlState(
                    section_ids=[f"s{i}" for i in range(n_sections)],
                    cover_ids=[f"c{i}" for i in range(10)],
                    obligation_ids=[],
                )
            )
        assert not traj.is_converging()


class TestSemanticTrajectoryExport:
    def test_export_returns_dict(
        self, sample_trajectory: SemanticTrajectory
    ) -> None:
        assert isinstance(sample_trajectory.export(), dict)

    def test_export_has_trajectory_id(
        self, sample_trajectory: SemanticTrajectory
    ) -> None:
        d = sample_trajectory.export()
        assert d["trajectory_id"] == sample_trajectory.trajectory_id

    def test_export_has_score_history(
        self, sample_trajectory: SemanticTrajectory
    ) -> None:
        d = sample_trajectory.export()
        assert "score_history" in d
        assert len(d["score_history"]) == sample_trajectory.length()

    def test_export_has_length(self, sample_trajectory: SemanticTrajectory) -> None:
        d = sample_trajectory.export()
        assert d["length"] == sample_trajectory.length()


class TestSemanticTrajectoryReplay:
    def test_replay_is_iterable(
        self, sample_trajectory: SemanticTrajectory
    ) -> None:
        result = list(sample_trajectory.replay())
        assert len(result) == sample_trajectory.length()

    def test_replay_yields_state_move_tuples(
        self, sample_trajectory: SemanticTrajectory
    ) -> None:
        for state, move in sample_trajectory.replay():
            assert isinstance(state, SemanticControlState)
            assert move is None or isinstance(move, AdmissibleMove)

    def test_replay_empty_trajectory(self) -> None:
        traj = SemanticTrajectory()
        assert list(traj.replay()) == []


class TestSemanticTrajectoryLatestState:
    def test_empty_returns_none(self) -> None:
        assert SemanticTrajectory().latest_state() is None

    def test_returns_last_appended(self) -> None:
        traj = SemanticTrajectory()
        s1 = SemanticControlState(state_id="first")
        s2 = SemanticControlState(state_id="last")
        traj.append(s1)
        traj.append(s2)
        assert traj.latest_state().state_id == "last"


class TestSemanticTrajectoryScoreHistory:
    def test_empty_returns_empty_list(self) -> None:
        assert SemanticTrajectory().score_history() == []

    def test_correct_length(self, sample_trajectory: SemanticTrajectory) -> None:
        scores = sample_trajectory.score_history()
        assert len(scores) == sample_trajectory.length()

    def test_values_are_floats_in_range(
        self, sample_trajectory: SemanticTrajectory
    ) -> None:
        for s in sample_trajectory.score_history():
            assert isinstance(s, float)
            assert 0.0 <= s <= 1.0


class TestSemanticTrajectoryCertificate:
    def test_try_issue_certificate_empty_trajectory(self) -> None:
        traj = SemanticTrajectory()
        assert traj.try_issue_certificate() is None

    def test_try_issue_certificate_converged_state(self) -> None:
        traj = SemanticTrajectory()
        # A state that meets WEAK convergence criterion (high coverage, 0 obligations)
        state = SemanticControlState(
            section_ids=[f"s{i}" for i in range(9)],
            cover_ids=[f"c{i}" for i in range(10)],
            obligation_ids=[],
        )
        traj.append(state)
        cert = traj.try_issue_certificate(mode=ConvergenceMode.WEAK)
        assert cert is not None
        assert isinstance(cert, ConvergenceCertificate)

    def test_try_issue_certificate_unmet_returns_none(self) -> None:
        traj = SemanticTrajectory()
        traj.append(SemanticControlState(obligation_ids=["o1"]))
        cert = traj.try_issue_certificate(mode=ConvergenceMode.WEAK)
        assert cert is None


# ===========================================================================
# Section 18 — Integration tests
# ===========================================================================


@pytest.mark.skipif(not _HAS_CONTROLLER, reason="jugeo.orchestration.controller unavailable")
class TestIntegrationWithController:
    def test_move_kind_from_controller_is_usable(self) -> None:
        """MoveKind values from controller module work as AdmissibleMove.kind."""
        for kind in ControllerMoveKind:
            m = AdmissibleMove(
                move_id=str(uuid.uuid4()),
                kind=kind,
                preconditions=[],
                postconditions=[],
                cost=1.0,
                priority=0.5,
                expected_gain=2.0,
            )
            assert m.validate() == []

    def test_verify_kind_apply_adds_to_cover_ids(self) -> None:
        """kind=VERIFY (value='verify') should add postconditions to cover_ids."""
        state = SemanticControlState(cover_ids=[])
        move = AdmissibleMove(
            move_id="ver-move",
            kind=ControllerMoveKind.VERIFY,
            preconditions=[],
            postconditions=["new-c"],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        successor = move.apply(state)
        assert "new-c" in successor.cover_ids

    def test_discharge_obligation_kind_removes_obligation(self) -> None:
        state = SemanticControlState(obligation_ids=["obl-99"])
        move = AdmissibleMove(
            move_id="dis-move",
            kind=ControllerMoveKind.DISCHARGE_OBLIGATION,
            preconditions=[],
            postconditions=["obl-99"],
            cost=1.0,
            priority=0.5,
            expected_gain=3.0,
        )
        successor = move.apply(state)
        assert "obl-99" not in successor.obligation_ids

    def test_negotiate_treaty_kind_adds_to_treaty_ids(self) -> None:
        state = SemanticControlState(treaty_ids=[])
        move = AdmissibleMove(
            move_id="neg-move",
            kind=ControllerMoveKind.NEGOTIATE_TREATY,
            preconditions=[],
            postconditions=["new-treaty"],
            cost=1.0,
            priority=0.5,
            expected_gain=2.0,
        )
        successor = move.apply(state)
        assert "new-treaty" in successor.treaty_ids

    def test_all_controller_move_kinds_produce_state_on_apply(self) -> None:
        state = SemanticControlState()
        for kind in ControllerMoveKind:
            m = AdmissibleMove(
                move_id=str(uuid.uuid4()),
                kind=kind,
                preconditions=[],
                postconditions=[],
                cost=1.0,
                priority=0.5,
                expected_gain=1.0,
            )
            result = m.apply(state)
            assert isinstance(result, SemanticControlState)

    def test_greedy_control_selects_highest_net_value(self) -> None:
        state = SemanticControlState()
        law = ControlLaw(kind=ControlLawKind.GREEDY)
        candidates = [
            AdmissibleMove(
                move_id=f"c{i}",
                kind=ControllerMoveKind.VERIFY,
                preconditions=[],
                postconditions=[],
                cost=1.0,
                priority=0.5,
                expected_gain=float(i),
            )
            for i in range(5)
        ]
        selected = law.select_move(state, candidates)
        assert selected is not None
        assert selected.expected_gain == pytest.approx(4.0)


@pytest.mark.skipif(not _HAS_TRUST, reason="jugeo.evidence.trust unavailable")
class TestIntegrationWithTrust:
    def test_trust_tier_value_as_trust_requirement(self) -> None:
        tier = TrustTier.PROPOSAL
        m = AdmissibleMove(
            move_id="trust-move",
            kind=next(iter(MoveKind)),
            trust_requirement=str(tier.value),
        )
        assert m.trust_requirement == str(tier.value)

    def test_trust_level_value_in_move_metadata(self) -> None:
        level = TrustLevel.SOLVER_DISCHARGED
        m = AdmissibleMove(
            move_id="tl-move",
            kind=next(iter(MoveKind)),
            metadata={"trust_level": level.value},
        )
        assert m.metadata["trust_level"] == level.value


class TestIntegrationCompleteWorkflow:
    """End-to-end: create state → enumerate moves → select → apply → check convergence."""

    def test_full_convergence_workflow(self) -> None:
        # Initial state: lots of obligations, no sections
        state = SemanticControlState(
            state_id="init",
            cover_ids=[f"c{i}" for i in range(10)],
            section_ids=[],
            obligation_ids=[f"o{i}" for i in range(10)],
            budget={"solver": 500.0},
        )

        law = ControlLaw(kind=ControlLawKind.GREEDY)
        traj = SemanticTrajectory()
        traj.append(state)

        # Build candidate moves: discharge obligations one by one
        candidates = [
            AdmissibleMove(
                move_id=f"discharge-{i}",
                kind=MoveKind.DISCHARGE_OBLIGATION,
                preconditions=[f"o{i}"],
                postconditions=[f"o{i}"],
                cost=1.0,
                priority=0.5,
                expected_gain=2.0,
            )
            for i in range(10)
        ]
        # Add some section-lifting moves
        for i in range(9):
            candidates.append(
                AdmissibleMove(
                    move_id=f"lift-{i}",
                    kind=MoveKind.LIFT_SECTION,
                    preconditions=[],
                    postconditions=[f"s{i}"],
                    cost=0.5,
                    priority=0.5,
                    expected_gain=1.5,
                )
            )

        current = state
        for _ in range(30):
            move = law.select_move(current, candidates)
            if move is None:
                break
            try:
                current = move.apply(current)
                traj.append(current, move)
                candidates = [c for c in candidates if c.move_id != move.move_id]
            except ValueError:
                break

        # After the loop, the trajectory should have advanced
        assert traj.length() > 1
        # Score should have improved overall
        history = traj.score_history()
        assert history[-1] >= history[0]
