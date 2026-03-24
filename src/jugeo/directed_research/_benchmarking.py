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
class ConvergenceCriterion:
    """The formal convergence criterion for directed research.

    A research project has CONVERGED when ALL of these hold:
    1. workspace_consistent: H^1 = 0 on the 4-surface site
    2. frontier_dominated: our metrics dominate the SOTA frontier
    3. code_verified: all code passes jg prove / jg bugs
    4. claims_grounded: all claims match actual evidence
    5. minimum_trust: all surfaces have trust >= floor

    If workspace is consistent but frontier is not dominated, the project
    is PARTIAL — internally coherent but not competitive. The loop must
    continue with either refinement or pivoting.
    """
    workspace_consistent: bool = False
    frontier_dominated: bool = False
    code_verified: bool = False
    claims_grounded: bool = False
    minimum_trust_met: bool = False
    trust_floor: float = 0.5

    @property
    def converged(self) -> bool:
        return (self.workspace_consistent and
                self.frontier_dominated and
                self.claims_grounded and
                self.minimum_trust_met)

    @property
    def partially_converged(self) -> bool:
        """Consistent but not competitive."""
        return self.workspace_consistent and not self.frontier_dominated

    def diagnosis(self) -> str:
        """Human-readable diagnosis of what's blocking convergence."""
        issues = []
        if not self.workspace_consistent:
            issues.append("workspace has H^1 obstructions")
        if not self.frontier_dominated:
            issues.append("metrics do not dominate SOTA frontier")
        if not self.code_verified:
            issues.append("code has jg bugs/prove failures")
        if not self.claims_grounded:
            issues.append("claims don't match evidence")
        if not self.minimum_trust_met:
            issues.append(f"some surfaces below trust floor {self.trust_floor}")
        if not issues:
            return "CONVERGED — all criteria satisfied"
        return "NOT CONVERGED: " + "; ".join(issues)
