"""Tests for jugeo.orchestration.mixed_evidence_routing.integration.

Covers:
- RoutingTrustIntegrator: ceiling enforcement, composition, validation,
  promotion, audit logging.
- RoutingDescentConnector: validate_routing_result, build_gluing_data,
  compute_obstruction, run_descent_validation.
- CopilotTrustGateway: process_query, enforce_ceiling, block_if_exceeds_ceiling,
  query_statistics, audit_trail.
- RoutingFleetBridge: register_channel_member, dispatch_to_fleet, fleet_health,
  channel_availability.
- MixedEvidenceOrchestrator: route, execute, batch, system_health,
  routing_summary, reset_statistics.
- End-to-end integration across diverse task types and channels.
- Multi-file integration with TrustAlgebra, Fleet, DescentEngine (all guarded).
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

# ---------------------------------------------------------------------------
# Sys.path preamble — ensure src/ is importable without an editable install
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[5]  # jugeo repo root
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from jugeo.orchestration.mixed_evidence_routing.integration import (
    CopilotQueryRecord,
    CopilotTrustGateway,
    MixedEvidenceOrchestrator,
    RoutingDescentConnector,
    RoutingFleetBridge,
    RoutingTrustIntegrator,
    _TRUST_ORDER,
    _rank,
    _weaker,
)

# ---------------------------------------------------------------------------
# Optional upstream imports (all guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustAlgebra  # type: ignore[import]
    _HAS_TRUST_ALGEBRA = True
except Exception:
    _HAS_TRUST_ALGEBRA = False

try:
    from jugeo.geometry.descent import DescentEngine  # type: ignore[import]
    _HAS_DESCENT_ENGINE = True
except Exception:
    _HAS_DESCENT_ENGINE = False

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember  # type: ignore[import]
    _HAS_FLEET = True
except Exception:
    _HAS_FLEET = False

# Import stubs / models used throughout (may come from integration.py's stubs)
try:
    from jugeo.orchestration.mixed_evidence_routing.models import (  # type: ignore[import]
        EvidenceChannel,
        RoutingDecision,
        JurisdictionMap,
        RoutingHistory,
    )
except Exception:
    from jugeo.orchestration.mixed_evidence_routing.integration import (
        EvidenceChannel,
        RoutingDecision,
        JurisdictionMap,
        RoutingHistory,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_decision(
    task_id: str = "task-1",
    channel: EvidenceChannel = EvidenceChannel.Z3,
    confidence: float = 0.9,
    metadata: dict | None = None,
) -> RoutingDecision:
    return RoutingDecision(
        decision_id=str(uuid.uuid4()),
        task_id=task_id,
        channel=channel,
        rationale="test",
        confidence=confidence,
        estimated_cost=1.0,
        estimated_latency=100.0,
        timestamp=time.time(),
        metadata=metadata or {},
    )


def _make_task(
    task_id: str | None = None,
    claim_kind: str = "smt",
    complexity: int = 3,
) -> dict:
    return {
        "task_id": task_id or str(uuid.uuid4()),
        "claim_kind": claim_kind,
        "complexity": complexity,
    }


# ===========================================================================
# Section 1: _rank / _weaker helpers
# ===========================================================================


def test_rank_order_is_correct() -> None:
    """CONTRADICTED is weakest (0), MECHANICALLY_VERIFIED is strongest (7)."""
    assert _rank("CONTRADICTED") == 0
    assert _rank("MECHANICALLY_VERIFIED") == 7
    assert _rank("COPILOT_SUGGESTED") < _rank("SOLVER_DISCHARGED")


def test_rank_unknown_returns_zero() -> None:
    """Unknown trust level falls back to rank 0."""
    assert _rank("TOTALLY_MADE_UP") == 0


def test_weaker_returns_lower_ranked() -> None:
    assert _weaker("SOLVER_DISCHARGED", "COPILOT_SUGGESTED") == "COPILOT_SUGGESTED"
    assert _weaker("COPILOT_SUGGESTED", "SOLVER_DISCHARGED") == "COPILOT_SUGGESTED"
    assert _weaker("CONTRADICTED", "MECHANICALLY_VERIFIED") == "CONTRADICTED"


def test_weaker_same_level() -> None:
    assert _weaker("HUMAN_ATTESTED", "HUMAN_ATTESTED") == "HUMAN_ATTESTED"


# ===========================================================================
# Section 2: RoutingTrustIntegrator
# ===========================================================================


class TestRoutingTrustIntegrator:

    def setup_method(self) -> None:
        self.integrator = RoutingTrustIntegrator.default()

    def test_default_constructor_creates_valid_instance(self) -> None:
        assert self.integrator.integrator_id
        assert EvidenceChannel.Z3.value in self.integrator.channel_ceilings
        assert EvidenceChannel.COPILOT_LLM.value in self.integrator.channel_ceilings

    def test_trust_rank_returns_correct_ordinal(self) -> None:
        assert self.integrator.trust_rank("CONTRADICTED") == 0
        assert self.integrator.trust_rank("MECHANICALLY_VERIFIED") == 7
        assert self.integrator.trust_rank("SOLVER_DISCHARGED") == 6

    def test_apply_trust_ceiling_demotes_above_ceiling(self) -> None:
        # COPILOT_LLM ceiling is COPILOT_SUGGESTED; MECHANICALLY_VERIFIED exceeds it
        decision = _make_decision(channel=EvidenceChannel.COPILOT_LLM)
        result = self.integrator.apply_trust_ceiling(decision, "MECHANICALLY_VERIFIED")
        assert result == "COPILOT_SUGGESTED"

    def test_apply_trust_ceiling_preserves_below_ceiling(self) -> None:
        decision = _make_decision(channel=EvidenceChannel.Z3)
        result = self.integrator.apply_trust_ceiling(decision, "SOLVER_DISCHARGED")
        # Z3 ceiling is MECHANICALLY_VERIFIED; SOLVER_DISCHARGED is below
        assert result == "SOLVER_DISCHARGED"

    def test_apply_trust_ceiling_unknown_channel_passthrough(self) -> None:
        integrator = RoutingTrustIntegrator.default()
        integrator.channel_ceilings.pop(EvidenceChannel.Z3.value, None)
        decision = _make_decision(channel=EvidenceChannel.Z3)
        result = integrator.apply_trust_ceiling(decision, "MECHANICALLY_VERIFIED")
        assert result == "MECHANICALLY_VERIFIED"

    def test_compose_evidence_trust_returns_weakest(self) -> None:
        result = self.integrator.compose_evidence_trust(
            ["MECHANICALLY_VERIFIED", "COPILOT_SUGGESTED", "SOLVER_DISCHARGED"]
        )
        assert result == "COPILOT_SUGGESTED"

    def test_compose_evidence_trust_single_item(self) -> None:
        assert self.integrator.compose_evidence_trust(["HUMAN_ATTESTED"]) == "HUMAN_ATTESTED"

    def test_compose_evidence_trust_empty_returns_contradicted(self) -> None:
        assert self.integrator.compose_evidence_trust([]) == "CONTRADICTED"

    def test_validate_trust_assignment_within_ceiling_passes(self) -> None:
        ok, msg = self.integrator.validate_trust_assignment(
            EvidenceChannel.COPILOT_LLM, "UNVERIFIED"
        )
        assert ok is True
        assert msg == ""

    def test_validate_trust_assignment_above_ceiling_fails(self) -> None:
        ok, msg = self.integrator.validate_trust_assignment(
            EvidenceChannel.COPILOT_LLM, "SOLVER_DISCHARGED"
        )
        assert ok is False
        assert "COPILOT_SUGGESTED" in msg

    def test_copilot_ceiling_satisfied_below(self) -> None:
        assert self.integrator.copilot_ceiling_satisfied("UNVERIFIED") is True
        assert self.integrator.copilot_ceiling_satisfied("COPILOT_SUGGESTED") is True

    def test_copilot_ceiling_satisfied_above(self) -> None:
        assert self.integrator.copilot_ceiling_satisfied("SOLVER_DISCHARGED") is False
        assert self.integrator.copilot_ceiling_satisfied("MECHANICALLY_VERIFIED") is False

    def test_promote_if_permitted_with_justification(self) -> None:
        result = self.integrator.promote_if_permitted(
            "UNVERIFIED", "explicit peer review", EvidenceChannel.HUMAN
        )
        # HUMAN ceiling is HUMAN_ATTESTED (rank 4); UNVERIFIED (rank 1) → COPILOT_SUGGESTED (rank 2)
        assert _rank(result) >= _rank("UNVERIFIED")

    def test_promote_if_permitted_empty_justification_blocked(self) -> None:
        result = self.integrator.promote_if_permitted(
            "UNVERIFIED", "", EvidenceChannel.COPILOT_LLM
        )
        assert result == "UNVERIFIED"

    def test_promote_if_permitted_respects_ceiling(self) -> None:
        # COPILOT_LLM ceiling is COPILOT_SUGGESTED; can't promote past it
        result = self.integrator.promote_if_permitted(
            "COPILOT_SUGGESTED", "valid reason", EvidenceChannel.COPILOT_LLM
        )
        assert _rank(result) <= _rank("COPILOT_SUGGESTED")

    def test_record_trust_event_appends_to_log(self) -> None:
        before = len(self.integrator.audit_log.entries)
        self.integrator.record_trust_event(
            "test_event", EvidenceChannel.Z3, "SOLVER_DISCHARGED", {"note": "x"}
        )
        assert len(self.integrator.audit_log.entries) == before + 1

    def test_trust_summary_returns_dict_with_expected_keys(self) -> None:
        summary = self.integrator.trust_summary()
        assert "integrator_id" in summary
        assert "channel_ceilings" in summary
        assert "audit_entry_count" in summary


# ===========================================================================
# Section 3: RoutingDescentConnector
# ===========================================================================


class TestRoutingDescentConnector:

    def setup_method(self) -> None:
        self.connector = RoutingDescentConnector.default()

    def test_default_constructor(self) -> None:
        assert self.connector.connector_id
        assert self.connector.strategy == "exhaustive"
        assert self.connector.timeout_s == 30.0

    def test_check_channel_overlap_consistency_same_channel(self) -> None:
        d1 = _make_decision("task-1", EvidenceChannel.Z3)
        d2 = _make_decision("task-1", EvidenceChannel.Z3)
        assert self.connector.check_channel_overlap_consistency(d1, d2) is True

    def test_check_channel_overlap_consistency_different_task(self) -> None:
        d1 = _make_decision("task-1", EvidenceChannel.Z3)
        d2 = _make_decision("task-2", EvidenceChannel.COPILOT_LLM)
        assert self.connector.check_channel_overlap_consistency(d1, d2) is True

    def test_check_channel_overlap_consistency_conflict(self) -> None:
        d1 = _make_decision("task-1", EvidenceChannel.Z3)
        d2 = _make_decision("task-1", EvidenceChannel.COPILOT_LLM)
        assert self.connector.check_channel_overlap_consistency(d1, d2) is False

    def test_compute_obstruction_no_conflicts(self) -> None:
        decisions = [
            _make_decision("task-1", EvidenceChannel.Z3),
            _make_decision("task-2", EvidenceChannel.COPILOT_LLM),
        ]
        obstructions = self.connector.compute_obstruction(decisions)
        assert obstructions == []

    def test_compute_obstruction_with_conflict(self) -> None:
        decisions = [
            _make_decision("task-1", EvidenceChannel.Z3),
            _make_decision("task-1", EvidenceChannel.COPILOT_LLM),
        ]
        obstructions = self.connector.compute_obstruction(decisions)
        assert len(obstructions) >= 1
        assert "task-1" in obstructions[0]

    def test_build_gluing_data_structure(self) -> None:
        decisions = [
            _make_decision("task-1", EvidenceChannel.Z3),
            _make_decision("task-2", EvidenceChannel.COPILOT_LLM),
        ]
        data = self.connector.build_gluing_data(decisions)
        assert "sections" in data
        assert "task-1" in data["sections"]
        assert data["task_count"] == 2

    def test_validate_routing_result_success(self) -> None:
        task = _make_task(task_id="t-42")
        decision = _make_decision("t-42", EvidenceChannel.Z3)
        ok, msg = self.connector.validate_routing_result([decision], task)
        assert ok is True
        assert msg == ""

    def test_validate_routing_result_no_decision(self) -> None:
        task = _make_task(task_id="t-99")
        ok, msg = self.connector.validate_routing_result([], task)
        assert ok is False
        assert "t-99" in msg

    def test_validate_routing_result_channel_conflict(self) -> None:
        task = _make_task(task_id="t-conflict")
        d1 = _make_decision("t-conflict", EvidenceChannel.Z3)
        d2 = _make_decision("t-conflict", EvidenceChannel.COPILOT_LLM)
        ok, msg = self.connector.validate_routing_result([d1, d2], task)
        assert ok is False

    def test_run_descent_validation_clean(self) -> None:
        tasks = [_make_task(task_id="t-1"), _make_task(task_id="t-2")]
        decisions = [
            _make_decision("t-1", EvidenceChannel.Z3),
            _make_decision("t-2", EvidenceChannel.COPILOT_LLM),
        ]
        result = self.connector.run_descent_validation(tasks, decisions)
        assert result["global_section_exists"] is True
        assert result["task_count"] == 2

    def test_descent_summary_keys(self) -> None:
        s = self.connector.descent_summary()
        assert "connector_id" in s
        assert "strategy" in s
        assert "timeout_s" in s


# ===========================================================================
# Section 4: CopilotTrustGateway
# ===========================================================================


class TestCopilotTrustGateway:

    def setup_method(self) -> None:
        self.gateway = CopilotTrustGateway.default()

    def test_default_constructor(self) -> None:
        assert self.gateway.gateway_id
        assert self.gateway.max_trust == "COPILOT_SUGGESTED"
        assert self.gateway.allow_promotion is False

    def test_enforce_ceiling_passes_below(self) -> None:
        assert self.gateway.enforce_ceiling("UNVERIFIED") == "UNVERIFIED"
        assert self.gateway.enforce_ceiling("COPILOT_SUGGESTED") == "COPILOT_SUGGESTED"

    def test_enforce_ceiling_demotes_above(self) -> None:
        assert self.gateway.enforce_ceiling("SOLVER_DISCHARGED") == "COPILOT_SUGGESTED"
        assert self.gateway.enforce_ceiling("MECHANICALLY_VERIFIED") == "COPILOT_SUGGESTED"

    def test_is_trust_within_ceiling_true(self) -> None:
        assert self.gateway.is_trust_within_ceiling("UNVERIFIED") is True

    def test_is_trust_within_ceiling_false(self) -> None:
        assert self.gateway.is_trust_within_ceiling("HUMAN_ATTESTED") is False

    def test_process_query_increments_count(self) -> None:
        self.gateway.process_query("q", "a", "UNVERIFIED")
        assert self.gateway.query_count == 1

    def test_process_query_blocked_count_increments_on_demotion(self) -> None:
        self.gateway.process_query("q", "a", "MECHANICALLY_VERIFIED")
        assert self.gateway.blocked_count == 1

    def test_process_query_returns_copilot_query_record(self) -> None:
        record = self.gateway.process_query("query text", "response", "UNVERIFIED", "gpt-4")
        assert isinstance(record, CopilotQueryRecord)
        assert record.query_text == "query text"
        assert record.model_id == "gpt-4"

    def test_process_query_enforced_trust_is_ceiling(self) -> None:
        record = self.gateway.process_query("q", "a", "SOLVER_DISCHARGED")
        assert record.enforced_trust == "COPILOT_SUGGESTED"
        assert record.blocked is True

    def test_block_if_exceeds_ceiling_non_copilot_passthrough(self) -> None:
        decision = _make_decision(channel=EvidenceChannel.Z3)
        blocked, returned = self.gateway.block_if_exceeds_ceiling(decision)
        assert blocked is False
        assert returned is decision

    def test_block_if_exceeds_ceiling_copilot_within_ceiling(self) -> None:
        decision = _make_decision(
            channel=EvidenceChannel.COPILOT_LLM,
            metadata={"trust": "COPILOT_SUGGESTED"},
        )
        blocked, returned = self.gateway.block_if_exceeds_ceiling(decision)
        assert blocked is False

    def test_block_if_exceeds_ceiling_copilot_above_ceiling(self) -> None:
        decision = _make_decision(
            channel=EvidenceChannel.COPILOT_LLM,
            metadata={"trust": "MECHANICALLY_VERIFIED"},
        )
        blocked, returned = self.gateway.block_if_exceeds_ceiling(decision)
        assert blocked is True
        assert returned.metadata["trust"] == "COPILOT_SUGGESTED"
        assert returned.metadata.get("trust_demoted") is True

    def test_query_statistics_block_rate(self) -> None:
        self.gateway.process_query("q1", "a1", "UNVERIFIED")
        self.gateway.process_query("q2", "a2", "MECHANICALLY_VERIFIED")
        stats = self.gateway.query_statistics()
        assert stats["query_count"] == 2
        assert stats["blocked_count"] == 1
        assert stats["block_rate"] == 0.5

    def test_audit_trail_returns_copy(self) -> None:
        self.gateway.process_query("q", "a", "UNVERIFIED")
        trail = self.gateway.audit_trail()
        assert len(trail) == 1
        trail.clear()  # mutating copy should not affect internal state
        assert len(self.gateway.audit_entries) == 1


# ===========================================================================
# Section 5: RoutingFleetBridge
# ===========================================================================


class TestRoutingFleetBridge:

    def setup_method(self) -> None:
        self.bridge = RoutingFleetBridge.default()

    def test_default_constructor(self) -> None:
        assert self.bridge.bridge_id
        assert self.bridge.channel_to_member_map == {}

    def test_register_channel_member(self) -> None:
        self.bridge.register_channel_member(EvidenceChannel.Z3, "member-z3")
        assert self.bridge.channel_to_member_map[EvidenceChannel.Z3.value] == "member-z3"

    def test_dispatch_to_fleet_registered_channel(self) -> None:
        self.bridge.register_channel_member(EvidenceChannel.Z3, "member-z3")
        decision = _make_decision(channel=EvidenceChannel.Z3)
        task = _make_task(task_id="t-dispatch")
        result = self.bridge.dispatch_to_fleet(decision, task)
        assert result["status"] == "dispatched"
        assert result["member_id"] == "member-z3"

    def test_dispatch_to_fleet_unregistered_channel_returns_error(self) -> None:
        decision = _make_decision(channel=EvidenceChannel.HUMAN)
        task = _make_task()
        result = self.bridge.dispatch_to_fleet(decision, task)
        assert result["status"] == "error"
        assert "human" in result["reason"].lower()

    def test_collect_fleet_result_returns_member_id(self) -> None:
        result = self.bridge.collect_fleet_result("member-xyz")
        assert result["member_id"] == "member-xyz"
        assert result["status"] == "collected"

    def test_fleet_health_contains_bridge_id(self) -> None:
        health = self.bridge.fleet_health()
        assert "bridge_id" in health
        assert "member_count" in health

    def test_channel_availability_all_false_initially(self) -> None:
        avail = self.bridge.channel_availability()
        assert all(not v for v in avail.values())

    def test_channel_availability_after_registration(self) -> None:
        self.bridge.register_channel_member(EvidenceChannel.Z3, "m1")
        avail = self.bridge.channel_availability()
        assert avail[EvidenceChannel.Z3.value] is True
        assert avail[EvidenceChannel.HUMAN.value] is False

    def test_bridge_summary_keys(self) -> None:
        summary = self.bridge.bridge_summary()
        assert "bridge_id" in summary
        assert "channel_map" in summary
        assert "channel_availability" in summary


# ===========================================================================
# Section 6: MixedEvidenceOrchestrator
# ===========================================================================


class TestMixedEvidenceOrchestrator:

    def setup_method(self) -> None:
        self.orch = MixedEvidenceOrchestrator.default()

    def test_default_constructor(self) -> None:
        assert self.orch.orchestrator_id
        assert self.orch.trust_integrator is not None
        assert self.orch.copilot_gateway is not None

    def test_route_returns_routing_decision(self) -> None:
        task = _make_task(task_id="t-route", claim_kind="smt")
        decision = self.orch.route(task)
        assert decision.task_id == "t-route"
        assert isinstance(decision.channel, EvidenceChannel)

    def test_route_increments_total_routed(self) -> None:
        task = _make_task(claim_kind="smt")
        self.orch.route(task)
        assert self.orch._total_routed == 1

    def test_route_records_in_history(self) -> None:
        task = _make_task(claim_kind="smt")
        decision = self.orch.route(task)
        assert any(d.decision_id == decision.decision_id for d in self.orch.routing_history.decisions)

    def test_execute_returns_result_dict(self) -> None:
        task = _make_task(task_id="t-exec", claim_kind="smt")
        result = self.orch.execute(task)
        assert "task_id" in result
        assert "channel" in result
        assert "decision_id" in result

    def test_execute_records_outcome(self) -> None:
        task = _make_task(claim_kind="smt")
        before = len(self.orch._outcomes)
        self.orch.execute(task)
        assert len(self.orch._outcomes) == before + 1

    def test_route_and_execute_batch_returns_one_per_task(self) -> None:
        tasks = [_make_task(claim_kind="smt") for _ in range(5)]
        results = self.orch.route_and_execute_batch(tasks)
        assert len(results) == 5

    def test_system_health_keys(self) -> None:
        health = self.orch.system_health()
        for key in ("orchestrator_id", "total_routed", "success_rate",
                    "copilot_gateway", "fleet_bridge", "trust_integrator"):
            assert key in health

    def test_routing_summary_keys(self) -> None:
        self.orch.route(_make_task(claim_kind="smt"))
        summary = self.orch.routing_summary()
        assert "total_decisions" in summary
        assert "by_channel" in summary
        assert summary["total_decisions"] >= 1

    def test_reset_statistics_zeroes_counters(self) -> None:
        self.orch.execute(_make_task(claim_kind="smt"))
        self.orch.reset_statistics()
        assert self.orch._total_routed == 0
        assert self.orch._total_success == 0
        assert self.orch._total_cost == 0.0
        assert self.orch._outcomes == []
        assert self.orch.routing_history.decisions == []

    def test_record_outcome_creates_routing_outcome(self) -> None:
        decision = _make_decision()
        outcome = self.orch.record_outcome(decision, True, 2.5, 80.0, "SOLVER_DISCHARGED")
        assert outcome.decision_id == decision.decision_id
        assert outcome.success is True
        assert outcome.trust_achieved == "SOLVER_DISCHARGED"


# ===========================================================================
# Section 7: End-to-end integration
# ===========================================================================


class TestEndToEndIntegration:

    def setup_method(self) -> None:
        self.orch = MixedEvidenceOrchestrator.default()

    def test_route_diverse_task_types(self) -> None:
        """Each claim kind routes to a non-None channel."""
        kinds = ["smt", "semantic", "runtime", "review", "composite"]
        for kind in kinds:
            task = _make_task(claim_kind=kind)
            decision = self.orch.route(task)
            assert decision.channel in list(EvidenceChannel)

    def test_batch_with_mixed_claim_kinds(self) -> None:
        tasks = [
            _make_task(task_id=f"batch-{i}", claim_kind=k)
            for i, k in enumerate(["smt", "heuristic", "trace", "approval"])
        ]
        results = self.orch.route_and_execute_batch(tasks)
        assert len(results) == 4
        for r in results:
            assert "task_id" in r

    def test_copilot_channel_trust_is_capped(self) -> None:
        """Routing a semantic task and checking the trust ceiling."""
        task = _make_task(claim_kind="semantic", task_id="t-cop")
        decision = self.orch.route(task)
        if decision.channel == EvidenceChannel.COPILOT_LLM:
            trust = decision.metadata.get("trust", "COPILOT_SUGGESTED")
            assert _rank(trust) <= _rank("COPILOT_SUGGESTED")

    def test_descent_validation_after_batch(self) -> None:
        tasks = [_make_task(task_id=f"dv-{i}", claim_kind="smt") for i in range(3)]
        results = self.orch.route_and_execute_batch(tasks)
        decisions = self.orch.routing_history.decisions
        validation = self.orch.descent_connector.run_descent_validation(tasks, decisions)
        # Each task should have its own consistent routing entry
        assert "task_results" in validation

    def test_trust_integrator_compose_after_routing(self) -> None:
        trusts = [
            self.orch.trust_integrator.apply_trust_ceiling(
                _make_decision(channel=EvidenceChannel.Z3), "MECHANICALLY_VERIFIED"
            ),
            self.orch.trust_integrator.apply_trust_ceiling(
                _make_decision(channel=EvidenceChannel.COPILOT_LLM), "SOLVER_DISCHARGED"
            ),
        ]
        composed = self.orch.trust_integrator.compose_evidence_trust(trusts)
        assert _rank(composed) <= _rank("COPILOT_SUGGESTED")

    def test_fleet_bridge_registered_channels_dispatched(self) -> None:
        bridge = self.orch.fleet_bridge
        bridge.register_channel_member(EvidenceChannel.Z3, "z3-worker")
        task = _make_task(task_id="fleet-test", claim_kind="smt")
        decision = self.orch.route(task)
        if decision.channel == EvidenceChannel.Z3:
            result = bridge.dispatch_to_fleet(decision, task)
            assert result["status"] == "dispatched"

    def test_system_health_after_batch(self) -> None:
        tasks = [_make_task(claim_kind="smt") for _ in range(10)]
        self.orch.route_and_execute_batch(tasks)
        health = self.orch.system_health()
        assert health["total_routed"] == 10

    def test_routing_summary_by_channel_populated(self) -> None:
        for _ in range(3):
            self.orch.route(_make_task(claim_kind="smt"))
        summary = self.orch.routing_summary()
        assert sum(summary["by_channel"].values()) >= 3


# ===========================================================================
# Section 8: Multi-file integration (all try/except)
# ===========================================================================


def test_trust_algebra_integration() -> None:
    """If TrustAlgebra is available, the integrator uses it without errors."""
    integrator = RoutingTrustIntegrator.default()
    # Should not raise regardless of whether TrustAlgebra is real or stub
    result = integrator.compose_evidence_trust(["SOLVER_DISCHARGED", "HUMAN_ATTESTED"])
    assert _rank(result) <= _rank("SOLVER_DISCHARGED")


def test_descent_engine_integration() -> None:
    """RoutingDescentConnector works regardless of DescentEngine availability."""
    connector = RoutingDescentConnector.default()
    decisions = [_make_decision("t-de", EvidenceChannel.Z3)]
    task = _make_task(task_id="t-de")
    ok, _ = connector.validate_routing_result(decisions, task)
    assert ok is True


def test_fleet_integration() -> None:
    """RoutingFleetBridge works regardless of Fleet availability."""
    bridge = RoutingFleetBridge.default()
    bridge.register_channel_member(EvidenceChannel.RUNTIME_WITNESS, "rw-1")
    decision = _make_decision(channel=EvidenceChannel.RUNTIME_WITNESS)
    task = _make_task()
    result = bridge.dispatch_to_fleet(decision, task)
    assert result["status"] == "dispatched"


def test_orchestrator_full_pipeline_no_errors() -> None:
    """Full pipeline must not raise for a well-formed task."""
    orch = MixedEvidenceOrchestrator.default()
    task = {
        "task_id": str(uuid.uuid4()),
        "claim_kind": "smt",
        "complexity": 2,
        "trust_claimed": "SOLVER_DISCHARGED",
    }
    result = orch.execute(task)
    assert "channel" in result
    assert "success" in result


def test_copilot_gateway_integrates_with_orchestrator() -> None:
    """CopilotTrustGateway is wired into orchestrator and audits queries."""
    orch = MixedEvidenceOrchestrator.default()
    # Simulate copilot interaction through the gateway
    record = orch.copilot_gateway.process_query(
        "Is this proof valid?", "Yes, it is.", "MECHANICALLY_VERIFIED", "gpt-4o"
    )
    assert record.enforced_trust == "COPILOT_SUGGESTED"
    assert record.blocked is True
    stats = orch.copilot_gateway.query_statistics()
    assert stats["blocked_count"] >= 1
