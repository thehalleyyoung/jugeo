"""Tests for the webapp verification pipeline."""
from __future__ import annotations

import os
import pytest

from jugeo.webapp.verification.models import (
    VerificationLevel,
    VerificationResult,
    VerificationConfig,
)
from jugeo.webapp.verification.pipeline import WebVerificationPipeline


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline() -> WebVerificationPipeline:
    return WebVerificationPipeline()


@pytest.fixture
def default_config() -> VerificationConfig:
    return VerificationConfig()


@pytest.fixture
def syntax_config() -> VerificationConfig:
    return VerificationConfig(level=VerificationLevel.SYNTAX_ONLY)


@pytest.fixture
def full_descent_config() -> VerificationConfig:
    return VerificationConfig(level=VerificationLevel.FULL_DESCENT)


@pytest.fixture
def project_dir(tmp_path):
    """Create a tiny project directory with one Python file."""
    py_dir = tmp_path / "myapp"
    py_dir.mkdir()
    (py_dir / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.route('/')\n"
        "def index():\n"
        "    return 'hello'\n",
        encoding="utf-8",
    )
    (py_dir / "style.css").write_text(
        ".container { width: 100%; }\n"
        ".header { background: blue; }\n",
        encoding="utf-8",
    )
    (py_dir / "index.html").write_text(
        '<html><head><meta name="viewport" content="width=device-width">'
        '</head><body><h1>Hello</h1></body></html>\n',
        encoding="utf-8",
    )
    return str(py_dir)


# ===================================================================
# VerificationLevel tests
# ===================================================================

class TestVerificationLevel:

    def test_verification_level_values(self):
        assert VerificationLevel.SYNTAX_ONLY.value == "syntax_only"
        assert VerificationLevel.SINGLE_LANGUAGE.value == "single_language"
        assert VerificationLevel.CROSS_LANGUAGE.value == "cross_language"
        assert VerificationLevel.FULL_DESCENT.value == "full_descent"
        assert VerificationLevel.VISUAL_INVARIANTS.value == "visual_invariants"

    def test_verification_level_count(self):
        assert len(list(VerificationLevel)) == 5

    def test_verification_level_is_string_enum(self):
        for lvl in VerificationLevel:
            assert isinstance(lvl.value, str)
            assert isinstance(lvl, str)


# ===================================================================
# VerificationResult tests
# ===================================================================

class TestVerificationResult:

    def test_verification_result_creation(self):
        result = VerificationResult(
            level=VerificationLevel.SYNTAX_ONLY,
            passed=["check1"],
            failed=["check2"],
            warnings=["warn1"],
            obstructions=[],
            timing_ms=42.5,
            overall_passed=False,
        )
        assert result.level == VerificationLevel.SYNTAX_ONLY
        assert result.pass_count == 1
        assert result.fail_count == 1

    def test_verification_result_to_dict(self):
        result = VerificationResult(
            level=VerificationLevel.CROSS_LANGUAGE,
            passed=["a", "b"],
            failed=["c"],
            timing_ms=10.0,
        )
        d = result.to_dict()
        assert d["level"] == "cross_language"
        assert len(d["passed"]) == 2
        assert len(d["failed"]) == 1
        assert "timing_ms" in d
        assert "evidence_coverage" in d
        assert "trust_summary" in d

    def test_verification_result_from_dict_roundtrip(self):
        result = VerificationResult(
            level=VerificationLevel.FULL_DESCENT,
            passed=["p1"],
            failed=["f1"],
            warnings=["w1"],
            obstructions=[{"id": "obs1", "description": "test"}],
            evidence_coverage={"total": 5, "covered": 3, "pct": 60.0},
            trust_summary={"highest": "SERVER_VALIDATED", "lowest": "USER_INPUT", "distribution": {}},
            timing_ms=99.0,
            overall_passed=False,
        )
        d = result.to_dict()
        restored = VerificationResult.from_dict(d)
        assert restored.level == result.level
        assert restored.passed == result.passed
        assert restored.failed == result.failed
        assert restored.warnings == result.warnings
        assert restored.timing_ms == result.timing_ms
        assert restored.overall_passed == result.overall_passed

    def test_verification_result_passed_default_true(self):
        result = VerificationResult(level=VerificationLevel.SYNTAX_ONLY)
        assert result.overall_passed is True

    def test_verification_result_empty_lists(self):
        result = VerificationResult(level=VerificationLevel.SYNTAX_ONLY)
        assert result.passed == []
        assert result.failed == []
        assert result.warnings == []
        assert result.obstructions == []

    def test_verification_result_timing_ms(self):
        result = VerificationResult(
            level=VerificationLevel.SYNTAX_ONLY,
            timing_ms=123.456,
        )
        assert result.timing_ms == 123.456

    def test_verification_result_properties(self):
        result = VerificationResult(
            level=VerificationLevel.SYNTAX_ONLY,
            passed=["a", "b", "c"],
            failed=["d"],
            obstructions=[{"id": "x"}, {"id": "y"}],
        )
        assert result.pass_count == 3
        assert result.fail_count == 1
        assert result.obstruction_count == 2


# ===================================================================
# VerificationConfig tests
# ===================================================================

class TestVerificationConfig:

    def test_verification_config_defaults(self):
        config = VerificationConfig()
        assert config.level == VerificationLevel.CROSS_LANGUAGE
        assert config.include_security is True
        assert config.include_accessibility is False
        assert config.timeout_ms == 30000.0

    def test_verification_config_default_level_is_cross_language(self):
        config = VerificationConfig()
        assert config.level == VerificationLevel.CROSS_LANGUAGE

    def test_verification_config_to_dict(self):
        config = VerificationConfig()
        d = config.to_dict()
        assert d["level"] == "cross_language"
        assert "layers_to_check" in d
        assert "trust_threshold" in d
        assert "include_security" in d

    def test_verification_config_from_dict_roundtrip(self):
        config = VerificationConfig(
            level=VerificationLevel.FULL_DESCENT,
            layers_to_check=["python", "css"],
            trust_threshold="ORM_TYPE_CHECKED",
            timeout_ms=5000.0,
            include_security=False,
        )
        d = config.to_dict()
        restored = VerificationConfig.from_dict(d)
        assert restored.level == config.level
        assert restored.layers_to_check == config.layers_to_check
        assert restored.trust_threshold == config.trust_threshold
        assert restored.timeout_ms == config.timeout_ms
        assert restored.include_security == config.include_security

    def test_verification_config_layers_default_has_python(self):
        config = VerificationConfig()
        assert "python" in config.layers_to_check

    def test_verification_config_trust_threshold_default(self):
        config = VerificationConfig()
        assert config.trust_threshold == "SERVER_VALIDATED"


# ===================================================================
# WebVerificationPipeline tests
# ===================================================================

class TestWebVerificationPipeline:

    def test_verify_nonexistent_dir(self, pipeline, default_config):
        result = pipeline.verify("/nonexistent/path/xyz", default_config)
        assert isinstance(result, VerificationResult)

    def test_verify_returns_verification_result(self, pipeline, project_dir):
        result = pipeline.verify(project_dir)
        assert isinstance(result, VerificationResult)

    def test_verify_syntax_only_config(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        assert result.level == VerificationLevel.SYNTAX_ONLY
        assert isinstance(result, VerificationResult)

    def test_verify_cross_language_config(self, pipeline, project_dir):
        config = VerificationConfig(level=VerificationLevel.CROSS_LANGUAGE)
        result = pipeline.verify(project_dir, config)
        assert result.level == VerificationLevel.CROSS_LANGUAGE

    def test_level_syntax_returns_dict(self, pipeline):
        step = pipeline._level_syntax("/nonexistent/path/xyz")
        assert isinstance(step, dict)
        assert "passed" in step or "failed" in step

    def test_level_syntax_handles_missing_dir(self, pipeline):
        step = pipeline._level_syntax("/nonexistent/path/xyz")
        assert isinstance(step, dict)

    def test_level_single_language_returns_dict(self, pipeline):
        step = pipeline._level_single_language("/nonexistent/path/xyz")
        assert isinstance(step, dict)

    def test_level_cross_language_returns_dict(self, pipeline):
        step = pipeline._level_cross_language("/nonexistent/path/xyz")
        assert isinstance(step, dict)

    def test_level_full_descent_returns_dict(self, pipeline):
        step = pipeline._level_full_descent("/nonexistent/path/xyz")
        assert isinstance(step, dict)

    def test_level_visual_returns_dict(self, pipeline):
        step = pipeline._level_visual("/nonexistent/path/xyz")
        assert isinstance(step, dict)

    def test_generate_report_returns_string(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        report = pipeline.generate_report(result)
        assert isinstance(report, str)
        assert len(report) > 0

    def test_generate_report_contains_passed(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        report = pipeline.generate_report(result)
        assert "PASSED" in report or "FAILED" in report

    def test_generate_report_contains_level(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        report = pipeline.generate_report(result)
        assert "syntax_only" in report

    def test_generate_json_report_returns_dict(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        json_report = pipeline.generate_json_report(result)
        assert isinstance(json_report, dict)

    def test_generate_json_report_has_level_key(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        json_report = pipeline.generate_json_report(result)
        assert "level" in json_report
        assert json_report["level"] == "syntax_only"

    def test_generate_json_report_has_metadata(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        json_report = pipeline.generate_json_report(result)
        assert "metadata" in json_report
        assert "pass_count" in json_report["metadata"]
        assert "fail_count" in json_report["metadata"]

    def test_verify_with_project_dir(self, pipeline, project_dir):
        result = pipeline.verify(project_dir, VerificationConfig(level=VerificationLevel.SYNTAX_ONLY))
        assert result.pass_count >= 1

    def test_verify_timing_ms_positive(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        assert result.timing_ms >= 0

    def test_verify_evidence_coverage_keys(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        cov = result.evidence_coverage
        assert "total" in cov
        assert "covered" in cov
        assert "pct" in cov

    def test_verify_trust_summary_keys(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        ts = result.trust_summary
        assert "highest" in ts
        assert "lowest" in ts

    def test_config_include_security_true(self, pipeline, project_dir):
        config = VerificationConfig(
            level=VerificationLevel.CROSS_LANGUAGE,
            include_security=True,
        )
        result = pipeline.verify(project_dir, config)
        assert isinstance(result, VerificationResult)

    def test_config_include_security_false(self, pipeline, project_dir):
        config = VerificationConfig(
            level=VerificationLevel.CROSS_LANGUAGE,
            include_security=False,
        )
        result = pipeline.verify(project_dir, config)
        assert isinstance(result, VerificationResult)

    def test_verification_result_obstructions_list(self, pipeline, project_dir, syntax_config):
        result = pipeline.verify(project_dir, syntax_config)
        assert isinstance(result.obstructions, list)

    def test_full_descent_config_runs(self, pipeline, project_dir, full_descent_config):
        result = pipeline.verify(project_dir, full_descent_config)
        assert isinstance(result, VerificationResult)
        assert result.level == VerificationLevel.FULL_DESCENT

    def test_visual_invariants_config_runs(self, pipeline, project_dir):
        config = VerificationConfig(level=VerificationLevel.VISUAL_INVARIANTS)
        result = pipeline.verify(project_dir, config)
        assert isinstance(result, VerificationResult)
        assert result.level == VerificationLevel.VISUAL_INVARIANTS

    def test_verify_default_config(self, pipeline, project_dir):
        result = pipeline.verify(project_dir)
        assert result.level == VerificationLevel.CROSS_LANGUAGE

    def test_syntax_only_detects_valid_python(self, pipeline, project_dir):
        result = pipeline.verify(project_dir, VerificationConfig(level=VerificationLevel.SYNTAX_ONLY))
        passed_descriptions = result.passed
        assert any("Syntax OK" in p for p in passed_descriptions)

    def test_syntax_only_detects_invalid_python(self, pipeline, tmp_path):
        bad_dir = tmp_path / "bad_project"
        bad_dir.mkdir()
        (bad_dir / "broken.py").write_text("def foo(:\n", encoding="utf-8")
        result = pipeline.verify(str(bad_dir), VerificationConfig(level=VerificationLevel.SYNTAX_ONLY))
        assert result.fail_count >= 1
        assert result.overall_passed is False
