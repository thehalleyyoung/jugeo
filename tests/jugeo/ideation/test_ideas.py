from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.ideation.ideas import (
    GainProfile,
    Idea,
    IdeaDependencyGraph,
    IdeaDiagnostics,
    IdeaEvaluator,
    IdeaHistory,
    IdeaLifecycle,
    IdeaPortfolio,
    IdeaProposal,
    IdeaSerializer,
    LifecycleStatus,
    TrustStatus,
    ValidationPath,
)


def _make_idea(
    idea_id: str,
    *,
    title: str = "Semantic bridge",
    purpose: str = "grow theorem yield",
    target_area: str = "semantic futures",
    theorem_yield: float = 4.0,
    bridge_impact: float = 3.0,
    cost: float = 1.5,
    uncertainty: float = 0.2,
    novelty_score: float = 0.7,
) -> Idea:
    return Idea(
        idea_id=idea_id,
        title=title,
        purpose=purpose,
        target_area=target_area,
        hypothesis="A focused bridge should unlock reusable lemmas.",
        predicted_gain=GainProfile(
            theorem_yield=theorem_yield,
            bridge_impact=bridge_impact,
            cost=cost,
            uncertainty=uncertainty,
        ),
        novelty_score=novelty_score,
        validation_plan=ValidationPath(
            steps=("state the bridge", "prove a witness theorem"),
            required_evidence=("witness proof",),
            success_criteria=("the witness theorem is reusable",),
        ),
        trust_status=TrustStatus.PROVISIONAL,
    )


def test_idea_proposal_keeps_support() -> None:
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    idea = IdeaProposal('title', 'hypothesis', SupportRegion(coordinate, frozenset({'p'})), 5)
    assert idea.support.patch_keys == frozenset({'p'})


def test_idea_portfolio_shortlist_prefers_stronger_idea() -> None:
    portfolio = IdeaPortfolio()
    strong = _make_idea("idea-strong", theorem_yield=7.0, bridge_impact=4.0, novelty_score=0.8)
    weak = _make_idea("idea-weak", theorem_yield=2.0, bridge_impact=1.0, cost=3.0, uncertainty=0.7, novelty_score=0.3)
    portfolio.add(weak)
    portfolio.add(strong)

    shortlist = portfolio.shortlist(limit=1, evaluator=IdeaEvaluator())

    assert shortlist == (strong,)


def test_idea_serializer_round_trip_preserves_fields() -> None:
    idea = _make_idea("idea-json", title="Copilot semantic bridge")

    payload = IdeaSerializer.to_json(idea)
    restored = IdeaSerializer.from_json(payload)

    assert restored.idea_id == idea.idea_id
    assert restored.predicted_gain.theorem_yield == idea.predicted_gain.theorem_yield
    assert restored.validation_plan.steps == idea.validation_plan.steps


def test_idea_lifecycle_records_history_and_validation_time() -> None:
    idea = _make_idea("idea-life")
    history = IdeaHistory()
    lifecycle = IdeaLifecycle(history=history)

    proposed = lifecycle.propose(idea, note="entered review")
    accepted = lifecycle.accept(idea.idea_id, note="validated by witness theorem")

    assert proposed is LifecycleStatus.PROPOSED
    assert accepted is LifecycleStatus.ACCEPTED
    assert history.time_to_validation(idea.idea_id) is not None
    assert history.success_rate() == 1.0


def test_idea_dependency_graph_reports_critical_path() -> None:
    graph = IdeaDependencyGraph()
    graph.add_dependency("c", "b")
    graph.add_dependency("b", "a")

    assert graph.dependencies("c") == ("b",)
    assert graph.prerequisites("c") == ("a", "b")
    assert graph.critical_paths("c") == (("a", "b", "c"),)


def test_idea_diagnostics_mentions_copilot_summary() -> None:
    diagnostics = IdeaDiagnostics()
    idea = _make_idea("idea-diag", title="Copilot frontier bridge")

    summary = diagnostics.copilot_idea_summary(idea)

    assert "Copilot view" in summary
    assert idea.idea_id in summary
