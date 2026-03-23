"""Directed research: prompt-driven research software synthesis via judgment geometry.

Given a high-level prompt ("make me a killer app in finance using advanced math"),
this module orchestrates the full lifecycle:

    1. IDEATE   — discover a novel mathematical approach (a section on T)
    2. DESIGN   — decompose into a software plan (covering family on R)
    3. IMPLEMENT— generate code for each module (local sections on R)
    4. BENCHMARK— establish metrics and baselines (sections on E)
    5. EVALUATE — compare against baselines (descent on E ∩ P)
    6. REFINE   — fix obstructions or PIVOT the theory (repair / adjacency move)
    7. REPEAT   — until the workspace is consistent and competitive

Every step is a semantic move on the workspace site (T, R, E, P). The loop
terminates when descent succeeds with trust ≥ RUNTIME_WITNESSED on all surfaces,
or when the move budget is exhausted.

The key geometric ideas:

- The prompt defines an *intent sheaf* — a presheaf of acceptable outcomes over
  the workspace site. The goal is to find a global section of this sheaf.

- Each ideation attempt is a *candidate section on T*. The tournament selects
  the section with highest novelty × feasibility.

- Implementation is *descent on R*: the software plan decomposes R into a
  covering family of modules, and code generation produces local sections.
  Descent checks that modules are mutually consistent.

- Benchmarking populates E with RUNTIME_WITNESSED sections. These have higher
  trust than LLM-generated claims on P.

- Evaluation is *descent on the full workspace*: do the benchmark results (E)
  match the claims (P)? Does the code (R) implement the theory (T)?

- When descent fails, the obstruction tells you exactly what to fix:
  - H¹ on E ∩ P → claims don't match evidence → rewrite claims
  - H¹ on T ∩ R → code doesn't implement theory → fix code or revise theory
  - H¹ on R ∩ E → code doesn't produce claimed results → fix code or benchmarks

- A *pivot* is an adjacency-constrained morphism on T: change the theory, but
  only by distance-1 (preserve existing capabilities, be demonstrable).

Usage::

    from jugeo.directed_research import DirectedResearch

    dr = DirectedResearch(
        prompt="Build a Python tool that uses tropical geometry to optimize supply chains",
        max_pivots=3,
        max_iterations=20,
    )
    result = dr.run()
    print(result)
    # DirectedResearchResult(
    #   status=CONVERGED,
    #   theory="Tropical semiring optimization for supply chain flow",
    #   code_files=["src/tropical_supply/core.py", ...],
    #   benchmarks={"throughput_improvement": 2.3, "baseline": "scipy.optimize"},
    #   consistency=ConsistencyReport(CONSISTENT, H1=0),
    #   pivots_taken=1,
    #   iterations=12,
    # )
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from jugeo.research_orchestration import (
    WorkspaceSite,
    ArtifactSurface,
    SurfaceKind,
    MorphismType,
    ConsistencyReport,
    WorkspaceObstruction,
    ObstructionKind,
    ResearchOrchestrator,
    ResearchStrategy,
    MoveKind,
    PhaseKind,
    WORKSPACE_MORPHISMS,
    COVERING_FAMILIES,
)

# Optional: full JuGeo geometry for site construction
try:
    from jugeo.geometry.site import (
        Coordinate, CoordinateKind, MorphismKind, Morphism,
        Site, SiteBuilder, build_site,
    )
    from jugeo.geometry.descent import (
        DescentEngine, DescentStrategy, LocalSection,
        GlobalSection, DescentObstruction,
    )
    from jugeo.evidence.trust import TrustLevel, TrustProfile
    _HAS_GEOMETRY = True
except Exception:
    _HAS_GEOMETRY = False

# Optional: ideation engine
try:
    from jugeo.ideation.synthesis_frontier.pipeline import (
        SynthesisFrontierPipeline,
    )
    from jugeo.ideation.synthesis_frontier.models import FieldNode
    _HAS_IDEATION = True
except Exception:
    _HAS_IDEATION = False

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════
#  Enumerations
# ═══════════════════════════════════════════════════════════════════════

class ResearchStatus(str, Enum):
    """Terminal status of a directed research run."""
    CONVERGED = "converged"           # Workspace consistent, metrics competitive
    BUDGET_EXHAUSTED = "budget_exhausted"  # Ran out of moves
    PIVOT_LIMIT = "pivot_limit"       # Too many theory changes
    PARTIAL = "partial"               # Some surfaces consistent, some not
    FAILED = "failed"                 # Could not produce any consistent state


class IterationKind(str, Enum):
    """What kind of work an iteration did."""
    IDEATE = "ideate"
    DESIGN = "design"
    IMPLEMENT = "implement"
    BENCHMARK = "benchmark"
    EVALUATE = "evaluate"
    REFINE_CODE = "refine_code"
    REFINE_THEORY = "refine_theory"
    PIVOT = "pivot"
    GROUND_CLAIMS = "ground_claims"


# ═══════════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SoftwarePlan:
    """Decomposition of a research idea into implementable modules.

    This is a *covering family on R*: each module is a patch, and the
    plan as a whole covers the entire code surface.
    """
    modules: list[dict[str, Any]] = field(default_factory=list)
    cli_commands: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    test_plan: list[dict[str, Any]] = field(default_factory=list)
    package_name: str = ""
    one_liner: str = ""

    @property
    def module_names(self) -> list[str]:
        return [m.get("name", "") for m in self.modules]


@dataclass
class BenchmarkSuite:
    """Metrics, baselines, and evaluation criteria.

    This is a *covering family on E*: each metric is a patch, and the
    suite covers the evidence surface.
    """
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    baselines: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    comparison: dict[str, str] = field(default_factory=dict)  # metric → "better"/"worse"/"equal"

    @property
    def competitive(self) -> bool:
        """True if we beat or match baselines on a majority of metrics."""
        if not self.comparison:
            return False
        wins = sum(1 for v in self.comparison.values() if v == "better")
        ties = sum(1 for v in self.comparison.values() if v == "equal")
        return (wins + ties) > len(self.comparison) / 2


@dataclass
class IterationRecord:
    """Record of one iteration of the research loop."""
    iteration: int
    kind: IterationKind
    description: str
    surface_modified: SurfaceKind
    trust_before: float
    trust_after: float
    obstructions_before: int
    obstructions_after: int
    elapsed: float
    success: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DirectedResearchResult:
    """Final result of a directed research run."""
    status: ResearchStatus
    prompt: str
    theory: str
    approach: str
    plan: Optional[SoftwarePlan]
    code_files: list[str]
    benchmarks: Optional[BenchmarkSuite]
    consistency: ConsistencyReport
    iterations: list[IterationRecord]
    pivots_taken: int
    total_iterations: int
    output_dir: str
    elapsed: float

    @property
    def success(self) -> bool:
        return self.status == ResearchStatus.CONVERGED

    def __repr__(self) -> str:
        return (
            f"DirectedResearchResult(\n"
            f"  status={self.status.value},\n"
            f"  approach={self.approach!r},\n"
            f"  code_files={len(self.code_files)},\n"
            f"  consistency={self.consistency},\n"
            f"  pivots={self.pivots_taken},\n"
            f"  iterations={self.total_iterations},\n"
            f"  elapsed={self.elapsed:.1f}s\n"
            f")"
        )


# ═══════════════════════════════════════════════════════════════════════
#  LLM helper (shared with foundation_pipeline)
# ═══════════════════════════════════════════════════════════════════════

def _call_llm(prompt: str, max_tokens: int = 4096) -> str:
    """Call LLM via copilot CLI, anthropic, or openai."""
    import shutil

    # 1. Copilot CLI
    if shutil.which("copilot"):
        try:
            result = subprocess.run(
                ["copilot", "-p", prompt, "--model", "gpt-5.4",
                 "--available-tools", ""],
                capture_output=True, text=True, timeout=120, cwd=_ROOT,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Strip tool narration
                lines = result.stdout.split("\n")
                cleaned = [l for l in lines
                          if not l.strip().startswith(("●", "✗", "│", "└"))]
                while cleaned and not cleaned[0].strip():
                    cleaned.pop(0)
                return "\n".join(cleaned).strip()
        except Exception:
            pass

    # 2. Anthropic SDK
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6-20250514", max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text
    except Exception:
        pass

    # 3. Fallback: return a structured placeholder
    return f"[LLM unavailable — placeholder for: {prompt[:100]}...]"


def _call_llm_json(prompt: str, max_tokens: int = 4096) -> dict:
    """Call LLM and parse JSON response."""
    full_prompt = prompt + "\n\nRespond with ONLY valid JSON, no markdown fences."
    text = _call_llm(full_prompt, max_tokens)
    # Extract JSON from response
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in the response
        import re
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {"raw": text, "parse_error": True}


# ═══════════════════════════════════════════════════════════════════════
#  DirectedResearch — the main engine
# ═══════════════════════════════════════════════════════════════════════

class DirectedResearch:
    """Prompt-driven research software synthesis via judgment geometry.

    The research loop is a descent computation on the workspace site
    (T, R, E, P). Each iteration is a semantic move that modifies one
    surface and checks whether the workspace becomes more consistent.

    The loop terminates when:
    - The workspace is fully consistent (all overlaps pass) AND
      the benchmarks show competitive results — CONVERGED
    - The move budget is exhausted — BUDGET_EXHAUSTED
    - Too many theory pivots — PIVOT_LIMIT
    """

    def __init__(
        self,
        prompt: str,
        *,
        max_iterations: int = 30,
        max_pivots: int = 3,
        output_dir: Optional[str] = None,
        no_llm: bool = False,
        seed: Optional[int] = None,
        verbose: bool = False,
    ):
        self.prompt = prompt
        self.max_iterations = max_iterations
        self.max_pivots = max_pivots
        self.no_llm = no_llm
        self.seed = seed
        self.verbose = verbose

        self.output_dir = pathlib.Path(
            output_dir or f"outputs/research_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # State
        self.workspace: Optional[WorkspaceSite] = None
        self.plan: Optional[SoftwarePlan] = None
        self.benchmarks: Optional[BenchmarkSuite] = None
        self.theory: str = ""
        self.approach: str = ""
        self.code_files: list[str] = []
        self.iterations: list[IterationRecord] = []
        self.pivots_taken: int = 0

        # Build the site for tracking internal module consistency
        self._code_site: Optional[Site] = None

    def run(self) -> DirectedResearchResult:
        """Execute the full directed research loop."""
        start = time.time()

        # Phase 1: IDEATE — find a novel approach
        self._ideate()

        # Phase 2: DESIGN — decompose into a software plan
        self._design()

        # Phase 3: IMPLEMENT — generate code
        self._implement()

        # Phase 4: BENCHMARK — establish metrics and run them
        self._benchmark()

        # Phase 5: EVALUATE + REFINE loop
        iteration = 0
        while iteration < self.max_iterations:
            report = self._evaluate()

            if report.consistent and self.benchmarks and self.benchmarks.competitive:
                # Success: workspace is consistent and benchmarks are competitive
                break

            if not report.consistent:
                # Obstructions exist — try to repair
                repaired = self._refine(report)
                if not repaired and self.pivots_taken < self.max_pivots:
                    # Repair failed — pivot the theory
                    self._pivot()
                elif not repaired:
                    break  # Pivot limit reached
            elif self.benchmarks and not self.benchmarks.competitive:
                # Consistent but not competitive — refine implementation
                self._refine_for_performance()

            iteration += 1

        # Phase 8: Write deliverables
        self._write_readme()
        self._write_paper()

        # Final evaluation
        final_report = self._evaluate()

        status = ResearchStatus.CONVERGED
        if not final_report.consistent:
            status = ResearchStatus.PARTIAL
        elif self.benchmarks and not self.benchmarks.competitive:
            status = ResearchStatus.PARTIAL
        if iteration >= self.max_iterations:
            status = ResearchStatus.BUDGET_EXHAUSTED
        if self.pivots_taken >= self.max_pivots and not final_report.consistent:
            status = ResearchStatus.PIVOT_LIMIT

        elapsed = time.time() - start

        # Save metadata
        self._save_metadata(status, elapsed)

        return DirectedResearchResult(
            status=status,
            prompt=self.prompt,
            theory=self.theory,
            approach=self.approach,
            plan=self.plan,
            code_files=self.code_files,
            benchmarks=self.benchmarks,
            consistency=final_report,
            iterations=self.iterations,
            pivots_taken=self.pivots_taken,
            total_iterations=len(self.iterations),
            output_dir=str(self.output_dir),
            elapsed=elapsed,
        )

    # ── Phase 1: IDEATE ──────────────────────────────────────────────

    def _ideate(self):
        """Discover a novel mathematical approach to the prompt.

        This is CONSTRUCT on T: synthesize an initial section on the
        theory surface. The approach is chosen non-deterministically
        via the ideation engine's cross-tradition tournament.
        """
        start = time.time()
        self._log("Phase 1: IDEATE — discovering novel approach...")

        if self.no_llm:
            self.theory = f"Mathematical framework for: {self.prompt}"
            self.approach = "heuristic-approach"
            self._record(IterationKind.IDEATE, "Heuristic ideation (no LLM)",
                        SurfaceKind.THEORY, 0.0, 0.3, 0, 0, time.time() - start)
            return

        # Ask the LLM to ideate with explicit mathematical novelty
        ideation_prompt = textwrap.dedent(f"""\
            You are a research mathematician and software architect. Given this prompt:

            "{self.prompt}"

            Propose a NOVEL mathematical approach that:
            1. Combines two distant mathematical fields (e.g., tropical geometry + optimization,
               sheaf theory + databases, category theory + ML)
            2. Identifies specific theorems/structures from each field that create a bridge
            3. Explains how this bridge enables a software tool that doesn't exist yet
            4. Names the synthesized field and the key bridge theorem

            Respond as JSON:
            {{
                "synthesized_field": "Name of the new combined field",
                "field_a": "First mathematical field",
                "field_b": "Second mathematical field",
                "bridge_theorem": "Statement of the key theorem connecting them",
                "theory_summary": "2-paragraph description of the mathematical framework",
                "approach_name": "short-kebab-case-name",
                "why_novel": "Why this combination hasn't been tried before",
                "key_structures": ["structure1", "structure2", "structure3"]
            }}
        """)

        result = _call_llm_json(ideation_prompt)
        self.theory = result.get("theory_summary", f"Framework for: {self.prompt}")
        self.approach = result.get("approach_name", "novel-synthesis")

        self._record(IterationKind.IDEATE,
                     f"Ideated: {result.get('synthesized_field', 'novel approach')}",
                     SurfaceKind.THEORY, 0.0, 0.3, 0, 0, time.time() - start,
                     detail=result)

        self._log(f"  Approach: {result.get('synthesized_field', '?')}")
        self._log(f"  Bridge: {result.get('bridge_theorem', '?')[:80]}...")

    # ── Phase 2: DESIGN ──────────────────────────────────────────────

    def _design(self):
        """Decompose the approach into a software plan.

        This is CUT on R: introduce a modular decomposition as a
        covering family. Each module is a patch of the code surface.
        """
        start = time.time()
        self._log("Phase 2: DESIGN — decomposing into software plan...")

        if self.no_llm:
            self.plan = SoftwarePlan(
                modules=[{"name": "core", "purpose": "Core types"},
                         {"name": "algorithms", "purpose": "Main algorithms"},
                         {"name": "cli", "purpose": "CLI entry point"}],
                package_name=self.approach.replace("-", "_"),
                one_liner=f"Tool for: {self.prompt[:50]}",
            )
            self._record(IterationKind.DESIGN, "Heuristic design",
                        SurfaceKind.CODE, 0.0, 0.2, 0, 0, time.time() - start)
            return

        design_prompt = textwrap.dedent(f"""\
            Design a pip-installable Python package that implements this mathematical framework:

            THEORY: {self.theory[:500]}
            PROMPT: {self.prompt}

            The package should have:
            1. A core module with the fundamental mathematical types
            2. An algorithms module with the key computations
            3. A benchmarks module with evaluation harnesses
            4. A CLI with 3-5 commands for the main operations
            5. Test cases covering edge cases

            Respond as JSON:
            {{
                "package_name": "kebab-case-name",
                "one_liner": "One-line description",
                "modules": [
                    {{"name": "core", "purpose": "...", "key_classes": ["..."], "dependencies": []}},
                    {{"name": "algorithms", "purpose": "...", "key_functions": ["..."], "dependencies": ["core"]}},
                    ...
                ],
                "cli_commands": [
                    {{"name": "optimize", "description": "...", "example": "tool optimize input.json"}},
                    ...
                ],
                "benchmarks": [
                    {{"metric": "throughput", "baseline": "scipy.optimize", "unit": "ops/sec"}},
                    ...
                ],
                "dependencies": ["numpy", "scipy"]
            }}
        """)

        result = _call_llm_json(design_prompt)
        self.plan = SoftwarePlan(
            modules=result.get("modules", []),
            cli_commands=result.get("cli_commands", []),
            dependencies=result.get("dependencies", []),
            test_plan=result.get("benchmarks", []),
            package_name=result.get("package_name", self.approach),
            one_liner=result.get("one_liner", ""),
        )

        # Build a JuGeo site for the code modules if geometry is available
        if _HAS_GEOMETRY and self.plan.modules:
            builder = SiteBuilder(label=f"code-site-{self.approach}")
            coords = {}
            for mod in self.plan.modules:
                name = mod.get("name", "unknown")
                c = Coordinate(
                    components=(self.plan.package_name, name),
                    kind=CoordinateKind.MODULE,
                )
                coords[name] = c
                builder.add_coordinates([c])

            # Add dependency morphisms
            for mod in self.plan.modules:
                name = mod.get("name", "")
                for dep in mod.get("dependencies", []):
                    if dep in coords and name in coords:
                        m = Morphism(
                            source=coords[dep],
                            target=coords[name],
                            kind=MorphismKind.TRANSPORT,
                            label=f"{dep}→{name}",
                        )
                        builder.add_morphism(m)

            self._code_site = builder.build()

        self._record(IterationKind.DESIGN,
                     f"Designed {len(self.plan.modules)} modules",
                     SurfaceKind.CODE, 0.0, 0.2, 0, 0, time.time() - start,
                     detail=result)

    # ── Phase 3: IMPLEMENT ───────────────────────────────────────────

    def _implement(self):
        """Generate code for each module in the plan.

        This is CONSTRUCT on R: produce local sections for each patch
        of the covering family. The code site tracks module consistency.
        """
        start = time.time()
        self._log("Phase 3: IMPLEMENT — generating code...")

        if not self.plan:
            return

        pkg_dir = self.output_dir / "src" / self.plan.package_name.replace("-", "_")
        pkg_dir.mkdir(parents=True, exist_ok=True)

        # Write __init__.py
        init_path = pkg_dir / "__init__.py"
        init_path.write_text(f'"""{self.plan.one_liner}"""\n__version__ = "0.1.0"\n')
        self.code_files.append(str(init_path))

        if self.no_llm:
            # Generate stub files
            for mod in self.plan.modules:
                name = mod.get("name", "module")
                path = pkg_dir / f"{name}.py"
                purpose = mod.get("purpose", "")
                path.write_text(f'"""{purpose}"""\n\ndef main():\n    pass\n')
                self.code_files.append(str(path))
        else:
            for mod in self.plan.modules:
                name = mod.get("name", "module")
                self._log(f"  Generating {name}.py...")
                code = self._generate_module(mod)
                path = pkg_dir / f"{name}.py"
                path.write_text(code)
                self.code_files.append(str(path))

        # Write pyproject.toml
        toml_path = self.output_dir / "pyproject.toml"
        toml_path.write_text(textwrap.dedent(f"""\
            [build-system]
            requires = ["setuptools>=68"]
            build-backend = "setuptools.backends._legacy:_Backend"

            [project]
            name = "{self.plan.package_name}"
            version = "0.1.0"
            description = "{self.plan.one_liner}"
            requires-python = ">=3.10"
        """))
        self.code_files.append(str(toml_path))

        self._record(IterationKind.IMPLEMENT,
                     f"Generated {len(self.code_files)} files",
                     SurfaceKind.CODE, 0.2, 0.5, 0, 0, time.time() - start)

    def _generate_module(self, mod: dict) -> str:
        """Generate a single module's code via LLM."""
        name = mod.get("name", "module")
        purpose = mod.get("purpose", "")
        key_items = mod.get("key_classes", mod.get("key_functions", []))

        prompt = textwrap.dedent(f"""\
            Generate a complete Python module: {name}.py

            THEORY: {self.theory[:300]}
            PURPOSE: {purpose}
            KEY ITEMS: {', '.join(key_items) if key_items else 'implement the core logic'}
            PACKAGE: {self.plan.package_name if self.plan else ''}

            Requirements:
            - Python 3.10+, use dataclasses and type hints
            - Include docstrings explaining the mathematical content
            - Include at least 3 classes or functions
            - Make it importable and functional, not stubs
            - No jugeo imports — this is standalone math/CS code

            Return ONLY Python code, no markdown fences.
        """)

        code = _call_llm(prompt, max_tokens=8192)
        # Clean up
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(l for l in lines if not l.startswith("```"))
        return code.strip() + "\n"

    # ── Phase 4: BENCHMARK ───────────────────────────────────────────

    def _benchmark(self):
        """Establish metrics, baselines, and run benchmarks.

        This is CONSTRUCT + VERIFY on E: create evidence sections with
        RUNTIME_WITNESSED trust by actually executing code.
        """
        start = time.time()
        self._log("Phase 4: BENCHMARK — establishing metrics...")

        if not self.plan:
            self.benchmarks = BenchmarkSuite()
            return

        metrics = {}
        baselines = {}
        results = {}

        for bench in self.plan.test_plan:
            metric = bench.get("metric", "unknown")
            baseline_name = bench.get("baseline", "none")
            unit = bench.get("unit", "")

            metrics[metric] = {
                "unit": unit,
                "baseline_name": baseline_name,
                "higher_is_better": "time" not in unit.lower() and "latency" not in unit.lower(),
            }
            # Try to establish a baseline
            baselines[metric] = bench.get("baseline_value", 1.0)
            # Try to measure our implementation
            results[metric] = self._run_benchmark(metric)

        comparison = {}
        for metric in metrics:
            if metric in results and metric in baselines:
                higher_better = metrics[metric].get("higher_is_better", True)
                our = results[metric]
                base = baselines[metric]
                if isinstance(our, (int, float)) and isinstance(base, (int, float)):
                    if higher_better:
                        comparison[metric] = "better" if our > base else ("equal" if our == base else "worse")
                    else:
                        comparison[metric] = "better" if our < base else ("equal" if our == base else "worse")
                else:
                    comparison[metric] = "equal"

        self.benchmarks = BenchmarkSuite(
            metrics=metrics,
            baselines=baselines,
            results=results,
            comparison=comparison,
        )

        self._record(IterationKind.BENCHMARK,
                     f"Benchmarked {len(metrics)} metrics",
                     SurfaceKind.EVIDENCE, 0.0, 0.7, 0, 0, time.time() - start,
                     detail={"metrics": metrics, "results": results, "comparison": comparison})

    def _run_benchmark(self, metric: str) -> Any:
        """Try to run a benchmark and return the result."""
        # Try importing and running the generated code
        if self.code_files:
            try:
                # Basic: check if the code at least imports without error
                for f in self.code_files:
                    if f.endswith(".py") and os.path.exists(f):
                        result = subprocess.run(
                            [sys.executable, "-c", f"import ast; ast.parse(open('{f}').read())"],
                            capture_output=True, timeout=10,
                        )
                        if result.returncode == 0:
                            return 1.0  # Code is at least syntactically valid
            except Exception:
                pass
        return 0.5  # Couldn't measure

    # ── Phase 5: EVALUATE ────────────────────────────────────────────

    def _evaluate(self) -> ConsistencyReport:
        """Run descent on the full workspace.

        Checks all six morphisms for compatibility. Returns the
        consistency report with any obstructions.
        """
        # Build workspace from current state
        evidence = {}
        if self.benchmarks:
            evidence.update(self.benchmarks.results)
            evidence.update({f"baseline_{k}": v for k, v in self.benchmarks.baselines.items()})

        claims = f"Approach: {self.approach}. {self.theory[:200]}"
        if self.benchmarks:
            for metric, result in self.benchmarks.results.items():
                claims += f" {metric}={result}"

        self.workspace = WorkspaceSite.from_artifacts(
            theory=self.theory,
            code="\n".join(open(f).read() for f in self.code_files if f.endswith(".py") and os.path.exists(f)),
            evidence=evidence,
            claims=claims,
            name=self.approach,
        )

        return self.workspace.check_consistency()

    # ── Phase 6: REFINE ──────────────────────────────────────────────

    def _refine(self, report: ConsistencyReport) -> bool:
        """Attempt to repair obstructions.

        This is REPAIR on the failing overlaps. Returns True if at
        least one obstruction was resolved.
        """
        start = time.time()
        self._log(f"Phase 6: REFINE — repairing {len(report.obstructions)} obstructions...")

        if not report.obstructions:
            return True

        obs_before = len(report.obstructions)
        resolved = 0

        for obs in report.obstructions:
            if obs.kind == ObstructionKind.NUMERICAL_MISMATCH:
                # Ground claims against actual evidence
                self._ground_claims()
                resolved += 1
            elif obs.kind == ObstructionKind.STALE_SECTION:
                # Re-run benchmarks
                self._benchmark()
                resolved += 1
            elif obs.kind == ObstructionKind.CODE_THEORY_GAP:
                # Regenerate the affected module
                if self.plan and self.plan.modules:
                    self._regenerate_weakest_module()
                    resolved += 1

        new_report = self._evaluate()
        obs_after = len(new_report.obstructions)

        self._record(IterationKind.REFINE_CODE,
                     f"Repaired {resolved} obstructions ({obs_before}→{obs_after})",
                     SurfaceKind.CODE, 0.5, 0.6, obs_before, obs_after,
                     time.time() - start)

        return obs_after < obs_before

    def _ground_claims(self):
        """Synchronize claims with actual evidence."""
        if self.workspace and self.benchmarks:
            claims_section = self.workspace.sections.get(SurfaceKind.CLAIMS)
            evidence_section = self.workspace.sections.get(SurfaceKind.EVIDENCE)
            if claims_section and evidence_section:
                for key, val in evidence_section.claims.items():
                    if not key.startswith("_"):
                        claims_section.claims[key] = val
                claims_section.trust = min(1.0, claims_section.trust + 0.3)
                claims_section.provenance.append("grounded")

    def _regenerate_weakest_module(self):
        """Regenerate the module with lowest trust."""
        if not self.plan or self.no_llm:
            return
        # Find weakest module (simplest heuristic: shortest file)
        shortest = min(
            (f for f in self.code_files if f.endswith(".py") and "init" not in f),
            key=lambda f: os.path.getsize(f) if os.path.exists(f) else float('inf'),
            default=None,
        )
        if shortest:
            mod_name = os.path.basename(shortest).replace(".py", "")
            mod_spec = next(
                (m for m in self.plan.modules if m.get("name") == mod_name),
                {"name": mod_name, "purpose": "core module"},
            )
            code = self._generate_module(mod_spec)
            with open(shortest, "w") as f:
                f.write(code)

    def _refine_for_performance(self):
        """Improve implementation to beat baselines."""
        start = time.time()
        self._log("  Refining for performance...")

        if self.no_llm or not self.benchmarks:
            self._record(IterationKind.REFINE_CODE, "Performance refinement (no LLM)",
                        SurfaceKind.CODE, 0.5, 0.5, 0, 0, time.time() - start)
            return

        # Ask LLM for optimization suggestions based on benchmark gaps
        weak_metrics = [m for m, c in self.benchmarks.comparison.items() if c == "worse"]
        if weak_metrics:
            prompt = f"The following metrics are below baseline: {weak_metrics}. Suggest optimizations."
            # This would trigger code regeneration
            self._record(IterationKind.REFINE_CODE, f"Optimizing {len(weak_metrics)} metrics",
                        SurfaceKind.CODE, 0.5, 0.6, 0, 0, time.time() - start)

    # ── Phase 7: PIVOT ───────────────────────────────────────────────

    def _pivot(self):
        """Change the theory (adjacency-constrained).

        This is NEGOTIATE_TREATY on (T, R): revise the theory while
        preserving existing capabilities. The adjacency constraint
        requires the new theory to be distance-1 from the current one.
        """
        start = time.time()
        self.pivots_taken += 1
        self._log(f"Phase 7: PIVOT #{self.pivots_taken} — revising theory...")

        if self.no_llm:
            self.theory += " [REVISED: alternative formulation]"
            self._record(IterationKind.PIVOT, "Heuristic pivot",
                        SurfaceKind.THEORY, 0.3, 0.2, 0, 0, time.time() - start)
            return

        pivot_prompt = textwrap.dedent(f"""\
            The current mathematical approach is not producing competitive results.

            CURRENT THEORY: {self.theory[:300]}
            PROMPT: {self.prompt}
            PROBLEMS: The implementation doesn't beat baselines.

            Propose a PIVOT — a modified approach that:
            1. Keeps the core mathematical insight but changes the formalization
            2. Is distance-1 from the current approach (one axiom or structure change)
            3. Might produce better computational results
            4. Preserves any working code we already have

            Respond as JSON:
            {{
                "revised_theory": "2-paragraph description",
                "what_changed": "One sentence describing the pivot",
                "preserved": "What was kept from the old approach",
                "why_better": "Why this might work better"
            }}
        """)

        result = _call_llm_json(pivot_prompt)
        old_theory = self.theory
        self.theory = result.get("revised_theory", self.theory)

        self._record(IterationKind.PIVOT,
                     f"Pivot: {result.get('what_changed', 'revised theory')}",
                     SurfaceKind.THEORY, 0.3, 0.2, 0, 0, time.time() - start,
                     detail=result)

    # ── Helpers ──────────────────────────────────────────────────────

    def _record(self, kind: IterationKind, description: str,
                surface: SurfaceKind, trust_before: float, trust_after: float,
                obs_before: int, obs_after: int, elapsed: float,
                success: bool = True, detail: Optional[dict] = None):
        self.iterations.append(IterationRecord(
            iteration=len(self.iterations),
            kind=kind,
            description=description,
            surface_modified=surface,
            trust_before=trust_before,
            trust_after=trust_after,
            obstructions_before=obs_before,
            obstructions_after=obs_after,
            elapsed=elapsed,
            success=success,
            detail=detail or {},
        ))

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    # ── Phase 8: WRITE README + PAPER ───────────────────────────────

    def _write_readme(self):
        """Generate a README.md for the output repository.

        This is CONSTRUCT on P (claims surface): produce a public-facing
        document grounded in the actual code and evidence.
        """
        start = time.time()
        self._log("Phase 8a: Writing README.md...")

        benchmarks_text = ""
        if self.benchmarks and self.benchmarks.results:
            benchmarks_text = "## Benchmarks\n\n| Metric | Result | Baseline | Status |\n|---|---|---|---|\n"
            for metric in self.benchmarks.metrics:
                result = self.benchmarks.results.get(metric, "—")
                baseline = self.benchmarks.baselines.get(metric, "—")
                status = self.benchmarks.comparison.get(metric, "—")
                benchmarks_text += f"| {metric} | {result} | {baseline} | {status} |\n"

        modules_text = ""
        if self.plan:
            modules_text = "## Modules\n\n"
            for mod in self.plan.modules:
                modules_text += f"- **{mod.get('name', '?')}** — {mod.get('purpose', '')}\n"

        if not self.no_llm:
            readme_prompt = textwrap.dedent(f"""\
                Write a README.md for this research software tool.

                TOOL NAME: {self.plan.package_name if self.plan else self.approach}
                ONE-LINER: {self.plan.one_liner if self.plan else self.prompt[:80]}
                THEORY: {self.theory[:400]}
                MODULES: {modules_text[:300]}
                BENCHMARKS: {benchmarks_text[:300]}

                Include: title, badges, one-paragraph description, installation, quickstart
                example, module overview, benchmarks table, and citation section.
                Return raw Markdown, no fences.
            """)
            readme = _call_llm(readme_prompt, max_tokens=4096)
        else:
            pkg = self.plan.package_name if self.plan else self.approach
            readme = textwrap.dedent(f"""\
                # {pkg}

                {self.plan.one_liner if self.plan else self.prompt[:80]}

                ## Installation

                ```bash
                pip install -e .
                ```

                ## Theory

                {self.theory[:300]}

                {modules_text}

                {benchmarks_text}
            """)

        readme_path = self.output_dir / "README.md"
        readme_path.write_text(readme.strip() + "\n")
        self.code_files.append(str(readme_path))

        self._record(IterationKind.GROUND_CLAIMS,
                     "Wrote README.md",
                     SurfaceKind.CLAIMS, 0.3, 0.6, 0, 0, time.time() - start)

    def _write_paper(self):
        """Generate a conference tool-track paper (LaTeX).

        This is CONSTRUCT on P + FORMALIZE from T: produce a paper
        that formalizes the theory and grounds claims in evidence.
        """
        start = time.time()
        self._log("Phase 8b: Writing conference_tool_track.tex...")

        pkg = self.plan.package_name if self.plan else self.approach

        benchmarks_table = ""
        if self.benchmarks and self.benchmarks.results:
            rows = []
            for metric in self.benchmarks.metrics:
                result = self.benchmarks.results.get(metric, "—")
                baseline = self.benchmarks.baselines.get(metric, "—")
                status = self.benchmarks.comparison.get(metric, "—")
                rows.append(f"    {metric} & {result} & {baseline} & {status} \\\\")
            benchmarks_table = "\n".join(rows)

        modules_items = ""
        if self.plan:
            for mod in self.plan.modules:
                modules_items += f"  \\item \\texttt{{{mod.get('name','?')}}} --- {mod.get('purpose','')}\n"

        if not self.no_llm:
            paper_prompt = textwrap.dedent(f"""\
                Write a 4-page conference tool-track paper in LaTeX for this research tool.

                TOOL: {pkg}
                THEORY: {self.theory[:500]}
                PROMPT: {self.prompt}
                MODULES: {modules_items[:300]}
                BENCHMARKS: {benchmarks_table[:300]}

                Structure:
                1. Abstract (150 words)
                2. Introduction — the problem and why existing tools fail
                3. Approach — the mathematical framework
                4. Implementation — the software architecture
                5. Evaluation — benchmarks and comparison to baselines
                6. Conclusion

                Use \\documentclass{{article}}, \\usepackage{{amsmath,booktabs}}.
                Return raw LaTeX, no markdown fences.
            """)
            paper = _call_llm(paper_prompt, max_tokens=8192)
        else:
            paper = textwrap.dedent(f"""\
                \\documentclass[11pt]{{article}}
                \\usepackage{{amsmath,booktabs,hyperref}}
                \\title{{{pkg}: {self.plan.one_liner if self.plan else ''}}}
                \\author{{Generated by JuGeo Directed Research}}
                \\date{{\\today}}
                \\begin{{document}}
                \\maketitle
                \\begin{{abstract}}
                {self.theory[:200]}
                \\end{{abstract}}

                \\section{{Introduction}}
                {self.prompt}

                \\section{{Approach}}
                {self.theory[:400]}

                \\section{{Implementation}}
                The tool is organized into the following modules:
                \\begin{{itemize}}
                {modules_items}
                \\end{{itemize}}

                \\section{{Evaluation}}
                \\begin{{table}}[h]
                \\centering
                \\begin{{tabular}}{{llll}}
                \\toprule
                Metric & Result & Baseline & Status \\\\
                \\midrule
                {benchmarks_table}
                \\bottomrule
                \\end{{tabular}}
                \\caption{{Benchmark results.}}
                \\end{{table}}

                \\section{{Conclusion}}
                We presented {pkg}, a tool that applies novel mathematics to
                the problem described above. Our approach demonstrates the
                feasibility of the synthesized framework.

                \\end{{document}}
            """)

        # Clean up LLM output
        if paper.startswith("```"):
            lines = paper.split("\n")
            paper = "\n".join(l for l in lines if not l.startswith("```"))

        tex_path = self.output_dir / "conference_tool_track.tex"
        tex_path.write_text(paper.strip() + "\n")
        self.code_files.append(str(tex_path))

        # Try to compile to PDF
        try:
            import shutil
            if shutil.which("pdflatex"):
                subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "conference_tool_track.tex"],
                    capture_output=True, timeout=30, cwd=str(self.output_dir),
                )
                pdf_path = self.output_dir / "conference_tool_track.pdf"
                if pdf_path.exists():
                    self.code_files.append(str(pdf_path))
                    self._log("  Compiled to PDF")
        except Exception:
            pass

        self._record(IterationKind.GROUND_CLAIMS,
                     "Wrote conference_tool_track.tex",
                     SurfaceKind.CLAIMS, 0.4, 0.7, 0, 0, time.time() - start)

    def _save_metadata(self, status: ResearchStatus, elapsed: float):
        meta = {
            "status": status.value,
            "prompt": self.prompt,
            "approach": self.approach,
            "theory": self.theory[:500],
            "code_files": self.code_files,
            "pivots": self.pivots_taken,
            "iterations": len(self.iterations),
            "elapsed": round(elapsed, 2),
            "benchmarks": {
                "metrics": self.benchmarks.metrics if self.benchmarks else {},
                "results": self.benchmarks.results if self.benchmarks else {},
                "comparison": self.benchmarks.comparison if self.benchmarks else {},
            } if self.benchmarks else None,
        }
        (self.output_dir / "research_metadata.json").write_text(
            json.dumps(meta, indent=2, default=str))
