"""Tests for jugeo.orchestration.mixed_evidence_routing.models (theory2.tex Ch45)."""

from __future__ import annotations

import time
from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.orchestration.mixed_evidence_routing.models import (
    ChannelStats,
    CopilotQueryRecord,
    EscalationUrgency,
    EvidenceChannel,
    HumanEscalation,
    JurisdictionMap,
    RoutingDecision,
    RoutingHistory,
    RoutingStrategy,
    EvidenceChannelSelector,
)

# ---------------------------------------------------------------------------
# Graceful upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.controller import OrchestratorState, MoveKind
    _CONTROLLER_AVAILABLE = True
except Exception:
    _CONTROLLER_AVAILABLE = False

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember
    _FLEET_AVAILABLE = True
except Exception:
    _FLEET_AVAILABLE = False

try:
    from jugeo.evidence.trust import TrustAlgebra, TrustLevel, TrustCeiling
    _TRUST_AVAILABLE = True
except Exception:
    _TRUST_AVAILABLE = False

try:
    from jugeo.geometry.descent import DescentEngine, GluingData, OverlapStatus
    _DESCENT_AVAILABLE = True
except Exception:
    _DESCENT_AVAILABLE = False


# ===========================================================================
# Enum tests
# ===========================================================================


class TestEvidenceChannel:
    def test_all_members_present(self):
        values = {m.value for m in EvidenceChannel}
        assert values == {"z3", "copilot_llm", "runtime_witness", "human", "composite"}

    def test_str_subclass(self):
        assert isinstance(EvidenceChannel.Z3, str)
        assert EvidenceChannel.Z3 == "z3"

    def test_membership(self):
        assert EvidenceChannel("z3") is EvidenceChannel.Z3
        assert EvidenceChannel("human") is EvidenceChannel.HUMAN

    def test_iteration_order_matches_definition(self):
        members = list(EvidenceChannel)
        assert members[0] is EvidenceChannel.Z3
        assert members[-1] is EvidenceChannel.COMPOSITE


class TestRoutingStrategy:
    def test_all_members_present(self):
        values = {m.value for m in RoutingStrategy}
        assert "strict_jurisdiction" in values
        assert "cost_optimal" in values
        assert "latency_optimal" in values
        assert "trust_optimal" in values
        assert "load_balanced" in values

    def test_str_subclass(self):
        assert isinstance(RoutingStrategy.COST_OPTIMAL, str)

    def test_round_trip(self):
        for member in RoutingStrategy:
            assert RoutingStrategy(member.value) is member


class TestEscalationUrgency:
    def test_all_members_present(self):
        values = {m.value for m in EscalationUrgency}
        assert values == {"low", "medium", "high", "critical"}

    def test_str_subclass(self):
        assert isinstance(EscalationUrgency.LOW, str)

    def test_round_trip(self):
        for member in EscalationUrgency:
            assert EscalationUrgency(member.value) is member


# ===========================================================================
# RoutingDecision tests
# ===========================================================================


class TestRoutingDecision:
    def _make(self, **kwargs) -> RoutingDecision:
        defaults = dict(
            task_id="task-1",
            channel=EvidenceChannel.Z3,
            rationale="test rationale",
        )
        defaults.update(kwargs)
        return RoutingDecision.new(**defaults)

    def test_new_creates_unique_ids(self):
        d1 = self._make()
        d2 = self._make()
        assert d1.decision_id != d2.decision_id

    def test_new_defaults(self):
        d = self._make()
        assert d.confidence == 0.8
        assert d.estimated_cost == 1.0
        assert d.estimated_latency == 1.0
        assert isinstance(d.metadata, dict)

    def test_new_custom_values(self):
        d = self._make(confidence=0.5, estimated_cost=2.0, estimated_latency=3.0)
        assert d.confidence == 0.5
        assert d.estimated_cost == 2.0
        assert d.estimated_latency == 3.0

    def test_new_with_metadata(self):
        d = self._make(metadata={"foo": "bar"})
        assert d.metadata == {"foo": "bar"}

    def test_new_timestamp_is_recent(self):
        before = time.time()
        d = self._make()
        after = time.time()
        assert before <= d.timestamp <= after

    def test_to_dict_keys(self):
        d = self._make()
        dct = d.to_dict()
        expected = {
            "decision_id", "task_id", "channel", "rationale",
            "confidence", "estimated_cost", "estimated_latency",
            "timestamp", "metadata",
        }
        assert set(dct.keys()) == expected

    def test_to_dict_channel_is_string(self):
        d = self._make()
        dct = d.to_dict()
        assert dct["channel"] == "z3"

    def test_is_confident_default_threshold(self):
        d_high = self._make(confidence=0.9)
        d_low = self._make(confidence=0.5)
        assert d_high.is_confident() is True
        assert d_low.is_confident() is False

    def test_is_confident_custom_threshold(self):
        d = self._make(confidence=0.5)
        assert d.is_confident(threshold=0.5) is True
        assert d.is_confident(threshold=0.51) is False

    def test_is_confident_boundary(self):
        d = self._make(confidence=0.7)
        assert d.is_confident(threshold=0.7) is True

    def test_cost_benefit_ratio(self):
        d = self._make(estimated_cost=2.0, confidence=0.5)
        assert d.cost_benefit_ratio() == pytest.approx(4.0)

    def test_cost_benefit_ratio_near_zero_confidence(self):
        d = self._make(estimated_cost=1.0, confidence=0.0)
        ratio = d.cost_benefit_ratio()
        assert ratio == pytest.approx(1.0 / 0.001)

    def test_age_seconds_non_negative(self):
        d = self._make()
        assert d.age_seconds() >= 0.0

    def test_age_seconds_increases_over_time(self):
        d = self._make()
        time.sleep(0.02)
        assert d.age_seconds() > 0.0

    def test_with_metadata_returns_new_instance(self):
        d = self._make()
        d2 = d.with_metadata("key", "value")
        assert d is not d2
        assert d2.metadata["key"] == "value"

    def test_with_metadata_preserves_existing(self):
        d = self._make(metadata={"a": 1})
        d2 = d.with_metadata("b", 2)
        assert d2.metadata == {"a": 1, "b": 2}
        assert d.metadata == {"a": 1}  # original unchanged

    def test_with_metadata_overwrites_key(self):
        d = self._make(metadata={"x": 1})
        d2 = d.with_metadata("x", 99)
        assert d2.metadata["x"] == 99

    def test_frozen_immutability(self):
        d = self._make()
        with pytest.raises((AttributeError, TypeError)):
            d.confidence = 0.0  # type: ignore[misc]


# ===========================================================================
# JurisdictionMap tests
# ===========================================================================


class TestJurisdictionMap:
    def _make(self, **kwargs) -> JurisdictionMap:
        defaults = dict(
            channel=EvidenceChannel.Z3,
            supported_claim_kinds=["equality", "arithmetic"],
            max_complexity=8.0,
        )
        defaults.update(kwargs)
        return JurisdictionMap.new(**defaults)

    def test_new_creates_unique_ids(self):
        m1 = self._make()
        m2 = self._make()
        assert m1.map_id != m2.map_id

    def test_new_defaults(self):
        m = self._make()
        assert m.min_trust_level == "UNVERIFIED"
        assert m.exclusions == ()

    def test_supported_kinds_converted_to_tuple(self):
        m = self._make(supported_claim_kinds=["equality", "arithmetic"])
        assert isinstance(m.supported_claim_kinds, tuple)
        assert "equality" in m.supported_claim_kinds

    def test_can_handle_true(self):
        m = self._make()
        claim = {"claim_kind": "equality", "complexity": 3.0}
        assert m.can_handle(claim) is True

    def test_can_handle_wrong_kind(self):
        m = self._make()
        claim = {"claim_kind": "novel_claim", "complexity": 1.0}
        assert m.can_handle(claim) is False

    def test_can_handle_too_complex(self):
        m = self._make(max_complexity=5.0)
        claim = {"claim_kind": "equality", "complexity": 6.0}
        assert m.can_handle(claim) is False

    def test_can_handle_at_boundary(self):
        m = self._make(max_complexity=8.0)
        claim = {"claim_kind": "equality", "complexity": 8.0}
        assert m.can_handle(claim) is True

    def test_can_handle_missing_complexity_defaults_to_one(self):
        m = self._make()
        claim = {"claim_kind": "equality"}
        assert m.can_handle(claim) is True

    def test_complexity_score_explicit(self):
        m = self._make()
        assert m.complexity_score({"complexity": 4.5}) == pytest.approx(4.5)

    def test_complexity_score_default(self):
        m = self._make()
        assert m.complexity_score({}) == pytest.approx(1.0)

    def test_to_dict_keys(self):
        m = self._make()
        dct = m.to_dict()
        assert "map_id" in dct
        assert "channel" in dct
        assert dct["channel"] == "z3"

    def test_is_exclusive_to_true(self):
        m = self._make(exclusions=["novel_claim"])
        assert m.is_exclusive_to("novel_claim") is True

    def test_is_exclusive_to_false(self):
        m = self._make()
        assert m.is_exclusive_to("equality") is False

    def test_coverage_fraction_full(self):
        m = self._make(supported_claim_kinds=["a", "b", "c"])
        assert m.coverage_fraction(["a", "b", "c"]) == pytest.approx(1.0)

    def test_coverage_fraction_partial(self):
        m = self._make(supported_claim_kinds=["a"])
        assert m.coverage_fraction(["a", "b"]) == pytest.approx(0.5)

    def test_coverage_fraction_empty_universe(self):
        m = self._make()
        assert m.coverage_fraction([]) == pytest.approx(0.0)


# ===========================================================================
# EvidenceChannelSelector tests
# ===========================================================================


class TestEvidenceChannelSelector:
    def test_default_creates_selector(self):
        sel = EvidenceChannelSelector.default()
        assert sel.selector_id
        assert len(sel.jurisdiction_maps) > 0

    def test_select_returns_routing_decision(self):
        sel = EvidenceChannelSelector.default()
        task = {"task_id": "t1", "claim_kind": "equality", "complexity": 2.0}
        decision = sel.select(task)
        assert isinstance(decision, RoutingDecision)
        assert decision.task_id == "t1"

    def test_select_fallback_on_unknown_claim(self):
        sel = EvidenceChannelSelector.default()
        task = {"task_id": "t2", "claim_kind": "unknown_claim_kind", "complexity": 1.0}
        decision = sel.select(task)
        assert decision.channel == EvidenceChannel.HUMAN

    def test_rank_channels_returns_list(self):
        sel = EvidenceChannelSelector.default()
        task = {"claim_kind": "equality", "complexity": 2.0}
        ranked = sel.rank_channels(task)
        assert isinstance(ranked, list)
        for channel, score in ranked:
            assert isinstance(channel, EvidenceChannel)
            assert isinstance(score, float)

    def test_rank_channels_sorted_descending(self):
        sel = EvidenceChannelSelector.default()
        task = {"claim_kind": "equality", "complexity": 2.0}
        ranked = sel.rank_channels(task)
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_explain_returns_string(self):
        sel = EvidenceChannelSelector.default()
        task = {"task_id": "t3", "claim_kind": "equality", "complexity": 1.0}
        decision = sel.select(task)
        explanation = sel.explain(decision)
        assert "Routing Decision" in explanation
        assert decision.decision_id in explanation

    def test_add_jurisdiction_map(self):
        sel = EvidenceChannelSelector.default()
        n_before = len(sel.jurisdiction_maps)
        new_map = JurisdictionMap.new(EvidenceChannel.COMPOSITE, ["special_claim"])
        sel.add_jurisdiction_map(new_map)
        assert len(sel.jurisdiction_maps) == n_before + 1

    def test_remove_jurisdiction_map_success(self):
        sel = EvidenceChannelSelector.default()
        jmap = sel.jurisdiction_maps[0]
        result = sel.remove_jurisdiction_map(jmap.map_id)
        assert result is True

    def test_remove_jurisdiction_map_not_found(self):
        sel = EvidenceChannelSelector.default()
        result = sel.remove_jurisdiction_map("nonexistent-id")
        assert result is False

    def test_channel_score_returns_float(self):
        sel = EvidenceChannelSelector.default()
        score = sel.channel_score(EvidenceChannel.Z3, {"complexity": 3.0})
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_select_task_without_task_id_gets_generated(self):
        sel = EvidenceChannelSelector.default()
        task = {"claim_kind": "equality", "complexity": 1.0}
        decision = sel.select(task)
        assert decision.task_id  # non-empty


# ===========================================================================
# CopilotQueryRecord tests
# ===========================================================================


class TestCopilotQueryRecord:
    def _make(self, **kwargs) -> CopilotQueryRecord:
        defaults = dict(query_text="What is 2+2?", response_text="4")
        defaults.update(kwargs)
        return CopilotQueryRecord.new(**defaults)

    def test_new_creates_unique_ids(self):
        r1 = self._make()
        r2 = self._make()
        assert r1.query_id != r2.query_id

    def test_new_defaults(self):
        r = self._make()
        assert r.trust_ceiling == "COPILOT_SUGGESTED"
        assert r.latency_ms == 0.0
        assert r.token_count == 0
        assert r.model_id == "unknown"

    def test_new_custom_values(self):
        r = self._make(latency_ms=500.0, token_count=42, model_id="gpt-4")
        assert r.latency_ms == 500.0
        assert r.token_count == 42
        assert r.model_id == "gpt-4"

    def test_trust_adjusted_score_range(self):
        r = self._make(token_count=500)
        score = r.trust_adjusted_score()
        assert 0.0 <= score <= 1.0

    def test_trust_adjusted_score_zero_tokens(self):
        r = self._make(token_count=0)
        score = r.trust_adjusted_score()
        assert score == pytest.approx(0.5)  # only base trust_penalty

    def test_trust_adjusted_score_large_tokens_capped(self):
        r = self._make(token_count=10000)
        score = r.trust_adjusted_score()
        assert score == pytest.approx(1.0)

    def test_to_dict_keys(self):
        r = self._make()
        dct = r.to_dict()
        expected = {
            "query_id", "query_text", "response_text", "trust_ceiling",
            "latency_ms", "token_count", "timestamp", "model_id",
        }
        assert set(dct.keys()) == expected

    def test_is_reliable_default(self):
        r = self._make(token_count=20, latency_ms=1000.0)
        assert r.is_reliable() is True

    def test_is_reliable_too_few_tokens(self):
        r = self._make(token_count=5, latency_ms=100.0)
        assert r.is_reliable(min_tokens=10) is False

    def test_is_reliable_too_slow(self):
        r = self._make(token_count=50, latency_ms=40_000.0)
        assert r.is_reliable(max_latency_ms=30_000.0) is False

    def test_token_efficiency_positive(self):
        r = self._make(token_count=100, latency_ms=500.0)
        assert r.token_efficiency() == pytest.approx(0.2)

    def test_token_efficiency_zero_latency(self):
        r = self._make(latency_ms=0.0)
        assert r.token_efficiency() == 0.0

    def test_summary_contains_model_id(self):
        r = self._make(model_id="gpt-5")
        s = r.summary()
        assert "gpt-5" in s

    def test_summary_truncates_long_response(self):
        r = self._make(response_text="X" * 200)
        s = r.summary()
        assert "..." in s


# ===========================================================================
# HumanEscalation tests
# ===========================================================================


class TestHumanEscalation:
    def _make(self, **kwargs) -> HumanEscalation:
        defaults = dict(task_id="task-99", reason="Ambiguous specification")
        defaults.update(kwargs)
        return HumanEscalation.new(**defaults)

    def test_new_creates_unique_ids(self):
        e1 = self._make()
        e2 = self._make()
        assert e1.escalation_id != e2.escalation_id

    def test_new_defaults(self):
        e = self._make()
        assert e.urgency == "medium"
        assert e.assigned_to is None
        assert e.resolved_at is None
        assert e.resolution is None

    def test_is_resolved_false(self):
        e = self._make()
        assert e.is_resolved() is False

    def test_resolve_marks_resolved(self):
        e = self._make()
        e.resolve("Resolved by reviewing spec.")
        assert e.is_resolved() is True
        assert e.resolution == "Resolved by reviewing spec."

    def test_resolve_sets_resolver(self):
        e = self._make()
        e.resolve("Fixed.", resolver="alice")
        assert e.assigned_to == "alice"

    def test_resolve_timestamp_recent(self):
        e = self._make()
        before = time.time()
        e.resolve("Done.")
        after = time.time()
        assert before <= e.resolved_at <= after

    def test_age_hours_non_negative(self):
        e = self._make()
        assert e.age_hours() >= 0.0

    def test_to_dict_keys(self):
        e = self._make()
        dct = e.to_dict()
        expected = {
            "escalation_id", "task_id", "reason", "urgency",
            "assigned_to", "resolved_at", "resolution", "created_at",
        }
        assert set(dct.keys()) == expected

    def test_urgency_level_known(self):
        e = self._make(urgency="critical")
        assert e.urgency_level() is EscalationUrgency.CRITICAL

    def test_urgency_level_unknown_defaults_to_medium(self):
        e = self._make(urgency="bogus")
        assert e.urgency_level() is EscalationUrgency.MEDIUM

    def test_sla_breached_when_old_and_unresolved(self):
        e = self._make()
        # Force a very old created_at
        e.created_at = time.time() - 100 * 3600
        assert e.sla_breached(sla_hours=24.0) is True

    def test_sla_not_breached_when_fresh(self):
        e = self._make()
        assert e.sla_breached(sla_hours=24.0) is False

    def test_sla_not_breached_when_resolved(self):
        e = self._make()
        e.created_at = time.time() - 100 * 3600
        e.resolve("Done.")
        assert e.sla_breached() is False


# ===========================================================================
# RoutingHistory tests
# ===========================================================================


class TestRoutingHistory:
    def _decision(self, channel=EvidenceChannel.Z3, confidence=0.9) -> RoutingDecision:
        return RoutingDecision.new(
            task_id="t",
            channel=channel,
            rationale="test",
            confidence=confidence,
        )

    def test_empty_history(self):
        h = RoutingHistory()
        assert h.decisions == []

    def test_record_appends(self):
        h = RoutingHistory()
        d = self._decision()
        h.record(d)
        assert len(h.decisions) == 1

    def test_recent_returns_last_n(self):
        h = RoutingHistory()
        for i in range(5):
            h.record(self._decision())
        recent = h.recent(3)
        assert len(recent) == 3

    def test_recent_all_when_n_exceeds_count(self):
        h = RoutingHistory()
        h.record(self._decision())
        recent = h.recent(10)
        assert len(recent) == 1

    def test_by_channel_filters_correctly(self):
        h = RoutingHistory()
        h.record(self._decision(channel=EvidenceChannel.Z3))
        h.record(self._decision(channel=EvidenceChannel.HUMAN))
        h.record(self._decision(channel=EvidenceChannel.Z3))
        z3_decisions = h.by_channel(EvidenceChannel.Z3)
        assert len(z3_decisions) == 2

    def test_success_rate_all_confident(self):
        h = RoutingHistory()
        for _ in range(4):
            h.record(self._decision(confidence=0.9))
        assert h.success_rate() == pytest.approx(1.0)

    def test_success_rate_none_confident(self):
        h = RoutingHistory()
        for _ in range(3):
            h.record(self._decision(confidence=0.1))
        assert h.success_rate() == pytest.approx(0.0)

    def test_success_rate_empty(self):
        h = RoutingHistory()
        assert h.success_rate() == 0.0

    def test_success_rate_filtered_by_channel(self):
        h = RoutingHistory()
        h.record(self._decision(channel=EvidenceChannel.Z3, confidence=0.9))
        h.record(self._decision(channel=EvidenceChannel.HUMAN, confidence=0.1))
        assert h.success_rate(EvidenceChannel.Z3) == pytest.approx(1.0)
        assert h.success_rate(EvidenceChannel.HUMAN) == pytest.approx(0.0)

    def test_average_confidence(self):
        h = RoutingHistory()
        h.record(self._decision(confidence=0.6))
        h.record(self._decision(confidence=0.8))
        assert h.average_confidence() == pytest.approx(0.7)

    def test_average_confidence_empty(self):
        h = RoutingHistory()
        assert h.average_confidence() == 0.0

    def test_to_dict_structure(self):
        h = RoutingHistory()
        h.record(self._decision())
        dct = h.to_dict()
        assert dct["decision_count"] == 1
        assert len(dct["decisions"]) == 1


# ===========================================================================
# ChannelStats tests
# ===========================================================================


class TestChannelStats:
    def _decision(self, cost=1.0, latency=2.0) -> RoutingDecision:
        return RoutingDecision.new(
            task_id="t",
            channel=EvidenceChannel.Z3,
            rationale="test",
            estimated_cost=cost,
            estimated_latency=latency,
        )

    def test_initial_state(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.total_cost == 0.0
        assert stats.total_latency == 0.0
        assert stats.last_used is None

    def test_update_increments_requests(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        stats.update(self._decision(), success=True)
        assert stats.total_requests == 1
        assert stats.successful_requests == 1

    def test_update_failure_not_counted_as_success(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        stats.update(self._decision(), success=False)
        assert stats.total_requests == 1
        assert stats.successful_requests == 0

    def test_update_accumulates_cost_and_latency(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        stats.update(self._decision(cost=2.0, latency=3.0), success=True)
        stats.update(self._decision(cost=1.0, latency=1.0), success=True)
        assert stats.total_cost == pytest.approx(3.0)
        assert stats.total_latency == pytest.approx(4.0)

    def test_success_rate_calculation(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        stats.update(self._decision(), success=True)
        stats.update(self._decision(), success=False)
        assert stats.success_rate() == pytest.approx(0.5)

    def test_success_rate_empty(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        assert stats.success_rate() == 0.0

    def test_average_latency(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        stats.update(self._decision(latency=2.0), success=True)
        stats.update(self._decision(latency=4.0), success=True)
        assert stats.average_latency() == pytest.approx(3.0)

    def test_average_cost(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        stats.update(self._decision(cost=3.0), success=True)
        stats.update(self._decision(cost=1.0), success=True)
        assert stats.average_cost() == pytest.approx(2.0)

    def test_last_used_set_after_update(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        before = time.time()
        stats.update(self._decision(), success=True)
        after = time.time()
        assert before <= stats.last_used <= after

    def test_to_dict_keys(self):
        stats = ChannelStats(channel=EvidenceChannel.Z3)
        dct = stats.to_dict()
        expected = {
            "channel", "total_requests", "successful_requests",
            "total_cost", "total_latency", "last_used",
            "success_rate", "average_latency", "average_cost",
        }
        assert set(dct.keys()) == expected

    def test_to_dict_channel_is_string(self):
        stats = ChannelStats(channel=EvidenceChannel.HUMAN)
        dct = stats.to_dict()
        assert dct["channel"] == "human"


# ===========================================================================
# Integration tests with upstream modules (graceful skip)
# ===========================================================================


@pytest.mark.skipif(not _TRUST_AVAILABLE, reason="jugeo.evidence.trust not available")
class TestTrustIntegration:
    def test_trust_level_values_align_with_model(self):
        """Routing decisions should be compatible with trust level names."""
        tl_names = {m.name for m in TrustLevel}
        assert "COPILOT_SUGGESTED" in tl_names

    def test_copilot_record_ceiling_in_trust_level(self):
        record = CopilotQueryRecord.new("q", "a")
        # ceiling should correspond to a valid TrustLevel name
        assert record.trust_ceiling in {m.name for m in TrustLevel}


@pytest.mark.skipif(not _CONTROLLER_AVAILABLE, reason="jugeo.orchestration.controller not available")
class TestControllerIntegration:
    def test_orchestrator_state_import(self):
        assert OrchestratorState is not None

    def test_move_kind_import(self):
        assert MoveKind is not None

    def test_routing_decision_can_carry_move_kind_metadata(self):
        move_kind_name = str(list(MoveKind)[0].name)
        d = RoutingDecision.new(
            task_id="t",
            channel=EvidenceChannel.Z3,
            rationale="orchestrated",
            metadata={"move_kind": move_kind_name},
        )
        assert d.metadata["move_kind"] == move_kind_name


@pytest.mark.skipif(not _FLEET_AVAILABLE, reason="jugeo.orchestration.fleet not available")
class TestFleetIntegration:
    def test_fleet_import(self):
        assert Fleet is not None
        assert FleetMember is not None


@pytest.mark.skipif(not _DESCENT_AVAILABLE, reason="jugeo.geometry.descent not available")
class TestDescentIntegration:
    def test_descent_engine_import(self):
        assert DescentEngine is not None

    def test_gluing_data_import(self):
        assert GluingData is not None

    def test_overlap_status_import(self):
        assert OverlapStatus is not None
