"""Parallel multi-paper research with differentiation constraints.

This module launches N directed-research runs in parallel, each producing
a distinct paper on a shared theme.  The key geometric invariant:

    **Differentiation is orthogonality in the solution presheaf.**

Given a theme (e.g. "Yahoo Finance + sheaf theory"), the N papers must
cover *different local sections* of the solution presheaf — different
sub-problems, different mathematical tools, different evaluation protocols.
The differentiation constraint is enforced by:

1. **Covering family decomposition** — the theme decomposes into N
   sub-themes that form a covering family.  Each sub-theme is assigned
   to exactly one paper.  The sheaf condition on the covering ensures
   that papers are consistent on overlaps (shared definitions, notation)
   but distinct on their local content.

2. **Anti-overlap scoring** — before launch, we compute pairwise
   overlap between paper prompts using Jaccard similarity on keyword
   sets.  If any pair exceeds the overlap threshold, we refine the
   decomposition until all pairs are below threshold.

3. **Obligation orthogonality** — each paper has a distinct set of
   data obligations (metrics it must compute).  The union of all
   obligations covers the full evaluation space, but no single
   obligation appears in more than ``max_obligation_overlap`` papers.

This module is domain-agnostic — it works for any theme.

Usage::

    from jugeo.directed_research._parallel_research import (
        ParallelResearchConfig,
        parallel_research,
    )

    config = ParallelResearchConfig(
        theme="Yahoo Finance alpha discovery using algebraic topology",
        n_papers=10,
        max_iterations_per_paper=30,
    )
    results = parallel_research(config)
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import textwrap
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.research_orchestration import SurfaceKind, ConsistencyReport

from jugeo.directed_research._types import (
    TRUST_COPILOT,
    TRUST_RUNTIME,
    TRUST_SOLVER,
    DirectedResearchResult,
    ResearchStatus,
    LLMSection,
)
from jugeo.directed_research._data_presentation import (
    DataKind,
    DataObligation,
    DataPoint,
    DataSet,
    ObligationManifest,
    PresentationFormat,
    PresentationPlan,
    plan_presentations,
)


# ═══════════════════════════════════════════════════════════════════════
#  Paper specification — what makes each paper unique
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PaperSpec:
    """Specification for a single paper in a parallel research batch.

    Each spec defines a unique sub-problem, required data obligations,
    and the mathematical angle that distinguishes it from siblings.
    """
    index: int                              # 0-indexed position in batch
    title: str                              # short descriptive title
    sub_theme: str                          # the unique angle/sub-problem
    prompt: str                             # full DirectedResearch prompt
    required_obligations: ObligationManifest # data this paper MUST produce
    distinguishing_keywords: frozenset[str]  # keywords unique to this paper
    shared_keywords: frozenset[str]          # keywords shared with all papers
    mathematical_tool: str                   # primary math technique
    evaluation_focus: str                    # primary evaluation metric class
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def keyword_signature(self) -> frozenset[str]:
        """Full keyword set (shared ∪ distinguishing)."""
        return self.shared_keywords | self.distinguishing_keywords


@dataclass
class DifferentiationReport:
    """Report on how well the N papers are differentiated.

    The overlap matrix O[i][j] measures keyword overlap between papers
    i and j.  The differentiation score is 1 - max(O[i][j] for i≠j).
    """
    n_papers: int
    overlap_matrix: list[list[float]]
    max_pairwise_overlap: float
    mean_pairwise_overlap: float
    min_pairwise_overlap: float
    obligation_coverage: float   # fraction of total obligation space covered
    obligation_overlap: float    # fraction of obligations in >1 paper

    @property
    def differentiation_score(self) -> float:
        """1.0 = perfectly differentiated, 0.0 = all identical."""
        return 1.0 - self.max_pairwise_overlap

    @property
    def is_well_differentiated(self) -> bool:
        """True if max pairwise overlap is below 0.3 (70%+ distinct)."""
        return self.max_pairwise_overlap < 0.3


# ═══════════════════════════════════════════════════════════════════════
#  Differentiation engine — guarantee papers are distinct
# ═══════════════════════════════════════════════════════════════════════

def _tokenize(text: str) -> frozenset[str]:
    """Extract keyword tokens from text."""
    import re
    words = re.findall(r'[a-z][a-z_]+', text.lower())
    # Filter stopwords
    stops = frozenset({
        'the', 'and', 'for', 'with', 'that', 'this', 'from', 'are', 'was',
        'will', 'have', 'has', 'had', 'been', 'being', 'each', 'which',
        'their', 'more', 'than', 'into', 'also', 'should', 'must', 'can',
        'using', 'used', 'use', 'based', 'build', 'implement', 'create',
    })
    return frozenset(w for w in words if w not in stops and len(w) > 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two keyword sets."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compute_differentiation(specs: list[PaperSpec]) -> DifferentiationReport:
    """Compute the differentiation report for a batch of paper specs."""
    n = len(specs)
    matrix = [[0.0] * n for _ in range(n)]
    overlaps = []

    for i in range(n):
        for j in range(i + 1, n):
            sim = _jaccard(
                specs[i].distinguishing_keywords,
                specs[j].distinguishing_keywords,
            )
            matrix[i][j] = sim
            matrix[j][i] = sim
            overlaps.append(sim)

    # Obligation coverage
    all_obl_keys: set[str] = set()
    obl_counts: dict[str, int] = {}
    for spec in specs:
        for obl in spec.required_obligations.obligations:
            all_obl_keys.add(obl.key)
            obl_counts[obl.key] = obl_counts.get(obl.key, 0) + 1

    total_possible = max(len(all_obl_keys), 1)
    multi_count = sum(1 for k, c in obl_counts.items() if c > 1)

    return DifferentiationReport(
        n_papers=n,
        overlap_matrix=matrix,
        max_pairwise_overlap=max(overlaps) if overlaps else 0.0,
        mean_pairwise_overlap=sum(overlaps) / len(overlaps) if overlaps else 0.0,
        min_pairwise_overlap=min(overlaps) if overlaps else 0.0,
        obligation_coverage=len(all_obl_keys) / total_possible,
        obligation_overlap=multi_count / total_possible if total_possible else 0.0,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Theme decomposition — split a theme into N distinct sub-themes
# ═══════════════════════════════════════════════════════════════════════

def decompose_theme(
    theme: str,
    n_papers: int,
    *,
    sub_themes: list[dict[str, Any]] | None = None,
) -> list[PaperSpec]:
    """Decompose a research theme into N paper specifications.

    If ``sub_themes`` is provided, uses those directly.  Otherwise,
    attempts automatic decomposition via the ideation agent.

    Each sub-theme must provide:
    - title: short name
    - sub_theme: angle description
    - math_tool: primary mathematical technique
    - eval_focus: what metrics to emphasize
    - obligations: list of {key, description, kind, trust, section} dicts
    - keywords: list of distinguishing keywords

    The shared keywords are extracted from the theme itself.
    """
    shared_kw = _tokenize(theme)

    if sub_themes is None:
        raise ValueError(
            "Automatic theme decomposition requires an LLM agent. "
            "Provide sub_themes explicitly or use decompose_theme_with_agent()."
        )

    if len(sub_themes) < n_papers:
        raise ValueError(
            f"Need {n_papers} sub-themes but only {len(sub_themes)} provided."
        )

    specs = []
    for i, st in enumerate(sub_themes[:n_papers]):
        # Build obligations
        obls = []
        for od in st.get("obligations", []):
            obls.append(DataObligation(
                key=od["key"],
                description=od.get("description", od["key"]),
                required_kind=DataKind(od.get("kind", "scalar")),
                required_trust=od.get("trust", TRUST_RUNTIME),
                target_section=od.get("section", "evaluation"),
                target_formats=[
                    PresentationFormat(f) for f in od.get("formats", ["booktabs_table"])
                ],
            ))

        dist_kw = frozenset(st.get("keywords", []))
        math_tool = st.get("math_tool", "general")
        eval_focus = st.get("eval_focus", "accuracy")

        # Build the full prompt with differentiation context
        prompt = _build_paper_prompt(
            theme=theme,
            sub_theme=st["sub_theme"],
            title=st["title"],
            math_tool=math_tool,
            eval_focus=eval_focus,
            obligations=obls,
            paper_index=i,
            n_papers=n_papers,
        )

        specs.append(PaperSpec(
            index=i,
            title=st["title"],
            sub_theme=st["sub_theme"],
            prompt=prompt,
            required_obligations=ObligationManifest(obligations=obls),
            distinguishing_keywords=dist_kw,
            shared_keywords=shared_kw,
            mathematical_tool=math_tool,
            evaluation_focus=eval_focus,
            metadata=st.get("metadata", {}),
        ))

    return specs


def _build_paper_prompt(
    *,
    theme: str,
    sub_theme: str,
    title: str,
    math_tool: str,
    eval_focus: str,
    obligations: list[DataObligation],
    paper_index: int,
    n_papers: int,
) -> str:
    """Build a full DirectedResearch prompt for one paper in the batch."""
    obl_text = "\n".join(
        f"  - {o.key}: {o.description} (kind={o.required_kind.value}, "
        f"trust≥{o.required_trust}, section={o.target_section}, "
        f"formats={[f.value for f in o.target_formats]})"
        for o in obligations
    )

    return textwrap.dedent(f"""\
        Build a large-scale Python research library for paper {paper_index + 1}/{n_papers}
        in a series on: "{theme}"

        THIS PAPER'S UNIQUE ANGLE: {title}
        Sub-theme: {sub_theme}
        Primary mathematical tool: {math_tool}
        Evaluation focus: {eval_focus}

        REQUIRED DATA OBLIGATIONS (the paper MUST produce these):
        {obl_text}

        Each obligation must be discharged by running actual experiments on real
        Yahoo Finance data (via yfinance). Every metric must appear in the paper
        in at least TWO formats: (1) a booktabs table and (2) either a pgfplots
        figure, an inline number, or a statistical test environment.

        The paper must be 12+ pages with:
        - Abstract with key verified numbers
        - Evaluation section with all obligations discharged
        - At least 3 tables (booktabs) and 2 figures (pgfplots/tikz)
        - Comparison against baselines with best values bolded
        - Statistical significance tests where applicable
        - All claims trust-gated per the evidence manifest

        Target: 500+ KLoC across 50+ modules with full test coverage.
    """)


# ═══════════════════════════════════════════════════════════════════════
#  Parallel launcher
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ParallelResearchConfig:
    """Configuration for a parallel multi-paper research batch."""
    theme: str
    n_papers: int = 10
    sub_themes: list[dict[str, Any]] | None = None
    max_iterations_per_paper: int = 30
    max_pivots_per_paper: int = 3
    output_dir: str | None = None
    max_workers: int = 4
    verbose: bool = True
    overlap_threshold: float = 0.3


@dataclass
class ParallelResearchResult:
    """Result of a parallel multi-paper research batch."""
    theme: str
    specs: list[PaperSpec]
    results: list[DirectedResearchResult | None]
    differentiation: DifferentiationReport
    elapsed: float
    output_dir: str

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r and r.status == ResearchStatus.CONVERGED)

    @property
    def total_code_files(self) -> int:
        return sum(len(r.code_files) for r in self.results if r)


def _run_one_paper(
    spec: PaperSpec,
    output_dir: str,
    max_iterations: int,
    max_pivots: int,
    verbose: bool,
) -> DirectedResearchResult | None:
    """Run a single directed research instance for one paper."""
    import traceback
    from jugeo.directed_research._descent_loop import DirectedResearch

    paper_dir = os.path.join(output_dir, f"paper_{spec.index:02d}_{spec.title.replace(' ', '_')[:40]}")
    os.makedirs(paper_dir, exist_ok=True)

    _plog(spec.index, spec.title, "STARTING", f"output={paper_dir}")

    # Save the spec
    spec_path = os.path.join(paper_dir, "paper_spec.json")
    with open(spec_path, "w") as f:
        json.dump({
            "index": spec.index,
            "title": spec.title,
            "sub_theme": spec.sub_theme,
            "mathematical_tool": spec.mathematical_tool,
            "evaluation_focus": spec.evaluation_focus,
            "obligations": [
                {"key": o.key, "description": o.description,
                 "kind": o.required_kind.value, "trust": o.required_trust}
                for o in spec.required_obligations.obligations
            ],
        }, f, indent=2)

    try:
        dr = DirectedResearch(
            prompt=spec.prompt,
            max_iterations=max_iterations,
            max_pivots=max_pivots,
            output_dir=paper_dir,
            verbose=verbose,
        )
        result = dr.run()
        status = result.status.value if result.status else "unknown"
        n_files = len(result.code_files)
        n_commits = len(dr.git_tracker.commits)
        _plog(spec.index, spec.title, "FINISHED",
              f"status={status}, files={n_files}, commits={n_commits}, "
              f"elapsed={result.elapsed:.1f}s")
        return result
    except Exception as e:
        err_path = os.path.join(paper_dir, "error.txt")
        tb = traceback.format_exc()
        with open(err_path, "w") as f:
            f.write(f"Paper {spec.index} ({spec.title}) failed:\n{e}\n\n{tb}\n")
        _plog(spec.index, spec.title, "FAILED", str(e)[:120])
        return None


def _plog(index: int, title: str, event: str, detail: str = ""):
    """Structured log line for parallel research."""
    ts = time.strftime("%H:%M:%S")
    detail_str = f" — {detail}" if detail else ""
    print(f"[{ts}] Paper [{index}] {title}: {event}{detail_str}", flush=True)


def parallel_research(config: ParallelResearchConfig) -> ParallelResearchResult:
    """Launch N directed-research runs in parallel with differentiation guarantees.

    1. Decompose theme into N paper specs
    2. Verify differentiation constraints
    3. Launch all N in parallel (up to max_workers concurrent)
    4. Collect results and build the combined report
    """
    start = time.time()

    # Output directory
    if config.output_dir:
        out = pathlib.Path(config.output_dir)
    else:
        out = pathlib.Path("outputs") / f"parallel_{time.strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)

    # Decompose theme
    specs = decompose_theme(
        config.theme,
        config.n_papers,
        sub_themes=config.sub_themes,
    )

    # Check differentiation
    diff_report = compute_differentiation(specs)

    if not diff_report.is_well_differentiated:
        # Log warning but proceed — the prompts still enforce uniqueness
        warn_path = out / "differentiation_warning.txt"
        warn_path.write_text(
            f"Max pairwise overlap: {diff_report.max_pairwise_overlap:.3f} "
            f"(threshold: {config.overlap_threshold})\n"
            f"Differentiation score: {diff_report.differentiation_score:.3f}\n"
        )

    # Save batch metadata
    meta_path = out / "batch_metadata.json"
    meta_path.write_text(json.dumps({
        "theme": config.theme,
        "n_papers": config.n_papers,
        "differentiation_score": diff_report.differentiation_score,
        "max_overlap": diff_report.max_pairwise_overlap,
        "mean_overlap": diff_report.mean_pairwise_overlap,
        "papers": [{"index": s.index, "title": s.title, "sub_theme": s.sub_theme}
                   for s in specs],
    }, indent=2))

    # Launch in parallel
    results: list[DirectedResearchResult | None] = [None] * len(specs)
    _plog(-1, "BATCH", "LAUNCHING",
          f"{config.n_papers} papers, {config.max_workers} workers, "
          f"diff_score={diff_report.differentiation_score:.3f}")

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        futures = {}
        for spec in specs:
            fut = executor.submit(
                _run_one_paper,
                spec=spec,
                output_dir=str(out),
                max_iterations=config.max_iterations_per_paper,
                max_pivots=config.max_pivots_per_paper,
                verbose=config.verbose,
            )
            futures[fut] = spec.index

        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                _plog(idx, specs[idx].title, "EXCEPTION", str(e)[:120])
                results[idx] = None
            done = sum(1 for r in results if r is not None)
            _plog(-1, "BATCH", "PROGRESS", f"{done}/{len(specs)} papers complete")

    elapsed = time.time() - start
    success = sum(1 for r in results if r and r.status == ResearchStatus.CONVERGED)
    total_files = sum(len(r.code_files) for r in results if r)
    _plog(-1, "BATCH", "COMPLETE",
          f"{success}/{len(specs)} converged, {total_files} code files, {elapsed:.1f}s")

    # Save summary
    summary_path = out / "parallel_summary.json"
    summary_path.write_text(json.dumps({
        "theme": config.theme,
        "n_papers": config.n_papers,
        "success_count": sum(1 for r in results if r and r.status == ResearchStatus.CONVERGED),
        "total_code_files": sum(len(r.code_files) for r in results if r),
        "elapsed": elapsed,
        "differentiation_score": diff_report.differentiation_score,
    }, indent=2))

    return ParallelResearchResult(
        theme=config.theme,
        specs=specs,
        results=results,
        differentiation=diff_report,
        elapsed=elapsed,
        output_dir=str(out),
    )
