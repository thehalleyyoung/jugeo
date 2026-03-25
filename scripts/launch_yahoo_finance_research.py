#!/usr/bin/env python3
"""Launch 10 parallel Yahoo Finance research papers via jugeo.

Each paper uses a different mathematical tool on a different sub-problem,
with guaranteed differentiation (orthogonality in the solution presheaf)
and multi-format data presentation (booktabs + pgfplots + statistical
tests + inline numbers) in the LaTeX output.

~95 lines excluding imports.  The heavy lifting is all jugeo.
"""
from __future__ import annotations

# ── jugeo core geometry ──────────────────────────────────────────────
from jugeo.geometry.site import Coordinate, CoordinateKind, Morphism, MorphismKind, Site, SiteBuilder
from jugeo.geometry.descent import DescentEngine, DescentStrategy, LocalSection, GlobalSection, DescentObstruction, GluingData, CohomologyClass
from jugeo.judgments.judgment_terms import Judgment, JudgmentBuilder, JudgmentAlgebra, JudgmentStatus
from jugeo.judgments.sections import JudgmentSection
from jugeo.judgments.comparisons import JudgmentComparator, JudgmentOrder
from jugeo.evidence.trust import TrustLevel, TrustProfile
from jugeo.sheaf_types import Sheaf

# ── jugeo research orchestration ─────────────────────────────────────
from jugeo.research_orchestration import WorkspaceSite, ArtifactSurface, SurfaceKind, ResearchOrchestrator, ResearchStrategy, WorkspaceSection, ConsistencyReport, ObstructionKind, MorphismType, MoveKind as OrcMoveKind

# ── jugeo directed research — types, loop, all subsystems ────────────
from jugeo.directed_research import DirectedResearch, DirectedResearchResult, ResearchStatus, LLMSection, MoveKind, MoveResult, BridgeProposition, UsefulNoveltyScore, DomainSite, MethodologicalTranslation, IdeationResult, ExcessNoveltyFraction, ProductivePairingCriterion, RelevanceFiltrationLevel
from jugeo.directed_research._types import TRUST_COPILOT, TRUST_RUNTIME, TRUST_SOLVER, AgentBackend
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
    ensure_dependencies,
    read_dependencies_from_pyproject,
)
from jugeo.directed_research._benchmarking import MetricFrontier, ConvergenceCriterion, establish_baselines, run_benchmarks, update_frontier_with_results
from jugeo.directed_research._verification import verify_all_code_files, verify_readme, verify_paper
from jugeo.directed_research._paper_gen import generate_paper, EvidenceManifest, build_evidence_manifest
from jugeo.directed_research._readme_gen import generate_readme
from jugeo.directed_research._pivot import pivot_theory
from jugeo.directed_research._workspace import build_workspace
from jugeo.directed_research._provenance import ResearchProvenance
from jugeo.directed_research._agent_channel import agent_call, agent_json
from jugeo.directed_research._consistency import full_consistency_check
from jugeo.directed_research._novelty import compute_enf, is_novel
from jugeo.directed_research._cech_complex import compute_overlap_count
from jugeo.directed_research._solution_presheaf import usefulness_pairing
from jugeo.directed_research._morphism_discovery import morphism_strength_to_kind
from jugeo.directed_research._graded_usefulness import graded_uns
from jugeo.directed_research._problem_functor import localize_search_space
from jugeo.directed_research._tournament import tournament_select

# ── jugeo directed research — NEW: data presentation + parallel ──────
from jugeo.directed_research._data_presentation import DataKind, DataPoint, DataSet, DataObligation, ObligationManifest, PresentationFormat, PresentationPlan, PresentationSlot, plan_presentations, render_booktabs_table, render_comparison_table, render_pgfplots_line, render_pgfplots_bar, render_statistical_test, render_inline_number
from jugeo.directed_research._parallel_research import PaperSpec, DifferentiationReport, ParallelResearchConfig, ParallelResearchResult, decompose_theme, compute_differentiation, parallel_research
from jugeo.directed_research._git_tracking import OutputRepoTracker

# ── jugeo large-scale orchestration ──────────────────────────────────
from jugeo.orchestration.large_scale import CoEvolutionEngine, ObligationManager, PhaseDetector, FleetManager, ConvergenceMonitor, BudgetAllocator
from jugeo.orchestration.large_scale.large_repo import LargeRepoOptimizer
from jugeo.orchestration.large_scale.models import Surface, SurfaceState, DriftEdge, CoEvolutionState, ObligationKind as LSObligationKind, TypedObligation, ObligationPresheaf, Phase, PhaseSignal, PhaseTransition, Strategy, Bid, FleetResult, ConvergenceCriterion as LSConvergenceCriterion, ConvergenceCertificate, MoveCategory, SemanticMove, MoveResult as LSMoveResult, MoveHistory, BudgetAllocation, BudgetUsage

# ── jugeo ideation / encodings / solver ──────────────────────────────
from jugeo.ideation.ideas import IdeaGenerator
from jugeo.ideation.novelty import NoveltySearcher, NoveltyScore, NoveltyMetric
from jugeo.ideation.federation import IdeationFederator, FederationRegistry, FederationHistory
from jugeo.ideation.regimes import IdeationRegime
from jugeo.ideation.scheduling import IdeationScheduler
from jugeo.encodings.scalar_encodings.refinement_type_encoder import RefinementTypeEncoder
from jugeo.encodings.scalar_encodings.path_condition_encoder import PathConditionEncoder
from jugeo.encodings.scalar_encodings.failure_artifact_encoder import FailureArtifactEncoder
from jugeo.solver.router import BackendKind, RoutingDecision, RouterConfiguration
from jugeo.solver.z3_session import Z3Session

# ── jugeo easy API ───────────────────────────────────────────────────
from jugeo.easy import prove, bugs, equiv, ideate, carry, spec, research

# ── stdlib ───────────────────────────────────────────────────────────
import json, pathlib, time

# ═════════════════════════════════════════════════════════════════════
#  THEME + 10 PAPER SPECIFICATIONS (each guaranteed distinct)
# ═════════════════════════════════════════════════════════════════════
THEME = "Sheaf-theoretic alpha discovery and risk management from Yahoo Finance data"

def _obl(key, desc, kind="scalar", trust=TRUST_RUNTIME, section="evaluation",
         fmts=("booktabs_table", "pgfplots_bar", "inline_number")):
    return {"key": key, "description": desc, "kind": kind, "trust": trust,
            "section": section, "formats": list(fmts)}

SUB_THEMES = [
  { "title": "Pricing Sheaf Cohomology",
    "sub_theme": "Model the S&P 500 as a Grothendieck site; compute H^1 of the pricing presheaf; show nonzero classes correspond to stat-arb opportunities",
    "math_tool": "Cech cohomology of presheaves on finite sites",
    "eval_focus": "annualized Sharpe ratio of H^1-derived signals",
    "keywords": ["cech_cohomology", "pricing_presheaf", "stat_arb", "coboundary", "cocycle"],
    "obligations": [_obl("sharpe_h1", "Sharpe ratio of H^1 signal portfolio"),
                    _obl("h1_dim", "Dimension of H^1 over rolling windows", kind="time_series", fmts=("pgfplots_line", "booktabs_table", "narrative")),
                    _obl("alpha_ttest", "t-test for alpha significance", kind="statistical_test", trust=TRUST_SOLVER, fmts=("theorem_environ", "booktabs_table", "inline_number")),
                    _obl("sharpe_baseline", "Sharpe ratio of equal-weight baseline")] },
  { "title": "Persistent Homology Momentum",
    "sub_theme": "Apply persistent homology to correlation filtrations; extract momentum signals from long-lived H^1 classes",
    "math_tool": "persistent homology via Rips/Vietoris filtrations",
    "eval_focus": "cumulative returns and Calmar ratio",
    "keywords": ["persistent_homology", "barcodes", "rips_filtration", "topological_momentum", "birth_death"],
    "obligations": [_obl("calmar_ratio", "Calmar ratio of persistence-momentum strategy"),
                    _obl("barcode_lengths", "Distribution of H^1 barcode lifetimes", kind="distribution", fmts=("histogram", "booktabs_table", "narrative")),
                    _obl("cum_return", "Cumulative return vs baseline", kind="time_series", fmts=("pgfplots_line", "inline_number", "narrative")),
                    _obl("turnover", "Annual portfolio turnover")] },
  { "title": "Sheaf Laplacian Clustering",
    "sub_theme": "Use the sheaf Laplacian to cluster assets into natural sectors; detect regime changes when eigenvalues cross",
    "math_tool": "spectral theory of cellular sheaves on graphs",
    "eval_focus": "adjusted Rand index vs GICS sectors; regime detection F1",
    "keywords": ["sheaf_laplacian", "spectral_clustering", "eigenvalue_crossing", "cellular_sheaf", "hodge"],
    "obligations": [_obl("ari_gics", "Adjusted Rand Index vs GICS classification"),
                    _obl("eigenvalue_gaps", "Top-5 sheaf Laplacian eigenvalue gaps over time", kind="time_series", fmts=("pgfplots_line", "booktabs_table", "narrative")),
                    _obl("regime_f1", "Regime detection F1 score"),
                    _obl("n_clusters", "Number of detected clusters vs GICS sectors", kind="comparison", fmts=("booktabs_table", "pgfplots_bar", "narrative"))] },
  { "title": "Trust-Weighted Portfolio Construction",
    "sub_theme": "Use jugeo's trust algebra to weight portfolio positions: Z3-verified cointegration → SOLVER trust, ADF tests → RUNTIME, pattern-match → COPILOT",
    "math_tool": "ordered algebra of trust levels with attenuation/promotion",
    "eval_focus": "max drawdown improvement over equal-trust baseline",
    "keywords": ["trust_algebra", "attenuation", "promotion", "z3_cointegration", "weighted_allocation"],
    "obligations": [_obl("max_dd_trust", "Max drawdown of trust-weighted portfolio"),
                    _obl("max_dd_equal", "Max drawdown of equal-weight portfolio"),
                    _obl("trust_distribution", "Distribution of signal trust levels", kind="distribution", fmts=("histogram", "pgfplots_bar", "booktabs_table")),
                    _obl("coint_count", "Number of Z3-verified cointegration pairs")] },
  { "title": "Descent-Driven Risk Decomposition",
    "sub_theme": "Model risk via a dual sheaf; factor risk = covering family structure; tail risk = H^2 obstructions that can't be hedged pairwise",
    "math_tool": "higher sheaf cohomology (H^2) on factor covers",
    "eval_focus": "tail risk prediction accuracy (VaR/CVaR exceedances)",
    "keywords": ["risk_sheaf", "h2_obstruction", "tail_risk", "cvar", "factor_cover", "dual_sheaf"],
    "obligations": [_obl("var_exceedance", "VaR exceedance rate (should be ≤5%)"),
                    _obl("cvar_accuracy", "CVaR prediction RMSE vs realized"),
                    _obl("h2_vs_tail", "Correlation of H^2 magnitude with tail events", kind="statistical_test", trust=TRUST_SOLVER, fmts=("theorem_environ", "booktabs_table", "inline_number")),
                    _obl("factor_decomp", "Variance explained by sheaf factor decomposition", kind="comparison", fmts=("booktabs_table", "pgfplots_bar", "narrative"))] },
  { "title": "Cointegration Coboundary Trading",
    "sub_theme": "Identify cointegrated pairs as exact Cech 1-coboundaries; trade the mean-reversion when the coboundary norm exceeds threshold",
    "math_tool": "Cech coboundary operators on cointegration graphs",
    "eval_focus": "pairs trading Sharpe ratio and win rate",
    "keywords": ["cointegration", "coboundary_operator", "pairs_trading", "mean_reversion", "johansen", "engle_granger"],
    "obligations": [_obl("pairs_sharpe", "Sharpe ratio of coboundary pairs strategy"),
                    _obl("win_rate", "Trade win rate (fraction profitable)"),
                    _obl("half_life", "Distribution of mean-reversion half-lives", kind="distribution", fmts=("histogram", "booktabs_table", "narrative")),
                    _obl("n_pairs", "Number of cointegrated pairs found per period", kind="time_series", fmts=("pgfplots_line", "booktabs_table", "inline_number"))] },
  { "title": "Covering Family Sector Rotation",
    "sub_theme": "Model sector ETFs as a covering family of the market; rotation signals emerge when covering consistency breaks on sector overlaps",
    "math_tool": "Grothendieck topology refinement and covering sieve comparison",
    "eval_focus": "sector rotation return vs buy-and-hold SPY",
    "keywords": ["sector_rotation", "covering_sieve", "refinement", "sector_etf", "topology_change"],
    "obligations": [_obl("rotation_return", "Annualized return of sector rotation strategy"),
                    _obl("spy_return", "Annualized return of SPY buy-and-hold"),
                    _obl("consistency_breaks", "Number of covering consistency breaks per year", kind="time_series", fmts=("pgfplots_line", "booktabs_table", "narrative")),
                    _obl("sector_overlap", "Pairwise sector overlap heatmap", kind="correlation", fmts=("booktabs_table", "narrative"))] },
  { "title": "Obstruction Magnitude as Volatility Predictor",
    "sub_theme": "Show that ‖H^1‖ of the pricing sheaf predicts realized volatility; build a vol-targeting strategy",
    "math_tool": "sheaf cohomology norm estimation and Granger causality",
    "eval_focus": "volatility prediction R² and strategy risk-adjusted return",
    "keywords": ["volatility_prediction", "h1_norm", "granger_causality", "vol_targeting", "realized_vol"],
    "obligations": [_obl("vol_r2", "R² of H^1-norm → realized vol regression"),
                    _obl("granger_pvalue", "Granger causality p-value (H^1 → vol)", kind="statistical_test", trust=TRUST_SOLVER, fmts=("theorem_environ", "inline_number", "booktabs_table")),
                    _obl("voltarget_sharpe", "Sharpe ratio of vol-targeting strategy"),
                    _obl("vol_timeseries", "Predicted vs realized vol time series", kind="time_series", fmts=("pgfplots_line", "booktabs_table", "narrative"))] },
  { "title": "Option Implied Sheaf Inconsistency",
    "sub_theme": "Extend the pricing sheaf to option-implied distributions; sheaf inconsistency between spot and implied = mispricing signal",
    "math_tool": "presheaf comparison via natural transformations and Kan extensions",
    "eval_focus": "mispricing signal PnL and information ratio",
    "keywords": ["option_implied", "natural_transformation", "kan_extension", "mispricing", "implied_vol_surface"],
    "obligations": [_obl("mispricing_pnl", "Annualized PnL of mispricing signal"),
                    _obl("info_ratio", "Information ratio vs benchmark"),
                    _obl("inconsistency_dist", "Distribution of spot-vs-implied inconsistency scores", kind="distribution", fmts=("histogram", "boxplot", "booktabs_table")),
                    _obl("skew_correlation", "Correlation of sheaf inconsistency with vol skew", kind="statistical_test", trust=TRUST_SOLVER, fmts=("theorem_environ", "booktabs_table", "inline_number"))] },
  { "title": "Multi-Horizon Descent Ensemble",
    "sub_theme": "Run sheaf descent at daily/weekly/monthly horizons; ensemble signals across horizons using the gluing axiom",
    "math_tool": "multi-resolution sheaf theory (restriction maps across time scales)",
    "eval_focus": "ensemble Sharpe ratio vs single-horizon strategies",
    "keywords": ["multi_horizon", "restriction_map", "time_scale", "ensemble", "gluing_axiom", "resolution"],
    "obligations": [_obl("ensemble_sharpe", "Sharpe ratio of multi-horizon ensemble"),
                    _obl("daily_sharpe", "Sharpe ratio of daily-only strategy"),
                    _obl("weekly_sharpe", "Sharpe ratio of weekly-only strategy"),
                    _obl("horizon_comparison", "All horizons + ensemble Sharpe comparison", kind="comparison", fmts=("booktabs_table", "pgfplots_bar", "narrative")),
                    _obl("gluing_success", "Fraction of horizons where gluing succeeded", kind="time_series", fmts=("pgfplots_line", "booktabs_table", "inline_number"))] },
]

# ═════════════════════════════════════════════════════════════════════
#  LAUNCH — parallel research with differentiation guarantees
# ═════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent.parent / "outputs" / f"yahoo_10papers_{time.strftime('%Y%m%d_%H%M%S')}"

    config = ParallelResearchConfig(
        theme=THEME,
        n_papers=10,
        sub_themes=SUB_THEMES,
        max_iterations_per_paper=30,
        max_pivots_per_paper=3,
        output_dir=str(out),
        max_workers=4,
        verbose=True,
    )

    # Pre-flight: verify differentiation before launching
    specs = decompose_theme(THEME, 10, sub_themes=SUB_THEMES)
    diff = compute_differentiation(specs)

    print("╔═══════════════════════════════════════════════════════════════╗")
    print("║  YAHOO FINANCE × SHEAF THEORY — 10 parallel research papers  ║")
    print("╠═══════════════════════════════════════════════════════════════╣")
    print(f"║  Differentiation score : {diff.differentiation_score:.3f}  (1.0 = perfect)          ║")
    print(f"║  Max pairwise overlap  : {diff.max_pairwise_overlap:.3f}  (< 0.30 = good)           ║")
    print(f"║  Obligation coverage   : {diff.obligation_coverage:.3f}                             ║")
    print(f"║  Output directory      : {str(out)[:42]}  ║")
    print("╠═══════════════════════════════════════════════════════════════╣")
    for s in specs:
        n_obl = len(s.required_obligations.obligations)
        print(f"║  [{s.index}] {s.title[:45]:<45} {n_obl} obls  ║")
    print("╚═══════════════════════════════════════════════════════════════╝")

    # Build presentation plans for each paper (multi-format requirement)
    for s in specs:
        datasets = [DataSet(name=s.title, description=s.sub_theme, points=[
            DataPoint(key=o.key, label=o.description, value=0.0,
                      kind=o.required_kind, trust=o.required_trust)
            for o in s.required_obligations.obligations
        ])]
        plan = plan_presentations(datasets, min_formats_per_datum=2)
        print(f"  Paper {s.index}: {plan.multi_format_coverage:.0%} multi-format coverage, "
              f"{len(plan.slots)} presentation slots")

    print(f"\nLaunching {config.n_papers} papers with {config.max_workers} workers...")
    result = parallel_research(config)

    print(f"\n{'═'*63}")
    print(f"  Completed    : {result.success_count}/{len(result.results)} papers converged")
    print(f"  Code files   : {result.total_code_files} total")
    print(f"  Elapsed      : {result.elapsed:.1f}s")
    print(f"  Diff score   : {result.differentiation.differentiation_score:.3f}")
    print(f"  Output       : {result.output_dir}")
    for i, r in enumerate(result.results):
        tag = "✓" if r and r.status == ResearchStatus.CONVERGED else "✗"
        approach = r.approach if r else "FAILED"
        files = len(r.code_files) if r else 0
        print(f"  [{i}] {tag} {specs[i].title[:40]:<40} {approach:<25} {files} files")
    print(f"{'═'*63}")

    (out / "final_result.json").write_text(json.dumps({
        "theme": THEME, "n_papers": config.n_papers,
        "success_count": result.success_count,
        "total_code_files": result.total_code_files,
        "elapsed": result.elapsed,
        "differentiation_score": result.differentiation.differentiation_score,
        "papers": [{"index": i, "title": specs[i].title,
                     "status": str(r.status) if r else "FAILED",
                     "code_files": len(r.code_files) if r else 0}
                    for i, r in enumerate(result.results)],
    }, indent=2))
