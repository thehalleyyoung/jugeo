"""jugeo.webapp.cli — CLI integration for the jugeo webapp command.

Provides argument parsing, pipeline orchestration, and output formatting
for the ``jugeo webapp`` subcommand.

Public API:
    register_webapp_command  — register with argparse subparsers
    run_webapp_command       — execute the webapp command
    WebappConfig             — configuration dataclass
    WebappPipeline           — full pipeline orchestrator
    CLIFormatter             — terminal output formatter
"""
from __future__ import annotations

from jugeo.webapp.cli.models import (
    WebappConfig,
    AppType,
    TemplateChoice,
    PipelineStage,
    PipelineResult,
    StageResult,
)
from jugeo.webapp.cli.webapp_command import register_webapp_command, run_webapp_command
from jugeo.webapp.cli.pipeline import WebappPipeline, SpecBuilder, TemplateSpecs
from jugeo.webapp.cli.formatters import CLIFormatter, JSONReportFormatter, MarkdownReportFormatter

__all__ = [
    "WebappConfig",
    "AppType",
    "TemplateChoice",
    "PipelineStage",
    "PipelineResult",
    "StageResult",
    "register_webapp_command",
    "run_webapp_command",
    "WebappPipeline",
    "SpecBuilder",
    "TemplateSpecs",
    "CLIFormatter",
    "JSONReportFormatter",
    "MarkdownReportFormatter",
]
