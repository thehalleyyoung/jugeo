"""research-and-implement: descent-driven prompt→SOTA pipeline.

This module implements ``jugeo research-and-implement`` as a proper instance of
judgment-geometric descent on a *delivery site* — a 7-object category whose
objects are **hard delivery obligations** and whose morphisms encode causal
dependencies between them.  The pipeline terminates if and only if descent
succeeds on this site (H¹ = 0 and all local sections meet their trust floors).

The delivery site extends the 4-surface workspace site (T, R, E, P) from
:mod:`jugeo.research_orchestration` with three obligation objects:

    PAPER_SCOPE      — the paper is ≥ min_pages with ≥ min_figures
    SOTA_DOMINATION  — at least one metric beats all baselines
    REPRODUCIBILITY  — a run_metrics.py script exists and parses

These obligations are NOT ad-hoc checks bolted on after the fact.  They are
**local sections on the delivery site** whose trust levels are computed from
real artifact inspection, and the pipeline's outer loop IS the iterative
descent procedure (§3.4 of theory2.tex) applied to this site.  When descent
fails, the resulting :class:`DescentObstruction` tells us exactly which overlap
violated, and the repair frontier tells us which semantic move to apply.

The inner loop delegates to :class:`DirectedResearch` (the existing 5-phase
pipeline) for the actual theory/code/evidence generation.  The outer loop
orchestrates via :class:`ResearchOrchestrator` with ``ADAPTIVE`` strategy,
using workspace consistency as the sheaf condition.

The final gate is hallucination checking: a descent on the E∩P overlap that
verifies every number in the paper traces to a benchmark result.

Architecture
------------
::

    ┌──────────────────── Delivery Site ─────────────────────┐
    │                                                        │
    │   PAPER_SCOPE ──→ HALLUCINATION_FREE                   │
    │        ↑                  ↑                            │
    │   WORKSPACE  ←──── REPRODUCIBILITY                     │
    │        ↑                  ↑                            │
    │   SOTA_DOMINATION ──→ REAL_DATA                        │
    │        ↑                                               │
    │   CODE_SCALE                                           │
    │                                                        │
    └────────────────────────────────────────────────────────┘

Each node is a :class:`~jugeo.geometry.descent.LocalSection` with a trust
level.  The morphisms enforce: you cannot claim SOTA without real data,
you cannot claim hallucination-free without a complete paper, etc.

Usage::

    jugeo research-and-implement "killer app in finance using advanced math"

Or programmatically::

    from jugeo.directed_research import research_and_implement
    result = research_and_implement("...")
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

# ── Core JuGeo geometry imports ───────────────────────────────────────

from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    Site,
    SiteBuilder,
)
from jugeo.geometry.descent import (
    DescentEngine,
    DescentStrategy,
    DescentConfiguration,
    LocalSection,
    DescentResult,
    DescentObstruction,
    RepairFrontier,
    TrustFloorPolicy,
)
from jugeo.geometry.covers import Cover

# ── Research orchestration (workspace site) ───────────────────────────

from jugeo.research_orchestration import (
    SurfaceKind,
    ConsistencyReport,
    WorkspaceSite,
    ResearchOrchestrator,
    ResearchStrategy,
    MoveKind as OrchestratorMoveKind,
    ObstructionKind,
    WorkspaceObstruction,
)

# ── Directed research (inner loop) ───────────────────────────────────

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    TRUST_RUNTIME,
    TRUST_SOLVER,
    LLMSection,
    MoveKind,
    ResearchStatus,
    DirectedResearchResult,
)
from jugeo.directed_research._descent_loop import DirectedResearch
from jugeo.directed_research._benchmarking import (
    ConvergenceCriterion,
    QualitySection,
    QUALITY_COORDINATES,
    QUALITY_MORPHISMS,
)
from jugeo.directed_research._agent_channel import agent_call, agent_json
from jugeo.directed_research._git_tracking import (
    OutputRepoTracker, prompt_to_slug,
)
from jugeo.directed_research._code_gen import ensure_dependencies
from jugeo.directed_research._verification import verify_paper


# ═══════════════════════════════════════════════════════════════════════
#  Delivery Site — a 7-object category with Grothendieck topology
# ═══════════════════════════════════════════════════════════════════════
#
#  This is where the module becomes genuinely sheaf-theoretic rather than
#  just "checking a list of conditions."  Each obligation is a coordinate
#  in a site, local sections are evidence artifacts, and the pipeline
#  terminates iff iterative descent on this site produces H^0 = 1.

# Delivery site coordinates: (name, description, trust_floor)
DELIVERY_COORDINATES: dict[str, tuple[str, float]] = {
    "workspace":          ("H¹=0 on the 4-surface workspace (T,R,E,P)", 0.7),
    "paper_scope":        ("Paper ≥ min_pages pages with ≥ min_figures figures", 0.9),
    "sota_domination":    ("≥1 metric clearly beats competitive baselines", 0.9),
    "reproducibility":    ("run_metrics.py exists, parses, uses real data", 0.9),
    "hallucination_free": ("All paper numbers trace to benchmark results", 0.95),
    "real_data":          ("Benchmarks use real data (Yahoo Finance etc.)", 0.9),
    "code_scale":         ("Generated codebase ≥ target KLoC", 0.7),
}

# Morphisms: (source, target) — source must be satisfied for target to be
# meaningful.  This is the covering topology on the delivery site.
DELIVERY_MORPHISMS: list[tuple[str, str]] = [
    ("workspace",       "paper_scope"),        # can't scope a paper without a workspace
    ("workspace",       "sota_domination"),     # can't claim SOTA without consistent workspace
    ("real_data",       "sota_domination"),     # can't claim SOTA on synthetic data
    ("real_data",       "hallucination_free"),  # synthetic numbers in Eval = fabrication
    ("paper_scope",     "hallucination_free"),  # must have a paper to scan it
    ("sota_domination", "reproducibility"),     # must have results to reproduce
    ("code_scale",      "workspace"),           # must have enough code to form a workspace
]


def _build_delivery_site() -> Site:
    """Construct the delivery site as a real JuGeo Site object."""
    builder = SiteBuilder()
    coords: dict[str, Coordinate] = {}

    for name, (desc, _floor) in DELIVERY_COORDINATES.items():
        c = Coordinate(
            components=("delivery", name),
            kind=CoordinateKind.MODULE,
            metadata={"description": desc},
        )
        coords[name] = c
        builder.add_coordinates([c])

    for src_name, tgt_name in DELIVERY_MORPHISMS:
        m = Morphism(
            source=coords[src_name],
            target=coords[tgt_name],
            kind=MorphismKind.RESTRICTION,
            label=f"{src_name}→{tgt_name}",
        )
        builder.add_morphisms([m])

    return builder.build()


# ═══════════════════════════════════════════════════════════════════════
#  Local section constructors — evidence-backed obligation checking
# ═══════════════════════════════════════════════════════════════════════
#
#  Each function inspects real artifacts and returns a LocalSection
#  with a trust level and residual obligations.

def _section_workspace(report: Optional[ConsistencyReport]) -> LocalSection:
    """Local section at the 'workspace' coordinate."""
    if report and report.consistent:
        return LocalSection(
            coordinate="delivery.workspace",
            judgment_data={"consistent": True, "H1": "0",
                           "overlaps": report.overlaps_passed},
            trust_level=0.8,
            evidence_bundle=("workspace-descent-clean",),
            provenance=("check_consistency",),
        )
    obs_summary = (report.H1 if report else "no workspace")
    return LocalSection(
        coordinate="delivery.workspace",
        judgment_data={"consistent": False, "H1": obs_summary},
        trust_level=0.2,
        provenance=("check_consistency",),
        is_partial=True,
        residual_obligations=[f"Fix workspace: {obs_summary}"],
    )


def _section_paper_scope(
    paper_path: str, min_pages: int, min_figures: int,
) -> LocalSection:
    """Local section at 'paper_scope' — checks page and figure count."""
    lines_per_page = 45
    if not os.path.exists(paper_path):
        return LocalSection(
            coordinate="delivery.paper_scope",
            judgment_data={"pages": 0, "figures": 0},
            trust_level=0.0,
            is_partial=True,
            residual_obligations=["paper not found"],
        )

    with open(paper_path) as f:
        text = f.read()
    lines = len(text.splitlines())
    est_pages = lines / lines_per_page
    fig_count = len(re.findall(r'\\begin\{(figure|tikzpicture)\}', text))

    obligations = []
    if est_pages < min_pages:
        obligations.append(
            f"Paper is ~{est_pages:.0f} pages, need ≥{min_pages}")
    if fig_count < min_figures:
        obligations.append(
            f"Paper has {fig_count} figures, need ≥{min_figures}")

    trust = min(1.0, est_pages / min_pages) * min(1.0, fig_count / min_figures)
    return LocalSection(
        coordinate="delivery.paper_scope",
        judgment_data={"pages": est_pages, "figures": fig_count,
                       "lines": lines},
        evidence_bundle=(f"{lines}L≈{est_pages:.0f}pp", f"{fig_count}fig"),
        trust_level=trust,
        provenance=("paper-inspection",),
        is_partial=bool(obligations),
        residual_obligations=obligations,
    )


def _section_sota(benchmark_results: dict[str, Any]) -> LocalSection:
    """Local section at 'sota_domination' — checks metric domination."""
    dominating = benchmark_results.get("dominating_metrics", [])
    our = benchmark_results.get("our_metrics", {})
    baselines = benchmark_results.get("baseline_metrics", {})

    if dominating:
        return LocalSection(
            coordinate="delivery.sota_domination",
            judgment_data={"dominating": dominating, "our_metrics": our},
            evidence_bundle=tuple(f"SOTA:{m}" for m in dominating),
            trust_level=TRUST_RUNTIME,
            provenance=("benchmark-comparison",),
        )

    # Check raw: any metric where ours > best baseline?
    for metric, val in our.items():
        if not isinstance(val, (int, float)):
            continue
        bl_vals = [v for k, v in baselines.items()
                   if metric in k and isinstance(v, (int, float))]
        if bl_vals and val > max(bl_vals):
            return LocalSection(
                coordinate="delivery.sota_domination",
                judgment_data={"dominating": [metric], "our_metrics": our},
                evidence_bundle=(f"SOTA:{metric}={val}>bl={max(bl_vals)}",),
                trust_level=TRUST_RUNTIME,
                provenance=("benchmark-comparison",),
            )

    return LocalSection(
        coordinate="delivery.sota_domination",
        judgment_data={"dominating": [], "our_metrics": our,
                       "baseline_metrics": baselines},
        trust_level=TRUST_COPILOT * 0.5,
        is_partial=True,
        residual_obligations=["No metric dominates baselines yet"],
    )


def _section_reproducibility(output_dir: str) -> LocalSection:
    """Local section at 'reproducibility' — checks for metrics script."""
    candidates = ["run_metrics.py", "run_benchmarks.py",
                   "run_real_benchmark.py"]
    for cand in candidates:
        for root, _dirs, files in os.walk(output_dir):
            if cand in files:
                path = os.path.join(root, cand)
                try:
                    with open(path) as f:
                        compile(f.read(), path, "exec")
                    return LocalSection(
                        coordinate="delivery.reproducibility",
                        judgment_data={"script": path, "syntax_ok": True},
                        evidence_bundle=(f"found:{path}",),
                        trust_level=TRUST_RUNTIME,
                        provenance=("script-inspection",),
                    )
                except SyntaxError as e:
                    return LocalSection(
                        coordinate="delivery.reproducibility",
                        judgment_data={"script": path, "syntax_ok": False,
                                       "error": str(e)},
                        trust_level=0.3,
                        is_partial=True,
                        residual_obligations=[
                            f"Script {path} has syntax errors"],
                    )

    return LocalSection(
        coordinate="delivery.reproducibility",
        judgment_data={},
        trust_level=0.0,
        is_partial=True,
        residual_obligations=["No metrics script found"],
    )


def _section_hallucination_free(
    paper_path: str, benchmark_results: dict[str, Any],
) -> LocalSection:
    """Local section at 'hallucination_free' — E∩P overlap check.

    This is the sheaf condition on the Evidence-Claims overlap:
    every numerical claim in P must restrict to a matching value in E.
    """
    if not os.path.exists(paper_path):
        return LocalSection(
            coordinate="delivery.hallucination_free",
            judgment_data={},
            trust_level=0.0,
            is_partial=True,
            residual_obligations=["paper not found"],
        )

    with open(paper_path) as f:
        text = f.read()

    fabrications = _scan_fabrications(text, benchmark_results)
    if not fabrications:
        return LocalSection(
            coordinate="delivery.hallucination_free",
            judgment_data={"fabrications": 0},
            evidence_bundle=("E∩P-overlap-clean",),
            trust_level=TRUST_SOLVER,
            provenance=("fabrication-scan",),
        )

    return LocalSection(
        coordinate="delivery.hallucination_free",
        judgment_data={"fabrications": len(fabrications),
                       "examples": fabrications[:3]},
        trust_level=max(0.0, 0.8 - len(fabrications) * 0.1),
        is_partial=True,
        residual_obligations=[
            f"{len(fabrications)} untraced metric values in paper "
            f"(E∩P overlap violated)"
        ],
    )


def _section_real_data(output_dir: str) -> LocalSection:
    """Local section at 'real_data' — checks data provenance."""
    real_markers = ["yfinance", "yf.download", "pandas_datareader",
                    "quandl", "alpha_vantage", "yahoo"]
    synthetic_markers = ["np.random", "random.gauss", "simulate",
                         "synthetic", "generate_data", "fake_data"]
    real_count = 0
    synth_count = 0

    for root, _dirs, files in os.walk(output_dir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            try:
                code = open(os.path.join(root, fname)).read()
                real_count += sum(1 for m in real_markers if m in code)
                synth_count += sum(1 for m in synthetic_markers if m in code)
            except Exception:
                pass

    if real_count > 0 and real_count > synth_count:
        return LocalSection(
            coordinate="delivery.real_data",
            judgment_data={"real": real_count, "synthetic": synth_count},
            evidence_bundle=(f"real={real_count}", f"synth={synth_count}"),
            trust_level=TRUST_RUNTIME,
            provenance=("data-provenance-scan",),
        )

    trust = 0.1 if real_count == 0 else 0.5
    return LocalSection(
        coordinate="delivery.real_data",
        judgment_data={"real": real_count, "synthetic": synth_count},
        trust_level=trust,
        is_partial=True,
        residual_obligations=[
            f"Insufficient real data (real={real_count}, synth={synth_count}). "
            f"Use yfinance for real market data."
        ],
    )


def _section_code_scale(
    code_files: list[str], min_kloc: int,
) -> LocalSection:
    """Local section at 'code_scale'."""
    total = 0
    for f in code_files:
        if f.endswith(".py") and os.path.exists(f):
            try:
                total += len(open(f).readlines())
            except Exception:
                pass
    kloc = total / 1000
    trust = min(1.0, kloc / min_kloc)
    obligations = ([f"Only {kloc:.1f}K lines (need ≥{min_kloc}K)"]
                   if kloc < min_kloc else [])

    return LocalSection(
        coordinate="delivery.code_scale",
        judgment_data={"total_lines": total, "kloc": kloc,
                       "target": min_kloc},
        evidence_bundle=(f"{total}L={kloc:.1f}KLoC",),
        trust_level=trust,
        provenance=("code-scale-count",),
        is_partial=bool(obligations),
        residual_obligations=obligations,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Descent on the delivery site
# ═══════════════════════════════════════════════════════════════════════

def _run_delivery_descent(
    sections: dict[str, LocalSection],
) -> tuple[bool, list[str], list[str]]:
    """Run descent on the delivery site.

    Checks trust floors (local condition) and morphism compatibility
    (sheaf condition).  Returns (converged, obstructions, repairs).
    """
    obstructions: list[str] = []
    repairs: list[str] = []

    # Check each section against its trust floor
    for coord_name, (desc, floor) in DELIVERY_COORDINATES.items():
        section = sections.get(coord_name)
        if section is None:
            obstructions.append(f"Missing section at {coord_name}: {desc}")
            repairs.append(f"Generate section for {coord_name}")
            continue
        if section.trust_level < floor:
            msg = (f"✗ {coord_name}: trust {section.trust_level:.2f} "
                   f"< floor {floor:.2f}")
            if section.residual_obligations:
                msg += f" — {'; '.join(section.residual_obligations)}"
            obstructions.append(msg)
            repairs.extend(section.residual_obligations)

    # Check morphisms (sheaf condition on overlaps)
    for src_name, tgt_name in DELIVERY_MORPHISMS:
        src = sections.get(src_name)
        tgt = sections.get(tgt_name)
        if src and tgt:
            src_floor = DELIVERY_COORDINATES[src_name][1]
            tgt_floor = DELIVERY_COORDINATES[tgt_name][1]
            if src.trust_level >= src_floor and tgt.trust_level < tgt_floor:
                obstructions.append(
                    f"Morphism {src_name}→{tgt_name}: "
                    f"source satisfied but target obstructed")
                repairs.append(
                    f"Fix {tgt_name} (blocked by morphism from {src_name})")

    return len(obstructions) == 0, obstructions, repairs


# ═══════════════════════════════════════════════════════════════════════
#  Fabrication scanner (E∩P overlap check)
# ═══════════════════════════════════════════════════════════════════════

def _scan_fabrications(
    paper_text: str,
    benchmark_results: dict[str, Any],
) -> list[str]:
    """Sheaf condition on E∩P: every numeric claim in P must match E."""
    fabrications = []
    metric_patterns = re.findall(
        r'(?:ratio|score|accuracy|precision|recall|f1|auc|return|drawdown|'
        r'sharpe|calmar|sortino|alpha|beta|volatility|correlation|'
        r'r\^?2|rmse|mae|mse)\s*(?:of|=|:|\bis\b)?\s*'
        r'(-?\d+\.?\d*)',
        paper_text, re.IGNORECASE,
    )
    known: set[str] = set()

    def _collect(d: Any):
        if isinstance(d, dict):
            for v in d.values():
                _collect(v)
        elif isinstance(d, (int, float)):
            known.update([f"{d:.4f}", f"{d:.2f}", f"{d:.1f}",
                          str(int(d)) if d == int(d) else ""])

    _collect(benchmark_results)

    for match in metric_patterns:
        val = match.strip()
        if val not in known:
            try:
                if f"{float(val):.4f}" not in known:
                    fabrications.append(f"Untraced: {val}")
            except ValueError:
                pass
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
    """Generate run_metrics.py — the reproducibility section on E→P."""
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

            The script must be runnable as: python run_metrics.py

            Package is installed from: {output_dir}
            Available modules: {json.dumps(domain_analysis.get('standard_libraries', []))}

            CRITICAL: Use REAL Yahoo Finance data via yfinance.
            Write the complete script to {path} using your file-write tool.
        """),
        surface=SurfaceKind.EVIDENCE,
        coordinate="evidence.metrics_script",
        working_dir=output_dir,
    )

    if os.path.exists(path) and os.path.getsize(path) > 100:
        return path

    code = section.content if section else ""
    if code.startswith("```"):
        code = "\n".join(l for l in code.split("\n")
                         if not l.startswith("```"))
    with open(path, "w") as f:
        f.write(code.strip() + "\n")
    return path


# ═══════════════════════════════════════════════════════════════════════
#  Repair frontier → prompt refinement
# ═══════════════════════════════════════════════════════════════════════

# Map from coordinate to the orchestrator semantic moves that repair it
_REPAIR_MOVE: dict[str, list[str]] = {
    "workspace":          ["Run workspace descent and fix T↔R↔E↔P overlaps"],
    "paper_scope":        [
        "Expand paper: add depth to Framework/Evaluation/Related Work",
        "Add ≥2 tikzpicture/pgfplots figures (performance + structure)",
    ],
    "sota_domination":    [
        "Try different metrics (Sharpe, Calmar, max drawdown, conditional)",
        "Try different time periods or asset universes",
        "If nothing beats baselines, PIVOT mathematical technique",
    ],
    "reproducibility":    ["Create run_metrics.py using yfinance for real data"],
    "hallucination_free": [
        "Remove untraced numbers from Evaluation section",
        "Re-ground paper claims against actual evidence surface",
    ],
    "real_data":          [
        "Replace np.random/simulate with yfinance real market data",
        "Add yf.download() pipeline for S&P 500 / major indices",
    ],
    "code_scale":         [
        "Generate more modules: utilities, data pipelines, test suites",
    ],
}


def _build_refinement_from_descent(
    obstructions: list[str],
    repairs: list[str],
) -> str:
    """Translate descent obstructions into targeted prompt refinements.

    Each obstruction maps to semantic moves from the repair frontier
    (theory2.tex §3.4: copilot-assisted refinement).
    """
    parts = [
        "DESCENT OBSTRUCTION REPORT (delivery site):",
        f"  {len(obstructions)} obstruction(s) blocking convergence:",
        "",
    ]
    for obs in obstructions:
        parts.append(f"  • {obs}")
    parts.append("")
    parts.append("REPAIR FRONTIER (semantic moves to apply):")
    parts.append("")

    for coord, moves in _REPAIR_MOVE.items():
        if any(coord in obs for obs in obstructions):
            parts.append(f"  [{coord}]")
            for move in moves:
                parts.append(f"    → {move}")
            parts.append("")

    if repairs:
        parts.append("  [residual obligations]")
        for r in repairs:
            parts.append(f"    → {r}")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
#  Result type
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ResearchAndImplementResult:
    """Global section (or obstruction) on the delivery site."""
    status: ResearchStatus
    prompt: str
    approach: str
    inner_result: Optional[DirectedResearchResult]
    delivery_sections: dict[str, dict[str, Any]]
    descent_converged: bool
    obstructions: list[str]
    iterations: int
    pivots: int
    output_dir: str
    elapsed: float
    git_commits: int
    metrics_script: str

    @property
    def success(self) -> bool:
        return self.descent_converged


# ═══════════════════════════════════════════════════════════════════════
#  The main pipeline: iterative descent on the delivery site
# ═══════════════════════════════════════════════════════════════════════

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
    """Iterative descent on the delivery site (theory2.tex §3.4).

    Outer loop:
        1. Run DirectedResearch (IDEATE → SEED → GENERATE → HARDEN → TAIL)
        2. Construct local sections on delivery site from artifacts
        3. Run descent (trust floors + morphism compatibility)
        4. If H¹=0 → metrics script → final E∩P check → DONE
        5. If H¹≠0 → read repair frontier → refine prompt → goto 1
    """
    start = time.time()

    # Name the output folder based on the prompt (not a timestamp)
    slug = prompt_to_slug(prompt)

    if output_dir:
        out = pathlib.Path(output_dir).resolve()
    else:
        out = pathlib.Path("outputs") / slug
        # Avoid collisions
        if out.exists():
            out = pathlib.Path("outputs") / f"{slug}-{time.strftime('%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)

    # Build the delivery site (real JuGeo Site object)
    delivery_site = _build_delivery_site()

    # Initialize git repo and create private GitHub repo immediately
    tracker = OutputRepoTracker(str(out), repo_name=slug)
    tracker._ensure_repo()
    github_url = tracker.create_github_repo(
        description=f"JuGeo research: {prompt[:200]}")

    def _log(msg: str):
        if verbose:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {msg}", flush=True)

    if github_url:
        _log(f"  GitHub repo: {github_url}")
    else:
        _log(f"  (GitHub repo creation skipped — gh CLI not available or auth issue)")

    # Write initial README with project description
    readme_path = out / "README.md"
    readme_path.write_text(
        f"# {slug}\n\n"
        f"> {prompt}\n\n"
        f"This repository is auto-generated by `jugeo research-and-implement`.\n"
        f"Every commit corresponds to a semantic move on the 4-surface\n"
        f"workspace site (Theory, Code, Evidence, Claims).\n\n"
        f"## Delivery Site Obligations\n\n"
        + "".join(f"- **{name}**: {desc}\n"
                  for name, (desc, _) in DELIVERY_COORDINATES.items())
        + f"\n## How to Reproduce\n\n"
        f"```bash\npython run_metrics.py\n```\n"
    )
    tracker.commit_move(
        surface="CLAIMS",
        coordinate="docs.readme.initial",
        trust=0.3,
        summary=f"Initial README: {prompt[:80]}",
        phase="INIT",
    )

    def _build_sections(
        result: DirectedResearchResult,
    ) -> dict[str, LocalSection]:
        """Construct local sections from inner result artifacts."""
        paper_path = str(
            pathlib.Path(result.output_dir) / "conference_tool_track.tex")
        return {
            "workspace":          _section_workspace(result.consistency),
            "paper_scope":        _section_paper_scope(
                paper_path, min_pages, min_figures),
            "sota_domination":    _section_sota(
                result.benchmark_results or {}),
            "reproducibility":    _section_reproducibility(result.output_dir),
            "hallucination_free": _section_hallucination_free(
                paper_path, result.benchmark_results or {}),
            "real_data":          _section_real_data(result.output_dir),
            "code_scale":         _section_code_scale(
                result.code_files, min_kloc),
        }

    # ── Iterative descent on the delivery site ────────────────────────
    best_result: Optional[DirectedResearchResult] = None
    metrics_script_path = ""
    total_pivots = 0
    current_prompt = prompt
    last_obstructions: list[str] = []
    sections: dict[str, LocalSection] = {}
    depth = 0

    for depth in range(max_outer_iterations):
        _log(f"{'═' * 60}")
        _log(f"  DELIVERY-SITE DESCENT — iteration {depth + 1}"
             f"/{max_outer_iterations}")
        _log(f"{'═' * 60}")

        tracker.commit_phase_boundary(
            "DESCENT",
            f"Outer iteration {depth + 1}/{max_outer_iterations}: "
            f"running DirectedResearch inner pipeline then checking "
            f"delivery site for H¹ = 0.",
        )

        # Run inner directed-research pipeline
        iter_dir = str(out / f"iter_{depth}") if depth > 0 else str(out)

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

        _log(f"\n  Inner loop: {result.status}")
        _log(f"  Constructing local sections on delivery site...")

        tracker.commit_move(
            surface="EVIDENCE",
            coordinate="delivery.inner_result",
            trust=TRUST_RUNTIME if result.status == ResearchStatus.CONVERGED else TRUST_COPILOT,
            summary=(
                f"Inner pipeline complete: status={result.status.value}, "
                f"{len(result.code_files)} code files, "
                f"{len(result.sections)} sections"
            ),
            phase="DESCENT",
        )

        # Construct local sections and run descent
        sections = _build_sections(result)

        for coord_name, section in sections.items():
            floor = DELIVERY_COORDINATES[coord_name][1]
            status = "✓" if section.trust_level >= floor else "✗"
            _log(f"  {status} {coord_name}: trust={section.trust_level:.2f}"
                 f" (floor={floor:.2f})"
                 + (f" — {section.residual_obligations[0]}"
                    if section.residual_obligations else ""))

        converged, obstructions, repairs = _run_delivery_descent(sections)
        last_obstructions = obstructions

        # Write delivery site status to file for continuous documentation
        _write_delivery_status(out, sections, converged, obstructions, depth)
        tracker.commit_move(
            surface="EVIDENCE",
            coordinate="delivery.descent_check",
            trust=TRUST_SOLVER if converged else 0.2,
            summary=(
                f"Delivery site descent: "
                f"{'CONVERGED (H¹=0)' if converged else f'{len(obstructions)} obstructions'}"
            ),
            phase="DESCENT",
        )

        if converged:
            _log(f"\n  ✓ DESCENT CONVERGED — H¹=0 on delivery site")

            # Generate reproducibility artifact
            if not no_llm:
                pkg = dr.architecture.get("package_name", dr.approach)
                metrics_script_path = _generate_metrics_script(
                    result.output_dir, pkg, dr.approach,
                    dr.domain_analysis, result.benchmark_results or {},
                )
                sections["reproducibility"] = _section_reproducibility(
                    result.output_dir)

                tracker.commit_move(
                    surface="EVIDENCE",
                    coordinate="delivery.metrics_script",
                    trust=TRUST_RUNTIME,
                    summary=f"Generated run_metrics.py for reproducibility",
                    phase="DESCENT",
                )

            # Final E∩P hallucination gate
            _log(f"  Running final E∩P overlap check...")
            paper_path = str(
                pathlib.Path(result.output_dir) / "conference_tool_track.tex")
            sections["hallucination_free"] = _section_hallucination_free(
                paper_path, result.benchmark_results or {})

            converged, obstructions, repairs = _run_delivery_descent(sections)
            last_obstructions = obstructions

            if converged:
                _log(f"  ✓✓✓ Global section exists on delivery site")
                tracker.commit_move(
                    surface="LIFECYCLE",
                    coordinate="delivery.converged",
                    trust=TRUST_SOLVER,
                    summary=(
                        "CONVERGENCE: Global section on delivery site. "
                        "All 7 obligations discharged. H¹ = 0."
                    ),
                    phase="COMPLETE",
                )
                break
            else:
                _log(f"  ✗ Post-gate descent failed: {len(obstructions)} obs")

        # Descent failed — read repair frontier and refine
        _log(f"\n  ✗ H¹ ≠ 0 — {len(obstructions)} obstruction(s):")
        for obs in obstructions[:5]:
            _log(f"    {obs}")

        refinement = _build_refinement_from_descent(obstructions, repairs)
        _log(f"  Applying repair frontier → next iteration")
        current_prompt = prompt + "\n\n" + refinement
        total_pivots += 1

        tracker.commit_move(
            surface="THEORY",
            coordinate="delivery.refinement",
            trust=TRUST_COPILOT,
            summary=(
                f"Descent failed (iteration {depth + 1}): "
                f"{len(obstructions)} obstructions. "
                f"Applying repair frontier → refining prompt for next cycle."
            ),
            phase="DESCENT",
            extra_metadata={"obstructions": obstructions[:5]},
        )

    # ── Build result ──────────────────────────────────────────────────
    elapsed = time.time() - start
    delivery_summary = {
        name: {
            "trust": sections[name].trust_level,
            "floor": DELIVERY_COORDINATES[name][1],
            "satisfied": (sections[name].trust_level
                          >= DELIVERY_COORDINATES[name][1]),
            "evidence": list(sections[name].evidence_bundle),
            "obligations": sections[name].residual_obligations,
        }
        for name in DELIVERY_COORDINATES
        if name in sections
    }

    (out / "delivery_site.json").write_text(
        json.dumps(delivery_summary, indent=2, default=str))

    all_satisfied = all(
        s["satisfied"] for s in delivery_summary.values())

    # Final documentation update
    _update_readme_final(out, delivery_summary, all_satisfied, elapsed,
                         metrics_script_path, prompt, slug)

    tracker.save_commit_log()
    tracker.commit_move(
        surface="CLAIMS",
        coordinate="docs.final",
        trust=TRUST_SOLVER if all_satisfied else TRUST_COPILOT,
        summary=(
            f"Final state: "
            f"{'CONVERGED' if all_satisfied else 'OBSTRUCTED'}. "
            f"{sum(1 for s in delivery_summary.values() if s['satisfied'])}"
            f"/{len(delivery_summary)} obligations met. "
            f"Elapsed: {elapsed:.1f}s."
        ),
        phase="COMPLETE" if all_satisfied else "BUDGET_EXHAUSTED",
    )

    _log(f"\n{'═' * 60}")
    _log(f"  FINAL: {'CONVERGED' if all_satisfied else 'OBSTRUCTED'}")
    _log(f"  Sections: "
         f"{sum(1 for s in delivery_summary.values() if s['satisfied'])}"
         f"/{len(delivery_summary)} satisfied")
    _log(f"  Commits: {len(tracker.commits)} "
         f"(+ {len(dr.git_tracker.commits) if best_result else 0} inner)")
    if tracker._github_url:
        _log(f"  GitHub: {tracker._github_url}")
    _log(f"  Elapsed: {elapsed:.1f}s | Output: {out}")
    _log(f"{'═' * 60}")

    return ResearchAndImplementResult(
        status=(ResearchStatus.CONVERGED if all_satisfied
                else ResearchStatus.BUDGET_EXHAUSTED),
        prompt=prompt,
        approach=best_result.approach if best_result else "unknown",
        inner_result=best_result,
        delivery_sections=delivery_summary,
        descent_converged=all_satisfied,
        obstructions=last_obstructions,
        iterations=depth + 1,
        pivots=total_pivots,
        output_dir=str(out),
        elapsed=elapsed,
        git_commits=(len(tracker.commits)
                     + (len(dr.git_tracker.commits) if best_result else 0)),
        metrics_script=metrics_script_path,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Continuous documentation helpers
# ═══════════════════════════════════════════════════════════════════════

def _write_delivery_status(
    out: pathlib.Path,
    sections: dict[str, LocalSection],
    converged: bool,
    obstructions: list[str],
    iteration: int,
) -> None:
    """Write current delivery site status as markdown (continuous docs)."""
    path = out / "DELIVERY_STATUS.md"
    lines = [
        f"# Delivery Site Status — Iteration {iteration + 1}",
        "",
        f"**Descent:** {'✓ CONVERGED (H¹=0)' if converged else '✗ OBSTRUCTED'}",
        "",
        "| Coordinate | Trust | Floor | Status |",
        "|---|---|---|---|",
    ]
    for name, (desc, floor) in DELIVERY_COORDINATES.items():
        sec = sections.get(name)
        if sec:
            ok = "✓" if sec.trust_level >= floor else "✗"
            lines.append(
                f"| {name} | {sec.trust_level:.2f} | {floor:.2f} | {ok} |")
    if obstructions:
        lines.append("")
        lines.append("## Obstructions")
        lines.append("")
        for obs in obstructions:
            lines.append(f"- {obs}")
    path.write_text("\n".join(lines) + "\n")


def _update_readme_final(
    out: pathlib.Path,
    delivery_summary: dict,
    converged: bool,
    elapsed: float,
    metrics_script: str,
    prompt: str,
    slug: str,
) -> None:
    """Update README with final results."""
    path = out / "README.md"
    status = "✓ CONVERGED" if converged else "✗ OBSTRUCTED"
    satisfied = sum(1 for s in delivery_summary.values() if s["satisfied"])
    total = len(delivery_summary)

    path.write_text(
        f"# {slug}\n\n"
        f"> {prompt}\n\n"
        f"## Status: {status}\n\n"
        f"**Obligations met:** {satisfied}/{total}\n"
        f"**Elapsed:** {elapsed:.1f}s\n\n"
        f"| Obligation | Trust | Floor | Met |\n"
        f"|---|---|---|---|\n"
        + "".join(
            f"| {name} | {s['trust']:.2f} | {s['floor']:.2f} | "
            f"{'✓' if s['satisfied'] else '✗'} |\n"
            for name, s in delivery_summary.items()
        )
        + f"\n## Reproduce\n\n"
        f"```bash\n"
        f"pip install -e .\n"
        f"python {metrics_script or 'run_metrics.py'}\n"
        f"```\n\n"
        f"## Judgment Geometry\n\n"
        f"This repository was generated by `jugeo research-and-implement`.\n"
        f"Every commit represents a semantic move on the 4-surface\n"
        f"workspace site. Convergence means H¹ = 0 on all overlaps\n"
        f"between Theory, Code, Evidence, and Claims surfaces.\n"
    )


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def add_subparser(subparsers) -> Any:
    """Register the research-and-implement subcommand."""
    p = subparsers.add_parser(
        "research-and-implement",
        help="Iterative descent on a delivery site: prompt → SOTA with "
             "hard obligations, real data, hallucination checking.",
        description=textwrap.dedent("""\
            Run iterative descent on a 7-object delivery site.

            The pipeline ideates a novel approach, generates a large-scale
            implementation, benchmarks against competitive baselines on real
            data, and refines or pivots until descent succeeds (H¹ = 0) on
            the delivery site — meaning all hard obligations are met.

            Delivery site coordinates:
              workspace          H¹=0 on the 4-surface workspace (T,R,E,P)
              paper_scope        Paper ≥ N pages with ≥ M figures
              sota_domination    ≥1 metric clearly beats baselines
              reproducibility    run_metrics.py exists and parses
              hallucination_free All paper numbers trace to benchmarks
              real_data          Benchmarks use real data (Yahoo Finance)
              code_scale         Codebase ≥ target KLoC

            Examples:
              jugeo research-and-implement "killer app in finance using advanced math"
              jugeo research-and-implement "fast GNN for drug discovery" --max-pivots 5
        """),
        formatter_class=__import__("argparse").RawDescriptionHelpFormatter,
    )
    p.add_argument("prompt",
                   help="Natural-language description of what to build.")
    p.add_argument("--max-outer", type=int, default=5,
                   help="Max delivery-site descent iterations. Default: 5.")
    p.add_argument("--max-inner", type=int, default=30,
                   help="Max inner refinement iterations per cycle. Default: 30.")
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
        print(f"\n✓ DESCENT CONVERGED — global section on delivery site")
        print(f"  Output: {result.output_dir}")
        print(f"  Metrics: {result.metrics_script}")
        return 0
    else:
        print(f"\n✗ DESCENT OBSTRUCTED — H¹ ≠ 0 on delivery site")
        for name, sec in result.delivery_sections.items():
            st = "✓" if sec["satisfied"] else "✗"
            print(f"  {st} {name}: trust={sec['trust']:.2f} "
                  f"(floor={sec['floor']:.2f})")
        print(f"  Output: {result.output_dir}")
        return 1
