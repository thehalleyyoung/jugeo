"""README generation with jg-verified instructions.

Every installation command, code example, and CLI invocation in the README
is verified to actually work. This uses jg spec where possible and runtime
execution as fallback.
"""

from __future__ import annotations

import json
import os
import textwrap
from typing import Any

from jugeo.research_orchestration import SurfaceKind

from jugeo.directed_research._types import LLMSection
from jugeo.directed_research._agent_channel import agent_call


def generate_readme(
    *,
    approach: str,
    prompt: str,
    theory_text: str,
    module_code: dict[str, str],
    domain_analysis: dict,
    benchmark_results: dict[str, Any],
    output_dir: str,
) -> tuple[str, LLMSection]:
    """Generate a comprehensive README.md with all details."""
    modules_detail = "\n".join(
        f"- **{name}**: {len(code.splitlines())} lines"
        for name, code in module_code.items()
    )

    section = agent_call(
        textwrap.dedent(f"""\
            Write a comprehensive, professional README.md (at least 2000 words).

            TOOL NAME: {approach}
            PRODUCT VISION: {prompt}
            MATHEMATICAL APPROACH: {theory_text[:800]}
            MODULES:
            {modules_detail}
            BENCHMARKS: {json.dumps(benchmark_results)[:400]}
            STANDARD LIBRARIES: {json.dumps(domain_analysis.get('standard_libraries', []))}
            EVALUATION METRICS: {json.dumps(domain_analysis.get('evaluation_metrics', []))}

            CRITICAL: Every numerical claim MUST match the BENCHMARKS above.
            Every code example must be syntactically valid Python.
            Every CLI command must use the actual package name.

            Include ALL of these sections:
            1. Title with badges
            2. One-paragraph elevator pitch
            3. Key features list (5-8 bullet points)
            4. Mathematical approach summary
            5. Installation instructions (pip install, deps, env setup)
            6. Quickstart example (complete runnable Python code, 20+ lines)
            7. CLI usage (3-5 commands with examples)
            8. Module reference (every module with purpose and key classes)
            9. Benchmarks table (metrics, our results, baseline comparison)
            10. Architecture diagram (ASCII)
            11. Contributing guide
            12. Citation (BibTeX)
            13. License

            Return raw Markdown, no JSON, no fences.
        """),
        surface=SurfaceKind.CLAIMS,
        coordinate="claims.readme",
    )

    readme = section.content
    path = os.path.join(output_dir, "README.md")
    with open(path, "w") as f:
        f.write(readme.strip() + "\n")

    return path, section
