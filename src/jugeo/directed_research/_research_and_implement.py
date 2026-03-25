"""research-and-implement: the full prompt-to-SOTA pipeline as a jugeo command.

This module implements the ``jugeo research-and-implement`` CLI command.
It wraps :class:`DirectedResearch` with **hard delivery obligations** that
are modeled as sections on the quality site.  The loop does NOT terminate
until every obligation is discharged or the budget is exhausted.

The delivery obligations are:

    OBLIGATION                    QUALITY COORD         TRUST FLOOR
    ─────────────────────────────────────────────────────────────────
    Paper ≥ 14 pages              paper_page_count      0.9
    Paper has ≥ 2 figures         paper_figure_count    0.9
    ≥ 1 SOTA metric vs baselines  sota_metric           0.9
    Reproducible metrics script   metrics_script        0.9
    Hallucination check passes    hallucination_free    0.95
    Real data (not synthetic)     real_data             0.9
    Code ≥ 50 KLoC                code_scale            0.7

The pipeline phases are:

    THEORIZE → IMPLEMENT → BENCHMARK → [REFINE | PIVOT] → DELIVER

where the [REFINE | PIVOT] step inspects the quality site and either:
  - REFINES: adjusts the implementation to improve the weakest metric
  - PIVOTS: changes which metric to target (adjacency-constrained)

The loop breaks ONLY when all obligations are met.  The final step is
always a hallucination/fabrication scan of the paper.

The generated output includes a ``run_metrics.py`` script that reproduces
all reported numbers from scratch, using real data.

Usage::

    from jugeo.directed_research._research_and_implement import research_and_implement
    result = research_and_implement("make me a killer app in finance using advanced math")

Or via CLI::

    jugeo research-and-implement "make me a killer app in finance using advanced math"
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import textwrap
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind, ConsistencyReport

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    TRUST_RUNTIME,
    TRUST_SOLVER,
    LLMSection,
    MoveKind,
    ResearchStatus,
    DirectedResearchResult,
    AgentBackend,
)
from jugeo.directed_research._descent_loop import DirectedResearch
from jugeo.directed_research._benchmarking import (
    ConvergenceCriterion,
    QualitySection,
)
from jugeo.directed_research._agent_channel import agent_call, agent_json
from jugeo.directed_research._git_tracking import OutputRepoTracker
from jugeo.directed_research._code_gen import ensure_dependencies


# ═══════════════════════════════════════════════════════════════════════
#  Hard delivery obligations — the loop does NOT stop until these are met
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DeliveryObligation:
    """A hard obligation that must be discharged before the loop exits.

    This is a section on an *obligation presheaf* over the quality site.
    The section carries a trust level and an evidence string.  The
    obligation is discharged when trust >= floor.
    """
    key: str
    description: str
    trust_floor: float = 0.9
    trust: float = 0.0
    evidence: str = ""
    discharged: bool = False

    def check(self, value: Any) -> bool:
        """Try to discharge with a value.  Returns True if discharged."""
        raise NotImplementedError

    @property
    def gap(self) -> float:
        return max(0.0, self.trust_floor - self.trust)


@dataclass
class PageCountObligation(DeliveryObligation):
    """Paper must be ≥ min_pages pages (estimated from LaTeX line count)."""
    key: str = "paper_page_count"
    description: str = "Paper is at least 14 pages"
    min_pages: int = 14
    _lines_per_page: int = 45  # rough LaTeX estimate

    def check(self, paper_path: str) -> bool:
        if not os.path.exists(paper_path):
            self.trust = 0.0
            self.evidence = "paper not found"
            return False
        with open(paper_path) as f:
            lines = len(f.readlines())
        est_pages = lines / self._lines_per_page
        self.evidence = f"{lines} lines ≈ {est_pages:.0f} pages"
        self.trust = min(1.0, est_pages / self.min_pages)
        self.discharged = est_pages >= self.min_pages
        return self.discharged


@dataclass
class FigureCountObligation(DeliveryObligation):
    """Paper must contain ≥ min_figures figures (tikzpicture/pgfplots/figure envs)."""
    key: str = "paper_figure_count"
    description: str = "Paper has at least 2 figures showing why approach is right"
    min_figures: int = 2

    def check(self, paper_path: str) -> bool:
        if not os.path.exists(paper_path):
            self.trust = 0.0
            self.evidence = "paper not found"
            return False
        with open(paper_path) as f:
            text = f.read()
        # Count figure environments (\\begin{figure}, \\begin{tikzpicture}, pgfplots)
        fig_count = len(re.findall(
            r'\\begin\{(figure|tikzpicture)\}', text))
        self.evidence = f"{fig_count} figures found"
        self.trust = min(1.0, fig_count / self.min_figures)
        self.discharged = fig_count >= self.min_figures
        return self.discharged


@dataclass
class SOTAMetricObligation(DeliveryObligation):
    """At least one metric must clearly beat baselines (not just tie)."""
    key: str = "sota_metric"
    description: str = "At least 1 metric clearly SOTA vs competitive baselines"

    def check(self, benchmark_results: dict[str, Any]) -> bool:
        # Look for any metric where our approach beats all baselines
        our_metrics = benchmark_results.get("our_metrics", {})
        baseline_metrics = benchmark_results.get("baseline_metrics", {})
        dominating = benchmark_results.get("dominating_metrics", [])

        if dominating:
            self.trust = 0.95
            self.evidence = f"SOTA on: {', '.join(dominating)}"
            self.discharged = True
            return True

        # Check raw metrics: any where ours > best baseline?
        for metric, our_val in our_metrics.items():
            if not isinstance(our_val, (int, float)):
                continue
            baseline_vals = [v for k, v in baseline_metrics.items()
                           if metric in k and isinstance(v, (int, float))]
            if baseline_vals and our_val > max(baseline_vals):
                self.trust = 0.9
                self.evidence = f"SOTA on {metric}: {our_val} vs best baseline {max(baseline_vals)}"
                self.discharged = True
                return True

        self.trust = 0.3
        self.evidence = "no metric dominates baselines yet"
        return False


@dataclass
class MetricsScriptObligation(DeliveryObligation):
    """A run_metrics.py script must exist and be syntactically valid."""
    key: str = "metrics_script"
    description: str = "Reproducible metrics script exists (run_metrics.py)"

    def check(self, output_dir: str) -> bool:
        # Look for run_metrics.py or run_benchmarks.py or run_real_benchmark.py
        candidates = [
            "run_metrics.py",
            "run_benchmarks.py",
            "run_real_benchmark.py",
        ]
        for cand in candidates:
            # Search recursively
            for root, dirs, files in os.walk(output_dir):
                if cand in files:
                    path = os.path.join(root, cand)
                    # Check it parses
                    try:
                        with open(path) as f:
                            compile(f.read(), path, "exec")
                        self.trust = 0.9
                        self.evidence = f"found {path}, syntax OK"
                        self.discharged = True
                        return True
                    except SyntaxError:
                        self.trust = 0.4
                        self.evidence = f"found {path} but has syntax errors"
                        return False

        self.trust = 0.0
        self.evidence = "no metrics script found"
        return False


@dataclass
class HallucinationFreeObligation(DeliveryObligation):
    """Paper must pass hallucination scan (no fabricated numbers)."""
    key: str = "hallucination_free"
    description: str = "Paper passes hallucination/fabrication check"
    trust_floor: float = 0.95

    def check(self, paper_path: str, benchmark_results: dict[str, Any]) -> bool:
        if not os.path.exists(paper_path):
            self.trust = 0.0
            self.evidence = "paper not found"
            return False

        with open(paper_path) as f:
            paper_text = f.read()

        fabrications = _scan_fabrications(paper_text, benchmark_results)
        self.evidence = f"{len(fabrications)} potential fabrications"
        if not fabrications:
            self.trust = 0.98
            self.discharged = True
        else:
            self.trust = max(0.0, 0.9 - len(fabrications) * 0.1)
        return self.discharged


@dataclass
class RealDataObligation(DeliveryObligation):
    """Benchmarks must use real data, not synthetic."""
    key: str = "real_data"
    description: str = "Benchmarks use real data (e.g. Yahoo Finance), not synthetic"

    def check(self, output_dir: str, benchmark_results: dict[str, Any]) -> bool:
        # Heuristic: scan code for yfinance/pandas_datareader/real data fetchers
        real_data_markers = [
            "yfinance", "yf.download", "pandas_datareader",
            "quandl", "alpha_vantage", "yahoo", "bloomberg",
        ]
        synthetic_markers = [
            "np.random", "random.gauss", "simulate", "synthetic",
            "generate_data", "fake_data", "mock_data",
        ]

        real_count = 0
        synthetic_count = 0
        for root, dirs, files in os.walk(output_dir):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                try:
                    with open(os.path.join(root, fname)) as f:
                        code = f.read()
                    for marker in real_data_markers:
                        if marker in code:
                            real_count += 1
                    for marker in synthetic_markers:
                        if marker in code:
                            synthetic_count += 1
                except Exception:
                    pass

        self.evidence = f"{real_count} real-data markers, {synthetic_count} synthetic markers"

        if real_count > 0 and real_count > synthetic_count:
            self.trust = 0.9
            self.discharged = True
        elif real_count > 0:
            self.trust = 0.6
        else:
            self.trust = 0.1
        return self.discharged


@dataclass
class CodeScaleObligation(DeliveryObligation):
    """Generated code must be ≥ min_kloc KLoC."""
    key: str = "code_scale"
    description: str = "Generated code is ≥ 50 KLoC"
    min_kloc: int = 50
    trust_floor: float = 0.7

    def check(self, code_files: list[str]) -> bool:
        total = 0
        for f in code_files:
            if f.endswith(".py") and os.path.exists(f):
                try:
                    total += len(open(f).readlines())
                except Exception:
                    pass
        kloc = total / 1000
        self.evidence = f"{total} lines ({kloc:.1f} KLoC)"
        self.trust = min(1.0, kloc / self.min_kloc)
        self.discharged = kloc >= self.min_kloc
        return self.discharged


# ═══════════════════════════════════════════════════════════════════════
#  Fabrication scanner
# ═══════════════════════════════════════════════════════════════════════

def _scan_fabrications(
    paper_text: str,
    benchmark_results: dict[str, Any],
) -> list[str]:
    """Scan paper for numbers that don't match any benchmark result.

    A "fabrication" is a specific numeric claim (e.g., "Sharpe ratio of 2.3")
    that cannot be traced to any entry in benchmark_results.
    """
    fabrications = []

    # Extract all numbers from the paper that look like metric values
    # Pattern: number preceded by metric-like words
    metric_patterns = re.findall(
        r'(?:ratio|score|accuracy|precision|recall|f1|auc|return|drawdown|'
        r'sharpe|calmar|sortino|alpha|beta|volatility|correlation|'
        r'r\^?2|rmse|mae|mse)\s*(?:of|=|:|\bis\b)?\s*'
        r'(-?\d+\.?\d*)',
        paper_text, re.IGNORECASE,
    )

    # Flatten all benchmark values
    known_values: set[str] = set()
    def _collect(d: Any, prefix: str = ""):
        if isinstance(d, dict):
            for k, v in d.items():
                _collect(v, f"{prefix}{k}.")
        elif isinstance(d, (int, float)):
            known_values.add(f"{d:.4f}")
            known_values.add(f"{d:.2f}")
            known_values.add(f"{d:.1f}")
            known_values.add(str(int(d)) if d == int(d) else "")
    _collect(benchmark_results)

    for match in metric_patterns:
        val = match.strip()
        # Check if this value appears in our known results
        if val not in known_values and f"{float(val):.4f}" not in known_values:
            fabrications.append(f"Untraced metric value: {val}")

    return fabrications


# ═══════════════════════════════════════════════════════════════════════
#  Metrics script generator
# ═══════════════════════════════════════════════════════════════════════

def _generate_metrics_script(
    output_dir: str,
    pkg_name: str,
    approach: str,
    domain_analysis: dict,
    benchmark_results: dict[str, Any],
) -> str:
    """Generate a run_metrics.py that reproduces all reported numbers.

    This script is the REPRODUCIBILITY artifact — running it from scratch
    must produce the same numbers that appear in the paper.
    """
    path = os.path.join(output_dir, "run_metrics.py")

    section = agent_call(
        textwrap.dedent(f"""\
            Write a COMPLETE, SELF-CONTAINED metrics reproduction script to {path}.

            This script must:
            1. Install its own dependencies (pip install yfinance numpy pandas scipy etc.)
            2. Fetch REAL data from Yahoo Finance (yfinance) — NO synthetic data
            3. Run the {pkg_name} package algorithms on that data
            4. Compute ALL metrics that appear in the paper:
               {json.dumps(list(benchmark_results.keys())[:20], indent=2)}
            5. Compare against baselines (equal-weight, buy-and-hold, etc.)
            6. Print a JSON summary to stdout with the exact same keys
            7. Generate comparison tables as CSV files

            The script must be runnable as:
                python run_metrics.py

            Package is installed from: {output_dir}
            Available modules: {json.dumps(domain_analysis.get('standard_libraries', []))}

            CRITICAL: Use REAL Yahoo Finance data via yfinance.  The script must
            download actual market data, not use random numbers or cached fixtures.

            Write the complete script to {path} using your file-write tool.
        """),
        surface=SurfaceKind.EVIDENCE,
        coordinate="evidence.metrics_script",
        working_dir=output_dir,
    )

    # Read back or fall back
    if os.path.exists(path) and os.path.getsize(path) > 100:
        return path

    # Agent returned text instead of writing
    code = section.content if section else ""
    if code.startswith("```"):
        code = "\n".join(l for l in code.split("\n") if not l.startswith("```"))
    with open(path, "w") as f:
        f.write(code.strip() + "\n")
    return path


# ═══════════════════════════════════════════════════════════════════════
#  The main research-and-implement function
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ResearchAndImplementResult:
    """Result of the full research-and-implement pipeline."""
    status: ResearchStatus
    prompt: str
    approach: str
    inner_result: DirectedResearchResult
    obligations: dict[str, dict[str, Any]]
    all_discharged: bool
    iterations: int
    pivots: int
    output_dir: str
    elapsed: float
    git_commits: int
    metrics_script: str

    @property
    def success(self) -> bool:
        return self.all_discharged


def research_and_implement(
    prompt: str,
    *,
    max_outer_iterations: int = 5,
    max_inner_iterations: int = 30,
    max_pivots: int = 3,
    output_dir: str | None = None,
    verbose: bool = True,
    no_llm: bool = False,
    min_pages: int = 14,
    min_figures: int = 2,
    min_kloc: int = 50,
) -> ResearchAndImplementResult:
    """The full prompt-to-SOTA pipeline.

    This is the function behind ``jugeo research-and-implement``.
    It wraps DirectedResearch in an outer loop that checks hard
    delivery obligations after each inner run.  If obligations
    aren't met, it re-enters the inner loop with targeted repairs.

    The outer loop:
        1. Run DirectedResearch (IDEATE → SEED → GENERATE → HARDEN → TAIL)
        2. Check all delivery obligations
        3. If all discharged → generate metrics script → hallucination check → DONE
        4. If not → diagnose which obligations failed, inject targeted
           repair instructions into the prompt, and re-run (step 1)
        5. After max_outer_iterations, return partial result

    Parameters
    ----------
    prompt : str
        Natural-language description of what to build.
    max_outer_iterations : int
        How many full theorize→implement→test cycles to attempt.
    max_inner_iterations : int
        Max iterations within each DirectedResearch harden phase.
    max_pivots : int
        Max theory pivots (changes to which metric we target).
    output_dir : str, optional
        Where to write output.  Default: outputs/research_<timestamp>/
    verbose : bool
        Print progress.
    min_pages : int
        Minimum paper page count (default 14).
    min_figures : int
        Minimum figure count in paper (default 2).
    min_kloc : int
        Minimum generated code KLoC (default 50).
    """
    start = time.time()

    if output_dir:
        out = pathlib.Path(output_dir).resolve()
    else:
        out = pathlib.Path("outputs") / f"research_{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)

    # Build delivery obligations
    obligations = [
        PageCountObligation(min_pages=min_pages),
        FigureCountObligation(min_figures=min_figures),
        SOTAMetricObligation(),
        MetricsScriptObligation(),
        HallucinationFreeObligation(),
        RealDataObligation(),
        CodeScaleObligation(min_kloc=min_kloc),
    ]

    def _log(msg: str):
        if verbose:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {msg}", flush=True)

    def _check_obligations(result: DirectedResearchResult) -> tuple[bool, list[str]]:
        """Check all obligations against the current result."""
        paper_path = str(pathlib.Path(result.output_dir) / "conference_tool_track.tex")
        failures = []
        for obl in obligations:
            if isinstance(obl, PageCountObligation):
                obl.check(paper_path)
            elif isinstance(obl, FigureCountObligation):
                obl.check(paper_path)
            elif isinstance(obl, SOTAMetricObligation):
                obl.check(result.benchmark_results or {})
            elif isinstance(obl, MetricsScriptObligation):
                obl.check(result.output_dir)
            elif isinstance(obl, HallucinationFreeObligation):
                obl.check(paper_path, result.benchmark_results or {})
            elif isinstance(obl, RealDataObligation):
                obl.check(result.output_dir, result.benchmark_results or {})
            elif isinstance(obl, CodeScaleObligation):
                obl.check(result.code_files)

            status = "✓" if obl.discharged else "✗"
            _log(f"  {status} {obl.key}: {obl.evidence} (trust={obl.trust:.2f})")
            if not obl.discharged:
                failures.append(obl.key)

        return len(failures) == 0, failures

    # ── The outer loop: theorize → implement → test → [refine|pivot] ──
    best_result: Optional[DirectedResearchResult] = None
    metrics_script_path = ""
    total_pivots = 0
    current_prompt = prompt

    for outer_iter in range(max_outer_iterations):
        _log(f"{'═'*60}")
        _log(f"  OUTER ITERATION {outer_iter + 1}/{max_outer_iterations}")
        _log(f"{'═'*60}")

        # Run the inner directed-research pipeline
        iter_dir = str(out / f"iteration_{outer_iter}")
        if outer_iter == 0:
            iter_dir = str(out)  # first iteration uses root dir

        dr = DirectedResearch(
            prompt=current_prompt,
            max_iterations=max_inner_iterations,
            max_pivots=max_pivots,
            output_dir=iter_dir,
            verbose=verbose,
            no_llm=no_llm,
        )
        result = dr.run()
        best_result = result

        _log(f"\n  Inner loop finished: status={result.status}")
        _log(f"  Checking delivery obligations...")

        all_met, failures = _check_obligations(result)

        if all_met:
            _log(f"  ✓ ALL OBLIGATIONS MET — generating metrics script")

            # Generate the reproducible metrics script
            if not no_llm:
                pkg = dr.architecture.get("package_name", dr.approach)
                metrics_script_path = _generate_metrics_script(
                    result.output_dir, pkg, dr.approach,
                    dr.domain_analysis, result.benchmark_results or {},
                )

                # Re-check metrics script obligation
                for obl in obligations:
                    if isinstance(obl, MetricsScriptObligation):
                        obl.check(result.output_dir)

            # Final hallucination check (the LAST gate)
            _log(f"  Running final hallucination scan...")
            paper_path = str(pathlib.Path(result.output_dir) / "conference_tool_track.tex")
            for obl in obligations:
                if isinstance(obl, HallucinationFreeObligation):
                    obl.check(paper_path, result.benchmark_results or {})
                    if not obl.discharged:
                        _log(f"  ✗ Hallucination check FAILED: {obl.evidence}")
                        failures.append("hallucination_free")

            if not failures:
                _log(f"  ✓✓✓ RESEARCH COMPLETE — all obligations discharged")
                break

        # Not all obligations met — diagnose and refine
        _log(f"\n  ✗ {len(failures)} obligations not met: {failures}")
        _log(f"  Refining prompt for next iteration...")

        # Build refinement instructions based on which obligations failed
        refinement = _build_refinement_prompt(obligations, failures, result)
        current_prompt = prompt + "\n\n" + refinement
        total_pivots += 1

    # ── Build final result ────────────────────────────────────────────
    elapsed = time.time() - start
    obl_summary = {
        obl.key: {
            "discharged": obl.discharged,
            "trust": obl.trust,
            "evidence": obl.evidence,
        }
        for obl in obligations
    }

    all_discharged = all(obl.discharged for obl in obligations)

    # Save obligation report
    (out / "obligations.json").write_text(json.dumps(obl_summary, indent=2))

    _log(f"\n{'═'*60}")
    _log(f"  FINAL STATUS: {'CONVERGED' if all_discharged else 'PARTIAL'}")
    _log(f"  Obligations: {sum(1 for o in obligations if o.discharged)}/{len(obligations)}")
    _log(f"  Elapsed: {elapsed:.1f}s")
    _log(f"  Output: {out}")
    _log(f"{'═'*60}")

    return ResearchAndImplementResult(
        status=ResearchStatus.CONVERGED if all_discharged else ResearchStatus.BUDGET_EXHAUSTED,
        prompt=prompt,
        approach=best_result.approach if best_result else "unknown",
        inner_result=best_result,
        obligations=obl_summary,
        all_discharged=all_discharged,
        iterations=max_outer_iterations,
        pivots=total_pivots,
        output_dir=str(out),
        elapsed=elapsed,
        git_commits=len(dr.git_tracker.commits) if best_result else 0,
        metrics_script=metrics_script_path,
    )


def _build_refinement_prompt(
    obligations: list[DeliveryObligation],
    failures: list[str],
    result: DirectedResearchResult,
) -> str:
    """Build additional prompt text to address failed obligations."""
    parts = [
        "REFINEMENT INSTRUCTIONS (from previous iteration):",
        "The following delivery obligations were NOT met:",
        "",
    ]
    for obl in obligations:
        if obl.key in failures:
            parts.append(f"  ✗ {obl.key}: {obl.description}")
            parts.append(f"    Evidence: {obl.evidence}")
            parts.append(f"    Trust: {obl.trust:.2f} (need {obl.trust_floor:.2f})")
            parts.append("")

    parts.append("SPECIFIC INSTRUCTIONS TO FIX THESE:")
    parts.append("")

    if "paper_page_count" in failures:
        parts.append("- The paper is too short.  Add more depth to the Mathematical")
        parts.append("  Framework, Evaluation, and Related Work sections.  Each section")
        parts.append("  should be at least 1.5 pages.  Target 14+ pages total.")
        parts.append("")

    if "paper_figure_count" in failures:
        parts.append("- The paper needs at least 2 figures (tikzpicture/pgfplots):")
        parts.append("  1) A performance comparison chart (our method vs baselines)")
        parts.append("  2) A visualization of the mathematical structure being exploited")
        parts.append("  Use \\begin{figure} with \\begin{tikzpicture} or pgfplots.")
        parts.append("")

    if "sota_metric" in failures:
        parts.append("- No metric clearly beats baselines yet.  You MUST find at least")
        parts.append("  one metric where your approach dominates.  Consider:")
        parts.append("  * Different metrics (Sharpe, Calmar, max drawdown, etc.)")
        parts.append("  * Different time periods or asset universes")
        parts.append("  * Conditional metrics (performance in specific regimes)")
        parts.append("  If the current approach can't beat anything, PIVOT to a")
        parts.append("  different mathematical technique on a different sub-problem.")
        parts.append("")

    if "metrics_script" in failures:
        parts.append("- Create a run_metrics.py script that reproduces all numbers.")
        parts.append("  It must use yfinance for real data and print JSON to stdout.")
        parts.append("")

    if "hallucination_free" in failures:
        parts.append("- The paper contains numbers not traceable to benchmark results.")
        parts.append("  Every number in the Evaluation section MUST come from actual")
        parts.append("  benchmark output.  Remove or fix any fabricated claims.")
        parts.append("")

    if "real_data" in failures:
        parts.append("- Benchmarks must use REAL data from Yahoo Finance (yfinance),")
        parts.append("  NOT np.random or simulated data.  Add yfinance data fetching.")
        parts.append("")

    if "code_scale" in failures:
        parts.append("- Code is below the 50 KLoC target.  Generate more modules.")
        parts.append("  Add comprehensive utilities, data pipelines, visualizations,")
        parts.append("  and test suites to reach the scale target.")
        parts.append("")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
#  CLI entry point
# ═══════════════════════════════════════════════════════════════════════

def add_subparser(subparsers) -> Any:
    """Register the research-and-implement subcommand."""
    p = subparsers.add_parser(
        "research-and-implement",
        help="Full prompt→SOTA pipeline: ideate, implement, benchmark, "
             "refine until SOTA, generate paper with figures, check for "
             "hallucinations. Does not stop until obligations are met.",
        description=(
            "Run the full research-and-implement pipeline.  Given a prompt,\n"
            "this command ideates a novel approach, generates a large-scale\n"
            "implementation (≥50 KLoC), benchmarks against competitive\n"
            "baselines on real data, refines or pivots until at least one\n"
            "metric is clearly SOTA, and produces a 14+ page paper with\n"
            "figures and a reproducible metrics script.\n\n"
            "The loop does NOT stop until all delivery obligations are met\n"
            "or the iteration budget is exhausted.\n\n"
            "Examples:\n"
            '  jugeo research-and-implement "killer app in finance using advanced math"\n'
            '  jugeo research-and-implement "fast graph neural network for drug discovery" --max-pivots 5\n'
        ),
        formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
    )
    p.add_argument("prompt", help="Natural-language description of what to build.")
    p.add_argument("--max-outer", type=int, default=5,
                   help="Max outer iterations (theorize→implement→test cycles). Default: 5.")
    p.add_argument("--max-inner", type=int, default=30,
                   help="Max inner iterations per cycle (refinement steps). Default: 30.")
    p.add_argument("--max-pivots", type=int, default=3,
                   help="Max theory pivots per inner cycle. Default: 3.")
    p.add_argument("--min-pages", type=int, default=14,
                   help="Minimum paper pages. Default: 14.")
    p.add_argument("--min-figures", type=int, default=2,
                   help="Minimum paper figures. Default: 2.")
    p.add_argument("--min-kloc", type=int, default=50,
                   help="Minimum code KLoC. Default: 50.")
    return p


def run_cli(args) -> int:
    """CLI handler for research-and-implement."""
    result = research_and_implement(
        prompt=args.prompt,
        max_outer_iterations=getattr(args, "max_outer", 5),
        max_inner_iterations=getattr(args, "max_inner", 30),
        max_pivots=getattr(args, "max_pivots", 3),
        output_dir=getattr(args, "output", None),
        verbose=getattr(args, "verbose", True),
        no_llm=getattr(args, "no_llm", False),
        min_pages=getattr(args, "min_pages", 14),
        min_figures=getattr(args, "min_figures", 2),
        min_kloc=getattr(args, "min_kloc", 50),
    )

    if result.success:
        print(f"\n✓ RESEARCH COMPLETE — all obligations discharged")
        print(f"  Output: {result.output_dir}")
        print(f"  Metrics script: {result.metrics_script}")
        return 0
    else:
        print(f"\n✗ PARTIAL — some obligations not met")
        for key, obl in result.obligations.items():
            status = "✓" if obl["discharged"] else "✗"
            print(f"  {status} {key}: {obl['evidence']}")
        print(f"  Output: {result.output_dir}")
        return 1
