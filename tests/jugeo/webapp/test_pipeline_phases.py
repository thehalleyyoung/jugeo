"""Tests for the 8-phase judgment-geometric webapp generation pipeline."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from jugeo.webapp.cli.models import (
    AppMode,
    AppType,
    PipelineResult,
    PipelineStage,
    StageResult,
    TemplateChoice,
    WebappConfig,
)
from jugeo.webapp.cli.pipeline import WebappPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(outdir: str, **kwargs) -> WebappConfig:
    return WebappConfig(outdir=outdir, **kwargs)


# ---------------------------------------------------------------------------
# Model-level tests (no I/O)
# ---------------------------------------------------------------------------

def test_app_mode_enum_values():
    assert AppMode.FLASK == "flask"
    assert AppMode.STATIC == "static"


def test_pipeline_stage_enum_has_new_stages():
    assert PipelineStage.OBLIGATIONS == "obligations"
    assert PipelineStage.SPEC_BUILD == "spec_build"
    assert PipelineStage.CROSS_LAYER == "cross_layer"
    assert PipelineStage.VISUAL_CHECK == "visual_check"
    assert PipelineStage.REPAIR == "repair"


def test_webapp_config_has_prompt():
    cfg = WebappConfig(outdir=".")
    assert hasattr(cfg, "prompt")
    assert cfg.prompt == ""


def test_webapp_config_has_app_mode():
    cfg = WebappConfig(outdir=".")
    assert hasattr(cfg, "app_mode")
    assert cfg.app_mode == AppMode.FLASK


def test_webapp_config_app_mode_roundtrip():
    cfg = WebappConfig(outdir=".", app_mode=AppMode.STATIC, prompt="hello")
    d = cfg.to_dict()
    assert d["app_mode"] == "static"
    assert d["prompt"] == "hello"
    restored = WebappConfig.from_dict(d)
    assert restored.app_mode == AppMode.STATIC
    assert restored.prompt == "hello"


def test_pipeline_result_has_new_fields():
    cfg = WebappConfig(outdir=".")
    pr = PipelineResult(config=cfg)
    assert hasattr(pr, "obligations_result")
    assert hasattr(pr, "cross_layer_result")
    assert hasattr(pr, "visual_result")
    assert hasattr(pr, "repair_iterations")
    assert pr.obligations_result == {}
    assert pr.cross_layer_result == {}
    assert pr.visual_result == {}
    assert pr.repair_iterations == 0


def test_pipeline_result_serialises_new_fields():
    cfg = WebappConfig(outdir=".")
    pr = PipelineResult(
        config=cfg,
        obligations_result={"skipped": True},
        cross_layer_result={"error_count": 0},
        visual_result={"passes_wcag_aa": True},
        repair_iterations=2,
    )
    d = pr.to_dict()
    assert d["obligations_result"] == {"skipped": True}
    assert d["cross_layer_result"] == {"error_count": 0}
    assert d["visual_result"] == {"passes_wcag_aa": True}
    assert d["repair_iterations"] == 2


def test_pipeline_result_from_dict_missing_new_fields():
    """from_dict should tolerate missing new fields (back-compat)."""
    cfg = WebappConfig(outdir=".")
    d = {
        "config": cfg.to_dict(),
        "stages_completed": [],
        "app_spec": {},
        "generation_result": {},
        "verification_result": {},
        "ideation_result": {},
        "descent_result": {},
        "output_dir": ".",
        "elapsed_ms": 0.0,
        # obligations_result, cross_layer_result, visual_result, repair_iterations
        # intentionally absent — from_dict should use setdefault
    }
    pr = PipelineResult.from_dict(d)
    assert pr.obligations_result == {}
    assert pr.cross_layer_result == {}
    assert pr.visual_result == {}
    assert pr.repair_iterations == 0


# ---------------------------------------------------------------------------
# Stage-level tests (no real module needed — advisory fallbacks tested)
# ---------------------------------------------------------------------------

def test_obligations_stage_skips_without_prompt(tmp_path):
    cfg = _make_config(str(tmp_path))  # no prompt
    pipeline = WebappPipeline()
    stage = pipeline._stage_obligations(cfg)
    assert stage.success
    assert stage.details.get("skipped") is True


def test_obligations_stage_skips_empty_prompt(tmp_path):
    cfg = _make_config(str(tmp_path), prompt="")
    pipeline = WebappPipeline()
    stage = pipeline._stage_obligations(cfg)
    assert stage.success
    assert stage.details.get("skipped") is True


def test_cross_layer_stage_skips_when_checkers_unavailable(tmp_path):
    """When _CHECKERS_AVAILABLE is False, cross-layer check skips gracefully."""
    import jugeo.webapp.cli.pipeline as _pl
    original = _pl._CHECKERS_AVAILABLE
    try:
        _pl._CHECKERS_AVAILABLE = False
        cfg = _make_config(str(tmp_path))
        pipeline = WebappPipeline()
        stage = pipeline._stage_cross_layer(cfg, str(tmp_path), {})
        assert stage.success
        assert stage.details.get("skipped") is True
    finally:
        _pl._CHECKERS_AVAILABLE = original


def test_visual_stage_skips_when_checkers_unavailable(tmp_path):
    """When _CHECKERS_AVAILABLE is False, visual check skips gracefully."""
    import jugeo.webapp.cli.pipeline as _pl
    original = _pl._CHECKERS_AVAILABLE
    try:
        _pl._CHECKERS_AVAILABLE = False
        cfg = _make_config(str(tmp_path))
        pipeline = WebappPipeline()
        stage = pipeline._stage_visual_check(cfg, str(tmp_path))
        assert stage.success
        assert stage.details.get("skipped") is True
    finally:
        _pl._CHECKERS_AVAILABLE = original


def test_cross_layer_stage_on_empty_dir(tmp_path):
    """Empty output dir → stage succeeds (either skipped or 0 errors)."""
    import jugeo.webapp.cli.pipeline as _pl
    original = _pl._CHECKERS_AVAILABLE
    try:
        _pl._CHECKERS_AVAILABLE = False
        cfg = _make_config(str(tmp_path))
        pipeline = WebappPipeline()
        stage = pipeline._stage_cross_layer(cfg, str(tmp_path), {})
        assert stage.success
    finally:
        _pl._CHECKERS_AVAILABLE = original


def test_visual_stage_on_empty_dir(tmp_path):
    """Empty output dir → stage succeeds."""
    import jugeo.webapp.cli.pipeline as _pl
    original = _pl._CHECKERS_AVAILABLE
    try:
        _pl._CHECKERS_AVAILABLE = False
        cfg = _make_config(str(tmp_path))
        pipeline = WebappPipeline()
        stage = pipeline._stage_visual_check(cfg, str(tmp_path))
        assert stage.success
    finally:
        _pl._CHECKERS_AVAILABLE = original


# ---------------------------------------------------------------------------
# Full pipeline integration tests
# ---------------------------------------------------------------------------

def test_full_pipeline_adds_cross_layer_and_visual_to_stages(tmp_path):
    """run() should always include cross_layer and visual stages in stages_completed."""
    cfg = _make_config(str(tmp_path))
    pipeline = WebappPipeline()
    result = pipeline.run(cfg)
    stage_names = [
        (s.stage if isinstance(s, StageResult) else s.get("stage", ""))
        for s in result.stages_completed
    ]
    assert PipelineStage.CROSS_LAYER in stage_names
    assert PipelineStage.VISUAL_CHECK in stage_names


def test_full_pipeline_with_prompt_adds_obligations_stage(tmp_path):
    cfg = _make_config(str(tmp_path), prompt="recipe sharing app")
    pipeline = WebappPipeline()
    result = pipeline.run(cfg)
    stage_names = [
        (s.stage if isinstance(s, StageResult) else s.get("stage", ""))
        for s in result.stages_completed
    ]
    assert PipelineStage.OBLIGATIONS in stage_names


def test_full_pipeline_without_prompt_omits_obligations_stage(tmp_path):
    cfg = _make_config(str(tmp_path))
    pipeline = WebappPipeline()
    result = pipeline.run(cfg)
    stage_names = [
        (s.stage if isinstance(s, StageResult) else s.get("stage", ""))
        for s in result.stages_completed
    ]
    assert PipelineStage.OBLIGATIONS not in stage_names


def test_full_pipeline_still_succeeds_with_prompt(tmp_path):
    cfg = _make_config(str(tmp_path), prompt="recipe sharing app")
    pipeline = WebappPipeline()
    result = pipeline.run(cfg)
    assert result.success


def test_full_pipeline_generates_app_py(tmp_path):
    cfg = _make_config(str(tmp_path))
    pipeline = WebappPipeline()
    pipeline.run(cfg)
    assert (tmp_path / "app.py").exists()


def test_obligations_stage_result_stored_on_pipeline_result(tmp_path):
    cfg = _make_config(str(tmp_path), prompt="recipe sharing app")
    pipeline = WebappPipeline()
    result = pipeline.run(cfg)
    # obligations_result is set (may be skipped dict if module unavailable, or real data)
    assert isinstance(result.obligations_result, dict)


def test_cross_layer_result_stored_on_pipeline_result(tmp_path):
    cfg = _make_config(str(tmp_path))
    pipeline = WebappPipeline()
    result = pipeline.run(cfg)
    assert isinstance(result.cross_layer_result, dict)


def test_visual_result_stored_on_pipeline_result(tmp_path):
    cfg = _make_config(str(tmp_path))
    pipeline = WebappPipeline()
    result = pipeline.run(cfg)
    assert isinstance(result.visual_result, dict)
