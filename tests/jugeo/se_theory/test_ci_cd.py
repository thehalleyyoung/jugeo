"""Tests for jugeo.se_theory.ci_cd."""
from __future__ import annotations

import pytest

from jugeo.se_theory.ci_cd.models import (
    Certificate,
    IncrementalScope,
    PipelineResult,
    PipelineStage,
    StageRequirement,
    StageResult,
    VerificationTask,
)
from jugeo.se_theory.ci_cd.algorithms import (
    CertificateEmitter,
    IncrementalVerifier,
    PipelinePlanner,
    StageExecutor,
)


# ===================================================================
# TestPipelinePlanner
# ===================================================================


class TestPipelinePlanner:
    """Tests for PipelinePlanner."""

    def test_scope_for_change_basic(self) -> None:
        planner = PipelinePlanner()
        scope = planner.scope_for_change(["A"], {"A": ["B"], "B": []})
        assert "A" in scope.affected_coordinates
        assert "B" in scope.affected_coordinates

    def test_scope_for_change_transitive(self) -> None:
        planner = PipelinePlanner()
        morphisms = {"A": ["B"], "B": ["C"], "C": []}
        scope = planner.scope_for_change(["A"], morphisms)
        assert set(scope.affected_coordinates) == {"A", "B", "C"}

    def test_plan_incremental(self) -> None:
        planner = PipelinePlanner()
        reqs = [
            StageRequirement(stage=PipelineStage.PRE_COMMIT),
            StageRequirement(stage=PipelineStage.PRE_MERGE),
            StageRequirement(stage=PipelineStage.POST_MERGE),
        ]
        tasks = planner.plan_incremental(
            changed_coords=["A", "B"],
            morphisms={"A": ["B"], "B": []},
            evidence={},
            stage_requirements=reqs,
        )
        assert len(tasks) > 0
        assert all(isinstance(t, VerificationTask) for t in tasks)

    def test_plan_full(self) -> None:
        planner = PipelinePlanner()
        reqs = [
            StageRequirement(stage=PipelineStage.PRE_COMMIT),
            StageRequirement(stage=PipelineStage.PRE_MERGE),
            StageRequirement(stage=PipelineStage.POST_MERGE),
            StageRequirement(stage=PipelineStage.RELEASE_GATE),
        ]
        tasks = planner.plan_full(
            all_coords=["A", "B", "C"],
            morphisms={},
            evidence={},
            stage_requirements=reqs,
        )
        assert len(tasks) > 0

    def test_estimate_duration(self) -> None:
        planner = PipelinePlanner()
        task = VerificationTask(
            coordinates=["c1", "c2", "c3"],
            overlaps_to_check=["o1", "o2"],
        )
        result = planner._estimate_duration(task)
        assert result == pytest.approx(3.0 * 1.0 + 2.0 * 0.5)


# ===================================================================
# TestStageExecutor
# ===================================================================


class TestStageExecutor:
    """Tests for StageExecutor."""

    def test_execute_stage_all_pass(self) -> None:
        executor = StageExecutor()
        tasks = [
            VerificationTask(
                coordinates=["c1", "c2"],
                trust_target="claim",
            ),
        ]
        sections = {
            "c1": {"trust": "proof", "propositions": ["p1"]},
            "c2": {"trust": "heuristic", "propositions": ["p1"]},
        }
        result = executor.execute_stage(tasks, sections, ["p1"])
        assert result.passed is True
        assert result.tasks_failed == 0

    def test_execute_stage_missing_coord(self) -> None:
        executor = StageExecutor()
        tasks = [
            VerificationTask(coordinates=["c1", "missing"], trust_target="claim"),
        ]
        sections = {"c1": {"trust": "proof"}}
        result = executor.execute_stage(tasks, sections, [])
        assert result.passed is False
        assert result.tasks_failed == 1
        assert any("missing" in o for o in result.obstructions_found)

    def test_execute_stage_low_trust(self) -> None:
        executor = StageExecutor()
        tasks = [
            VerificationTask(coordinates=["c1"], trust_target="proof"),
        ]
        sections = {"c1": {"trust": "claim"}}
        result = executor.execute_stage(tasks, sections, [])
        assert result.passed is False

    def test_check_stage_requirements_pass(self) -> None:
        executor = StageExecutor()
        result = StageResult(
            trust_achieved="proof",
            coverage_achieved=0.8,
            duration_s=10.0,
        )
        req = StageRequirement(
            trust_minimum="heuristic",
            required_coverage=0.5,
            max_duration_s=60.0,
        )
        assert executor.check_stage_requirements(result, req) is True

    def test_check_stage_requirements_fail_trust(self) -> None:
        executor = StageExecutor()
        result = StageResult(trust_achieved="claim", coverage_achieved=1.0)
        req = StageRequirement(trust_minimum="proof")
        assert executor.check_stage_requirements(result, req) is False


# ===================================================================
# TestCertificateEmitter
# ===================================================================


class TestCertificateEmitter:
    """Tests for CertificateEmitter."""

    def test_emit_certificate_success(self) -> None:
        emitter = CertificateEmitter()
        pipeline = PipelineResult(overall_passed=True)
        cert = emitter.emit_certificate(
            pipeline,
            trust_levels={"c1": "proof"},
            coverage=0.95,
            obligations=[],
        )
        assert isinstance(cert, Certificate)
        assert cert.signature != ""

    def test_emit_certificate_failed_pipeline(self) -> None:
        emitter = CertificateEmitter()
        pipeline = PipelineResult(overall_passed=False)
        with pytest.raises(ValueError):
            emitter.emit_certificate(pipeline, {}, 0.0, [])

    def test_verify_certificate_valid(self) -> None:
        emitter = CertificateEmitter()
        pipeline = PipelineResult(overall_passed=True)
        cert = emitter.emit_certificate(
            pipeline,
            trust_levels={"c1": "proof"},
            coverage=0.95,
            obligations=[],
        )
        assert emitter.verify_certificate(cert, {"c1": "proof"}, 0.95) is True

    def test_verify_certificate_tampered(self) -> None:
        emitter = CertificateEmitter()
        pipeline = PipelineResult(overall_passed=True)
        cert = emitter.emit_certificate(
            pipeline,
            trust_levels={"c1": "proof"},
            coverage=0.95,
            obligations=[],
        )
        cert.signature = "tampered"
        assert emitter.verify_certificate(cert, {"c1": "proof"}, 0.95) is False

    def test_compute_signature_deterministic(self) -> None:
        emitter = CertificateEmitter()
        data = {"a": 1, "b": [2, 3]}
        sig1 = emitter._compute_signature(data)
        sig2 = emitter._compute_signature(data)
        assert sig1 == sig2

    def test_certificates_for_coordinate(self) -> None:
        emitter = CertificateEmitter()
        certs = [
            Certificate(id="cert1", trust_levels={"c1": "proof", "c2": "claim"}),
            Certificate(id="cert2", trust_levels={"c3": "heuristic"}),
        ]
        result = emitter.certificates_for_coordinate("c1", certs)
        assert len(result) == 1
        assert result[0].id == "cert1"

    def test_invalidate_certificate(self) -> None:
        emitter = CertificateEmitter()
        pipeline = PipelineResult(overall_passed=True)
        cert = emitter.emit_certificate(pipeline, {"c1": "proof"}, 1.0, [])
        emitter.invalidate_certificate(cert.id, "test reason")
        assert emitter.verify_certificate(cert, {"c1": "proof"}, 1.0) is False


# ===================================================================
# TestIncrementalVerifier
# ===================================================================


class TestIncrementalVerifier:
    """Tests for IncrementalVerifier."""

    def test_verify_change_all_pass(self) -> None:
        verifier = IncrementalVerifier()
        sections = {
            "c1": {"trust": "proof"},
            "c2": {"trust": "heuristic"},
        }
        reqs = [
            StageRequirement(stage=PipelineStage.PRE_COMMIT, trust_minimum="claim"),
            StageRequirement(stage=PipelineStage.PRE_MERGE, trust_minimum="claim"),
            StageRequirement(stage=PipelineStage.POST_MERGE, trust_minimum="claim"),
        ]
        result = verifier.verify_change(
            changed_coords=["c1"],
            morphisms={"c1": ["c2"], "c2": []},
            evidence={},
            requirements=reqs,
            sections=sections,
        )
        assert result.overall_passed is True
        assert result.certificate_issued is True

    def test_verify_change_missing_coord(self) -> None:
        verifier = IncrementalVerifier()
        sections = {"c1": {"trust": "proof"}}
        reqs = [
            StageRequirement(stage=PipelineStage.PRE_COMMIT, trust_minimum="claim"),
            StageRequirement(stage=PipelineStage.PRE_MERGE, trust_minimum="claim"),
            StageRequirement(stage=PipelineStage.POST_MERGE, trust_minimum="claim"),
        ]
        result = verifier.verify_change(
            changed_coords=["c1"],
            morphisms={"c1": ["c2"], "c2": []},
            evidence={},
            requirements=reqs,
            sections=sections,
        )
        assert result.overall_passed is False

    def test_with_cache(self) -> None:
        verifier = IncrementalVerifier()
        cache: dict[str, tuple[bool, list[str]]] = {"key1": (True, [])}
        returned = verifier.with_cache(cache)
        assert returned is verifier
        assert verifier._cache == cache


# ===================================================================
# TestModels
# ===================================================================


class TestModels:
    """Serialisation round-trip tests for ci_cd models."""

    def test_stage_requirement_serialization(self) -> None:
        req = StageRequirement(
            stage=PipelineStage.PRE_MERGE,
            scope="PACKAGE",
            trust_minimum="heuristic",
        )
        d = req.to_dict()
        req2 = StageRequirement.from_dict(d)
        assert req2.stage == PipelineStage.PRE_MERGE
        assert req2.scope == "PACKAGE"
        assert req2.to_dict() == d

    def test_verification_task_serialization(self) -> None:
        task = VerificationTask(
            id="task1",
            stage=PipelineStage.POST_MERGE,
            coordinates=["c1", "c2"],
        )
        d = task.to_dict()
        task2 = VerificationTask.from_dict(d)
        assert task2.id == "task1"
        assert task2.stage == PipelineStage.POST_MERGE
        assert task2.to_dict() == d

    def test_stage_result_serialization(self) -> None:
        result = StageResult(
            stage=PipelineStage.RELEASE_GATE,
            tasks_run=5,
            tasks_passed=4,
            tasks_failed=1,
            passed=False,
        )
        d = result.to_dict()
        result2 = StageResult.from_dict(d)
        assert result2.stage == PipelineStage.RELEASE_GATE
        assert result2.tasks_failed == 1
        assert result2.to_dict() == d

    def test_pipeline_result_serialization(self) -> None:
        pr = PipelineResult(
            stages=[StageResult(stage=PipelineStage.PRE_COMMIT, passed=True)],
            overall_passed=True,
            certificate_issued=True,
            certificate_id="cert1",
        )
        d = pr.to_dict()
        pr2 = PipelineResult.from_dict(d)
        assert pr2.overall_passed is True
        assert pr2.certificate_id == "cert1"
        assert len(pr2.stages) == 1

    def test_certificate_serialization(self) -> None:
        cert = Certificate(
            id="cert1",
            site_id="site1",
            trust_levels={"c1": "proof"},
            coverage=0.95,
        )
        d = cert.to_dict()
        cert2 = Certificate.from_dict(d)
        assert cert2.id == "cert1"
        assert cert2.trust_levels == {"c1": "proof"}
        assert cert2.to_dict() == d

    def test_incremental_scope_serialization(self) -> None:
        scope = IncrementalScope(
            change_id="ch1",
            affected_coordinates=["c1", "c2"],
            stages_needed=[PipelineStage.PRE_COMMIT, PipelineStage.PRE_MERGE],
        )
        d = scope.to_dict()
        scope2 = IncrementalScope.from_dict(d)
        assert scope2.change_id == "ch1"
        assert scope2.stages_needed == [
            PipelineStage.PRE_COMMIT,
            PipelineStage.PRE_MERGE,
        ]
        assert scope2.to_dict() == d
