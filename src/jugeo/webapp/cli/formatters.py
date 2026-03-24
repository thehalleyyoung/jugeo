"""Output formatters for the webapp CLI.

Provides CLIFormatter (terminal), JSONReportFormatter, and
MarkdownReportFormatter.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


# ═══════════════════════════════════════════════════════════════════════════
# CLIFormatter — terminal / ANSI output
# ═══════════════════════════════════════════════════════════════════════════

class CLIFormatter:
    """Formats pipeline results for human-readable terminal output."""

    # -- box drawing helpers -------------------------------------------------

    @staticmethod
    def _box(title: str, width: int = 60) -> str:
        top = "+" + "-" * (width - 2) + "+"
        mid = "|" + title.center(width - 2) + "|"
        bot = "+" + "-" * (width - 2) + "+"
        return "\n".join([top, mid, bot])

    # -- public formatters ---------------------------------------------------

    @staticmethod
    def format_launch_instructions(config) -> str:
        """Human-readable instructions for launching the generated app."""
        outdir = getattr(config, "outdir", ".")
        port = getattr(config, "port", 5000)
        name = getattr(config, "app_name", "app")
        lines = [
            CLIFormatter._box(f" Launch: {name} "),
            "",
            f"  cd {outdir}",
            f"  pip install -r requirements.txt",
            f"  python app.py",
            "",
            f"  App will be served on http://localhost:{port}",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_generation_report(result: dict) -> str:
        """Format a generation StageResult.details dict."""
        lines = [CLIFormatter._box(" Generation Report "), ""]
        files = result.get("files_created", [])
        lines.append(f"  Files created: {len(files)}")
        for f in files:
            lines.append(f"    - {f}")
        odir = result.get("output_dir", "")
        if odir:
            lines.append(f"  Output directory: {odir}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_verification_report(result: dict) -> str:
        """Format a verification StageResult.details dict."""
        lines = [CLIFormatter._box(" Verification Report "), ""]
        passed = result.get("passed", False)
        lines.append(f"  Overall: {'PASS' if passed else 'FAIL'}")
        for chk in result.get("checks", []):
            mark = "[OK]" if chk.get("passed") else "[FAIL]"
            lines.append(f"    {mark} {chk.get('name', '?')}")
        errors = result.get("errors", [])
        if errors:
            lines.append("  Errors:")
            for e in errors:
                lines.append(f"    - {e}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_ideation_report(result: dict) -> str:
        """Format an ideation result dict."""
        lines = [CLIFormatter._box(" Ideation Report "), ""]
        for key, val in result.items():
            if isinstance(val, list):
                lines.append(f"  {key}: ({len(val)} items)")
            elif isinstance(val, dict):
                lines.append(f"  {key}: {{...}}")
            else:
                lines.append(f"  {key}: {val}")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def format_error(message: str) -> str:
        """Format an error message for the terminal."""
        return "\n".join([
            CLIFormatter._box(" ERROR "),
            "",
            f"  {message}",
            "",
        ])

    @staticmethod
    def format_pipeline_result(result) -> str:
        """Format a full PipelineResult."""
        lines = [CLIFormatter._box(" Pipeline Result "), ""]
        lines.append(f"  Success: {result.success}")
        lines.append(f"  Elapsed: {result.elapsed_ms:.1f} ms")
        lines.append(f"  Output:  {result.output_dir}")
        lines.append(f"  Stages:  {len(result.stages_completed)}")
        lines.append("")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# JSONReportFormatter
# ═══════════════════════════════════════════════════════════════════════════

class JSONReportFormatter:
    """Serialise a PipelineResult to JSON."""

    @staticmethod
    def format(result) -> str:
        """Return a JSON string."""
        if hasattr(result, "to_dict"):
            data = result.to_dict()
        elif isinstance(result, dict):
            data = result
        else:
            data = {"result": str(result)}
        return json.dumps(data, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════════
# MarkdownReportFormatter
# ═══════════════════════════════════════════════════════════════════════════

class MarkdownReportFormatter:
    """Serialise a PipelineResult to Markdown."""

    @staticmethod
    def format(result) -> str:
        """Return a Markdown string."""
        if hasattr(result, "to_dict"):
            data = result.to_dict()
        elif isinstance(result, dict):
            data = result
        else:
            data = {"result": str(result)}

        lines = ["# Pipeline Report", ""]

        config = data.get("config", {})
        if config:
            lines.append("## Configuration")
            lines.append("")
            for k, v in config.items():
                lines.append(f"- **{k}**: {v}")
            lines.append("")

        lines.append("## Results")
        lines.append("")
        lines.append(f"- **Success**: {data.get('success', 'N/A')}")
        lines.append(f"- **Elapsed**: {data.get('elapsed_ms', 0):.1f} ms")
        lines.append(f"- **Output**: {data.get('output_dir', '')}")
        lines.append("")

        stages = data.get("stages_completed", [])
        if stages:
            lines.append("## Stages")
            lines.append("")
            for s in stages:
                if isinstance(s, dict):
                    name = s.get("stage", "?")
                    ok = s.get("success", False)
                    lines.append(f"- **{name}**: {'OK' if ok else 'FAIL'}")
                else:
                    lines.append(f"- {s}")
            lines.append("")

        return "\n".join(lines)
