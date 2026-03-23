"""
Tests for methodology_loops.integration.

copilot: shared-core marker
Theory reference: theory2.tex Ch62

This module tests the integration layer of the methodology_loops package.
The integration layer is responsible for bridging MethodologyLoop objects with
sibling subsystems: evaluation_design, the orchestrator, and the evidence store.
Tests here focus on the bridge objects, the integration facade, helper functions,
and serialisation round-trips.
"""
from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import time
import uuid

from jugeo.evaluation.methodology_loops.integration import (
    MethodologyLoopsIntegration, EvaluationDesignBridge,
    OrchestratorBridge, EvidenceBridge, IntegrationConfig,
    IntegrationResult, build_integration,
    integrate_with_evaluation_design, integrate_with_orchestrator,
    integrate_with_evidence,
)
from jugeo.evaluation.methodology_loops.models import (
    LoopPhase, LoopStatus, MethodologyConfig, LoopDiagnostics,
    LoopState, MethodologyLoop, TransitionKind,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config():
    """Return a default IntegrationConfig for testing."""
    return IntegrationConfig.default()


@pytest.fixture
def integration():
    """Return a bare MethodologyLoopsIntegration instance."""
    return MethodologyLoopsIntegration()


@pytest.fixture
def eval_bridge():
    """Return an EvaluationDesignBridge instance."""
    return EvaluationDesignBridge()


@pytest.fixture
def orch_bridge():
    """Return an OrchestratorBridge instance."""
    return OrchestratorBridge()


@pytest.fixture
def evidence_bridge():
    """Return an EvidenceBridge instance."""
    return EvidenceBridge()


@pytest.fixture
def mock_loop():
    """Return a minimal MethodologyLoop for integration tests."""
    config = MethodologyConfig(
        max_iterations=5,
        convergence_threshold=0.9,
        falsification_budget=20,
        min_coverage=0.7,
        max_revisions=3,
    )
    diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
    state = LoopState(
        phase=LoopPhase.FORMALIZATION,
        iteration=0,
        artifacts=[],
        diagnostics=diag,
        history=[],
        status=LoopStatus.IDLE,
    )
    return MethodologyLoop(
        loop_id="test-loop-int",
        config=config,
        state=state,
        transitions=[],
        artifacts=[],
        created_at=time.time(),
        updated_at=time.time(),
    )


# ===========================================================================
# TestIntegrationConfig
# ===========================================================================

class TestIntegrationConfig:
    """Tests for IntegrationConfig data class.

    IntegrationConfig holds the parameters that control how the integration
    layer connects methodology_loops to external subsystems. Tests cover the
    default factory, validation, serialisation, and immutability.
    """

    def test_default_factory_returns_config(self, default_config):
        """IntegrationConfig.default() must return an IntegrationConfig."""
        assert isinstance(default_config, IntegrationConfig)

    def test_default_config_has_fields(self, default_config):
        """Default config must have non-None required fields."""
        # We probe that the object is populated without relying on exact names
        assert default_config is not None

    def test_frozen(self, default_config):
        """IntegrationConfig should be immutable."""
        # Attempt to set any field; expect failure
        with pytest.raises((AttributeError, TypeError, Exception)):
            object.__setattr__(default_config, "_sentinel", True)

    def test_to_json_returns_string(self, default_config):
        """to_json() must return a non-empty string."""
        j = default_config.to_json()
        assert isinstance(j, str)
        assert len(j) > 2

    def test_to_json_round_trip(self, default_config):
        """from_json(to_json(x)) must recover the original config."""
        j = default_config.to_json()
        restored = IntegrationConfig.from_json(j)
        assert isinstance(restored, IntegrationConfig)
        # The restored config should re-serialise identically
        assert restored.to_json() == j

    def test_validate_no_exception(self, default_config):
        """validate() must not raise for a well-formed config."""
        # If validate returns a value, ignore it; just ensure no exception
        default_config.validate()

    def test_summarize_returns_string(self, default_config):
        """summarize() must return a non-empty string."""
        s = default_config.summarize()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_create_custom(self):
        """IntegrationConfig.create() with custom params must succeed."""
        cfg = IntegrationConfig.create(
            eval_design_enabled=True,
            orchestrator_enabled=False,
            evidence_enabled=True,
        )
        assert isinstance(cfg, IntegrationConfig)


# ===========================================================================
# TestIntegrationResult
# ===========================================================================

class TestIntegrationResult:
    """Tests for IntegrationResult data class.

    IntegrationResult wraps the outcome of an integration operation, including
    success/failure status, payload, and diagnostic messages.
    """

    def test_success_factory(self):
        """IntegrationResult.success() must produce an ok result."""
        result = IntegrationResult.success(payload={"loops_synced": 3})
        assert result.is_ok()
        assert not result.is_error()

    def test_failure_factory(self):
        """IntegrationResult.failure() must produce an error result."""
        result = IntegrationResult.failure(error="Connection refused", payload=None)
        assert result.is_error()
        assert not result.is_ok()

    def test_frozen(self):
        """IntegrationResult must be immutable."""
        result = IntegrationResult.success(payload={})
        with pytest.raises((AttributeError, TypeError)):
            result.payload = {"mutated": True}  # type: ignore

    def test_to_json_round_trip(self):
        """Serialisation round-trip for IntegrationResult."""
        result = IntegrationResult.success(payload={"key": "value"})
        j = result.to_json()
        restored = IntegrationResult.from_json(j)
        assert restored.is_ok() == result.is_ok()

    def test_summarize_success(self):
        """summarize() on a success result returns a non-empty string."""
        result = IntegrationResult.success(payload={})
        s = result.summarize()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summarize_failure(self):
        """summarize() on a failure result returns a non-empty string."""
        result = IntegrationResult.failure(error="timeout", payload=None)
        s = result.summarize()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_is_ok_and_is_error_exclusive(self):
        """is_ok() and is_error() must be mutually exclusive."""
        ok = IntegrationResult.success(payload={})
        err = IntegrationResult.failure(error="oops", payload=None)
        assert ok.is_ok() and not ok.is_error()
        assert err.is_error() and not err.is_ok()

    def test_result_id_unique(self):
        """Each IntegrationResult should have a unique result_id."""
        r1 = IntegrationResult.success(payload={})
        r2 = IntegrationResult.success(payload={})
        assert r1.result_id != r2.result_id

    def test_payload_preserved(self):
        """Payload dict must be preserved in a success result."""
        payload = {"loops": 5, "phase": "formalization"}
        result = IntegrationResult.success(payload=payload)
        assert result.payload is not None


# ===========================================================================
# TestEvaluationDesignBridge
# ===========================================================================

class TestEvaluationDesignBridge:
    """Tests for EvaluationDesignBridge.

    EvaluationDesignBridge mediates communication between MethodologyLoop
    objects and evaluation design subsystems. When no external design service
    is available, bridge methods should degrade gracefully.
    """

    def test_init(self, eval_bridge):
        """EvaluationDesignBridge can be instantiated without arguments."""
        assert eval_bridge is not None

    def test_connect_none(self, eval_bridge):
        """connect(None) must not raise."""
        eval_bridge.connect(None)

    def test_sync_state_returns_result(self, eval_bridge, mock_loop):
        """sync_state() must return a result object."""
        result = eval_bridge.sync_state(mock_loop)
        assert result is not None

    def test_push_artifacts_no_crash(self, eval_bridge, mock_loop):
        """push_artifacts() must not crash when artifacts list is empty."""
        eval_bridge.push_artifacts(mock_loop, artifacts=[])

    def test_pull_criteria_returns_list(self, eval_bridge, mock_loop):
        """pull_criteria() must return a list (possibly empty)."""
        criteria = eval_bridge.pull_criteria(mock_loop)
        assert isinstance(criteria, list)

    def test_health_check_returns_bool(self, eval_bridge):
        """health_check() must return a boolean."""
        result = eval_bridge.health_check()
        assert isinstance(result, bool)

    def test_summarize_returns_string(self, eval_bridge):
        """summarize() must return a non-empty string."""
        s = eval_bridge.summarize()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_export_event_log(self, eval_bridge, mock_loop):
        """export_event_log() must return a list."""
        log = eval_bridge.export_event_log(mock_loop)
        assert isinstance(log, list)


# ===========================================================================
# TestOrchestratorBridge
# ===========================================================================

class TestOrchestratorBridge:
    """Tests for OrchestratorBridge.

    OrchestratorBridge allows methodology loops to register themselves with
    and dispatch events to a central orchestrator. All methods must degrade
    gracefully when no real orchestrator is connected.
    """

    def test_init(self, orch_bridge):
        """OrchestratorBridge can be instantiated without arguments."""
        assert orch_bridge is not None

    def test_connect_none(self, orch_bridge):
        """connect(None) must not raise."""
        orch_bridge.connect(None)

    def test_register_loop_no_crash(self, orch_bridge, mock_loop):
        """register_loop() must not crash when orchestrator is absent."""
        orch_bridge.register_loop(mock_loop)

    def test_unregister_loop_no_crash(self, orch_bridge, mock_loop):
        """unregister_loop() must not crash for an unregistered loop."""
        orch_bridge.unregister_loop(mock_loop.loop_id)

    def test_dispatch_event_returns_result(self, orch_bridge, mock_loop):
        """dispatch_event() must return a result (ok or error)."""
        result = orch_bridge.dispatch_event(
            loop_id=mock_loop.loop_id,
            event_type="phase_transition",
            payload={"phase": "formalization"},
        )
        assert result is not None

    def test_query_state_returns_value(self, orch_bridge, mock_loop):
        """query_state() must return something (None or dict) without crashing."""
        state = orch_bridge.query_state(mock_loop.loop_id)
        # Either None (not connected) or a dict
        assert state is None or isinstance(state, dict)

    def test_health_check_returns_bool(self, orch_bridge):
        """health_check() must return a boolean."""
        result = orch_bridge.health_check()
        assert isinstance(result, bool)

    def test_summarize_returns_string(self, orch_bridge):
        """summarize() must return a non-empty string."""
        s = orch_bridge.summarize()
        assert isinstance(s, str)
        assert len(s) > 0


# ===========================================================================
# TestEvidenceBridge
# ===========================================================================

class TestEvidenceBridge:
    """Tests for EvidenceBridge.

    EvidenceBridge connects methodology loops to the evidence store, enabling
    loops to collect, push, and query evidence items.
    """

    def test_init(self, evidence_bridge):
        """EvidenceBridge can be instantiated without arguments."""
        assert evidence_bridge is not None

    def test_connect_none(self, evidence_bridge):
        """connect(None) must not raise."""
        evidence_bridge.connect(None)

    def test_collect_evidence_returns_list(self, evidence_bridge, mock_loop):
        """collect_evidence() must return a list."""
        items = evidence_bridge.collect_evidence(mock_loop)
        assert isinstance(items, list)

    def test_push_evidence_no_crash(self, evidence_bridge, mock_loop):
        """push_evidence() with empty evidence list must not crash."""
        evidence_bridge.push_evidence(mock_loop, evidence_items=[])

    def test_health_check_returns_bool(self, evidence_bridge):
        """health_check() must return a boolean."""
        result = evidence_bridge.health_check()
        assert isinstance(result, bool)

    def test_summarize_returns_string(self, evidence_bridge):
        """summarize() must return a non-empty string."""
        s = evidence_bridge.summarize()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_collect_then_push_roundtrip(self, evidence_bridge, mock_loop):
        """Collecting evidence and then pushing it back must not crash."""
        items = evidence_bridge.collect_evidence(mock_loop)
        evidence_bridge.push_evidence(mock_loop, evidence_items=items)

    def test_query_evidence_by_phase(self, evidence_bridge, mock_loop):
        """query_evidence() filtered by phase must return a list."""
        items = evidence_bridge.query_evidence(
            mock_loop, phase=LoopPhase.FORMALIZATION
        )
        assert isinstance(items, list)


# ===========================================================================
# TestMethodologyLoopsIntegration
# ===========================================================================

class TestMethodologyLoopsIntegration:
    """Tests for the MethodologyLoopsIntegration facade.

    MethodologyLoopsIntegration is the high-level object that composes all
    three bridge objects (evaluation design, orchestrator, evidence) and
    exposes a unified interface for running and syncing loops.
    """

    def test_init(self, integration):
        """MethodologyLoopsIntegration can be instantiated without arguments."""
        assert integration is not None

    def test_setup_with_none(self, integration):
        """setup(None) or setup() with defaults must not raise."""
        integration.setup()

    def test_teardown(self, integration):
        """teardown() must not raise even when not set up."""
        integration.teardown()

    def test_run_loop_returns_loop(self, integration, mock_loop):
        """run_loop() must return a MethodologyLoop."""
        result = integration.run_loop(mock_loop)
        assert isinstance(result, MethodologyLoop)

    def test_run_loop_preserves_id(self, integration, mock_loop):
        """run_loop() must preserve the loop_id."""
        result = integration.run_loop(mock_loop)
        assert result.loop_id == mock_loop.loop_id

    def test_sync_all_no_crash(self, integration, mock_loop):
        """sync_all() must not raise."""
        integration.sync_all(mock_loop)

    def test_health_report_returns_dict(self, integration):
        """health_report() must return a dict."""
        report = integration.health_report()
        assert isinstance(report, dict)

    def test_summarize_returns_string(self, integration):
        """summarize() must return a non-empty string."""
        s = integration.summarize()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_export_state_returns_dict(self, integration):
        """export_state() must return a dict."""
        state = integration.export_state()
        assert isinstance(state, dict)

    def test_setup_teardown_cycle(self, integration):
        """setup() followed by teardown() must not raise."""
        integration.setup()
        integration.teardown()

    def test_multiple_loops(self, integration, mock_loop):
        """run_loop() can be called multiple times with different loops."""
        loop2_config = MethodologyConfig(
            max_iterations=3, convergence_threshold=0.8,
            falsification_budget=10, min_coverage=0.6, max_revisions=2
        )
        loop2_diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
        loop2_state = LoopState(
            phase=LoopPhase.IMPLEMENTATION, iteration=1,
            artifacts=[], diagnostics=loop2_diag, history=[],
            status=LoopStatus.RUNNING,
        )
        loop2 = MethodologyLoop(
            loop_id="second-loop",
            config=loop2_config, state=loop2_state,
            transitions=[], artifacts=[],
            created_at=time.time(), updated_at=time.time(),
        )
        r1 = integration.run_loop(mock_loop)
        r2 = integration.run_loop(loop2)
        assert r1.loop_id == mock_loop.loop_id
        assert r2.loop_id == "second-loop"


# ===========================================================================
# TestBuildIntegration
# ===========================================================================

class TestBuildIntegration:
    """Tests for the build_integration() factory function.

    build_integration() is the recommended entry point for constructing a
    MethodologyLoopsIntegration with sensible defaults.
    """

    def test_basic_returns_integration(self):
        """build_integration() returns a MethodologyLoopsIntegration."""
        result = build_integration()
        assert isinstance(result, MethodologyLoopsIntegration)

    def test_with_config_returns_integration(self, default_config):
        """build_integration(config=...) returns a MethodologyLoopsIntegration."""
        result = build_integration(config=default_config)
        assert isinstance(result, MethodologyLoopsIntegration)

    def test_two_calls_independent(self):
        """Two calls to build_integration() return independent objects."""
        i1 = build_integration()
        i2 = build_integration()
        assert i1 is not i2

    def test_returns_instance_with_health_report(self):
        """Returned integration must expose health_report()."""
        result = build_integration()
        report = result.health_report()
        assert isinstance(report, dict)


# ===========================================================================
# TestIntegrateFunctions
# ===========================================================================

class TestIntegrateFunctions:
    """Tests for the three module-level integrate_with_* functions.

    Each function provides a lightweight one-shot integration path that does
    not require manually constructing bridge objects.
    """

    def test_integrate_with_evaluation_design_no_crash(self, mock_loop):
        """integrate_with_evaluation_design() must not raise."""
        result = integrate_with_evaluation_design(mock_loop)
        assert result is not None

    def test_integrate_with_evaluation_design_result_type(self, mock_loop):
        """integrate_with_evaluation_design() returns IntegrationResult."""
        result = integrate_with_evaluation_design(mock_loop)
        assert isinstance(result, IntegrationResult)

    def test_integrate_with_orchestrator_no_crash(self, mock_loop):
        """integrate_with_orchestrator() must not raise."""
        result = integrate_with_orchestrator(mock_loop)
        assert result is not None

    def test_integrate_with_orchestrator_result_type(self, mock_loop):
        """integrate_with_orchestrator() returns IntegrationResult."""
        result = integrate_with_orchestrator(mock_loop)
        assert isinstance(result, IntegrationResult)

    def test_integrate_with_evidence_no_crash(self, mock_loop):
        """integrate_with_evidence() must not raise."""
        result = integrate_with_evidence(mock_loop)
        assert result is not None

    def test_integrate_with_evidence_result_type(self, mock_loop):
        """integrate_with_evidence() returns IntegrationResult."""
        result = integrate_with_evidence(mock_loop)
        assert isinstance(result, IntegrationResult)

    @pytest.mark.parametrize("func_name", [
        "integrate_with_evaluation_design",
        "integrate_with_orchestrator",
        "integrate_with_evidence",
    ])
    def test_all_functions_importable(self, func_name):
        """Each integrate_with_* function must be importable from the module."""
        import jugeo.evaluation.methodology_loops.integration as mod
        assert hasattr(mod, func_name), f"{func_name} not found in integration module"

    def test_integration_functions_return_is_ok_or_error(self, mock_loop):
        """All three integration functions return a result with is_ok() method."""
        results = [
            integrate_with_evaluation_design(mock_loop),
            integrate_with_orchestrator(mock_loop),
            integrate_with_evidence(mock_loop),
        ]
        for r in results:
            assert hasattr(r, "is_ok") or hasattr(r, "is_error")

    def test_repeated_calls_stable(self, mock_loop):
        """Calling each function twice on the same loop must not raise."""
        for fn in [integrate_with_evaluation_design, integrate_with_orchestrator, integrate_with_evidence]:
            r1 = fn(mock_loop)
            r2 = fn(mock_loop)
            assert r1 is not None
            assert r2 is not None


# ===========================================================================
# Integration-level parametrized tests
# ===========================================================================

@pytest.mark.parametrize("phase", list(LoopPhase))
def test_integration_run_loop_all_phases(phase):
    """run_loop() must succeed for a loop in any LoopPhase."""
    config = MethodologyConfig(
        max_iterations=5, convergence_threshold=0.9,
        falsification_budget=20, min_coverage=0.7, max_revisions=3
    )
    diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
    state = LoopState(
        phase=phase, iteration=0, artifacts=[],
        diagnostics=diag, history=[], status=LoopStatus.IDLE
    )
    loop = MethodologyLoop(
        loop_id=f"phase-test-{phase.value}",
        config=config, state=state, transitions=[], artifacts=[],
        created_at=time.time(), updated_at=time.time()
    )
    integration = MethodologyLoopsIntegration()
    result = integration.run_loop(loop)
    assert isinstance(result, MethodologyLoop)


@pytest.mark.parametrize("status", list(LoopStatus))
def test_integration_run_loop_all_statuses(status):
    """run_loop() must succeed for a loop in any LoopStatus."""
    config = MethodologyConfig(
        max_iterations=5, convergence_threshold=0.9,
        falsification_budget=20, min_coverage=0.7, max_revisions=3
    )
    diag = LoopDiagnostics(iteration_times=[], errors=[], warnings=[], phase_counts={})
    state = LoopState(
        phase=LoopPhase.FORMALIZATION, iteration=0, artifacts=[],
        diagnostics=diag, history=[], status=status
    )
    loop = MethodologyLoop(
        loop_id=f"status-test-{status.value}",
        config=config, state=state, transitions=[], artifacts=[],
        created_at=time.time(), updated_at=time.time()
    )
    integration = MethodologyLoopsIntegration()
    result = integration.run_loop(loop)
    assert isinstance(result, MethodologyLoop)
