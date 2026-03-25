"""Data models for the jugeo webapp CLI.

Defines enumerations, configuration, and result dataclasses used by the
pipeline and command-line interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AppType(str, Enum):
    """Kind of web application to generate."""

    CRUD = "crud"
    API = "api"
    DASHBOARD = "dashboard"
    FORM_WORKFLOW = "form_workflow"
    CUSTOM = "custom"


class TemplateChoice(str, Enum):
    """Template complexity level."""

    MINIMAL = "minimal"
    STANDARD = "standard"
    FULL = "full"
    CUSTOM = "custom"


class AppMode(str, Enum):
    """Whether this is a Flask/server app or a static HTML app."""

    FLASK = "flask"
    STATIC = "static"


class PipelineStage(str, Enum):
    """Named stages in the webapp generation pipeline."""

    IDEATE = "ideate"
    SPECIFY = "specify"
    DESCENT_CHECK = "descent_check"
    GENERATE = "generate"
    VERIFY = "verify"
    REPORT = "report"
    OBLIGATIONS = "obligations"    # Phase 0: prompt → obligation presheaf
    SPEC_BUILD   = "spec_build"    # Phase 1: obligations → validated spec
    CROSS_LAYER  = "cross_layer"   # Phase 3: cross-layer descent check
    VISUAL_CHECK = "visual_check"  # Phase 4: visual correctness
    REPAIR       = "repair"        # Phase 5: iterative repair


# ---------------------------------------------------------------------------
# WebappConfig
# ---------------------------------------------------------------------------

@dataclass
class WebappConfig:
    """Configuration for a single webapp-generation run."""

    outdir: str
    port: int = 5000
    app_name: str = "app"
    app_type: AppType = AppType.CRUD
    domain: str = ""
    user_population: str = ""
    template: TemplateChoice = TemplateChoice.STANDARD
    verify: bool = True
    verbose: bool = False
    include_tests: bool = False
    include_docker: bool = False
    include_security_scan: bool = False
    models_json: list = field(default_factory=list)
    routes_json: list = field(default_factory=list)
    app_mode: AppMode = AppMode.FLASK
    prompt: str = ""  # raw natural language prompt if provided

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary."""
        return {
            "outdir": self.outdir,
            "port": self.port,
            "app_name": self.app_name,
            "app_type": self.app_type.value if isinstance(self.app_type, AppType) else self.app_type,
            "domain": self.domain,
            "user_population": self.user_population,
            "template": self.template.value if isinstance(self.template, TemplateChoice) else self.template,
            "verify": self.verify,
            "verbose": self.verbose,
            "include_tests": self.include_tests,
            "include_docker": self.include_docker,
            "include_security_scan": self.include_security_scan,
            "models_json": list(self.models_json),
            "routes_json": list(self.routes_json),
            "app_mode": self.app_mode.value if isinstance(self.app_mode, AppMode) else self.app_mode,
            "prompt": self.prompt,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WebappConfig:
        """Deserialise from a plain dictionary."""
        d = dict(data)  # shallow copy
        if "app_type" in d:
            d["app_type"] = AppType(d["app_type"])
        if "template" in d:
            d["template"] = TemplateChoice(d["template"])
        if "app_mode" in d:
            d["app_mode"] = AppMode(d["app_mode"])
        return cls(**d)


# ---------------------------------------------------------------------------
# StageResult
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """Outcome of a single pipeline stage."""

    stage: PipelineStage
    success: bool
    duration_ms: float = 0.0
    details: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value if isinstance(self.stage, PipelineStage) else self.stage,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "details": dict(self.details),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: dict) -> StageResult:
        d = dict(data)
        if "stage" in d:
            d["stage"] = PipelineStage(d["stage"])
        return cls(**d)


# ---------------------------------------------------------------------------
# PipelineResult
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Aggregated outcome of the full pipeline run."""

    config: WebappConfig
    stages_completed: list = field(default_factory=list)
    app_spec: dict = field(default_factory=dict)
    generation_result: dict = field(default_factory=dict)
    verification_result: dict = field(default_factory=dict)
    ideation_result: dict = field(default_factory=dict)
    descent_result: dict = field(default_factory=dict)
    output_dir: str = ""
    elapsed_ms: float = 0.0
    obligations_result: dict = field(default_factory=dict)
    cross_layer_result: dict = field(default_factory=dict)
    visual_result: dict = field(default_factory=dict)
    repair_iterations: int = 0

    # -- derived properties --------------------------------------------------

    @property
    def success(self) -> bool:
        """True when every completed stage had no errors."""
        if not self.stages_completed:
            return True
        for stage_result in self.stages_completed:
            if isinstance(stage_result, StageResult):
                if stage_result.errors:
                    return False
            elif isinstance(stage_result, dict):
                if stage_result.get("errors"):
                    return False
        return True

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        stages = []
        for s in self.stages_completed:
            if isinstance(s, StageResult):
                stages.append(s.to_dict())
            elif isinstance(s, dict):
                stages.append(s)
            else:
                stages.append(str(s))
        return {
            "config": self.config.to_dict(),
            "stages_completed": stages,
            "app_spec": dict(self.app_spec),
            "generation_result": dict(self.generation_result),
            "verification_result": dict(self.verification_result),
            "ideation_result": dict(self.ideation_result),
            "descent_result": dict(self.descent_result),
            "output_dir": self.output_dir,
            "elapsed_ms": self.elapsed_ms,
            "obligations_result": dict(self.obligations_result),
            "cross_layer_result": dict(self.cross_layer_result),
            "visual_result": dict(self.visual_result),
            "repair_iterations": self.repair_iterations,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PipelineResult:
        d = dict(data)
        d["config"] = WebappConfig.from_dict(d["config"])
        stages = []
        for s in d.get("stages_completed", []):
            if isinstance(s, dict) and "stage" in s:
                stages.append(StageResult.from_dict(s))
            else:
                stages.append(s)
        d["stages_completed"] = stages
        d.setdefault("descent_result", {})
        d.setdefault("obligations_result", {})
        d.setdefault("cross_layer_result", {})
        d.setdefault("visual_result", {})
        d.setdefault("repair_iterations", 0)
        return cls(**d)
