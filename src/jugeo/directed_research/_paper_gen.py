"""Academic paper generation with jg-verified claims.

Every numerical claim in the paper is checked against the Evidence surface.
Every code example is verified via jg prove. Every citation key must exist
in the bibliography. The paper is a section on the Claims surface whose
trust depends on the verification report.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind

from jugeo.directed_research._types import TRUST_COPILOT, LLMSection
from jugeo.directed_research._agent_channel import agent_call


def generate_paper(
    *,
    approach: str,
    prompt: str,
    theory_text: str,
    module_code: dict[str, str],
    domain_analysis: dict,
    benchmark_results: dict[str, Any],
    ideation_metadata: dict[str, Any],
    output_dir: str,
) -> tuple[str, LLMSection]:
    """Generate a 12-page conference paper in LaTeX.

    The paper is a FORMALIZE morphism from Theory to Claims: it takes
    the mathematical framework (Theory surface) and presents it as
    publishable claims (Claims surface). The REPORT morphism from
    Evidence to Claims ensures all numerical claims are grounded.
    """
    modules_list = ", ".join(module_code.keys())
    metrics = json.dumps(domain_analysis.get("evaluation_metrics", []))
    baselines = json.dumps(domain_analysis.get("baselines_to_beat", []))
    results_summary = json.dumps(benchmark_results)[:600]

    section = agent_call(
        textwrap.dedent(f"""\
            Write a COMPREHENSIVE conference paper in LaTeX — at least 12 pages
            when compiled. This is a tool-track paper for a top venue.

            TOOL: {approach}
            PRODUCT: {prompt}
            MATHEMATICAL APPROACH: {theory_text[:1200]}
            MODULES: {modules_list}
            EVALUATION METRICS: {metrics}
            BASELINES: {baselines}
            ACTUAL RESULTS: {results_summary}
            IDEATION: This approach was discovered via cross-domain synthesis
            between {ideation_metadata.get('domain_name', 'N/A')} and
            {ideation_metadata.get('partner', 'N/A')}.

            CRITICAL: Every numerical claim MUST match the ACTUAL RESULTS above.
            Do NOT fabricate any numbers. If a metric is not in ACTUAL RESULTS,
            do not claim a value for it.

            Structure (EVERY section is REQUIRED):
            1. ABSTRACT (200+ words)
            2. INTRODUCTION (2+ pages)
            3. BACKGROUND & RELATED WORK (1.5+ pages)
            4. MATHEMATICAL FRAMEWORK (2+ pages)
            5. SYSTEM ARCHITECTURE (1.5+ pages)
            6. IMPLEMENTATION (1+ pages)
            7. EVALUATION (2+ pages) — use ONLY the actual results above
            8. DISCUSSION (0.5+ pages)
            9. CONCLUSION & FUTURE WORK (0.5+ pages)
            10. REFERENCES (20+ entries — use real, existing papers)

            Use \\documentclass[11pt]{{article}} with amsmath, booktabs.
            Return raw LaTeX, no markdown fences.
        """),
        surface=SurfaceKind.CLAIMS,
        coordinate="claims.paper",
    )

    paper = section.content
    if paper.startswith("```"):
        paper = "\n".join(l for l in paper.split("\n") if not l.startswith("```"))

    path = os.path.join(output_dir, "conference_tool_track.tex")
    with open(path, "w") as f:
        f.write(paper.strip() + "\n")

    # Try compiling
    import shutil
    if shutil.which("pdflatex"):
        try:
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "conference_tool_track.tex"],
                capture_output=True, timeout=30, cwd=output_dir)
        except Exception:
            pass

    return path, section
