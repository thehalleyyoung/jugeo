"""Benchmark establishment, SOTA comparison, and metric-driven convergence.

This module formalizes what it means to "reach SOTA" in judgment-geometry terms.
The key insight: SOTA comparison is a *descent condition* on the Evidence surface.

A research project converges when:
    1. The workspace is internally consistent (H^1 = 0 on the 4-surface site)
    2. The Evidence surface has sections that DOMINATE known baselines
    3. The domination is RUNTIME_WITNESSED (trust >= 0.7)

"Domination" is defined via a *metric frontier*: a set of (metric, value) pairs
that the project must exceed. The frontier is established by:
    1. Identifying relevant metrics for the domain
    2. Finding SOTA baselines for each metric
    3. Running the generated code on the same evaluation protocol
    4. Comparing: does our code exceed the frontier on at least one axis
       without regressing on others?

If not, the descent loop must either:
    - REFINE: fix the implementation to improve metrics (repair obstruction)
    - PIVOT: change the mathematical approach (adjacency-constrained theory change)

The "try things until SOTA" loop is formally:

    while not dominates_frontier(evidence, frontier):
        obstructions = descent(workspace)
        if obstructions on E surface:
            repair_evidence(obstructions)    # fix benchmarks
        elif obstructions on R surface:
            repair_code(obstructions)        # fix code
        elif obstructions on T↔R morphism:
            refine_theory_or_pivot()         # change approach
        else:
            # Consistent but not competitive — need better approach
            if pivots_remaining > 0:
                pivot_theory()
            else:
                declare_partial_success()

This is the geometric content of "try things until you reach SOTA."
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind, ConsistencyReport

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    TRUST_RUNTIME,
    TRUST_SOLVER,
    LLMSection,
    HAS_EASY,
)
from jugeo.directed_research._agent_channel import agent_call, agent_json


# ═══════════════════════════════════════════════════════════════════════
#  Metric frontier — what SOTA means
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class MetricBaseline:
    """A single baseline metric from a known tool/method."""
    name: str           # metric name (e.g., "Sharpe ratio", "F1 score")
    tool: str           # baseline tool (e.g., "QuantLib", "scikit-learn")
    value: float        # baseline value
    higher_is_better: bool = True
    source: str = ""    # where the baseline comes from

    def is_dominated_by(self, our_value: float) -> bool:
        """Does our value dominate this baseline?"""
        if self.higher_is_better:
            return our_value > self.value
        else:
            return our_value < self.value


@dataclass
class MetricFrontier:
    """The SOTA frontier — the set of baselines we need to exceed.

    The frontier is a Pareto surface: we must dominate on at least one
    metric without regressing on others. In geometric terms, the frontier
    is the *boundary of the feasible region* in metric space, and our
    evidence sections must lie strictly outside it.
    """
    baselines: list[MetricBaseline] = field(default_factory=list)
    our_results: dict[str, float] = field(default_factory=dict)

    def add_baseline(self, name: str, tool: str, value: float,
                     higher_is_better: bool = True, source: str = ""):
        self.baselines.append(MetricBaseline(
            name=name, tool=tool, value=value,
            higher_is_better=higher_is_better, source=source))

    def dominates(self) -> bool:
        """Do our results dominate the frontier on at least one axis?

        We dominate if:
        1. At least one metric strictly exceeds the best baseline
        2. No metric is strictly worse than all baselines

        This is Pareto domination — the geometric criterion for "SOTA."
        """
        if not self.baselines or not self.our_results:
            return False

        has_improvement = False
        has_regression = False

        for baseline in self.baselines:
            if baseline.name in self.our_results:
                our = self.our_results[baseline.name]
                if baseline.is_dominated_by(our):
                    has_improvement = True
                elif baseline.higher_is_better and our < baseline.value * 0.95:
                    has_regression = True
                elif not baseline.higher_is_better and our > baseline.value * 1.05:
                    has_regression = True

        return has_improvement and not has_regression

    def gap_analysis(self) -> dict[str, dict[str, Any]]:
        """For each metric, compute the gap between our result and SOTA."""
        gaps = {}
        for baseline in self.baselines:
            if baseline.name in self.our_results:
                our = self.our_results[baseline.name]
                if baseline.higher_is_better:
                    gap = our - baseline.value
                    pct = (gap / max(abs(baseline.value), 1e-10)) * 100
                else:
                    gap = baseline.value - our
                    pct = (gap / max(abs(baseline.value), 1e-10)) * 100
                gaps[baseline.name] = {
                    "our_value": our,
                    "baseline": baseline.value,
                    "baseline_tool": baseline.tool,
                    "gap": gap,
                    "gap_pct": pct,
                    "dominates": baseline.is_dominated_by(our),
                }
        return gaps

    def weakest_metric(self) -> Optional[str]:
        """Which metric has the largest negative gap (worst performance)?"""
        gaps = self.gap_analysis()
        worst_name = None
        worst_gap = float("inf")
        for name, info in gaps.items():
            if info["gap_pct"] < worst_gap:
                worst_gap = info["gap_pct"]
                worst_name = name
        return worst_name

    def to_dict(self) -> dict:
        return {
            "baselines": [
                {"name": b.name, "tool": b.tool, "value": b.value,
                 "higher_is_better": b.higher_is_better}
                for b in self.baselines
            ],
            "our_results": self.our_results,
            "dominates": self.dominates(),
            "gap_analysis": self.gap_analysis(),
        }


# ═══════════════════════════════════════════════════════════════════════
#  Baseline establishment
# ═══════════════════════════════════════════════════════════════════════

def establish_baselines(
    domain_analysis: dict,
    approach: str,
    verbose: bool = False,
) -> MetricFrontier:
    """Establish SOTA baselines from domain analysis.

    Uses the baselines_to_beat and evaluation_metrics from the domain
    analysis to construct the metric frontier.
    """
    frontier = MetricFrontier()

    # From domain analysis
    for baseline in domain_analysis.get("baselines_to_beat", []):
        name = baseline.get("metric", baseline.get("name", "unknown"))
        tool = baseline.get("name", baseline.get("tool", "unknown"))
        value_str = str(baseline.get("value", "0"))
        try:
            value = float(value_str.replace("%", ""))
            if "%" in value_str:
                value /= 100.0
        except ValueError:
            continue
        frontier.add_baseline(name, tool, value, source="domain_analysis")

    # If no baselines from analysis, ask the agent
    if not frontier.baselines:
        data, _ = agent_json(
            f"""What are the standard evaluation metrics and SOTA baselines
for this type of project?

Approach: {approach}
Domain analysis: {json.dumps(domain_analysis)[:500]}

Respond as JSON:
{{
    "baselines": [
        {{"name": "metric_name", "tool": "baseline_tool",
          "value": 0.85, "higher_is_better": true}}
    ]
}}""",
            surface=SurfaceKind.EVIDENCE,
            coordinate="evidence.baselines",
        )
        for b in data.get("baselines", []):
            frontier.add_baseline(
                b.get("name", "unknown"),
                b.get("tool", "unknown"),
                float(b.get("value", 0.0)),
                b.get("higher_is_better", True),
                source="agent_estimated",
            )

    return frontier


# ═══════════════════════════════════════════════════════════════════════
#  Benchmark execution
# ═══════════════════════════════════════════════════════════════════════

def run_benchmarks(
    output_dir: str,
    pkg_name: str,
    metrics: list[str],
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run benchmarks on the generated code.

    Attempts to:
    1. Import the generated package
    2. Run any benchmark/evaluation scripts
    3. Capture metrics from stdout/files
    4. Return a dict of metric → value

    Trust level: RUNTIME_WITNESSED (0.7) if benchmarks actually run.
    """
    results: dict[str, Any] = {}
    src_dir = os.path.join(output_dir, "src")

    # Try to import the package
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, '{src_dir}'); import {pkg_name}; "
             f"print('OK')"],
            capture_output=True, text=True, timeout=30)
        results["import_success"] = r.returncode == 0
        if r.returncode != 0:
            results["import_error"] = r.stderr[:500]
    except Exception as e:
        results["import_success"] = False
        results["import_error"] = str(e)

    # Try to run benchmark script if it exists
    bench_script = os.path.join(src_dir, pkg_name, "benchmark.py")
    if os.path.exists(bench_script):
        try:
            r = subprocess.run(
                [sys.executable, bench_script],
                capture_output=True, text=True, timeout=120,
                cwd=output_dir,
                env={**os.environ, "PYTHONPATH": src_dir})
            if r.returncode == 0:
                # Try to parse JSON metrics from output
                try:
                    results.update(json.loads(r.stdout))
                except json.JSONDecodeError:
                    results["benchmark_output"] = r.stdout[:1000]
        except Exception as e:
            results["benchmark_error"] = str(e)

    # Try to run tests
    test_dir = os.path.join(output_dir, "tests")
    if os.path.exists(test_dir):
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", test_dir, "-v", "--tb=short"],
                capture_output=True, text=True, timeout=120,
                cwd=output_dir,
                env={**os.environ, "PYTHONPATH": src_dir})
            results["tests_passed"] = r.returncode == 0
            results["test_output"] = r.stdout[-500:]
        except Exception:
            results["tests_passed"] = False

    # Syntax check all .py files
    py_files = []
    for root, dirs, files in os.walk(os.path.join(src_dir, pkg_name)):
        for f in files:
            if f.endswith(".py"):
                py_files.append(os.path.join(root, f))
    syntax_ok = 0
    for f in py_files:
        try:
            r = subprocess.run(
                [sys.executable, "-c",
                 f"import ast; ast.parse(open('{f}').read())"],
                capture_output=True, timeout=10)
            if r.returncode == 0:
                syntax_ok += 1
        except Exception:
            pass
    results["syntax_pass_rate"] = syntax_ok / max(len(py_files), 1)
    results["total_py_files"] = len(py_files)
    results["total_lines"] = sum(
        len(open(f).readlines()) for f in py_files if os.path.exists(f))

    return results


def update_frontier_with_results(
    frontier: MetricFrontier,
    benchmark_results: dict[str, Any],
) -> MetricFrontier:
    """Update the frontier with our benchmark results."""
    for baseline in frontier.baselines:
        if baseline.name in benchmark_results:
            frontier.our_results[baseline.name] = benchmark_results[baseline.name]
    return frontier


# ═══════════════════════════════════════════════════════════════════════
#  Convergence criterion
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class QualitySection:
    """A local section on the quality site — one dimension of 'is this good?'

    Every dimension of research quality is modeled as a local section at a
    coordinate in a *quality site*, with a trust level from the trust algebra.
    The section is either evidenced (trust >= some floor) or obstructed
    (trust below floor, indicating descent failure on that dimension).

    The quality site has the following coordinates (objects):

        WORKSPACE_CONSISTENCY   — H^1 = 0 on the 4-surface workspace
        SOTA_DOMINATION         — metrics Pareto-dominate known baselines
        CODE_CORRECTNESS        — jg prove / jg bugs pass on all code
        CLAIMS_GROUNDING        — all paper claims match evidence surface
        PAPER_COMPLETENESS      — paper has all required sections, >= 12 pages
        PAPER_HONESTY           — no fabricated numbers (E∩P overlap clean)
        CODE_SCALE              — code is large-scale (>= target LOC)
        TEST_COVERAGE           — tests exist and pass
        REPRODUCIBILITY         — README instructions actually work
        THEORY_CODE_ALIGNMENT   — T→R morphism: code implements the theory

    The morphisms between these coordinates express dependencies:
        CLAIMS_GROUNDING requires WORKSPACE_CONSISTENCY
        PAPER_HONESTY requires CLAIMS_GROUNDING
        SOTA_DOMINATION requires CODE_CORRECTNESS
        REPRODUCIBILITY requires CODE_CORRECTNESS + TEST_COVERAGE
        THEORY_CODE_ALIGNMENT requires CODE_CORRECTNESS

    Descent on this quality site checks all overlaps. A research project
    CONVERGES when descent succeeds — all local sections are present,
    all overlaps agree, and all trust levels are above their floors.
    """
    coordinate: str
    description: str
    trust: float = 0.0
    trust_floor: float = 0.5
    evidence: str = ""
    obstructions: list[str] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return self.trust >= self.trust_floor and not self.obstructions

    @property
    def obstruction_summary(self) -> str:
        if self.satisfied:
            return f"✓ {self.coordinate} (trust={self.trust:.2f})"
        issues = "; ".join(self.obstructions) if self.obstructions else f"trust {self.trust:.2f} < floor {self.trust_floor:.2f}"
        return f"✗ {self.coordinate}: {issues}"


# Quality site coordinates with their trust floors
QUALITY_COORDINATES: dict[str, tuple[str, float]] = {
    "workspace_consistency": ("H^1 = 0 on the 4-surface workspace site", 0.7),
    "sota_domination": ("Metrics Pareto-dominate SOTA baselines", 0.7),
    "code_correctness": ("All code passes jg prove and jg bugs", 0.9),
    "claims_grounding": ("All paper/README claims match evidence", 0.7),
    "paper_completeness": ("Paper has all sections, >= 12 pages", 0.5),
    "paper_honesty": ("No fabricated numbers (E∩P overlap clean)", 0.9),
    "code_scale": ("Codebase >= target LOC", 0.7),
    "test_coverage": ("Tests exist and pass", 0.7),
    "reproducibility": ("README instructions produce expected output", 0.7),
    "theory_code_alignment": ("Code implements the theory (T→R morphism)", 0.5),
}

# Morphisms: (source, target) — source must be satisfied for target to be meaningful
QUALITY_MORPHISMS: list[tuple[str, str]] = [
    ("workspace_consistency", "claims_grounding"),
    ("claims_grounding", "paper_honesty"),
    ("code_correctness", "sota_domination"),
    ("code_correctness", "reproducibility"),
    ("test_coverage", "reproducibility"),
    ("code_correctness", "theory_code_alignment"),
]


@dataclass
class ConvergenceCriterion:
    """The formal convergence criterion as DESCENT on the quality site.

    Each dimension of "is this a good research paper and large-scale tool?"
    is a local section at a coordinate in the quality site, carrying a trust
    level from the trust algebra. The morphisms between coordinates express
    dependencies (e.g., paper honesty requires claims grounding).

    The research project CONVERGES when descent on the quality site succeeds:
    all local sections are present, all satisfy their trust floors, and all
    morphism-connected pairs are compatible (if the source is satisfied,
    the target must also be satisfied — the sheaf condition).

    This is the same descent computation as the workspace site, but over
    quality dimensions instead of artifact surfaces.
    """
    sections: dict[str, QualitySection] = field(default_factory=dict)

    def __post_init__(self):
        # Initialize all coordinates if empty
        if not self.sections:
            for coord, (desc, floor) in QUALITY_COORDINATES.items():
                self.sections[coord] = QualitySection(
                    coordinate=coord, description=desc, trust_floor=floor)

    # ── Builders: set evidence for each quality dimension ──────────

    def set_workspace_consistency(self, consistent: bool, h1: str = ""):
        s = self.sections["workspace_consistency"]
        s.trust = 0.9 if consistent else 0.0
        s.evidence = f"H1={h1}"
        if not consistent:
            s.obstructions = [f"H^1 ≠ 0: {h1}"]
        else:
            s.obstructions = []

    def set_sota_domination(self, frontier: Optional['MetricFrontier'] = None):
        s = self.sections["sota_domination"]
        if frontier and frontier.dominates():
            s.trust = 0.8
            s.evidence = "Pareto-dominates frontier"
            s.obstructions = []
        elif frontier:
            weakest = frontier.weakest_metric()
            gap = frontier.gap_analysis()
            s.trust = 0.3
            s.obstructions = [
                f"Does not dominate frontier; weakest: {weakest} "
                f"(gap: {gap.get(weakest, {}).get('gap_pct', '?'):.1f}%)"
                if weakest else "No metrics to compare"
            ]
        else:
            s.trust = 0.0
            s.obstructions = ["No frontier established"]

    def set_code_correctness(self, jg_results: list = None,
                             syntax_ok: bool = False, import_ok: bool = False):
        s = self.sections["code_correctness"]
        issues = []
        if not syntax_ok:
            issues.append("syntax errors in generated code")
        if not import_ok:
            issues.append("import errors in generated package")
        if jg_results:
            jg_failures = [r for r in jg_results if not r.verified]
            if jg_failures:
                issues.extend(f"jg: {r.claim}" for r in jg_failures[:3])
        s.obstructions = issues
        s.trust = 0.9 if not issues else (0.5 if syntax_ok else 0.0)
        s.evidence = f"syntax={'OK' if syntax_ok else 'FAIL'}, import={'OK' if import_ok else 'FAIL'}"

    def set_claims_grounding(self, consistent: bool):
        s = self.sections["claims_grounding"]
        s.trust = 0.8 if consistent else 0.2
        s.obstructions = [] if consistent else ["claims diverge from evidence"]

    def set_paper_completeness(self, paper_path: str):
        s = self.sections["paper_completeness"]
        if not paper_path or not os.path.exists(paper_path):
            s.trust = 0.0
            s.obstructions = ["paper not generated"]
            return
        with open(paper_path) as f:
            text = f.read()
        # Check required sections
        required = ["abstract", "introduction", "background", "framework",
                     "architecture", "implementation", "evaluation",
                     "discussion", "conclusion", "references"]
        missing = [sec for sec in required
                   if sec not in text.lower() and f"\\section{{{sec}" not in text.lower()]
        line_count = len(text.splitlines())
        issues = []
        if missing:
            issues.append(f"missing sections: {', '.join(missing)}")
        if line_count < 400:  # ~12 pages is ~400+ lines of LaTeX
            issues.append(f"too short ({line_count} lines, need ~400+)")
        s.obstructions = issues
        s.trust = 0.8 if not issues else 0.3
        s.evidence = f"{line_count} lines, {len(required) - len(missing)}/{len(required)} sections"

    def set_paper_honesty(self, fabrication_count: int = 0):
        s = self.sections["paper_honesty"]
        s.trust = 0.95 if fabrication_count == 0 else max(0.0, 0.5 - fabrication_count * 0.1)
        s.obstructions = [f"{fabrication_count} potentially fabricated claims"] if fabrication_count else []

    def set_code_scale(self, total_lines: int, target: int = 5000):
        s = self.sections["code_scale"]
        ratio = total_lines / max(target, 1)
        s.trust = min(1.0, ratio)
        s.evidence = f"{total_lines}/{target} lines ({ratio:.0%})"
        if total_lines < target * 0.5:
            s.obstructions = [f"only {total_lines} lines (target: {target})"]
        else:
            s.obstructions = []

    def set_test_coverage(self, tests_exist: bool, tests_pass: bool):
        s = self.sections["test_coverage"]
        if tests_exist and tests_pass:
            s.trust = 0.8
        elif tests_exist:
            s.trust = 0.4
            s.obstructions = ["tests exist but fail"]
        else:
            s.trust = 0.2
            s.obstructions = ["no test suite"]

    def set_reproducibility(self, readme_verification_rate: float):
        s = self.sections["reproducibility"]
        s.trust = readme_verification_rate
        if readme_verification_rate < 0.5:
            s.obstructions = [f"only {readme_verification_rate:.0%} of README claims verified"]
        else:
            s.obstructions = []

    def set_theory_code_alignment(self, consistent: bool):
        s = self.sections["theory_code_alignment"]
        s.trust = 0.7 if consistent else 0.2
        s.obstructions = [] if consistent else ["T→R morphism: code does not implement theory"]

    # ── Descent on the quality site ───────────────────────────────

    def run_descent(self) -> tuple[bool, list[str]]:
        """Run descent on the quality site.

        Checks:
        1. Every local section satisfies its trust floor
        2. Every morphism is compatible: if source satisfied, target must be too
           (the sheaf condition on the quality site)

        Returns (converged, list_of_obstruction_descriptions).
        """
        obstructions: list[str] = []

        # Check each local section against its trust floor
        for coord, section in self.sections.items():
            if not section.satisfied:
                obstructions.append(section.obstruction_summary)

        # Check morphisms (sheaf condition: compatible on overlaps)
        for source_coord, target_coord in QUALITY_MORPHISMS:
            source = self.sections.get(source_coord)
            target = self.sections.get(target_coord)
            if source and target:
                if source.satisfied and not target.satisfied:
                    obstructions.append(
                        f"Morphism {source_coord}→{target_coord}: "
                        f"source satisfied but target obstructed "
                        f"({target.obstruction_summary})")

        return len(obstructions) == 0, obstructions

    @property
    def converged(self) -> bool:
        ok, _ = self.run_descent()
        return ok

    @property
    def partially_converged(self) -> bool:
        """Workspace consistent but not SOTA-dominating."""
        ws = self.sections.get("workspace_consistency")
        sota = self.sections.get("sota_domination")
        return (ws and ws.satisfied) and (sota and not sota.satisfied)

    def diagnosis(self) -> str:
        """Full descent report on the quality site."""
        ok, obstructions = self.run_descent()
        if ok:
            return "CONVERGED — all quality sections satisfied, descent clean"
        lines = [f"NOT CONVERGED — {len(obstructions)} obstruction(s):"]
        for obs in obstructions:
            lines.append(f"  {obs}")
        return "\n".join(lines)
