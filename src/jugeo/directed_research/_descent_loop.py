"""The descent-driven research loop — the heart of directed research.

This module implements the main ``DirectedResearch`` class whose ``.run()``
method executes the full pipeline:

    Ideate → Generate → Benchmark → Compare → Refine/Pivot → Paper → README

The entire loop is formalized as **sheaf descent** over the workspace site.
The workspace has four surfaces (Theory, Code, Evidence, Claims) connected
by six morphisms. At each iteration:

    1. Run descent on the workspace → obstructions tell you what's wrong
    2. Check convergence criterion → are we BOTH consistent AND SOTA?
    3. If converged → write deliverables (paper, README), verify them, done
    4. If not → select repair move based on obstruction type, execute it

The convergence criterion (from _benchmarking.ConvergenceCriterion) requires
ALL of:
    - workspace_consistent: H^1 = 0 (all surfaces agree on overlaps)
    - frontier_dominated: metrics dominate SOTA baselines (Pareto criterion)
    - code_verified: all code passes jg prove / jg bugs
    - claims_grounded: all claims match actual evidence (no hallucination)
    - minimum_trust_met: all surfaces have trust >= floor

This is what it means for a claim to be "useful AND true" in judgment-geometry
terms. A claim is TRUE when it passes descent (no obstructions, trust >= SOLVER).
A claim is USEFUL when its evidence dominates the SOTA frontier. The conjunction
of these two conditions — Theorem 9.1 from concept-ideation.html — is exactly
the convergence criterion.

The loop formally encodes "try things until you reach SOTA":

    frontier = establish_baselines(domain)
    while not converged:
        evidence = run_benchmarks(code)
        if dominates(evidence, frontier):
            if workspace_consistent:
                CONVERGED — useful AND true
            else:
                repair obstructions → iterate
        else:
            gap = frontier.weakest_metric()
            if can_refine(gap):
                refine code/theory to close the gap → iterate
            elif pivots_remaining:
                pivot theory (adjacency-constrained change) → restart generate
            else:
                PARTIAL — true but not useful enough

Usage::

    from jugeo.directed_research import DirectedResearch

    dr = DirectedResearch("Build a killer app in finance using advanced math")
    result = dr.run()
    print(result.status)  # CONVERGED, BUDGET_EXHAUSTED, etc.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import textwrap
import time
from typing import Any, Optional

from jugeo.research_orchestration import (
    WorkspaceSite,
    SurfaceKind,
    ConsistencyReport,
    ObstructionKind,
)

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    TRUST_RUNTIME,
    TRUST_SOLVER,
    LLMSection,
    MoveKind,
    MoveResult,
    ResearchStatus,
    DirectedResearchResult,
    IdeationResult,
    AgentBackend,
    HAS_EASY,
    HAS_GEOMETRY,
)
from jugeo.directed_research._agent_channel import agent_call, agent_json
from jugeo.directed_research._trust_algebra import TrustManager
from jugeo.directed_research._ideation import run_ideation
from jugeo.directed_research._domain_site import decompose_domain
from jugeo.directed_research._code_gen import (
    design_architecture,
    generate_module,
    generate_tests,
    write_pyproject,
    syntax_check_all,
    import_check,
)
from jugeo.directed_research._benchmarking import (
    MetricFrontier,
    ConvergenceCriterion,
    establish_baselines,
    run_benchmarks,
    update_frontier_with_results,
)
from jugeo.directed_research._verification import (
    verify_all_code_files,
    verify_readme,
    verify_paper,
)
from jugeo.directed_research._paper_gen import generate_paper
from jugeo.directed_research._readme_gen import generate_readme
from jugeo.directed_research._pivot import pivot_theory
from jugeo.directed_research._workspace import build_workspace
from jugeo.directed_research._provenance import ResearchProvenance

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


class DirectedResearch:
    """The research loop IS descent.

    Every iteration:
    1. Run descent on the workspace site
    2. Check convergence: consistent AND competitive AND verified
    3. If converged → write deliverables, verify, done
    4. If obstructions → the obstruction type determines the next move
    5. If consistent but not competitive → refine or pivot
    6. Execute the move (which dispatches to a coding agent)
    7. Install the resulting section in the workspace
    8. Go to 1

    The convergence criterion formalizes "useful AND true":
    - TRUE: H^1 = 0, jg prove passes, claims match evidence
    - USEFUL: metrics dominate SOTA frontier on at least one axis
    """

    def __init__(
        self,
        prompt: str,
        *,
        max_iterations: int = 30,
        max_pivots: int = 3,
        output_dir: str | None = None,
        no_llm: bool = False,
        seed: int | None = None,
        verbose: bool = False,
        agent_backend: AgentBackend | None = None,
        trust_floor: float = 0.5,
    ):
        self.prompt = prompt
        self.max_iterations = max_iterations
        self.max_pivots = max_pivots
        self.no_llm = no_llm
        self.seed = seed
        self.verbose = verbose
        self.agent_backend = agent_backend
        self.trust_floor = trust_floor

        default_dir = pathlib.Path(_ROOT) / "outputs" / f"research_{time.strftime('%Y%m%d_%H%M%S')}"
        self.output_dir = pathlib.Path(output_dir or str(default_dir)).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Core state
        self.workspace: Optional[WorkspaceSite] = None
        self.trust_manager = TrustManager()
        self.provenance = ResearchProvenance(prompt=prompt)

        # Accumulated sections and moves
        self.sections: list[LLMSection] = []
        self.moves: list[MoveResult] = []
        self.code_files: list[str] = []
        self.pivots: int = 0

        # State built up across phases
        self.ideation_result: Optional[IdeationResult] = None
        self.domain_analysis: dict = {}
        self.theory_text: str = ""
        self.approach: str = ""
        self.architecture: dict = {}
        self.module_code: dict[str, str] = {}
        self.benchmark_results: dict[str, Any] = {}
        self.frontier: Optional[MetricFrontier] = None

    def _log(self, msg: str):
        if self.verbose:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {msg}", flush=True)

    # ══════════════════════════════════════════════════════════════════
    #  THE MAIN LOOP
    # ══════════════════════════════════════════════════════════════════

    def run(self) -> DirectedResearchResult:
        """Execute the full directed research pipeline.

        Phase 1 — IDEATION: Cross-domain synthesis via geometry of ideation
        Phase 2 — SEED: Domain analysis + theory elaboration
        Phase 3 — GENERATE: Architecture design + module generation
        Phase 4 — HARDEN: Descent loop (benchmark, verify, refine/pivot)
        Phase 5 — TAIL: Paper + README + verification

        The harden phase is the heart: it runs until convergence or budget
        exhaustion, with the convergence criterion requiring BOTH consistency
        (true) AND SOTA domination (useful).
        """
        start = time.time()

        # ── Phase 1: IDEATION ─────────────────────────────────────────
        self._log("═══ PHASE 1: IDEATION ═══")
        self._phase_ideation()

        # ── Phase 2: SEED ─────────────────────────────────────────────
        self._log("═══ PHASE 2: SEED ═══")
        self._phase_seed()

        # ── Phase 3: GENERATE ─────────────────────────────────────────
        self._log("═══ PHASE 3: GENERATE ═══")
        self._phase_generate()

        # ── Phase 4: HARDEN (the descent loop) ────────────────────────
        self._log("═══ PHASE 4: HARDEN ═══")
        status = self._phase_harden()

        # ── Phase 5: TAIL ─────────────────────────────────────────────
        self._log("═══ PHASE 5: TAIL ═══")
        readme_report, paper_report = self._phase_tail()

        # ── Build final result ────────────────────────────────────────
        self._rebuild_workspace()
        final_report = self.workspace.check_consistency() if self.workspace else None

        elapsed = time.time() - start
        self._save_metadata(status, elapsed)
        self.provenance.save(str(self.output_dir))

        return DirectedResearchResult(
            status=status,
            prompt=self.prompt,
            theory_summary=self.theory_text[:500],
            approach=self.approach,
            code_files=self.code_files,
            sections=self.sections,
            consistency=final_report,
            moves=self.moves,
            pivots=self.pivots,
            output_dir=str(self.output_dir),
            elapsed=elapsed,
            ideation=self.ideation_result,
            readme_verification=readme_report,
            paper_verification=paper_report,
            benchmark_results=self.benchmark_results,
        )

    # ══════════════════════════════════════════════════════════════════
    #  Phase 1: IDEATION — cross-domain synthesis
    # ══════════════════════════════════════════════════════════════════

    def _phase_ideation(self):
        """Run the geometry-of-ideation pipeline (§9).

        Uses the domain site / solution presheaf / demand sheaf machinery
        to find a novel, useful approach via cross-domain synthesis.
        """
        if self.no_llm:
            self.approach = "heuristic"
            return

        self._log("  Running cross-domain ideation...")
        self.ideation_result = run_ideation(
            self.prompt,
            n_partner_candidates=3,
            n_propositions=5,
            verbose=self.verbose,
        )

        if self.ideation_result.selected_approach:
            bp = self.ideation_result.selected_approach
            self._log(f"  Selected approach: {bp.title} (UNS={bp.useful_novelty_score:.2f})")
            self.approach = bp.title.lower().replace(" ", "-")
        else:
            self.approach = "direct-approach"

        # Record ideation as a theory section
        ideation_section = LLMSection(
            surface=SurfaceKind.THEORY,
            coordinate="theory.ideation",
            content=json.dumps({
                "approach": self.approach,
                "bridge_propositions": [
                    {"title": bp.title, "uns": bp.useful_novelty_score,
                     "novelty": bp.novelty_score, "relevance": bp.relevance_score}
                    for bp in self.ideation_result.bridge_propositions
                ],
            }),
            trust=TRUST_COPILOT,
            provenance="ideation-pipeline",
        )
        self._record_section(ideation_section)

    # ══════════════════════════════════════════════════════════════════
    #  Phase 2: SEED — domain analysis + theory elaboration
    # ══════════════════════════════════════════════════════════════════

    def _phase_seed(self):
        """Produce initial sections on Theory surface."""
        self._move_analyze_domain()
        self._move_elaborate_theory()
        self._move_design_architecture()

    def _move_analyze_domain(self):
        """Understand the domain and identify the best approach."""
        self._log("  MOVE: analyze_domain → T")
        if self.no_llm:
            self.domain_analysis = {
                "domain_analysis": self.prompt,
                "best_math_approach": {"name": self.approach},
                "standard_libraries": ["numpy", "pandas"],
                "evaluation_metrics": [],
                "baselines_to_beat": [],
            }
            return

        # Use the ideation result if available
        ideation_context = ""
        if self.ideation_result and self.ideation_result.selected_approach:
            bp = self.ideation_result.selected_approach
            ideation_context = f"""
IDEATION RESULT: The cross-domain synthesis engine selected this approach:
  Title: {bp.title}
  Description: {bp.description}
  Source domain: {bp.source_domain}
  Partner domain: {bp.target_domain}
  Novelty score: {bp.novelty_score:.2f}
  Relevance score: {bp.relevance_score:.2f}

Use this as the foundation for the domain analysis.
"""

        data, section = agent_json(
            textwrap.dedent(f"""\
                Analyze this product request and determine the BEST mathematical approach:

                "{self.prompt}"
                {ideation_context}

                Think about what ACTUALLY works. What specific computational problems
                need solving? What are the 3 best existing tools? Where do they fail?
                What math lets us do something they CAN'T?

                Identify: standard Python libraries, data formats/APIs, datasets,
                and evaluation metrics practitioners actually use.

                Respond as JSON:
                {{
                    "domain_analysis": "3-paragraph analysis",
                    "computational_problems": ["problem1", ...],
                    "existing_tools": [{{"name": "...", "weakness": "..."}}],
                    "best_math_approach": {{
                        "name": "Name", "field_a": "...", "field_b": "...",
                        "why": "...", "key_theorems": [...], "key_algorithms": [...]
                    }},
                    "standard_libraries": ["numpy", "pandas", ...],
                    "standard_datasets": ["..."],
                    "evaluation_metrics": ["metric1", ...],
                    "baselines_to_beat": [{{"name": "...", "metric": "...", "value": "..."}}]
                }}
            """),
            surface=SurfaceKind.THEORY,
            coordinate="theory.domain_analysis",
        )
        self.domain_analysis = data
        approach_name = data.get("best_math_approach", {}).get("name", self.approach)
        if approach_name and approach_name != self.approach:
            self.approach = approach_name.lower().replace(" ", "-")
        self._record_section(section)

    def _move_elaborate_theory(self):
        """Produce a deep mathematical framework document."""
        self._log("  MOVE: elaborate_theory → T")
        if self.no_llm:
            self.theory_text = f"Mathematical framework for: {self.prompt}"
            return

        ma = self.domain_analysis.get("best_math_approach", {})
        context_path = str(self.output_dir / "context.md")
        section = agent_call(
            textwrap.dedent(f"""\
                Write a COMPREHENSIVE mathematical framework document (5000+ words)
                to the file {context_path}.

                PRODUCT: {self.prompt}
                APPROACH: {ma.get('name', self.approach)}
                PRIMARY FIELD: {ma.get('field_a', '')}
                SECONDARY FIELD: {ma.get('field_b', '')}
                KEY THEOREMS: {json.dumps(ma.get('key_theorems', []))}

                Structure: (1) Introduction (500+ words), (2) Mathematical Foundations
                (1500+ words), (3) Computational Framework (1000+ words),
                (4) Application with real data (1000+ words),
                (5) Evaluation Strategy — metrics, baselines (500+ words),
                (6) Key Propositions — 5-10 formal propositions (500+ words).

                Use LaTeX notation. Be specific about algorithms.
                Write the complete document to {context_path} using your file-write tool.
            """),
            surface=SurfaceKind.THEORY,
            coordinate="theory.foundations",
            working_dir=str(self.output_dir),
        )
        # Read back what the agent wrote
        if os.path.exists(context_path) and os.path.getsize(context_path) > 200:
            self.theory_text = open(context_path).read()
        else:
            self.theory_text = section.content
            (self.output_dir / "context.md").write_text(
                f"# {self.approach}\n\n{section.content}\n")
        self._record_section(section)

    def _move_design_architecture(self):
        """Decompose into a covering family of modules."""
        self._log("  MOVE: design_architecture → R")
        if self.no_llm:
            self.architecture = {
                "package_name": self.approach.replace("-", "_"),
                "modules": [
                    {"name": "core", "purpose": "Core types"},
                    {"name": "algorithms", "purpose": "Algorithms"},
                    {"name": "cli", "purpose": "CLI"},
                ],
            }
            return

        data, section = design_architecture(
            self.prompt, self.approach, self.theory_text, self.domain_analysis)
        self.architecture = data
        if "package_name" not in self.architecture:
            self.architecture["package_name"] = self.approach.replace("-", "_")
        self._record_section(section)

    # ══════════════════════════════════════════════════════════════════
    #  Phase 3: GENERATE — code generation
    # ══════════════════════════════════════════════════════════════════

    def _phase_generate(self):
        """Generate all code modules."""
        modules = self.architecture.get("modules", [])
        if not modules:
            modules = [
                {"name": "core", "purpose": "Core types and data structures"},
                {"name": "algorithms", "purpose": "Main algorithms"},
                {"name": "cli", "purpose": "Command-line interface"},
            ]
        for mod in modules:
            self._move_generate_module(mod)

        # Integration module
        self._move_generate_integration()

        # Write pyproject.toml
        pkg = self.architecture.get("package_name", self.approach.replace("-", "_"))
        deps = self.domain_analysis.get("standard_libraries", ["numpy", "pandas"])
        path = write_pyproject(str(self.output_dir), pkg, self.approach, deps)
        self.code_files.append(path)

        # Generate tests
        self._move_generate_tests()

    def _move_generate_module(self, mod: dict):
        """Generate one module."""
        name = mod.get("name", "module")
        self._log(f"  MOVE: generate_module({name}) → R")
        if self.no_llm:
            pkg = self.architecture.get("package_name", "output")
            pkg_clean = re.sub(r'[^a-zA-Z0-9_]', '_', pkg).strip('_').lower() or "output"
            pkg_dir = self.output_dir / "src" / pkg_clean
            pkg_dir.mkdir(parents=True, exist_ok=True)
            path = pkg_dir / f"{name}.py"
            path.write_text(f'"""{mod.get("purpose", "")}"""\n\ndef main():\n    pass\n')
            self.code_files.append(str(path))
            self.module_code[name] = path.read_text()
            return

        path, section = generate_module(
            mod,
            pkg_name=self.architecture.get("package_name", self.approach),
            output_dir=str(self.output_dir),
            prompt=self.prompt,
            approach=self.approach,
            theory_text=self.theory_text,
            domain_analysis=self.domain_analysis,
            existing_modules=self.module_code,
        )
        self.code_files.append(path)
        with open(path) as f:
            self.module_code[mod.get("name", "module")] = f.read()
        self._record_section(section)
        self._log(f"    Generated {name}.py: {section.token_count} tokens")

    def _move_generate_integration(self):
        """Generate integration module."""
        self._log("  MOVE: generate_integration → R")
        if self.no_llm:
            return

        pkg = self.architecture.get("package_name", self.approach)
        pkg_clean = re.sub(r'[^a-zA-Z0-9_]', '_', pkg).strip('_').lower() or "output"
        pkg_dir = self.output_dir / "src" / pkg_clean
        pkg_dir.mkdir(parents=True, exist_ok=True)
        path = pkg_dir / "integration.py"

        section = agent_call(
            f"Write an integration module to the file {path}\n\n"
            f"Package: {pkg_clean}\n"
            f"Connect to {json.dumps(self.domain_analysis.get('standard_libraries', []))} "
            f"and {json.dumps(self.domain_analysis.get('standard_datasets', []))}.\n"
            f"Existing modules: {list(self.module_code.keys())}.\n"
            f"Write 500+ lines of real, working Python code to {path} "
            f"using your file-write tool.",
            surface=SurfaceKind.CODE,
            coordinate="code.integration",
            working_dir=str(self.output_dir),
        )

        # Read back what the agent wrote
        if path.exists() and path.stat().st_size > 50:
            code = path.read_text()
        else:
            code = section.content
            if code.startswith("```"):
                code = "\n".join(l for l in code.split("\n") if not l.startswith("```"))
            path.write_text(code.strip() + "\n")

        self.code_files.append(str(path))
        self.module_code["integration"] = code
        self._record_section(section)

    def _move_generate_tests(self):
        """Generate test suite."""
        self._log("  MOVE: generate_tests → E")
        if self.no_llm:
            return

        pkg = self.architecture.get("package_name", self.approach)
        modules = self.architecture.get("modules", [])
        path, section = generate_tests(
            pkg, str(self.output_dir), modules)
        self.code_files.append(path)
        self._record_section(section)

    # ══════════════════════════════════════════════════════════════════
    #  Phase 4: HARDEN — the descent loop
    # ══════════════════════════════════════════════════════════════════

    def _install_deps(self):
        """Install the project's dependencies so benchmarks can run."""
        self._log("  Installing dependencies...")
        import subprocess as _sp
        pkg = self.architecture.get("package_name", "")
        deps = self.domain_analysis.get("standard_libraries", [])
        # pip install deps (best-effort, ignore failures on optional ones)
        for dep in deps:
            try:
                _sp.run([sys.executable, "-m", "pip", "install", dep, "-q"],
                       capture_output=True, timeout=120)
            except Exception:
                pass
        # pip install the project itself in editable mode
        pyproject = self.output_dir / "pyproject.toml"
        if pyproject.exists():
            try:
                _sp.run([sys.executable, "-m", "pip", "install", "-e",
                        str(self.output_dir), "-q"],
                       capture_output=True, timeout=120)
            except Exception:
                pass

    def _phase_harden(self) -> ResearchStatus:
        """The descent-driven hardening loop.

        Restructured to fix the spinning problem:
        1. Install deps FIRST so benchmarks can actually run
        2. Write paper/README EARLY so quality site can check them
        3. Each iteration inspects the quality site obstructions and
           dispatches the SPECIFIC repair that addresses the worst one
        4. Only re-run jg on the first and last iterations (expensive)

        The loop terminates when:
        - CONVERGED: all quality dimensions satisfied
        - BUDGET_EXHAUSTED: max_iterations hit
        - PIVOT_LIMIT: too many pivots without progress
        """
        pkg = self.architecture.get("package_name", "")

        # ── Step 0: install dependencies ──────────────────────────────
        self._install_deps()

        # ── Step 1: establish baselines ───────────────────────────────
        self._log("  Establishing SOTA baselines...")
        self.frontier = establish_baselines(self.domain_analysis, self.approach)

        # ── Step 2: initial syntax/import check ───────────────────────
        syntax_results = syntax_check_all(self.code_files)
        syntax_ok = all(syntax_results.values()) if syntax_results else False
        import_ok, _ = import_check(str(self.output_dir), pkg)
        self._log(f"  Syntax: {sum(syntax_results.values())}/{len(syntax_results)} OK, "
                  f"Import: {'OK' if import_ok else 'FAIL'}")

        # ── Step 3: initial benchmarks ────────────────────────────────
        self._log("  Running initial benchmarks...")
        self.benchmark_results = run_benchmarks(
            str(self.output_dir), pkg,
            self.domain_analysis.get("evaluation_metrics", []))
        if self.frontier:
            self.frontier = update_frontier_with_results(
                self.frontier, self.benchmark_results)

        # ── Step 4: write paper + README early so they can be checked ─
        self._log("  Writing initial paper + README (for quality site)...")
        self._write_deliverables()

        # ── Step 5: initial jg verification (once, it's expensive) ────
        self._log("  Running jg verification...")
        jg_results = verify_all_code_files(self.code_files, verbose=self.verbose)

        # ── Step 6: the descent loop ──────────────────────────────────
        for iteration in range(self.max_iterations):
            self._log(f"  ── Iteration {iteration} ──")

            # Build quality site and run descent
            self._rebuild_workspace()
            report = self.workspace.check_consistency() if self.workspace else None

            criterion = ConvergenceCriterion()
            criterion.set_workspace_consistency(
                report.consistent if report else False,
                report.H1 if report else "no workspace")
            criterion.set_sota_domination(self.frontier)
            criterion.set_code_correctness(
                jg_results=jg_results, syntax_ok=syntax_ok, import_ok=import_ok)
            criterion.set_claims_grounding(report.consistent if report else False)
            criterion.set_paper_completeness(
                str(self.output_dir / "conference_tool_track.tex"))
            criterion.set_code_scale(
                self.benchmark_results.get("total_lines", 0), target=5000)
            criterion.set_test_coverage(
                tests_exist=os.path.exists(str(self.output_dir / "tests")),
                tests_pass=self.benchmark_results.get("tests_passed", False))
            criterion.set_theory_code_alignment(
                report.consistent if report else False)
            criterion.set_reproducibility(0.5)  # conservative until README verified

            self._log(f"  {criterion.diagnosis()}")

            # CONVERGED?
            if criterion.converged:
                self._log("  ✓ CONVERGED — useful AND true")
                return ResearchStatus.CONVERGED

            # Find the worst obstruction and dispatch a targeted repair
            repaired = self._repair_quality_obstruction(criterion, report)
            if not repaired:
                # No repairable obstruction — try pivot
                if self.pivots < self.max_pivots:
                    self._execute_pivot()
                else:
                    self._log("  No repairs possible, pivot limit reached")
                    break

            # After repair, re-check syntax/import (cheap)
            syntax_results = syntax_check_all(self.code_files)
            syntax_ok = all(syntax_results.values()) if syntax_results else False
            import_ok, _ = import_check(str(self.output_dir), pkg)

            # Re-run benchmarks (cheap)
            self.benchmark_results = run_benchmarks(
                str(self.output_dir), pkg,
                self.domain_analysis.get("evaluation_metrics", []))
            if self.frontier:
                self.frontier = update_frontier_with_results(
                    self.frontier, self.benchmark_results)

        # Final jg pass
        self._log("  Final jg verification...")
        verify_all_code_files(self.code_files, verbose=self.verbose)

        return ResearchStatus.BUDGET_EXHAUSTED

    def _write_deliverables(self):
        """Write paper + README (called early in harden, then again in tail)."""
        if self.no_llm:
            return
        try:
            readme_path, readme_section = generate_readme(
                approach=self.approach, prompt=self.prompt,
                theory_text=self.theory_text, module_code=self.module_code,
                domain_analysis=self.domain_analysis,
                benchmark_results=self.benchmark_results,
                output_dir=str(self.output_dir))
            if readme_section:
                self._record_section(readme_section)
            if readme_path not in self.code_files:
                self.code_files.append(readme_path)
        except Exception as e:
            self._log(f"  README generation failed: {e}")

        try:
            paper_path, paper_section = generate_paper(
                approach=self.approach, prompt=self.prompt,
                theory_text=self.theory_text, module_code=self.module_code,
                domain_analysis=self.domain_analysis,
                benchmark_results=self.benchmark_results,
                ideation_metadata=self.ideation_result.metadata if self.ideation_result else {},
                output_dir=str(self.output_dir),
                sections=self.sections, code_files=self.code_files)
            if paper_section:
                self._record_section(paper_section)
            if paper_path not in self.code_files:
                self.code_files.append(paper_path)
        except Exception as e:
            self._log(f"  Paper generation failed: {e}")

    def _repair_quality_obstruction(
        self, criterion: ConvergenceCriterion, report
    ) -> bool:
        """Dispatch a targeted repair for the worst quality obstruction.

        Inspects the quality site and fixes the obstruction with the
        highest impact (lowest trust relative to its floor).
        """
        # Find the worst-obstructed quality dimension
        worst_coord = None
        worst_gap = 0.0
        for coord, section in criterion.sections.items():
            if not section.satisfied:
                gap = section.trust_floor - section.trust
                if gap > worst_gap:
                    worst_gap = gap
                    worst_coord = coord

        if worst_coord is None:
            return False

        self._log(f"  REPAIR: {worst_coord} (gap={worst_gap:.2f})")

        if worst_coord == "workspace_consistency":
            # Fix workspace H^1 obstructions
            if report and report.obstructions:
                return self._repair_from_obstructions(report)
            return False

        elif worst_coord == "sota_domination":
            # Need to actually run benchmarks against real baselines
            # Ask agent to create a benchmark runner that uses real data
            self._log("  → Creating benchmark runner with real data...")
            self._create_real_data_benchmark()
            return True

        elif worst_coord == "code_correctness":
            # Fix code bugs — ask agent to fix the worst file
            self._log("  → Fixing code issues...")
            if self.architecture.get("modules"):
                self._move_generate_module(self.architecture["modules"][0])
            return True

        elif worst_coord == "claims_grounding":
            # Sync claims with evidence
            self._ground_claims()
            return True

        elif worst_coord == "paper_completeness":
            # Regenerate paper
            self._log("  → Regenerating paper...")
            self._write_deliverables()
            return True

        elif worst_coord == "paper_honesty":
            # Regenerate paper with updated evidence
            self._log("  → Regenerating honest paper...")
            self._write_deliverables()
            return True

        elif worst_coord == "test_coverage":
            # Run tests, fix failures
            self._log("  → Fixing tests...")
            self._move_generate_tests()
            return True

        elif worst_coord == "reproducibility":
            # Regenerate README with working examples
            self._log("  → Regenerating README...")
            self._write_deliverables()
            return True

        elif worst_coord == "theory_code_alignment":
            # Code doesn't match theory — regenerate weakest module
            self._log("  → Realigning code with theory...")
            if self.architecture.get("modules"):
                self._move_generate_module(self.architecture["modules"][0])
            return True

        elif worst_coord == "data_provenance":
            # Need real data — create real data fetcher
            self._log("  → Creating real data pipeline...")
            self._create_real_data_benchmark()
            return True

        return False

    def _create_real_data_benchmark(self):
        """Ask agent to create a benchmark that fetches + uses real data."""
        if self.no_llm:
            return
        pkg = self.architecture.get("package_name", self.approach)
        pkg_clean = re.sub(r'[^a-zA-Z0-9_]', '_', pkg).strip('_').lower() or "output"
        bench_path = self.output_dir / "src" / pkg_clean / "run_real_benchmark.py"

        section = agent_call(
            f"Write a benchmark script to {bench_path} that:\n\n"
            f"1. Downloads REAL market data using yfinance (Yahoo Finance API)\n"
            f"   - Fetch S&P 500 components or major indices\n"
            f"   - At least 2 years of daily data\n"
            f"2. Runs the {pkg_clean} package on this real data\n"
            f"3. Computes standard evaluation metrics\n"
            f"4. Prints results as JSON to stdout\n"
            f"5. Compares against simple baselines (equal weight, market cap, etc.)\n\n"
            f"The script must use REAL data from yfinance, NOT synthetic data.\n"
            f"Install yfinance if needed: pip install yfinance\n\n"
            f"Package location: {self.output_dir / 'src'}\n"
            f"Available modules: {list(self.module_code.keys())}\n\n"
            f"Write the complete script to {bench_path} using your file-write tool.",
            surface=SurfaceKind.EVIDENCE,
            coordinate="evidence.real_benchmark",
            working_dir=str(self.output_dir),
        )
        self._record_section(section)

        # Try running it
        if bench_path.exists():
            self._log("  → Running real data benchmark...")
            import subprocess as _sp
            try:
                _sp.run([sys.executable, "-m", "pip", "install", "yfinance", "-q"],
                       capture_output=True, timeout=60)
            except Exception:
                pass
            try:
                r = _sp.run(
                    [sys.executable, str(bench_path)],
                    capture_output=True, text=True, timeout=300,
                    cwd=str(self.output_dir),
                    env={**os.environ, "PYTHONPATH": str(self.output_dir / "src")})
                if r.returncode == 0 and r.stdout.strip():
                    try:
                        results = json.loads(r.stdout)
                        self.benchmark_results.update(results)
                        self._log(f"  → Real benchmark results: {list(results.keys())}")
                    except json.JSONDecodeError:
                        self.benchmark_results["real_benchmark_output"] = r.stdout[:500]
                else:
                    self._log(f"  → Benchmark failed: {r.stderr[:200]}")
            except Exception as e:
                self._log(f"  → Benchmark error: {e}")

    def _refine_for_metric(self, metric_name: str):
        """Refine the code to improve a specific metric."""
        self._log(f"  REFINE: improving {metric_name}")
        if self.no_llm:
            return

        gap = self.frontier.gap_analysis().get(metric_name, {}) if self.frontier else {}
        section = agent_call(
            f"The metric '{metric_name}' is below the SOTA baseline. "
            f"Current: {gap.get('our_value', 'unknown')}, "
            f"Baseline: {gap.get('baseline', 'unknown')} ({gap.get('baseline_tool', '')}). "
            f"Gap: {gap.get('gap_pct', 0):.1f}%. "
            f"Analyze the code and suggest specific improvements to close this gap. "
            f"Focus on algorithmic changes, not cosmetic ones. "
            f"Return a JSON plan: {{\"changes\": [{{\"file\": \"...\", \"change\": \"...\"}}]}}",
            surface=SurfaceKind.CODE,
            coordinate=f"code.refine.{metric_name}",
        )
        self._record_section(section)
        # TODO: apply the suggested changes

    def _execute_pivot(self):
        """Execute an adjacency-constrained theory pivot."""
        self.pivots += 1
        self._log(f"  PIVOT #{self.pivots}")
        if self.no_llm:
            self.theory_text += "\n\n[REVISED]"
            return

        obs_descriptions = []
        if self.workspace:
            report = self.workspace.check_consistency()
            obs_descriptions = [o.description for o in report.obstructions[:5]]

        new_theory, section = pivot_theory(
            self.theory_text, self.prompt, obs_descriptions, self.pivots)
        self.theory_text = new_theory
        (self.output_dir / "context.md").write_text(
            f"# {self.approach} (pivot {self.pivots})\n\n{new_theory}\n")
        self._record_section(section)

        # Regenerate code after pivot
        self._log("  Regenerating code after pivot...")
        for mod in self.architecture.get("modules", []):
            self._move_generate_module(mod)

    def _repair_from_obstructions(self, report: ConsistencyReport) -> bool:
        """Use obstructions to determine and execute the right repair move."""
        if not report.obstructions:
            return True
        obs = report.obstructions[0]
        if obs.kind == ObstructionKind.NUMERICAL_MISMATCH:
            self._ground_claims()
            return True
        elif obs.kind == ObstructionKind.STALE_SECTION:
            return True
        elif obs.kind in (ObstructionKind.CODE_THEORY_GAP,
                          ObstructionKind.MISSING_EVIDENCE):
            if self.architecture.get("modules"):
                self._move_generate_module(self.architecture["modules"][0])
                return True
        return False

    def _ground_claims(self):
        """Sync claims with actual evidence."""
        if self.workspace:
            ev = self.workspace.sections.get(SurfaceKind.EVIDENCE)
            cl = self.workspace.sections.get(SurfaceKind.CLAIMS)
            if ev and cl:
                for k, v in ev.claims.items():
                    if not k.startswith("_"):
                        cl.claims[k] = v
                cl.trust = min(1.0, cl.trust + 0.3)

    # ══════════════════════════════════════════════════════════════════
    #  Phase 5: TAIL — deliverables + verification
    # ══════════════════════════════════════════════════════════════════

    def _phase_tail(self):
        """Final pass: rewrite paper/README with latest evidence, then verify."""
        self._ground_claims()

        # Rewrite deliverables with final benchmark data
        self._log("  Rewriting deliverables with final evidence...")
        self._write_deliverables()

        readme_path = str(self.output_dir / "README.md")
        paper_path = str(self.output_dir / "conference_tool_track.tex")

        # Verify README
        self._log("  Verifying README claims with jg...")
        readme_report = verify_readme(
            readme_path, self.benchmark_results, self.code_files,
            verbose=self.verbose)

        # Verify paper
        self._log("  Verifying paper claims with jg...")
        paper_report = verify_paper(
            paper_path, self.benchmark_results,
            verbose=self.verbose)

        self._log(f"  README: {readme_report.verification_rate:.0%} claims verified")
        self._log(f"  Paper: {paper_report.verification_rate:.0%} claims verified")

        return readme_report, paper_report

    # ══════════════════════════════════════════════════════════════════
    #  Internal helpers
    # ══════════════════════════════════════════════════════════════════

    def _record_section(self, section: LLMSection):
        """Record a section and update provenance."""
        self.sections.append(section)
        self.trust_manager.register_section(section)
        self.provenance.record_section(section)

    def _rebuild_workspace(self):
        """Reconstruct the workspace site from all accumulated sections."""
        theory = self.theory_text
        code = "\n\n".join(self.module_code.values())
        evidence = dict(self.benchmark_results)
        for s in self.sections:
            if s.surface == SurfaceKind.EVIDENCE:
                evidence[s.coordinate] = s.trust

        claims_parts = []
        for s in self.sections:
            if s.surface == SurfaceKind.CLAIMS:
                claims_parts.append(s.content[:500])
        claims = "\n".join(claims_parts) if claims_parts else self.approach

        self.workspace = build_workspace(
            theory=theory, code=code, evidence=evidence,
            claims=claims, name=self.approach)

    def _save_metadata(self, status: ResearchStatus, elapsed: float):
        """Save research metadata to disk."""
        meta = {
            "status": status.value,
            "prompt": self.prompt,
            "approach": self.approach,
            "elapsed": round(elapsed, 2),
            "sections": len(self.sections),
            "moves": len(self.moves),
            "pivots": self.pivots,
            "code_files": self.code_files,
            "trust_profile": {s.coordinate: s.trust for s in self.sections},
            "ideation": {
                "partner": self.ideation_result.metadata.get("partner")
                    if self.ideation_result else None,
                "best_uns": self.ideation_result.best_uns
                    if self.ideation_result else None,
            },
            "benchmark_results": self.benchmark_results,
            "frontier": self.frontier.to_dict() if self.frontier else None,
        }
        (self.output_dir / "research_metadata.json").write_text(
            json.dumps(meta, indent=2, default=str))
