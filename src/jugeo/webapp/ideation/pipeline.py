"""Stage 7 – Full ideation pipeline orchestrator.

Standalone module — Python stdlib only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import (
    ApplicationCoordinate, AppIdeationPurpose, ExistingApp,
    GainProfile, GapType, IdeaPortfolio, IdeaProposal,
    IdeaSource, IdeationResult, RankedIdea, ValidationResult,
    ValidationStatus, CoverageReport, Gap,
)
from .app_coordinates import COORD_SPACE
from .portfolio_builder import BuiltinPortfolios
from .coverage_estimator import AppCoverageEstimator, GapDetector
from .gap_filler import GapFillerGenerator
from .analogy_transporter import AppAnalogyTransporter
from .intersection_detector import IntersectionDetector
from .novelty_functional import PurposeConditionedNoveltyFunctional
from .validator import AppIdeaValidator
from .marginal_analyzer import AppMarginalAnalyzer

AC = ApplicationCoordinate


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class IdeationConfig:
    """Configuration knobs for :class:`IdeationPipeline`."""

    use_builtin_portfolio: bool = True
    builtin_domain: str = "personal_finance"
    max_candidates: int = 50
    min_feasibility: float = 0.3
    min_novelty: float = 0.2
    include_triple_gaps: bool = True
    budget_hours: float = 200.0

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation."""
        return {
            "use_builtin_portfolio": self.use_builtin_portfolio,
            "builtin_domain": self.builtin_domain,
            "max_candidates": self.max_candidates,
            "min_feasibility": self.min_feasibility,
            "min_novelty": self.min_novelty,
            "include_triple_gaps": self.include_triple_gaps,
            "budget_hours": self.budget_hours,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdeationConfig:
        """Reconstruct from a plain dict."""
        return cls(
            use_builtin_portfolio=bool(data.get("use_builtin_portfolio", True)),
            builtin_domain=str(data.get("builtin_domain", "personal_finance")),
            max_candidates=int(data.get("max_candidates", 50)),
            min_feasibility=float(data.get("min_feasibility", 0.3)),
            min_novelty=float(data.get("min_novelty", 0.2)),
            include_triple_gaps=bool(data.get("include_triple_gaps", True)),
            budget_hours=float(data.get("budget_hours", 200.0)),
        )


# ---------------------------------------------------------------------------
# Builtin portfolio lookup
# ---------------------------------------------------------------------------

_BUILTIN_PORTFOLIOS: dict[str, Any] = {
    "personal_finance": BuiltinPortfolios.personal_finance,
    "education": BuiltinPortfolios.education,
    "developer_tools": BuiltinPortfolios.developer_tools,
    "data_science": BuiltinPortfolios.data_science,
    "small_business": BuiltinPortfolios.small_business,
}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class IdeationPipeline:
    """The full 6-stage ideation pipeline.

    Stages: Portfolio → Coverage → Candidates → Scoring → Validation → Ranking.
    """

    def __init__(self, config: IdeationConfig | None = None) -> None:
        self.config = config or IdeationConfig()

        self._coverage_estimator = AppCoverageEstimator()
        self._gap_detector = GapDetector()
        self._gap_filler = GapFillerGenerator()
        self._analogy_transporter = AppAnalogyTransporter()
        self._intersection_detector = IntersectionDetector()
        self._novelty_functional = PurposeConditionedNoveltyFunctional()
        self._validator = AppIdeaValidator()
        self._marginal_analyzer = AppMarginalAnalyzer()
        self._last_coverage: CoverageReport | None = None

    # -- public API ----------------------------------------------------------

    def run(
        self,
        purpose: AppIdeationPurpose,
        portfolio: IdeaPortfolio | None = None,
    ) -> IdeationResult:
        """Run the full 6-stage ideation pipeline."""
        cfg = self.config
        metadata: dict[str, Any] = {
            "stages": {},
            "total_time": 0.0,
            "config": cfg.to_dict(),
        }
        t_start = time.time()

        # Stage 1 – Portfolio
        t0 = time.time()
        if portfolio is None:
            portfolio = self._stage1_portfolio(purpose, cfg)
        metadata["stages"]["portfolio"] = {
            "time": time.time() - t0,
            "app_count": len(portfolio.ideas),
        }

        # Stage 2 – Coverage
        t0 = time.time()
        coverage = self._coverage_estimator.estimate(portfolio)
        self._last_coverage = coverage
        metadata["stages"]["coverage"] = {
            "time": time.time() - t0,
            "gap_count": len(coverage.gaps),
        }

        # Stage 3 – Candidate generation
        t0 = time.time()
        candidates = self._stage3_candidates(
            coverage.gaps, portfolio, purpose, coverage, cfg,
        )
        metadata["stages"]["candidates"] = {
            "time": time.time() - t0,
            "candidate_count": len(candidates),
        }

        # Stage 4 – Novelty filtering
        t0 = time.time()
        scored = self._novelty_functional.filter_and_rank(
            candidates, portfolio, purpose, min_score=cfg.min_novelty,
        )
        metadata["stages"]["scoring"] = {
            "time": time.time() - t0,
            "scored_count": len(scored),
        }

        # Stage 5 – Validation
        t0 = time.time()
        validated = self._validator.batch_validate(scored, portfolio)
        metadata["stages"]["validation"] = {
            "time": time.time() - t0,
            "validated_count": len(validated),
        }

        # Stage 6 – Marginal ranking
        t0 = time.time()
        ranked = self._marginal_analyzer.rank(validated, purpose)
        metadata["stages"]["ranking"] = {
            "time": time.time() - t0,
            "ranked_count": len(ranked),
        }

        metadata["total_time"] = time.time() - t_start

        return IdeationResult(
            purpose=purpose,
            portfolio=portfolio,
            coverage=coverage,
            candidates=scored,
            ranked_ideas=ranked,
            pipeline_metadata=metadata,
        )

    # -- stage helpers -------------------------------------------------------

    def _stage1_portfolio(
        self, purpose: AppIdeationPurpose, cfg: IdeationConfig,
    ) -> IdeaPortfolio:
        """Stage 1: assemble portfolio from builtins or return empty."""
        if cfg.use_builtin_portfolio:
            factory = _BUILTIN_PORTFOLIOS.get(cfg.builtin_domain)
            if factory is None:
                factory = _BUILTIN_PORTFOLIOS["personal_finance"]
            return factory()
        return IdeaPortfolio(ideas=[])

    def _stage3_candidates(
        self,
        gaps: list[Gap],
        portfolio: IdeaPortfolio,
        purpose: AppIdeationPurpose,
        coverage: CoverageReport,
        cfg: IdeationConfig,
    ) -> list[IdeaProposal]:
        """Stage 3: generate candidates via gap-fill, analogy, intersection."""
        active_gaps = gaps
        if not cfg.include_triple_gaps:
            active_gaps = [g for g in gaps if g.gap_type != GapType.TRIPLE]

        gap_ideas = self._gap_filler.generate(active_gaps, purpose)
        analogy_ideas = self._analogy_transporter.generate_candidates(
            purpose, portfolio,
        )
        intersection_ideas = self._intersection_detector.detect(
            portfolio, coverage, purpose,
        )

        all_candidates = gap_ideas + analogy_ideas + intersection_ideas
        all_candidates = _deduplicate_proposals(all_candidates)
        return all_candidates[: cfg.max_candidates]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_title(title: str) -> str:
    """Lower-case, strip whitespace and punctuation for comparison."""
    return "".join(ch for ch in title.lower() if ch.isalnum() or ch == " ").strip()


def _deduplicate_proposals(proposals: list[IdeaProposal]) -> list[IdeaProposal]:
    """Remove proposals whose normalised titles collide (first wins)."""
    seen: set[str] = set()
    result: list[IdeaProposal] = []
    for p in proposals:
        key = _normalise_title(p.title)
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


# ---------------------------------------------------------------------------
# §5.6 Worked examples
# ---------------------------------------------------------------------------

def _ex(
    title: str,
    hypothesis: str,
    area: str,
    coords: set[AC],
    gain: tuple[float, float, float, float],
    feas: float,
    novel: float,
) -> IdeaProposal:
    """Shorthand factory for worked-example proposals."""
    return IdeaProposal.create(
        title=title,
        hypothesis=hypothesis,
        target_area=area,
        coordinates=coords,
        gain=GainProfile(
            theorem_yield=gain[0], bridge_impact=gain[1],
            cost=gain[2], uncertainty=gain[3],
        ),
        source=IdeaSource.MANUAL,
        feasibility_score=feas,
        novelty_score=novel,
    )


WORKED_EXAMPLES: list[IdeaProposal] = [
    # 1. Feasibility-Space Scheduling Visualizer
    _ex(
        "Feasibility-Space Scheduling Visualizer",
        "A web tool that renders the feasibility polytope of a scheduling "
        "problem lets users visually understand which constraints are "
        "binding and where slack exists, reducing scheduling errors and "
        "improving plan quality.",
        "operations_research",
        {AC.SCHEDULING, AC.DATA_VISUALIZATION, AC.CONSTRAINT_SATISFACTION},
        (0.7, 0.8, 120.0, 0.3), 0.65, 0.78,
    ),
    # 2. Decision Journal
    _ex(
        "Decision Journal",
        "A structured decision journal that records predictions, tracks "
        "outcomes, and computes calibration curves enables individuals to "
        "measurably improve their judgment over time.",
        "personal_productivity",
        {AC.FORM_WORKFLOW, AC.AUDIT_TRAIL, AC.COMPUTATION_ON_DEMAND},
        (0.8, 0.75, 80.0, 0.25), 0.80, 0.72,
    ),
    # 3. Fair Division Calculator
    _ex(
        "Fair Division Calculator",
        "A web calculator implementing envy-free and proportional fair-"
        "division algorithms makes equitable resource splitting accessible "
        "to non-experts—roommates dividing rent, co-founders splitting "
        "equity, or inheritors allocating assets.",
        "personal_finance",
        {AC.CONSTRAINT_SATISFACTION, AC.COMPUTATION_ON_DEMAND, AC.COMPARISON},
        (0.9, 0.85, 90.0, 0.2), 0.82, 0.68,
    ),
    # 4. Combinatorial Auction Platform
    _ex(
        "Combinatorial Auction Platform",
        "A web app that lets users design, simulate, and compare "
        "combinatorial auction mechanisms makes mechanism-design theory "
        "practically usable for small organisations allocating shared "
        "resources.",
        "market_design",
        {AC.MATCHING, AC.CONSTRAINT_SATISFACTION, AC.SIMULATION},
        (0.75, 0.65, 160.0, 0.4), 0.50, 0.85,
    ),
    # 5. Batch Data Quality Monitor
    _ex(
        "Batch Data Quality Monitor",
        "A monitoring dashboard that profiles every incoming batch for "
        "schema drift, null-rate spikes, and distribution shifts catches "
        "data-quality regressions before they propagate downstream, saving "
        "hours of ad-hoc debugging.",
        "data_engineering",
        {AC.BATCH_PROCESSING, AC.DATA_INGESTION, AC.NOTIFICATION},
        (0.72, 0.70, 110.0, 0.28), 0.74, 0.60,
    ),
    # 6. Interactive Simulation Dashboard
    _ex(
        "Interactive Simulation Dashboard",
        "An interactive dashboard that lets users tweak model parameters "
        "and instantly see simulated outcomes bridges the gap between "
        "static reports and full Monte-Carlo toolkits, making what-if "
        "analysis accessible to decision-makers without coding skills.",
        "decision_support",
        {AC.SIMULATION, AC.INTERACTIVE_DASHBOARD, AC.DATA_VISUALIZATION},
        (0.82, 0.76, 140.0, 0.32), 0.58, 0.81,
    ),
    # 7. API Usage Analytics Exporter
    _ex(
        "API Usage Analytics Exporter",
        "A lightweight service that aggregates per-endpoint usage metrics "
        "and exports them to CSV, JSON, or webhook destinations gives API "
        "providers actionable visibility into consumption patterns without "
        "requiring a full observability stack.",
        "developer_tools",
        {AC.API_PROVISION, AC.DATA_EXPORT, AC.AGGREGATION},
        (0.68, 0.62, 70.0, 0.22), 0.85, 0.55,
    ),
]
