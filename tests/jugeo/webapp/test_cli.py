"""Tests for src/jugeo/webapp/cli/ — CLI integration."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.webapp.cli.models import (
    AppType,
    PipelineResult,
    PipelineStage,
    StageResult,
    TemplateChoice,
    WebappConfig,
)
from jugeo.webapp.cli.pipeline import SpecBuilder, TemplateSpecs, WebappPipeline
from jugeo.webapp.cli.formatters import (
    CLIFormatter,
    JSONReportFormatter,
    MarkdownReportFormatter,
)
from jugeo.webapp.cli.webapp_command import (
    _parse_models_arg,
    _parse_routes_arg,
    register_webapp_command,
)


# ═══════════════════════════════════════════════════════════════════════════
# Enum tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAppType:
    def test_crud(self):
        assert AppType.CRUD == "crud"

    def test_api(self):
        assert AppType.API == "api"

    def test_dashboard(self):
        assert AppType.DASHBOARD == "dashboard"

    def test_form_workflow(self):
        assert AppType.FORM_WORKFLOW == "form_workflow"

    def test_custom(self):
        assert AppType.CUSTOM == "custom"

    def test_count(self):
        assert len(AppType) == 5

    def test_is_str(self):
        assert isinstance(AppType.CRUD, str)


class TestTemplateChoice:
    def test_minimal(self):
        assert TemplateChoice.MINIMAL == "minimal"

    def test_standard(self):
        assert TemplateChoice.STANDARD == "standard"

    def test_full(self):
        assert TemplateChoice.FULL == "full"

    def test_custom(self):
        assert TemplateChoice.CUSTOM == "custom"

    def test_count(self):
        assert len(TemplateChoice) == 4


class TestPipelineStage:
    def test_ideate(self):
        assert PipelineStage.IDEATE == "ideate"

    def test_specify(self):
        assert PipelineStage.SPECIFY == "specify"

    def test_generate(self):
        assert PipelineStage.GENERATE == "generate"

    def test_verify(self):
        assert PipelineStage.VERIFY == "verify"

    def test_report(self):
        assert PipelineStage.REPORT == "report"

    def test_count(self):
        assert len(PipelineStage) == 11


# ═══════════════════════════════════════════════════════════════════════════
# WebappConfig tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWebappConfig:
    def test_defaults(self):
        cfg = WebappConfig(outdir="/out")
        assert cfg.port == 5000
        assert cfg.app_name == "app"
        assert cfg.app_type == AppType.CRUD
        assert cfg.template == TemplateChoice.STANDARD
        assert cfg.verify is True
        assert cfg.verbose is False
        assert cfg.include_tests is False
        assert cfg.include_docker is False
        assert cfg.models_json == []
        assert cfg.routes_json == []

    def test_to_dict_from_dict_roundtrip(self):
        cfg = WebappConfig(outdir="/out", port=8080, app_name="myapp")
        d = cfg.to_dict()
        cfg2 = WebappConfig.from_dict(d)
        assert cfg2.outdir == cfg.outdir
        assert cfg2.port == cfg.port
        assert cfg2.app_name == cfg.app_name
        assert cfg2.app_type == cfg.app_type

    def test_custom_models_json(self):
        models = [{"name": "Widget", "fields": []}]
        cfg = WebappConfig(outdir="/out", models_json=models)
        assert cfg.models_json == models

    def test_custom_routes_json(self):
        routes = [{"path": "/foo", "method": "GET", "handler": "foo"}]
        cfg = WebappConfig(outdir="/out", routes_json=routes)
        assert cfg.routes_json == routes

    def test_to_dict_contains_all_keys(self):
        cfg = WebappConfig(outdir="/x")
        d = cfg.to_dict()
        assert "outdir" in d
        assert "port" in d
        assert "app_name" in d
        assert "app_type" in d
        assert "template" in d
        assert "models_json" in d
        assert "routes_json" in d


# ═══════════════════════════════════════════════════════════════════════════
# StageResult tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStageResult:
    def test_to_dict_from_dict(self):
        sr = StageResult(stage=PipelineStage.GENERATE, success=True, duration_ms=42.0)
        d = sr.to_dict()
        sr2 = StageResult.from_dict(d)
        assert sr2.stage == PipelineStage.GENERATE
        assert sr2.success is True
        assert sr2.duration_ms == 42.0

    def test_errors(self):
        sr = StageResult(
            stage=PipelineStage.VERIFY,
            success=False,
            errors=["file not found"],
        )
        assert sr.errors == ["file not found"]

    def test_warnings(self):
        sr = StageResult(stage=PipelineStage.IDEATE, success=True, warnings=["w1"])
        assert sr.warnings == ["w1"]


# ═══════════════════════════════════════════════════════════════════════════
# PipelineResult tests
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineResult:
    def _cfg(self):
        return WebappConfig(outdir="/out")

    def test_to_dict_from_dict(self):
        pr = PipelineResult(config=self._cfg(), elapsed_ms=100.0)
        d = pr.to_dict()
        pr2 = PipelineResult.from_dict(d)
        assert pr2.elapsed_ms == 100.0

    def test_success_true_when_no_stages(self):
        pr = PipelineResult(config=self._cfg())
        assert pr.success is True

    def test_success_true_when_no_errors(self):
        pr = PipelineResult(
            config=self._cfg(),
            stages_completed=[
                StageResult(stage=PipelineStage.GENERATE, success=True),
            ],
        )
        assert pr.success is True

    def test_success_false_when_errors(self):
        pr = PipelineResult(
            config=self._cfg(),
            stages_completed=[
                StageResult(
                    stage=PipelineStage.VERIFY,
                    success=False,
                    errors=["bad"],
                ),
            ],
        )
        assert pr.success is False

    def test_elapsed_ms_field(self):
        pr = PipelineResult(config=self._cfg(), elapsed_ms=123.4)
        assert pr.elapsed_ms == 123.4


# ═══════════════════════════════════════════════════════════════════════════
# TemplateSpecs tests
# ═══════════════════════════════════════════════════════════════════════════


class TestTemplateSpecs:
    def test_minimal_structure(self):
        spec = TemplateSpecs.minimal("myapp", 5000)
        assert spec["name"] == "myapp"
        assert spec["port"] == 5000
        assert "routes" in spec
        assert "models" in spec
        assert "auth" in spec

    def test_minimal_has_one_route(self):
        spec = TemplateSpecs.minimal("myapp", 5000)
        assert len(spec["routes"]) == 1

    def test_standard_has_auth(self):
        spec = TemplateSpecs.standard("myapp", 5000)
        assert spec["auth"] is True

    def test_standard_has_3_models(self):
        spec = TemplateSpecs.standard("myapp", 5000)
        assert len(spec["models"]) >= 3

    def test_standard_has_crud_routes(self):
        spec = TemplateSpecs.standard("myapp", 5000)
        methods = {r["method"] for r in spec["routes"]}
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods
        assert "DELETE" in methods

    def test_full_has_blueprints(self):
        spec = TemplateSpecs.full("myapp", 5000)
        assert len(spec["blueprints"]) > 0

    def test_full_has_auth(self):
        spec = TemplateSpecs.full("myapp", 5000)
        assert spec["auth"] is True


# ═══════════════════════════════════════════════════════════════════════════
# SpecBuilder tests
# ═══════════════════════════════════════════════════════════════════════════


class TestSpecBuilder:
    def _cfg(self, **kw):
        return WebappConfig(outdir="/out", **kw)

    def test_from_config(self):
        spec = SpecBuilder.from_config(self._cfg(app_name="test", port=8080))
        assert spec["name"] == "test"
        assert spec["port"] == 8080

    def test_from_template_minimal(self):
        spec = SpecBuilder.from_template("minimal", self._cfg())
        assert spec["type"] == "minimal"
        assert len(spec["routes"]) == 1

    def test_from_template_full(self):
        spec = SpecBuilder.from_template("full", self._cfg())
        assert "blueprints" in spec
        assert len(spec["blueprints"]) > 0

    def test_from_models_json(self):
        models = [{"name": "Widget", "fields": []}]
        spec = SpecBuilder.from_models_json(models, self._cfg())
        assert len(spec["routes"]) > 1
        assert spec["models"] == models

    def test_from_routes_json(self):
        routes = [{"path": "/x", "method": "GET", "handler": "x"}]
        spec = SpecBuilder.from_routes_json(routes, self._cfg())
        assert spec["routes"] == routes

    def test_from_ideation(self):
        idea = {"name": "ideated", "routes": [{"path": "/"}], "models": [], "description": "test"}
        spec = SpecBuilder.from_ideation(idea, self._cfg())
        assert spec["name"] == "ideated"
        assert spec["description"] == "test"


# ═══════════════════════════════════════════════════════════════════════════
# _parse helpers tests
# ═══════════════════════════════════════════════════════════════════════════


class TestParseHelpers:
    def test_parse_models_json_string(self):
        result = _parse_models_arg('[{"name": "A"}]')
        assert len(result) == 1
        assert result[0]["name"] == "A"

    def test_parse_models_empty(self):
        assert _parse_models_arg("") == []

    def test_parse_routes_json_string(self):
        result = _parse_routes_arg('[{"path": "/"}]')
        assert len(result) == 1

    def test_parse_routes_empty(self):
        assert _parse_routes_arg("") == []

    def test_parse_models_from_file(self):
        td = tempfile.mkdtemp()
        try:
            fpath = os.path.join(td, "models.json")
            with open(fpath, "w") as f:
                json.dump([{"name": "FromFile"}], f)
            result = _parse_models_arg(fpath)
            assert len(result) == 1
            assert result[0]["name"] == "FromFile"
        finally:
            os.remove(fpath)
            os.rmdir(td)

    def test_parse_routes_from_file(self):
        td = tempfile.mkdtemp()
        try:
            fpath = os.path.join(td, "routes.json")
            with open(fpath, "w") as f:
                json.dump([{"path": "/file"}], f)
            result = _parse_routes_arg(fpath)
            assert len(result) == 1
            assert result[0]["path"] == "/file"
        finally:
            os.remove(fpath)
            os.rmdir(td)


# ═══════════════════════════════════════════════════════════════════════════
# register_webapp_command tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRegisterWebappCommand:
    def _parser(self):
        p = argparse.ArgumentParser()
        sub = p.add_subparsers()
        register_webapp_command(sub)
        return p

    def test_adds_webapp(self):
        p = self._parser()
        args = p.parse_args(["webapp", "--outdir", "/x"])
        assert hasattr(args, "outdir")

    def test_outdir_arg(self):
        p = self._parser()
        args = p.parse_args(["webapp", "--outdir", "/mydir"])
        assert args.outdir == "/mydir"

    def test_port_default(self):
        p = self._parser()
        args = p.parse_args(["webapp", "--outdir", "/x"])
        assert args.port == 5000

    def test_port_custom(self):
        p = self._parser()
        args = p.parse_args(["webapp", "--outdir", "/x", "--port", "9000"])
        assert args.port == 9000

    def test_type_arg(self):
        p = self._parser()
        args = p.parse_args(["webapp", "--outdir", "/x", "--type", "api"])
        assert args.app_type == "api"

    def test_template_arg(self):
        p = self._parser()
        args = p.parse_args(["webapp", "--outdir", "/x", "--template", "full"])
        assert args.template == "full"

    def test_verify_flag(self):
        p = self._parser()
        args = p.parse_args(["webapp", "--outdir", "/x", "--no-verify"])
        assert args.verify is False

    def test_ideate_flag(self):
        p = self._parser()
        args = p.parse_args(["webapp", "--outdir", "/x", "--ideate"])
        assert args.ideate is True


# ═══════════════════════════════════════════════════════════════════════════
# WebappPipeline tests
# ═══════════════════════════════════════════════════════════════════════════


class TestWebappPipeline:
    def _cfg(self, td, **kw):
        return WebappConfig(outdir=td, **kw)

    def test_instantiation(self):
        p = WebappPipeline()
        assert p is not None

    def test_stage_specify(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            p = WebappPipeline()
            sr = p._stage_specify(cfg)
            assert sr.success is True
            assert sr.stage == PipelineStage.SPECIFY
        finally:
            _clean(td)

    def test_stage_generate_creates_app_py(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            spec = SpecBuilder.from_template("minimal", cfg)
            p = WebappPipeline()
            sr = p._stage_generate(cfg, spec)
            assert sr.success is True
            assert (Path(td) / "app.py").exists()
        finally:
            _clean(td)

    def test_stage_generate_creates_requirements_txt(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            spec = SpecBuilder.from_template("minimal", cfg)
            p = WebappPipeline()
            p._stage_generate(cfg, spec)
            assert (Path(td) / "requirements.txt").exists()
        finally:
            _clean(td)

    def test_stage_generate_creates_readme(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            spec = SpecBuilder.from_template("minimal", cfg)
            p = WebappPipeline()
            p._stage_generate(cfg, spec)
            assert (Path(td) / "README.md").exists()
        finally:
            _clean(td)

    def test_stage_verify_success(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            spec = SpecBuilder.from_template("minimal", cfg)
            p = WebappPipeline()
            p._stage_generate(cfg, spec)
            vr = p._stage_verify(cfg, td)
            assert vr.success is True
        finally:
            _clean(td)

    def test_stage_verify_failure_missing(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            p = WebappPipeline()
            vr = p._stage_verify(cfg, td)
            assert vr.success is False
        finally:
            _clean(td)

    def test_stage_verify_checks_flask_keyword(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            # Write a file without Flask
            (Path(td) / "app.py").write_text("print('hello')\n")
            p = WebappPipeline()
            vr = p._stage_verify(cfg, td)
            assert vr.success is False
        finally:
            _clean(td)

    def test_stage_ideate(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            p = WebappPipeline()
            sr = p._stage_ideate(cfg)
            assert isinstance(sr, StageResult)
            assert sr.success is True
        finally:
            _clean(td)

    def test_stage_report(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            pr = PipelineResult(config=cfg)
            p = WebappPipeline()
            sr = p._stage_report(cfg, pr)
            assert sr.success is True
            assert "output_dir" in sr.details
        finally:
            _clean(td)

    def test_run_returns_pipeline_result(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td)
            p = WebappPipeline()
            pr = p.run(cfg)
            assert isinstance(pr, PipelineResult)
            assert pr.success is True
        finally:
            _clean(td)

    def test_run_with_include_tests(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td, include_tests=True)
            p = WebappPipeline()
            p.run(cfg)
            assert (Path(td) / "tests" / "test_app.py").exists()
        finally:
            _clean(td)

    def test_run_with_include_docker(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td, include_docker=True)
            p = WebappPipeline()
            p.run(cfg)
            assert (Path(td) / "Dockerfile").exists()
        finally:
            _clean(td)

    def test_stage_generate_minimal_template(self):
        td = tempfile.mkdtemp()
        try:
            cfg = self._cfg(td, template=TemplateChoice.MINIMAL)
            spec = SpecBuilder.from_template("minimal", cfg)
            p = WebappPipeline()
            sr = p._stage_generate(cfg, spec)
            assert sr.success is True
            content = (Path(td) / "app.py").read_text()
            assert "Flask" in content
        finally:
            _clean(td)


# ═══════════════════════════════════════════════════════════════════════════
# Formatter tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCLIFormatter:
    def _cfg(self):
        return WebappConfig(outdir="/out", port=8080)

    def test_format_launch_instructions_has_port(self):
        text = CLIFormatter.format_launch_instructions(self._cfg())
        assert "8080" in text

    def test_format_launch_instructions_has_outdir(self):
        text = CLIFormatter.format_launch_instructions(self._cfg())
        assert "/out" in text

    def test_format_generation_report(self):
        text = CLIFormatter.format_generation_report({"files_created": ["app.py"]})
        assert len(text) > 0

    def test_format_verification_report(self):
        text = CLIFormatter.format_verification_report({"passed": True, "checks": []})
        assert len(text) > 0

    def test_format_ideation_report(self):
        text = CLIFormatter.format_ideation_report({"name": "test"})
        assert len(text) > 0

    def test_format_error(self):
        text = CLIFormatter.format_error("something broke")
        assert "something broke" in text


class TestJSONReportFormatter:
    def test_format_returns_valid_json(self):
        cfg = WebappConfig(outdir="/out")
        pr = PipelineResult(config=cfg, elapsed_ms=50.0)
        text = JSONReportFormatter.format(pr)
        data = json.loads(text)
        assert isinstance(data, dict)

    def test_format_dict_input(self):
        text = JSONReportFormatter.format({"hello": "world"})
        assert '"hello"' in text


class TestMarkdownReportFormatter:
    def test_format_has_heading(self):
        cfg = WebappConfig(outdir="/out")
        pr = PipelineResult(config=cfg)
        text = MarkdownReportFormatter.format(pr)
        assert text.startswith("# ")


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _clean(path: str) -> None:
    """Remove temp directory tree."""
    import shutil
    shutil.rmtree(path, ignore_errors=True)
