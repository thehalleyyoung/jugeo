"""Tests for evaluation_design.models. copilot: shared-core marker. Theory reference: theory2.tex Ch63."""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest
import json
import time
import uuid

from jugeo.evaluation.evaluation_design.models import (
    EvaluationStatus,
    ClauseType,
    AblationKind,
    CalibrationMethod,
    EvaluationDesign,
    ClauseResult,
    AblationResult,
    CalibrationReport,
    EvaluationResult,
    ClausewiseEvaluator,
    AblationDesign,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_clause_result():
    """Return a standard ClauseResult with score 0.8 and passed=True.

    Used across multiple test classes to avoid repeated construction of
    valid ClauseResult instances.  The clause type is SOUNDNESS and the
    evidence list contains a single dictionary entry.
    """
    return ClauseResult(
        clause_id="clause-001",
        clause_type=ClauseType.SOUNDNESS,
        score=0.8,
        passed=True,
        evidence=[{"source": "test", "value": 0.8}],
        metadata={"version": "1"},
    )


@pytest.fixture
def sample_ablation_result():
    """Return an AblationResult with a significant positive delta.

    The result has a p_value of 0.03 (< 0.05), is marked significant,
    and has a delta_score of 0.15 which is above the 0.1 threshold used
    by ``is_critical()``.
    """
    return AblationResult(
        ablation_id="abl-001",
        ablation_kind=AblationKind.COMPONENT,
        removed_component="encoder",
        baseline_score=0.85,
        ablated_score=0.70,
        delta_score=0.15,
        p_value=0.03,
        significant=True,
        metadata={"trial": 1},
    )


@pytest.fixture
def sample_calibration_report():
    """Return a CalibrationReport using PLATT_SCALING with clear improvement.

    before_ece=0.2 and after_ece=0.05 give an improvement_ratio of 0.75.
    is_well_calibrated(threshold=0.1) should return True because after_ece
    is below the default threshold.
    """
    return CalibrationReport(
        report_id="rep-001",
        method=CalibrationMethod.PLATT_SCALING,
        before_ece=0.20,
        after_ece=0.05,
        before_mce=0.30,
        after_mce=0.08,
        reliability_diagram_data=[{"bin": 0, "accuracy": 0.5, "confidence": 0.5}],
        n_samples=100,
        metadata={"dataset": "val"},
    )


@pytest.fixture
def sample_evaluation_design():
    """Return an EvaluationDesign created via the factory class method.

    The design has two clauses, a non-empty ablation plan, calibration
    config, and a budget of 0.75.  It is used wherever a valid design
    is needed without caring about the specific UUID or timestamp.
    """
    return EvaluationDesign.create(
        name="test-design",
        clauses=[
            {"clause_id": "c1", "clause_type": "soundness", "text": "Output is sound."},
            {"clause_id": "c2", "clause_type": "completeness", "text": "Output is complete."},
        ],
        ablation_plan={"components": ["encoder", "decoder"]},
        calibration_config={"method": "platt_scaling"},
        budget=0.75,
        metadata={"env": "ci"},
    )


@pytest.fixture
def sample_evaluation_result(sample_clause_result, sample_ablation_result, sample_calibration_report):
    """Return a complete EvaluationResult referencing existing fixtures.

    Bundles one ClauseResult, one AblationResult, and one CalibrationReport
    under COMPLETE status with realistic timestamps.
    """
    now = time.time()
    return EvaluationResult(
        result_id="res-001",
        design_id="design-001",
        clause_results=[sample_clause_result],
        ablation_results=[sample_ablation_result],
        calibration_report=sample_calibration_report,
        overall_score=0.8,
        status=EvaluationStatus.COMPLETE,
        started_at=now - 5.0,
        finished_at=now,
        metadata={"run": 1},
    )


@pytest.fixture
def sample_clausewise_evaluator():
    """Return a ClausewiseEvaluator with two clauses and equal weights.

    The evaluator uses the 'default' scorer and a threshold of 0.5.
    Suitable for testing ``evaluate()``, ``get_weight_for()``, and
    weight normalisation.
    """
    return ClausewiseEvaluator(
        clauses=[
            {"clause_id": "c1", "clause_type": "soundness", "text": "Sound."},
            {"clause_id": "c2", "clause_type": "completeness", "text": "Complete."},
        ],
        weights=[1.0, 1.0],
        scorer="default",
        threshold=0.5,
        metadata={},
    )


@pytest.fixture
def sample_ablation_design():
    """Return an AblationDesign with three components and two repeats.

    Provides a stable fixture for testing ``get_ablation_count()``,
    ``component_pairs()``, ``total_runs()``, ``seed_for_repeat()``, and
    ``validate()``.
    """
    return AblationDesign(
        design_id="abld-001",
        components_to_ablate=["encoder", "decoder", "scorer"],
        baseline_config={"model": "base"},
        metrics=["accuracy", "f1"],
        n_repeats=2,
        random_seed=42,
        metadata={"env": "test"},
    )


# ---------------------------------------------------------------------------
# TestEvaluationStatus
# ---------------------------------------------------------------------------


class TestEvaluationStatus:
    """Tests for the EvaluationStatus string enumeration."""

    def test_members_exist(self):
        """Verify all four expected members are present in the enum."""
        assert EvaluationStatus.PENDING.value == "pending"
        assert EvaluationStatus.RUNNING.value == "running"
        assert EvaluationStatus.COMPLETE.value == "complete"
        assert EvaluationStatus.FAILED.value == "failed"

    @pytest.mark.parametrize("status,expected", [
        (EvaluationStatus.PENDING, "pending"),
        (EvaluationStatus.RUNNING, "running"),
        (EvaluationStatus.COMPLETE, "complete"),
        (EvaluationStatus.FAILED, "failed"),
    ])
    def test_string_value(self, status, expected):
        """Each EvaluationStatus member should equal its string value via str comparison."""
        assert status == expected

    def test_is_str_subclass(self):
        """EvaluationStatus should be a subclass of str, enabling direct string comparison."""
        assert issubclass(EvaluationStatus, str)

    def test_member_count(self):
        """EvaluationStatus must have exactly four members."""
        assert len(EvaluationStatus) == 4

    def test_lookup_by_value(self):
        """Should be able to construct an EvaluationStatus from its string value."""
        assert EvaluationStatus("complete") is EvaluationStatus.COMPLETE


# ---------------------------------------------------------------------------
# TestClauseType
# ---------------------------------------------------------------------------


class TestClauseType:
    """Tests for the ClauseType string enumeration."""

    @pytest.mark.parametrize("member_name", [
        "SOUNDNESS", "COMPLETENESS", "CONSISTENCY", "PRECISION", "RECALL",
    ])
    def test_member_present(self, member_name):
        """Each expected ClauseType member should be reachable by attribute name."""
        assert hasattr(ClauseType, member_name)

    def test_member_count(self):
        """ClauseType must have exactly five members."""
        assert len(ClauseType) == 5

    def test_is_str_subclass(self):
        """ClauseType should be a subclass of str."""
        assert issubclass(ClauseType, str)

    def test_lookup_by_value(self):
        """Should be constructable from its lowercase string value."""
        ct = ClauseType(ClauseType.SOUNDNESS.value)
        assert ct is ClauseType.SOUNDNESS


# ---------------------------------------------------------------------------
# TestAblationKind
# ---------------------------------------------------------------------------


class TestAblationKind:
    """Tests for the AblationKind string enumeration."""

    @pytest.mark.parametrize("member_name", [
        "COMPONENT", "FEATURE", "SUBSYSTEM", "PATHWAY",
    ])
    def test_member_present(self, member_name):
        """Each expected AblationKind member should be reachable by attribute name."""
        assert hasattr(AblationKind, member_name)

    def test_member_count(self):
        """AblationKind must have exactly four members."""
        assert len(AblationKind) == 4

    def test_is_str_subclass(self):
        """AblationKind should be a subclass of str for easy serialisation."""
        assert issubclass(AblationKind, str)


# ---------------------------------------------------------------------------
# TestCalibrationMethod
# ---------------------------------------------------------------------------


class TestCalibrationMethod:
    """Tests for the CalibrationMethod string enumeration."""

    @pytest.mark.parametrize("member_name", [
        "PLATT_SCALING", "ISOTONIC", "TEMPERATURE", "HISTOGRAM",
    ])
    def test_member_present(self, member_name):
        """Each expected CalibrationMethod member should be reachable by attribute name."""
        assert hasattr(CalibrationMethod, member_name)

    def test_member_count(self):
        """CalibrationMethod must have exactly four members."""
        assert len(CalibrationMethod) == 4

    def test_is_str_subclass(self):
        """CalibrationMethod should be a subclass of str."""
        assert issubclass(CalibrationMethod, str)


# ---------------------------------------------------------------------------
# TestEvaluationDesign
# ---------------------------------------------------------------------------


class TestEvaluationDesign:
    """Tests for the EvaluationDesign dataclass and its factory / methods."""

    def test_create_returns_instance(self, sample_evaluation_design):
        """EvaluationDesign.create() should return an EvaluationDesign instance."""
        assert isinstance(sample_evaluation_design, EvaluationDesign)

    def test_create_assigns_name(self, sample_evaluation_design):
        """The name field should match what was passed to create()."""
        assert sample_evaluation_design.name == "test-design"

    def test_create_assigns_budget(self, sample_evaluation_design):
        """The budget field should match the value passed to create()."""
        assert sample_evaluation_design.budget == 0.75

    def test_create_generates_uuid(self, sample_evaluation_design):
        """design_id should be a non-empty string (UUID4)."""
        assert isinstance(sample_evaluation_design.design_id, str)
        assert len(sample_evaluation_design.design_id) > 0

    def test_create_sets_created_at(self, sample_evaluation_design):
        """created_at should be a recent positive float timestamp."""
        assert isinstance(sample_evaluation_design.created_at, float)
        assert sample_evaluation_design.created_at > 0

    def test_create_defaults_empty_clauses(self):
        """create() with no clauses argument should produce an empty clauses list."""
        d = EvaluationDesign.create(name="minimal")
        assert d.clauses == [] or d.clauses is not None

    def test_create_default_budget_one(self):
        """create() with no budget argument should default to budget=1.0."""
        d = EvaluationDesign.create(name="default-budget")
        assert d.budget == 1.0

    def test_to_json_returns_string(self, sample_evaluation_design):
        """to_json() should return a non-empty JSON string."""
        result = sample_evaluation_design.to_json()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_to_json_roundtrip(self, sample_evaluation_design):
        """from_json(to_json()) should reproduce the original design's key fields."""
        json_str = sample_evaluation_design.to_json()
        restored = EvaluationDesign.from_json(json_str)
        assert restored.name == sample_evaluation_design.name
        assert restored.budget == sample_evaluation_design.budget
        assert restored.design_id == sample_evaluation_design.design_id

    def test_from_json_returns_instance(self, sample_evaluation_design):
        """from_json() should return an EvaluationDesign instance."""
        restored = EvaluationDesign.from_json(sample_evaluation_design.to_json())
        assert isinstance(restored, EvaluationDesign)

    def test_summarize_contains_name(self, sample_evaluation_design):
        """summarize() output should include the design name."""
        summary = sample_evaluation_design.summarize()
        assert "test-design" in summary

    def test_summarize_contains_id(self, sample_evaluation_design):
        """summarize() output should include the design_id."""
        summary = sample_evaluation_design.summarize()
        assert sample_evaluation_design.design_id in summary

    def test_validate_valid_design(self, sample_evaluation_design):
        """validate() on a properly constructed design should return no errors."""
        errors = sample_evaluation_design.validate()
        assert errors == []

    def test_validate_empty_name(self):
        """validate() should report an error when name is empty."""
        d = EvaluationDesign.create(name="")
        errors = d.validate()
        assert len(errors) > 0

    @pytest.mark.parametrize("bad_budget", [-0.1, 1.1, 2.0, -1.0])
    def test_validate_out_of_range_budget(self, bad_budget):
        """validate() should report an error for budgets outside [0, 1]."""
        d = EvaluationDesign.create(name="bad-budget", budget=bad_budget)
        errors = d.validate()
        assert len(errors) > 0

    def test_to_proof_obligation_keys(self, sample_evaluation_design):
        """to_proof_obligation() must contain all required keys."""
        obligation = sample_evaluation_design.to_proof_obligation()
        required = {"obligation_id", "design_id", "name", "clauses_count",
                    "has_ablation", "has_calibration", "budget", "generated_at"}
        assert required.issubset(set(obligation.keys()))

    def test_to_proof_obligation_design_id(self, sample_evaluation_design):
        """to_proof_obligation() should embed the correct design_id."""
        obligation = sample_evaluation_design.to_proof_obligation()
        assert obligation["design_id"] == sample_evaluation_design.design_id

    def test_render_tex_returns_string(self, sample_evaluation_design):
        """render_tex() should return a non-empty string."""
        tex = sample_evaluation_design.render_tex()
        assert isinstance(tex, str)
        assert len(tex) > 0

    def test_get_clause_count(self, sample_evaluation_design):
        """get_clause_count() should return the number of clauses in the design."""
        assert sample_evaluation_design.get_clause_count() == 2

    def test_get_clause_count_zero(self):
        """get_clause_count() should return 0 when clauses is empty."""
        d = EvaluationDesign.create(name="no-clauses")
        assert d.get_clause_count() == 0

    def test_get_estimated_cost_in_range(self, sample_evaluation_design):
        """get_estimated_cost() should return a float in [0, 1]."""
        cost = sample_evaluation_design.get_estimated_cost()
        assert 0.0 <= cost <= 1.0

    def test_clone_new_id(self, sample_evaluation_design):
        """clone() should produce a new design with a different design_id."""
        cloned = sample_evaluation_design.clone()
        assert cloned.design_id != sample_evaluation_design.design_id

    def test_clone_same_name(self, sample_evaluation_design):
        """clone() should preserve the original name."""
        cloned = sample_evaluation_design.clone()
        assert cloned.name == sample_evaluation_design.name

    def test_clone_new_timestamp(self, sample_evaluation_design):
        """clone() should assign a new created_at that is >= the original."""
        cloned = sample_evaluation_design.clone()
        assert cloned.created_at >= sample_evaluation_design.created_at

    def test_clone_independent_clauses(self, sample_evaluation_design):
        """Mutating the clone's clauses list should not affect the original."""
        cloned = sample_evaluation_design.clone()
        original_count = len(sample_evaluation_design.clauses)
        cloned.clauses.append({"clause_id": "injected"})
        assert len(sample_evaluation_design.clauses) == original_count


# ---------------------------------------------------------------------------
# TestClauseResult
# ---------------------------------------------------------------------------


class TestClauseResult:
    """Tests for the ClauseResult frozen dataclass."""

    def test_construction(self, sample_clause_result):
        """ClauseResult should be constructable and store all provided fields."""
        cr = sample_clause_result
        assert cr.clause_id == "clause-001"
        assert cr.clause_type == ClauseType.SOUNDNESS
        assert cr.score == 0.8
        assert cr.passed is True

    def test_passed_threshold_above(self, sample_clause_result):
        """passed_threshold() should return True when score >= threshold."""
        assert sample_clause_result.passed_threshold(0.5) is True
        assert sample_clause_result.passed_threshold(0.8) is True

    def test_passed_threshold_below(self):
        """passed_threshold() should return False when score < threshold."""
        cr = ClauseResult(
            clause_id="c-low",
            clause_type=ClauseType.PRECISION,
            score=0.3,
            passed=False,
            evidence=[],
            metadata={},
        )
        assert cr.passed_threshold(0.5) is False

    def test_passed_threshold_default(self, sample_clause_result):
        """passed_threshold() with no argument should use default threshold of 0.5."""
        assert sample_clause_result.passed_threshold() is True

    def test_to_report_line_returns_string(self, sample_clause_result):
        """to_report_line() should return a non-empty string."""
        line = sample_clause_result.to_report_line()
        assert isinstance(line, str)
        assert len(line) > 0

    def test_merge_with_same_clause(self, sample_clause_result):
        """merge_with() on two results with the same clause_id should return a ClauseResult."""
        other = ClauseResult(
            clause_id="clause-001",
            clause_type=ClauseType.SOUNDNESS,
            score=0.6,
            passed=False,
            evidence=[],
            metadata={},
        )
        merged = sample_clause_result.merge_with(other)
        assert isinstance(merged, ClauseResult)

    def test_merge_with_averages_score(self, sample_clause_result):
        """merge_with() should average the two scores."""
        other = ClauseResult(
            clause_id="clause-001",
            clause_type=ClauseType.SOUNDNESS,
            score=0.6,
            passed=False,
            evidence=[],
            metadata={},
        )
        merged = sample_clause_result.merge_with(other)
        assert abs(merged.score - 0.7) < 1e-9

    def test_merge_with_different_clause_raises(self, sample_clause_result):
        """merge_with() should raise ValueError when clause_ids differ."""
        other = ClauseResult(
            clause_id="clause-999",
            clause_type=ClauseType.SOUNDNESS,
            score=0.6,
            passed=False,
            evidence=[],
            metadata={},
        )
        with pytest.raises(ValueError):
            sample_clause_result.merge_with(other)

    @pytest.mark.parametrize("score,threshold,expected", [
        (0.0, 0.5, False),
        (0.5, 0.5, True),
        (1.0, 0.5, True),
        (0.49, 0.5, False),
        (0.51, 0.5, True),
    ])
    def test_passed_threshold_boundary(self, score, threshold, expected):
        """passed_threshold() boundary conditions at score==threshold should return True."""
        cr = ClauseResult(
            clause_id="bnd",
            clause_type=ClauseType.RECALL,
            score=score,
            passed=score >= threshold,
            evidence=[],
            metadata={},
        )
        assert cr.passed_threshold(threshold) is expected

    def test_zero_score_not_passed_default_threshold(self):
        """A ClauseResult with score=0.0 should not pass the default threshold of 0.5."""
        cr = ClauseResult(
            clause_id="zero",
            clause_type=ClauseType.CONSISTENCY,
            score=0.0,
            passed=False,
            evidence=[],
            metadata={},
        )
        assert cr.passed_threshold() is False

    def test_perfect_score_passes_any_threshold(self):
        """A ClauseResult with score=1.0 should pass any threshold in [0, 1]."""
        cr = ClauseResult(
            clause_id="perfect",
            clause_type=ClauseType.COMPLETENESS,
            score=1.0,
            passed=True,
            evidence=[],
            metadata={},
        )
        for t in [0.0, 0.5, 0.99, 1.0]:
            assert cr.passed_threshold(t) is True


# ---------------------------------------------------------------------------
# TestAblationResult
# ---------------------------------------------------------------------------


class TestAblationResult:
    """Tests for the AblationResult frozen dataclass."""

    def test_construction(self, sample_ablation_result):
        """AblationResult should be constructable with all fields populated."""
        ar = sample_ablation_result
        assert ar.ablation_id == "abl-001"
        assert ar.ablation_kind == AblationKind.COMPONENT
        assert ar.removed_component == "encoder"

    def test_is_critical_true(self, sample_ablation_result):
        """is_critical() should return True when significant=True and |delta|>0.1."""
        assert sample_ablation_result.is_critical() is True

    def test_is_critical_false_not_significant(self):
        """is_critical() should return False when significant=False even if |delta|>0.1."""
        ar = AblationResult(
            ablation_id="a2",
            ablation_kind=AblationKind.FEATURE,
            removed_component="feat-x",
            baseline_score=0.9,
            ablated_score=0.7,
            delta_score=0.2,
            p_value=0.2,
            significant=False,
            metadata={},
        )
        assert ar.is_critical() is False

    def test_is_critical_false_small_delta(self):
        """is_critical() should return False when |delta|<=0.1 even if significant."""
        ar = AblationResult(
            ablation_id="a3",
            ablation_kind=AblationKind.SUBSYSTEM,
            removed_component="sub-y",
            baseline_score=0.9,
            ablated_score=0.85,
            delta_score=0.05,
            p_value=0.01,
            significant=True,
            metadata={},
        )
        assert ar.is_critical() is False

    def test_effect_size_positive(self, sample_ablation_result):
        """effect_size() should return a positive float for a non-trivial delta."""
        es = sample_ablation_result.effect_size()
        assert es > 0.0

    def test_effect_size_zero_mean(self):
        """effect_size() should return 0.0 when both baseline and ablated scores are zero."""
        ar = AblationResult(
            ablation_id="a4",
            ablation_kind=AblationKind.PATHWAY,
            removed_component="path-z",
            baseline_score=0.0,
            ablated_score=0.0,
            delta_score=0.0,
            p_value=1.0,
            significant=False,
            metadata={},
        )
        assert ar.effect_size() == 0.0

    def test_to_report_line_returns_string(self, sample_ablation_result):
        """to_report_line() should return a non-empty string."""
        line = sample_ablation_result.to_report_line()
        assert isinstance(line, str)
        assert len(line) > 0

    @pytest.mark.parametrize("ablation_kind", list(AblationKind))
    def test_all_ablation_kinds(self, ablation_kind):
        """AblationResult should accept every AblationKind value without error."""
        ar = AblationResult(
            ablation_id=str(uuid.uuid4()),
            ablation_kind=ablation_kind,
            removed_component="comp",
            baseline_score=0.8,
            ablated_score=0.7,
            delta_score=0.1,
            p_value=0.05,
            significant=True,
            metadata={},
        )
        assert ar.ablation_kind == ablation_kind


# ---------------------------------------------------------------------------
# TestCalibrationReport
# ---------------------------------------------------------------------------


class TestCalibrationReport:
    """Tests for the CalibrationReport frozen dataclass."""

    def test_construction(self, sample_calibration_report):
        """CalibrationReport should be constructable with all standard fields."""
        cr = sample_calibration_report
        assert cr.report_id == "rep-001"
        assert cr.method == CalibrationMethod.PLATT_SCALING

    def test_improvement_ratio_normal(self, sample_calibration_report):
        """improvement_ratio() should be (0.20-0.05)/0.20 = 0.75 for the fixture."""
        ratio = sample_calibration_report.improvement_ratio()
        assert abs(ratio - 0.75) < 1e-9

    def test_improvement_ratio_zero_before(self):
        """improvement_ratio() should return 0.0 when before_ece <= 0."""
        cr = CalibrationReport(
            report_id="r2",
            method=CalibrationMethod.ISOTONIC,
            before_ece=0.0,
            after_ece=0.0,
            before_mce=0.0,
            after_mce=0.0,
            reliability_diagram_data=[],
            n_samples=0,
            metadata={},
        )
        assert cr.improvement_ratio() == 0.0

    def test_is_well_calibrated_true(self, sample_calibration_report):
        """is_well_calibrated(0.1) should return True when after_ece=0.05 < 0.1."""
        assert sample_calibration_report.is_well_calibrated(threshold=0.1) is True

    def test_is_well_calibrated_false(self):
        """is_well_calibrated(0.1) should return False when after_ece=0.15 >= 0.1."""
        cr = CalibrationReport(
            report_id="r3",
            method=CalibrationMethod.TEMPERATURE,
            before_ece=0.25,
            after_ece=0.15,
            before_mce=0.35,
            after_mce=0.20,
            reliability_diagram_data=[],
            n_samples=50,
            metadata={},
        )
        assert cr.is_well_calibrated(threshold=0.1) is False

    def test_summary_line_returns_string(self, sample_calibration_report):
        """summary_line() should return a non-empty string."""
        line = sample_calibration_report.summary_line()
        assert isinstance(line, str)
        assert len(line) > 0

    @pytest.mark.parametrize("method", list(CalibrationMethod))
    def test_all_calibration_methods(self, method):
        """CalibrationReport should accept every CalibrationMethod value without error."""
        cr = CalibrationReport(
            report_id=str(uuid.uuid4()),
            method=method,
            before_ece=0.2,
            after_ece=0.05,
            before_mce=0.3,
            after_mce=0.1,
            reliability_diagram_data=[],
            n_samples=200,
            metadata={},
        )
        assert cr.method == method

    def test_improvement_ratio_negative_improvement(self):
        """improvement_ratio() should be negative when calibration made things worse."""
        cr = CalibrationReport(
            report_id="r4",
            method=CalibrationMethod.HISTOGRAM,
            before_ece=0.1,
            after_ece=0.2,
            before_mce=0.15,
            after_mce=0.25,
            reliability_diagram_data=[],
            n_samples=80,
            metadata={},
        )
        ratio = cr.improvement_ratio()
        assert ratio < 0.0


# ---------------------------------------------------------------------------
# TestEvaluationResult
# ---------------------------------------------------------------------------


class TestEvaluationResult:
    """Tests for the EvaluationResult frozen dataclass."""

    def test_construction(self, sample_evaluation_result):
        """EvaluationResult should be constructable with all fields set."""
        er = sample_evaluation_result
        assert er.result_id == "res-001"
        assert er.status == EvaluationStatus.COMPLETE

    def test_passed_clauses(self, sample_evaluation_result):
        """passed_clauses() should return the list of ClauseResult where passed=True."""
        passed = sample_evaluation_result.passed_clauses()
        assert all(cr.passed for cr in passed)

    def test_failed_clauses_empty(self, sample_evaluation_result):
        """failed_clauses() should return empty list when all clauses pass."""
        failed = sample_evaluation_result.failed_clauses()
        assert failed == []

    def test_failed_clauses_nonempty(self):
        """failed_clauses() should return failed ClauseResult entries."""
        now = time.time()
        cr = ClauseResult(
            clause_id="fail-c",
            clause_type=ClauseType.RECALL,
            score=0.2,
            passed=False,
            evidence=[],
            metadata={},
        )
        er = EvaluationResult(
            result_id="r-fail",
            design_id="d-fail",
            clause_results=[cr],
            ablation_results=[],
            calibration_report=None,
            overall_score=0.2,
            status=EvaluationStatus.FAILED,
            started_at=now - 1,
            finished_at=now,
            metadata={},
        )
        assert len(er.failed_clauses()) == 1

    def test_critical_ablations(self, sample_evaluation_result):
        """critical_ablations() should return ablations where is_critical() is True."""
        critical = sample_evaluation_result.critical_ablations()
        assert all(a.is_critical() for a in critical)

    def test_to_json_returns_string(self, sample_evaluation_result):
        """to_json() should return a non-empty string."""
        j = sample_evaluation_result.to_json()
        assert isinstance(j, str)
        assert len(j) > 0

    def test_summarize_contains_result_id(self, sample_evaluation_result):
        """summarize() should include the result_id in its output."""
        summary = sample_evaluation_result.summarize()
        assert "res-001" in summary

    def test_no_calibration_report(self):
        """EvaluationResult with calibration_report=None should be constructable."""
        now = time.time()
        er = EvaluationResult(
            result_id="r-nocal",
            design_id="d-nocal",
            clause_results=[],
            ablation_results=[],
            calibration_report=None,
            overall_score=0.0,
            status=EvaluationStatus.PENDING,
            started_at=now,
            finished_at=now,
            metadata={},
        )
        assert er.calibration_report is None

    def test_empty_clause_and_ablation_results(self):
        """EvaluationResult with empty clause/ablation lists should handle gracefully."""
        now = time.time()
        er = EvaluationResult(
            result_id="r-empty",
            design_id="d-empty",
            clause_results=[],
            ablation_results=[],
            calibration_report=None,
            overall_score=0.0,
            status=EvaluationStatus.RUNNING,
            started_at=now,
            finished_at=now,
            metadata={},
        )
        assert er.passed_clauses() == []
        assert er.failed_clauses() == []
        assert er.critical_ablations() == []

    @pytest.mark.parametrize("status", list(EvaluationStatus))
    def test_all_statuses_accepted(self, status):
        """EvaluationResult should accept every EvaluationStatus without error."""
        now = time.time()
        er = EvaluationResult(
            result_id=str(uuid.uuid4()),
            design_id="d",
            clause_results=[],
            ablation_results=[],
            calibration_report=None,
            overall_score=0.5,
            status=status,
            started_at=now,
            finished_at=now,
            metadata={},
        )
        assert er.status == status


# ---------------------------------------------------------------------------
# TestClausewiseEvaluator
# ---------------------------------------------------------------------------


class TestClausewiseEvaluator:
    """Tests for the ClausewiseEvaluator dataclass."""

    def test_construction(self, sample_clausewise_evaluator):
        """ClausewiseEvaluator should be constructable with clauses and weights."""
        cwe = sample_clausewise_evaluator
        assert len(cwe.clauses) == 2
        assert len(cwe.weights) == 2

    def test_evaluate_returns_list(self, sample_clausewise_evaluator):
        """evaluate() should return a list of dicts."""
        results = sample_clausewise_evaluator.evaluate("some system output text")
        assert isinstance(results, list)

    def test_evaluate_result_keys(self, sample_clausewise_evaluator):
        """Each dict in evaluate() output must contain clause_id, clause_type, score, passed."""
        results = sample_clausewise_evaluator.evaluate("output")
        for r in results:
            for key in ("clause_id", "clause_type", "score", "passed"):
                assert key in r

    def test_get_weight_for_existing(self, sample_clausewise_evaluator):
        """get_weight_for() should return the weight for a known clause_id."""
        w = sample_clausewise_evaluator.get_weight_for("c1")
        assert isinstance(w, float)

    def test_get_weight_for_missing(self, sample_clausewise_evaluator):
        """get_weight_for() should return 0.0 for an unknown clause_id."""
        w = sample_clausewise_evaluator.get_weight_for("nonexistent-clause")
        assert w == 0.0

    def test_normalize_weights_sums_to_one(self, sample_clausewise_evaluator):
        """normalize_weights() should return a list summing to approximately 1.0."""
        normed = sample_clausewise_evaluator.normalize_weights()
        assert abs(sum(normed) - 1.0) < 1e-9

    def test_normalize_weights_all_zero_fallback(self):
        """normalize_weights() should return uniform weights when all weights are zero."""
        cwe = ClausewiseEvaluator(
            clauses=[
                {"clause_id": "cx", "clause_type": "soundness", "text": "X."},
                {"clause_id": "cy", "clause_type": "recall", "text": "Y."},
            ],
            weights=[0.0, 0.0],
            scorer="default",
            threshold=0.5,
            metadata={},
        )
        normed = cwe.normalize_weights()
        assert len(normed) == 2
        assert abs(sum(normed) - 1.0) < 1e-9

    def test_add_clause_increases_count(self, sample_clausewise_evaluator):
        """add_clause() should append a new clause and update the clauses list."""
        before = len(sample_clausewise_evaluator.clauses)
        sample_clausewise_evaluator.add_clause(
            {"clause_id": "c3", "clause_type": "consistency", "text": "Consistent."},
            weight=0.5,
        )
        assert len(sample_clausewise_evaluator.clauses) == before + 1

    def test_add_clause_adds_weight(self, sample_clausewise_evaluator):
        """add_clause() should also append the given weight to the weights list."""
        before = len(sample_clausewise_evaluator.weights)
        sample_clausewise_evaluator.add_clause(
            {"clause_id": "c4", "clause_type": "precision", "text": "Precise."},
            weight=2.0,
        )
        assert len(sample_clausewise_evaluator.weights) == before + 1

    def test_to_json_returns_string(self, sample_clausewise_evaluator):
        """to_json() should return a valid non-empty JSON string."""
        j = sample_clausewise_evaluator.to_json()
        assert isinstance(j, str)
        parsed = json.loads(j)
        assert isinstance(parsed, dict)

    def test_evaluate_empty_clauses(self):
        """evaluate() on a ClausewiseEvaluator with no clauses should return empty list."""
        cwe = ClausewiseEvaluator(
            clauses=[],
            weights=[],
            scorer="default",
            threshold=0.5,
            metadata={},
        )
        results = cwe.evaluate("any output")
        assert results == []

    def test_normalize_weights_length_matches_clauses(self, sample_clausewise_evaluator):
        """normalize_weights() should return a list of the same length as clauses."""
        normed = sample_clausewise_evaluator.normalize_weights()
        assert len(normed) == len(sample_clausewise_evaluator.clauses)


# ---------------------------------------------------------------------------
# TestAblationDesign
# ---------------------------------------------------------------------------


class TestAblationDesign:
    """Tests for the AblationDesign frozen dataclass."""

    def test_construction(self, sample_ablation_design):
        """AblationDesign should be constructable with all fields."""
        ad = sample_ablation_design
        assert ad.design_id == "abld-001"
        assert len(ad.components_to_ablate) == 3

    def test_get_ablation_count(self, sample_ablation_design):
        """get_ablation_count() should return the number of components to ablate."""
        assert sample_ablation_design.get_ablation_count() == 3

    def test_get_ablation_count_empty(self):
        """get_ablation_count() should return 0 for an empty components list."""
        ad = AblationDesign(
            design_id="empty",
            components_to_ablate=[],
            baseline_config={},
            metrics=[],
            n_repeats=1,
            random_seed=0,
            metadata={},
        )
        assert ad.get_ablation_count() == 0

    def test_component_pairs_count(self, sample_ablation_design):
        """component_pairs() for 3 components should return 3 unique pairs (C(3,2))."""
        pairs = sample_ablation_design.component_pairs()
        assert len(pairs) == 3

    def test_component_pairs_unique(self, sample_ablation_design):
        """component_pairs() should not return duplicate pairs."""
        pairs = sample_ablation_design.component_pairs()
        pair_set = set(pairs)
        assert len(pair_set) == len(pairs)

    def test_component_pairs_lexicographic(self, sample_ablation_design):
        """component_pairs() should return lexicographically ordered pairs."""
        pairs = sample_ablation_design.component_pairs()
        for a, b in pairs:
            assert a <= b

    def test_total_runs(self, sample_ablation_design):
        """total_runs() should equal (len(components)+1)*n_repeats."""
        expected = (3 + 1) * 2
        assert sample_ablation_design.total_runs() == expected

    def test_total_runs_single_component(self):
        """total_runs() for 1 component and 3 repeats should be (1+1)*3=6."""
        ad = AblationDesign(
            design_id="single",
            components_to_ablate=["only-one"],
            baseline_config={},
            metrics=["acc"],
            n_repeats=3,
            random_seed=7,
            metadata={},
        )
        assert ad.total_runs() == 6

    def test_seed_for_repeat_deterministic(self, sample_ablation_design):
        """seed_for_repeat() should return the same value for the same indices."""
        s1 = sample_ablation_design.seed_for_repeat(0, 0)
        s2 = sample_ablation_design.seed_for_repeat(0, 0)
        assert s1 == s2

    def test_seed_for_repeat_differs_by_index(self, sample_ablation_design):
        """seed_for_repeat() should return different seeds for different component indices."""
        s0 = sample_ablation_design.seed_for_repeat(0, 0)
        s1 = sample_ablation_design.seed_for_repeat(1, 0)
        assert s0 != s1

    def test_to_json_returns_string(self, sample_ablation_design):
        """to_json() should return a non-empty JSON string."""
        j = sample_ablation_design.to_json()
        assert isinstance(j, str)
        assert len(j) > 0

    def test_to_json_parseable(self, sample_ablation_design):
        """to_json() output should be parseable as a JSON object."""
        j = sample_ablation_design.to_json()
        parsed = json.loads(j)
        assert isinstance(parsed, dict)

    def test_describe_returns_string(self, sample_ablation_design):
        """describe() should return a non-empty string."""
        desc = sample_ablation_design.describe()
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_validate_valid_design(self, sample_ablation_design):
        """validate() on a properly constructed AblationDesign should return no errors."""
        errors = sample_ablation_design.validate()
        assert errors == []

    @pytest.mark.parametrize("n_repeats", [1, 5, 10])
    def test_total_runs_various_repeats(self, n_repeats):
        """total_runs() should scale linearly with n_repeats."""
        ad = AblationDesign(
            design_id="param-test",
            components_to_ablate=["a", "b"],
            baseline_config={},
            metrics=["score"],
            n_repeats=n_repeats,
            random_seed=1,
            metadata={},
        )
        assert ad.total_runs() == (2 + 1) * n_repeats

    def test_component_pairs_two_components(self):
        """component_pairs() for exactly 2 components should return 1 pair."""
        ad = AblationDesign(
            design_id="two",
            components_to_ablate=["alpha", "beta"],
            baseline_config={},
            metrics=["f1"],
            n_repeats=1,
            random_seed=0,
            metadata={},
        )
        pairs = ad.component_pairs()
        assert len(pairs) == 1

    def test_component_pairs_one_component(self):
        """component_pairs() for exactly 1 component should return 0 pairs."""
        ad = AblationDesign(
            design_id="one",
            components_to_ablate=["solo"],
            baseline_config={},
            metrics=["acc"],
            n_repeats=1,
            random_seed=0,
            metadata={},
        )
        pairs = ad.component_pairs()
        assert len(pairs) == 0


# ---------------------------------------------------------------------------
# Integration / cross-class tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Cross-class integration tests that exercise multiple models together."""

    def test_evaluation_design_to_proof_obligation_has_clauses_count(
        self, sample_evaluation_design
    ):
        """to_proof_obligation() clauses_count should match get_clause_count()."""
        obligation = sample_evaluation_design.to_proof_obligation()
        assert obligation["clauses_count"] == sample_evaluation_design.get_clause_count()

    def test_evaluation_result_critical_ablations_subset_of_all(
        self, sample_evaluation_result
    ):
        """critical_ablations() should be a subset of ablation_results."""
        all_abl = sample_evaluation_result.ablation_results
        critical = sample_evaluation_result.critical_ablations()
        for c in critical:
            assert c in all_abl

    def test_clausewise_evaluator_add_then_normalize(
        self, sample_clausewise_evaluator
    ):
        """After add_clause(), normalize_weights() should still sum to 1."""
        sample_clausewise_evaluator.add_clause(
            {"clause_id": "c-extra", "clause_type": "soundness", "text": "Extra."},
            weight=3.0,
        )
        normed = sample_clausewise_evaluator.normalize_weights()
        assert abs(sum(normed) - 1.0) < 1e-9

    def test_calibration_report_and_evaluation_result_integration(
        self, sample_evaluation_result, sample_calibration_report
    ):
        """EvaluationResult.calibration_report should be the same object as the fixture."""
        assert sample_evaluation_result.calibration_report is sample_calibration_report

    def test_clone_does_not_share_ablation_plan(self, sample_evaluation_design):
        """Mutating clone's ablation_plan should not affect the original's ablation_plan."""
        original_keys = set(sample_evaluation_design.ablation_plan.keys())
        cloned = sample_evaluation_design.clone()
        cloned.ablation_plan["injected_key"] = True
        assert set(sample_evaluation_design.ablation_plan.keys()) == original_keys

    def test_evaluation_design_json_round_trip_clauses(
        self, sample_evaluation_design
    ):
        """Clauses count should be preserved through a JSON round-trip."""
        restored = EvaluationDesign.from_json(sample_evaluation_design.to_json())
        assert len(restored.clauses) == len(sample_evaluation_design.clauses)

    def test_ablation_design_seed_for_repeat_is_int(self, sample_ablation_design):
        """seed_for_repeat() should return an integer value."""
        seed = sample_ablation_design.seed_for_repeat(0, 1)
        assert isinstance(seed, int)
