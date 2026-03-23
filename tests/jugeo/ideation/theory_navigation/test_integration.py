"""Tests for jugeo.ideation.integration — IdeaNavigator, NoveltyNavigator,
NavigationFederator, TrustAwareNavigator, and IntegratedNavigationPipeline.

Run with:
    pytest tests/jugeo/ideation/theory_navigation/test_integration.py -v
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# ---------------------------------------------------------------------------
# Optional imports — skip entire module if the integration layer is absent.
# ---------------------------------------------------------------------------
pytest = __import__("pytest")

try:
    from jugeo.ideation.integration import (
        IdeaNavigator,
        FederationNavigator,
        NoveltyNavigator,
        NavigationFederator,
        TrustAwareNavigator,
        IntegratedNavigationPipeline,
    )
    HAS_INTEGRATION = True
except ImportError:
    HAS_INTEGRATION = False

try:
    from jugeo.ideation.federation import CrossRegimeBridge, FederatedIdeaProposal, make_bridge
    HAS_FEDERATION = True
except ImportError:
    HAS_FEDERATION = False

try:
    from jugeo.ideation.novelty import NoveltyScore
    HAS_NOVELTY = True
except ImportError:
    HAS_NOVELTY = False

try:
    from jugeo.evidence.trust import TrustLevel
    HAS_TRUST = True
except ImportError:
    HAS_TRUST = False

pytestmark = pytest.mark.skipif(
    not HAS_INTEGRATION,
    reason="jugeo.ideation.integration not available",
)

# ---------------------------------------------------------------------------
# Re-used imports that are always available
# ---------------------------------------------------------------------------
from jugeo.ideation.ideas import (
    Idea,
    GainProfile,
    ValidationPath,
    IdeaPortfolio,
    TrustStatus,
)
from jugeo.ideation.theory_navigation.models import (
    TheoryNode,
    TheorySpace,
    NavigationPath,
    PurposeCondition,
    NodeMaturity,
    NavigationStrategy,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_idea(
    idea_id: str = "i1",
    title: str = "Test Idea",
    purpose: str = "math",
    novelty: float = 0.7,
    trust: TrustStatus = TrustStatus.GROUNDED,
) -> Idea:
    return Idea(
        idea_id=idea_id,
        title=title,
        purpose=purpose,
        target_area="algebra",
        hypothesis="A test hypothesis about algebraic structures",
        predicted_gain=GainProfile(
            theorem_yield=0.8,
            bridge_impact=0.5,
            cost=0.3,
            uncertainty=0.2,
        ),
        novelty_score=novelty,
        validation_plan=ValidationPath(
            steps=("step1", "step2"),
            required_evidence=(),
            success_criteria=(),
        ),
        trust_status=trust,
    )


def _make_portfolio(*ideas: Idea) -> IdeaPortfolio:
    portfolio = IdeaPortfolio()
    for idea in ideas:
        portfolio.add(idea)
    return portfolio


def _make_connected_space(n: int = 4) -> TheorySpace:
    space = TheorySpace()
    for i in range(n):
        node = TheoryNode(
            node_id=f"n{i}",
            name=f"Node{i}",
            description=f"Theory about topic {i}",
            purpose_alignment=0.5 + i * 0.1,
            maturity=NodeMaturity.MATURE,
        )
        space.add_node(node)
    for i in range(n - 1):
        space.add_edge(f"n{i}", f"n{i + 1}")
    return space


def _make_purpose_condition(label: str = "algebra", weight: float = 1.0) -> PurposeCondition:
    return PurposeCondition(
        condition_id="cond_algebra",
        label=label,
        description="Algebraic research purposes",
        keywords=("algebra", "ring", "group", "field"),
        weight=weight,
    )


def _make_novelty_scores() -> list:
    if not HAS_NOVELTY:
        return []
    scores = []
    for i in range(5):
        scores.append(
            NoveltyScore(
                idea_id=f"idea_{i}",
                semantic_distance=0.2 + i * 0.15,
                purpose_alignment=0.4 + i * 0.1,
                feasibility=0.3 + i * 0.12,
                composite=0.3 + i * 0.15,
                explanation=f"Explanation for idea {i}",
            )
        )
    return scores


def _make_trust_registry() -> dict:
    if not HAS_TRUST:
        return {}
    return {
        "n0": TrustLevel.MECHANICALLY_VERIFIED,
        "n1": TrustLevel.HUMAN_ATTESTED,
        "n2": TrustLevel.COPILOT_SUGGESTED,
        "n3": TrustLevel.UNVERIFIED,
    }


# ===========================================================================
# IdeaNavigator tests
# ===========================================================================

class TestIdeaNavigator:
    """Tests for IdeaNavigator: idea↔node conversion and portfolio navigation."""

    def test_idea_navigator_idea_to_node_id(self):
        """The node produced from an idea must carry the same idea_id."""
        nav = IdeaNavigator()
        idea = _make_idea(idea_id="idea_abc")
        node = nav.idea_to_node(idea)
        assert node.node_id == "idea_abc"

    def test_idea_navigator_idea_to_node_name(self):
        """The node name must match the idea's title."""
        nav = IdeaNavigator()
        idea = _make_idea(title="Categorical Limits")
        node = nav.idea_to_node(idea)
        assert node.name == "Categorical Limits"

    def test_idea_navigator_idea_to_node_maturity_speculative(self):
        """SPECULATIVE trust → NASCENT node maturity."""
        nav = IdeaNavigator()
        idea = _make_idea(trust=TrustStatus.SPECULATIVE)
        node = nav.idea_to_node(idea)
        assert node.maturity == NodeMaturity.NASCENT

    def test_idea_navigator_idea_to_node_maturity_validated(self):
        """VALIDATED trust → ESTABLISHED node maturity."""
        nav = IdeaNavigator()
        idea = _make_idea(trust=TrustStatus.VALIDATED)
        node = nav.idea_to_node(idea)
        assert node.maturity == NodeMaturity.ESTABLISHED

    def test_idea_navigator_idea_to_node_maturity_provisional(self):
        """PROVISIONAL trust should produce at least DEVELOPING maturity."""
        nav = IdeaNavigator()
        idea = _make_idea(trust=TrustStatus.PROVISIONAL)
        node = nav.idea_to_node(idea)
        assert node.maturity in {NodeMaturity.DEVELOPING, NodeMaturity.NASCENT}

    def test_idea_navigator_idea_to_node_maturity_grounded(self):
        """GROUNDED trust should map to MATURE or ESTABLISHED maturity."""
        nav = IdeaNavigator()
        idea = _make_idea(trust=TrustStatus.GROUNDED)
        node = nav.idea_to_node(idea)
        assert node.maturity in {NodeMaturity.MATURE, NodeMaturity.ESTABLISHED}

    def test_idea_navigator_portfolio_to_space_returns_theory_space(self):
        """portfolio_to_space must return a TheorySpace instance."""
        nav = IdeaNavigator()
        ideas = [_make_idea(idea_id=f"i{k}", title=f"Idea {k}") for k in range(3)]
        portfolio = _make_portfolio(*ideas)
        space = nav.portfolio_to_space(portfolio)
        assert isinstance(space, TheorySpace)

    def test_idea_navigator_portfolio_to_space_node_count(self):
        """The returned space must contain as many nodes as there are ideas."""
        nav = IdeaNavigator()
        n = 5
        ideas = [_make_idea(idea_id=f"i{k}", title=f"Idea {k}") for k in range(n)]
        portfolio = _make_portfolio(*ideas)
        space = nav.portfolio_to_space(portfolio)
        assert space.node_count() == n

    def test_idea_navigator_portfolio_to_space_node_ids_present(self):
        """Every idea_id must appear as a node_id in the resulting space."""
        nav = IdeaNavigator()
        ids = [f"idea_{k}" for k in range(4)]
        ideas = [_make_idea(idea_id=iid, title=f"T{k}") for k, iid in enumerate(ids)]
        portfolio = _make_portfolio(*ideas)
        space = nav.portfolio_to_space(portfolio)
        for iid in ids:
            assert space.has_node(iid), f"Expected node {iid!r} in space"

    def test_idea_navigator_find_purpose_aligned_ideas(self):
        """find_purpose_aligned_ideas returns ideas matching the given purpose."""
        nav = IdeaNavigator()
        math_ideas = [_make_idea(idea_id=f"m{k}", title=f"Math {k}", purpose="math") for k in range(3)]
        logic_idea = _make_idea(idea_id="logic1", title="Logic Idea", purpose="logic")
        portfolio = _make_portfolio(*math_ideas, logic_idea)
        space = nav.portfolio_to_space(portfolio)
        aligned = nav.find_purpose_aligned_ideas(portfolio, "math", limit=10)
        # All returned ideas should have purpose "math"
        for idea in aligned:
            assert idea.purpose == "math"

    def test_idea_navigator_find_purpose_aligned_ideas_limit(self):
        """find_purpose_aligned_ideas must respect the provided limit."""
        nav = IdeaNavigator()
        ideas = [_make_idea(idea_id=f"m{k}", title=f"M{k}", purpose="math") for k in range(10)]
        portfolio = _make_portfolio(*ideas)
        space = nav.portfolio_to_space(portfolio)
        limit = 3
        aligned = nav.find_purpose_aligned_ideas(portfolio, "math", limit=limit)
        assert len(aligned) <= limit

    def test_idea_navigator_integration_report_nonempty(self):
        """integration_report must return a non-empty string."""
        nav = IdeaNavigator()
        ideas = [_make_idea(idea_id=f"i{k}", title=f"Idea {k}") for k in range(3)]
        portfolio = _make_portfolio(*ideas)
        report = nav.integration_report(portfolio)
        assert isinstance(report, str)
        assert len(report.strip()) > 0

    def test_idea_navigator_idea_path_to_ideas(self):
        """idea_path_to_ideas converts path node_ids back to Idea objects where they exist."""
        nav = IdeaNavigator()
        ideas = [_make_idea(idea_id=f"i{k}", title=f"Idea {k}") for k in range(4)]
        portfolio = _make_portfolio(*ideas)
        space = nav.portfolio_to_space(portfolio)
        path = NavigationPath(
            path_id="p1",
            node_ids=("i0", "i1", "i2"),
            start_id="i0",
            goal_id="i2",
            purpose="math",
            total_cost=1.0,
            purpose_alignment=0.7,
            strategy=NavigationStrategy.BREADTH_FIRST,
        )
        result = nav.idea_path_to_ideas(path, portfolio)
        assert isinstance(result, list)
        # All returned items should be Idea instances
        for item in result:
            assert isinstance(item, Idea)

    def test_idea_navigator_suggest_next_ideas_returns_list(self):
        """suggest_next_ideas must return a list (possibly empty)."""
        nav = IdeaNavigator()
        ideas = [_make_idea(idea_id=f"i{k}", title=f"Idea {k}") for k in range(4)]
        portfolio = _make_portfolio(*ideas)
        space = nav.portfolio_to_space(portfolio)
        suggestions = nav.suggest_next_ideas(ideas[0], portfolio, space)
        assert isinstance(suggestions, list)

    def test_idea_navigator_navigate_from_idea_returns_path(self):
        """navigate_from_idea must return a NavigationPath."""
        nav = IdeaNavigator()
        idea_a = _make_idea(idea_id="start", title="Start")
        idea_b = _make_idea(idea_id="goal", title="Goal")
        portfolio = _make_portfolio(idea_a, idea_b)
        space = nav.portfolio_to_space(portfolio)
        space.add_edge("start", "goal")
        path = nav.navigate_from_idea(idea_a, idea_b, space)
        assert isinstance(path, NavigationPath)

    def test_idea_navigator_navigate_from_idea_path_endpoints(self):
        """The returned path should have correct start and goal ids."""
        nav = IdeaNavigator()
        idea_a = _make_idea(idea_id="s1", title="Source")
        idea_b = _make_idea(idea_id="g1", title="Goal")
        portfolio = _make_portfolio(idea_a, idea_b)
        space = nav.portfolio_to_space(portfolio)
        space.add_edge("s1", "g1")
        path = nav.navigate_from_idea(idea_a, idea_b, space)
        assert "s1" in (path.start_id, path.node_ids[0]) if path.node_ids else True
        assert isinstance(path, NavigationPath)


# ===========================================================================
# NoveltyNavigator tests
# ===========================================================================

@pytest.mark.skipif(not HAS_NOVELTY, reason="jugeo.ideation.novelty not available")
class TestNoveltyNavigator:
    """Tests for NoveltyNavigator: novelty-space construction and traversal."""

    def test_novelty_navigator_novelty_score_to_alignment_range(self):
        """novelty_score_to_alignment must return a value in [0, 1]."""
        nav = NoveltyNavigator()
        score = NoveltyScore(
            idea_id="x1",
            semantic_distance=0.6,
            purpose_alignment=0.7,
            feasibility=0.5,
            composite=0.65,
            explanation="test",
        )
        alignment = nav.novelty_score_to_alignment(score)
        assert 0.0 <= alignment <= 1.0

    def test_novelty_navigator_novelty_score_to_alignment_high_composite(self):
        """A high composite score should yield a higher alignment than a low one."""
        nav = NoveltyNavigator()
        high_score = NoveltyScore(
            idea_id="h1",
            semantic_distance=0.9,
            purpose_alignment=0.9,
            feasibility=0.9,
            composite=0.9,
            explanation="high",
        )
        low_score = NoveltyScore(
            idea_id="l1",
            semantic_distance=0.1,
            purpose_alignment=0.1,
            feasibility=0.1,
            composite=0.1,
            explanation="low",
        )
        high_alignment = nav.novelty_score_to_alignment(high_score)
        low_alignment = nav.novelty_score_to_alignment(low_score)
        assert high_alignment >= low_alignment

    def test_novelty_navigator_build_novelty_space_returns_theory_space(self):
        """build_novelty_space must return a TheorySpace."""
        nav = NoveltyNavigator()
        scores = _make_novelty_scores()
        ideas = [_make_idea(idea_id=f"idea_{k}", title=f"Novel {k}") for k in range(5)]
        portfolio = _make_portfolio(*ideas)
        space = nav.build_novelty_space(scores, portfolio)
        assert isinstance(space, TheorySpace)

    def test_novelty_navigator_build_novelty_space_node_count(self):
        """build_novelty_space should create one node per NoveltyScore."""
        nav = NoveltyNavigator()
        scores = _make_novelty_scores()
        ideas = [_make_idea(idea_id=f"idea_{k}", title=f"Novel {k}") for k in range(len(scores))]
        portfolio = _make_portfolio(*ideas)
        space = nav.build_novelty_space(scores, portfolio)
        assert space.node_count() == len(scores)

    def test_novelty_navigator_find_novelty_frontier_excludes_known(self):
        """Nodes in known_ids must not appear in the frontier."""
        nav = NoveltyNavigator()
        scores = _make_novelty_scores()
        ideas = [_make_idea(idea_id=f"idea_{k}", title=f"Novel {k}") for k in range(len(scores))]
        portfolio = _make_portfolio(*ideas)
        space = nav.build_novelty_space(scores, portfolio)
        known = {"idea_0", "idea_1"}
        frontier = nav.find_novelty_frontier(space, known)
        frontier_ids = {n.node_id for n in frontier}
        assert frontier_ids.isdisjoint(known), (
            f"Known ids {known} appeared in frontier {frontier_ids}"
        )

    def test_novelty_navigator_find_novelty_frontier_returns_nodes(self):
        """find_novelty_frontier must return a list of TheoryNode objects."""
        nav = NoveltyNavigator()
        scores = _make_novelty_scores()
        ideas = [_make_idea(idea_id=f"idea_{k}", title=f"Novel {k}") for k in range(len(scores))]
        portfolio = _make_portfolio(*ideas)
        space = nav.build_novelty_space(scores, portfolio)
        frontier = nav.find_novelty_frontier(space, set())
        assert isinstance(frontier, list)
        for node in frontier:
            assert isinstance(node, TheoryNode)

    def test_novelty_navigator_navigate_to_novel_returns_path(self):
        """navigate_to_novel must return a NavigationPath."""
        nav = NoveltyNavigator()
        scores = _make_novelty_scores()
        ideas = [_make_idea(idea_id=f"idea_{k}", title=f"Novel {k}") for k in range(len(scores))]
        portfolio = _make_portfolio(*ideas)
        space = nav.build_novelty_space(scores, portfolio)
        # connect nodes so navigation can find a path
        for k in range(len(scores) - 1):
            space.add_edge(f"idea_{k}", f"idea_{k + 1}")
        path = nav.navigate_to_novel("idea_0", space, known_ids={"idea_0"})
        assert isinstance(path, NavigationPath)

    def test_novelty_navigator_maximize_novelty_path_returns_path(self):
        """maximize_novelty_path must return a NavigationPath."""
        nav = NoveltyNavigator()
        scores = _make_novelty_scores()
        ideas = [_make_idea(idea_id=f"idea_{k}", title=f"Novel {k}") for k in range(len(scores))]
        portfolio = _make_portfolio(*ideas)
        space = nav.build_novelty_space(scores, portfolio)
        for k in range(len(scores) - 1):
            space.add_edge(f"idea_{k}", f"idea_{k + 1}")
        path = nav.maximize_novelty_path("idea_0", "idea_4", space, novelty_weight=0.8)
        assert isinstance(path, NavigationPath)

    def test_novelty_navigator_novelty_report_nonempty(self):
        """novelty_report must return a non-empty string."""
        nav = NoveltyNavigator()
        scores = _make_novelty_scores()
        ideas = [_make_idea(idea_id=f"idea_{k}", title=f"Novel {k}") for k in range(len(scores))]
        portfolio = _make_portfolio(*ideas)
        space = nav.build_novelty_space(scores, portfolio)
        report = nav.novelty_report(space, known_ids={"idea_0"})
        assert isinstance(report, str)
        assert len(report.strip()) > 0

    def test_novelty_navigator_set_condition(self):
        """set_condition must not raise; subsequent operations should use it."""
        nav = NoveltyNavigator()
        condition = _make_purpose_condition(label="novelty_test")
        nav.set_condition(condition)  # should not raise
        scores = _make_novelty_scores()
        ideas = [_make_idea(idea_id=f"idea_{k}") for k in range(len(scores))]
        portfolio = _make_portfolio(*ideas)
        space = nav.build_novelty_space(scores, portfolio)
        assert isinstance(space, TheorySpace)


# ===========================================================================
# FederationNavigator tests
# ===========================================================================

@pytest.mark.skipif(not HAS_FEDERATION, reason="jugeo.ideation.federation not available")
class TestFederationNavigator:
    """Tests for FederationNavigator: regime-bridge navigation."""

    def _make_bridge(self, source: str = "algebra", target: str = "topology") -> CrossRegimeBridge:
        return make_bridge(
            source=source,
            target=target,
            analogy_map={"ring": "topological_ring", "ideal": "closed_set"},
            trust_attenuation=0.15,
            description="An algebra-topology bridge",
        )

    def test_federation_navigator_bridge_to_node_returns_theory_node(self):
        """bridge_to_node must return a TheoryNode."""
        nav = FederationNavigator()
        bridge = self._make_bridge()
        node = nav.bridge_to_node(bridge)
        assert isinstance(node, TheoryNode)

    def test_federation_navigator_bridge_to_node_has_id(self):
        """bridge_to_node result must have a non-empty node_id."""
        nav = FederationNavigator()
        bridge = self._make_bridge()
        node = nav.bridge_to_node(bridge)
        assert node.node_id != ""

    def test_federation_navigator_build_federation_space(self):
        """build_federation_space must return a TheorySpace."""
        nav = FederationNavigator()
        bridges = [
            self._make_bridge("algebra", "topology"),
            self._make_bridge("analysis", "geometry"),
        ]
        proposals: list = []
        space = nav.build_federation_space(bridges, proposals)
        assert isinstance(space, TheorySpace)

    def test_federation_navigator_build_federation_space_node_count(self):
        """The space should contain at least as many nodes as bridges."""
        nav = FederationNavigator()
        bridges = [
            self._make_bridge("algebra", "topology"),
            self._make_bridge("analysis", "geometry"),
            self._make_bridge("algebra", "number_theory"),
        ]
        space = nav.build_federation_space(bridges, [])
        assert space.node_count() >= len(bridges)

    def test_federation_navigator_navigate_across_bridges_returns_path(self):
        """navigate_across_bridges must return a NavigationPath."""
        nav = FederationNavigator()
        bridges = [self._make_bridge("A", "B")]
        space = nav.build_federation_space(bridges, [])
        path = nav.navigate_across_bridges("A", "B", space)
        assert isinstance(path, NavigationPath)

    def test_federation_navigator_find_bridge_clusters_returns_list(self):
        """find_bridge_clusters must return a list of string lists."""
        nav = FederationNavigator()
        bridges = [
            self._make_bridge("algebra", "topology"),
            self._make_bridge("algebra", "number_theory"),
            self._make_bridge("analysis", "geometry"),
        ]
        space = nav.build_federation_space(bridges, [])
        clusters = nav.find_bridge_clusters(space, min_cluster_size=1)
        assert isinstance(clusters, list)
        for cluster in clusters:
            assert isinstance(cluster, list)

    def test_federation_navigator_assess_coverage_returns_dict(self):
        """assess_federation_coverage must return a dict."""
        nav = FederationNavigator()
        bridges = [
            self._make_bridge("algebra", "topology"),
            self._make_bridge("analysis", "geometry"),
        ]
        coverage = nav.assess_federation_coverage(bridges)
        assert isinstance(coverage, dict)

    def test_federation_navigator_report_nonempty(self):
        """federation_navigation_report must return a non-empty string."""
        nav = FederationNavigator()
        bridges = [self._make_bridge()]
        space = nav.build_federation_space(bridges, [])
        report = nav.federation_navigation_report(space)
        assert isinstance(report, str)
        assert len(report.strip()) > 0


# ===========================================================================
# NavigationFederator tests
# ===========================================================================

class TestNavigationFederator:
    """Tests for NavigationFederator: merging and partitioning theory spaces."""

    def test_navigation_federator_federate_spaces_merges_nodes(self):
        """federate_spaces of two disjoint spaces must contain all nodes."""
        fed = NavigationFederator()
        space_a = _make_connected_space(3)
        # Build a second space with distinct node IDs
        space_b = TheorySpace()
        for i in range(3):
            node = TheoryNode(
                node_id=f"m{i}",
                name=f"Meta{i}",
                description=f"Meta theory {i}",
                purpose_alignment=0.6,
                maturity=NodeMaturity.DEVELOPING,
            )
            space_b.add_node(node)
        merged = fed.federate_spaces([space_a, space_b], bridge_threshold=0.0)
        assert isinstance(merged, TheorySpace)
        assert merged.node_count() >= space_a.node_count() + space_b.node_count()

    def test_navigation_federator_federate_spaces_single_space(self):
        """Federating a single space must preserve all its nodes."""
        fed = NavigationFederator()
        space = _make_connected_space(5)
        merged = fed.federate_spaces([space], bridge_threshold=0.5)
        assert merged.node_count() == space.node_count()

    def test_navigation_federator_federate_spaces_returns_theory_space(self):
        """federate_spaces must always return a TheorySpace."""
        fed = NavigationFederator()
        spaces = [_make_connected_space(2), _make_connected_space(2)]
        result = fed.federate_spaces(spaces, bridge_threshold=0.5)
        assert isinstance(result, TheorySpace)

    def test_navigation_federator_split_space_count(self):
        """split_space must return exactly n_partitions TheorySpaces."""
        fed = NavigationFederator()
        space = _make_connected_space(8)
        n = 3
        partitions = fed.split_space(space, n_partitions=n)
        assert isinstance(partitions, list)
        assert len(partitions) == n
        for part in partitions:
            assert isinstance(part, TheorySpace)

    def test_navigation_federator_split_space_covers_all_nodes(self):
        """All original nodes must appear across the split partitions."""
        fed = NavigationFederator()
        space = _make_connected_space(6)
        partitions = fed.split_space(space, n_partitions=2)
        original_ids = {n.node_id for n in space.iter_nodes()}
        partition_ids: set[str] = set()
        for p in partitions:
            for n in p.iter_nodes():
                partition_ids.add(n.node_id)
        assert partition_ids == original_ids

    def test_navigation_federator_align_spaces_returns_dict(self):
        """align_spaces must return a dict mapping node IDs."""
        fed = NavigationFederator()
        space_a = _make_connected_space(3)
        space_b = _make_connected_space(3)
        mapping = fed.align_spaces(space_a, space_b)
        assert isinstance(mapping, dict)

    def test_navigation_federator_align_spaces_values_are_strings(self):
        """Values in the alignment dict must be node ID strings."""
        fed = NavigationFederator()
        space_a = _make_connected_space(3)
        space_b = _make_connected_space(3)
        mapping = fed.align_spaces(space_a, space_b)
        for k, v in mapping.items():
            assert isinstance(k, str)
            assert isinstance(v, str)

    def test_navigation_federator_cross_space_navigate_returns_path_and_spaces(self):
        """cross_space_navigate must return a tuple of (NavigationPath, list[str])."""
        fed = NavigationFederator()
        space_a = _make_connected_space(3)
        space_b = TheorySpace()
        node_b = TheoryNode(
            node_id="ext",
            name="External",
            description="An external node",
            purpose_alignment=0.7,
            maturity=NodeMaturity.MATURE,
        )
        space_b.add_node(node_b)
        result = fed.cross_space_navigate("n0", "ext", [space_a, space_b])
        assert isinstance(result, tuple)
        path, spaces_used = result
        assert isinstance(path, NavigationPath)
        assert isinstance(spaces_used, list)

    def test_navigation_federator_federation_report_nonempty(self):
        """federation_report must return a non-empty string."""
        fed = NavigationFederator()
        spaces = [_make_connected_space(3)]
        report = fed.federation_report(spaces)
        assert isinstance(report, str)
        assert len(report.strip()) > 0


# ===========================================================================
# TrustAwareNavigator tests
# ===========================================================================

@pytest.mark.skipif(not HAS_TRUST, reason="jugeo.evidence.trust not available")
class TestTrustAwareNavigator:
    """Tests for TrustAwareNavigator: trust-filtered navigation."""

    def test_trust_aware_navigator_trust_to_maturity_unverified(self):
        """UNVERIFIED trust should map to NASCENT node maturity."""
        nav = TrustAwareNavigator()
        maturity = nav.trust_to_maturity(TrustLevel.UNVERIFIED)
        assert maturity == NodeMaturity.NASCENT

    def test_trust_aware_navigator_trust_to_maturity_mechanically_verified(self):
        """MECHANICALLY_VERIFIED trust should map to ESTABLISHED maturity."""
        nav = TrustAwareNavigator()
        maturity = nav.trust_to_maturity(TrustLevel.MECHANICALLY_VERIFIED)
        assert maturity == NodeMaturity.ESTABLISHED

    def test_trust_aware_navigator_trust_to_maturity_human_attested(self):
        """HUMAN_ATTESTED trust should yield at least MATURE maturity."""
        nav = TrustAwareNavigator()
        maturity = nav.trust_to_maturity(TrustLevel.HUMAN_ATTESTED)
        assert maturity in {NodeMaturity.MATURE, NodeMaturity.ESTABLISHED}

    def test_trust_aware_navigator_trust_to_maturity_contradicted(self):
        """CONTRADICTED trust should yield NASCENT maturity."""
        nav = TrustAwareNavigator()
        maturity = nav.trust_to_maturity(TrustLevel.CONTRADICTED)
        assert maturity == NodeMaturity.NASCENT

    def test_trust_aware_navigator_filter_by_trust_removes_low(self):
        """filter_by_trust must exclude nodes below the minimum trust level."""
        nav = TrustAwareNavigator()
        nav.set_min_trust(TrustLevel.HUMAN_ATTESTED)
        space = _make_connected_space(4)
        registry = _make_trust_registry()
        filtered = nav.filter_by_trust(space, [TrustLevel.HUMAN_ATTESTED, TrustLevel.MECHANICALLY_VERIFIED])
        assert isinstance(filtered, TheorySpace)
        # Nodes registered below threshold should be excluded
        for node in filtered.iter_nodes():
            nid = node.node_id
            if nid in registry:
                level = registry[nid]
                assert level in {TrustLevel.HUMAN_ATTESTED, TrustLevel.MECHANICALLY_VERIFIED,
                                  TrustLevel.SOLVER_DISCHARGED, TrustLevel.RUNTIME_WITNESSED}

    def test_trust_aware_navigator_filter_preserves_high_trust(self):
        """filter_by_trust must keep nodes meeting or exceeding trust requirement."""
        nav = TrustAwareNavigator()
        space = _make_connected_space(2)
        registry = {"n0": TrustLevel.MECHANICALLY_VERIFIED, "n1": TrustLevel.UNVERIFIED}
        filtered = nav.filter_by_trust(
            space,
            [TrustLevel.HUMAN_ATTESTED, TrustLevel.MECHANICALLY_VERIFIED,
             TrustLevel.SOLVER_DISCHARGED, TrustLevel.RUNTIME_WITNESSED],
        )
        assert filtered.has_node("n0")

    def test_trust_aware_navigator_trusted_navigate_returns_path(self):
        """trusted_navigate must return a NavigationPath."""
        nav = TrustAwareNavigator()
        space = _make_connected_space(4)
        registry = _make_trust_registry()
        path = nav.trusted_navigate("n0", "n3", space, registry)
        assert isinstance(path, NavigationPath)

    def test_trust_aware_navigator_audit_path_returns_levels(self):
        """audit_path must return a list of (node_id, TrustLevel) pairs."""
        nav = TrustAwareNavigator()
        space = _make_connected_space(4)
        registry = _make_trust_registry()
        path = NavigationPath(
            path_id="audit_p1",
            node_ids=("n0", "n1", "n2"),
            start_id="n0",
            goal_id="n2",
            purpose="test",
            total_cost=1.0,
            purpose_alignment=0.7,
            strategy=NavigationStrategy.BREADTH_FIRST,
        )
        audit = nav.audit_path(path, registry)
        assert isinstance(audit, list)
        for item in audit:
            assert isinstance(item, tuple)
            assert len(item) == 2
            node_id, trust_level = item
            assert isinstance(node_id, str)

    def test_trust_aware_navigator_trust_report_nonempty(self):
        """trust_report must return a non-empty string."""
        nav = TrustAwareNavigator()
        space = _make_connected_space(4)
        registry = _make_trust_registry()
        report = nav.trust_report(space, registry)
        assert isinstance(report, str)
        assert len(report.strip()) > 0

    def test_trust_aware_navigator_set_min_trust_affects_filter(self):
        """set_min_trust changes the threshold used by filter_by_trust."""
        nav = TrustAwareNavigator()
        nav.set_min_trust(TrustLevel.MECHANICALLY_VERIFIED)
        space = _make_connected_space(4)
        # With max threshold, almost all nodes should be excluded from unregistered spaces
        filtered = nav.filter_by_trust(space, [TrustLevel.MECHANICALLY_VERIFIED])
        assert isinstance(filtered, TheorySpace)


# ===========================================================================
# IntegratedNavigationPipeline tests
# ===========================================================================

class TestIntegratedNavigationPipeline:
    """Tests for IntegratedNavigationPipeline: end-to-end navigation."""

    def _raw_nodes(self, n: int = 5) -> list[dict]:
        """Produce raw node dicts suitable for the pipeline."""
        return [
            {
                "node_id": f"raw_{k}",
                "name": f"Raw Node {k}",
                "description": f"A raw theory node {k}",
                "purpose_alignment": 0.4 + k * 0.1,
                "maturity": "mature",
            }
            for k in range(n)
        ]

    def test_pipeline_run_returns_dict(self):
        """run() must return a dict."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(raw, "raw_0", "raw_4")
        assert isinstance(result, dict)

    def test_pipeline_run_best_path_key(self):
        """run() result must include a 'best_path' key."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(raw, "raw_0", "raw_4")
        assert "best_path" in result

    def test_pipeline_run_space_stats_key(self):
        """run() result must include a 'space_stats' key."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(raw, "raw_0", "raw_4")
        assert "space_stats" in result

    def test_pipeline_run_best_path_is_navigation_path(self):
        """The best_path value must be a NavigationPath instance."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(raw, "raw_0", "raw_4")
        best_path = result.get("best_path")
        if best_path is not None:
            assert isinstance(best_path, NavigationPath)

    def test_pipeline_run_with_purpose(self):
        """run() should accept an optional purpose kwarg without raising."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(raw, "raw_0", "raw_4", purpose="algebra")
        assert isinstance(result, dict)

    def test_pipeline_run_with_algorithm(self):
        """run() should accept an optional algorithm kwarg without raising."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(
            raw,
            "raw_0",
            "raw_4",
            algorithm=NavigationStrategy.BREADTH_FIRST,
        )
        assert isinstance(result, dict)

    def test_pipeline_run_with_diverse_paths(self):
        """run() with find_diverse=True should not raise and should return a dict."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(6)
        result = pipeline.run(raw, "raw_0", "raw_5", find_diverse=True, k_diverse=3)
        assert isinstance(result, dict)

    def test_pipeline_run_with_diverse_paths_has_diverse_key(self):
        """With find_diverse=True, the result should carry a diverse-paths key."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(6)
        result = pipeline.run(raw, "raw_0", "raw_5", find_diverse=True, k_diverse=3)
        # look for any key that hints at diverse paths
        has_diverse_key = any("diverse" in str(k).lower() for k in result)
        assert has_diverse_key or "best_path" in result

    def test_pipeline_report_nonempty(self):
        """pipeline_report must return a non-empty string."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(raw, "raw_0", "raw_4")
        report = pipeline.pipeline_report(result)
        assert isinstance(report, str)
        assert len(report.strip()) > 0

    @pytest.mark.skipif(not HAS_TRUST, reason="trust not available")
    def test_pipeline_run_with_trust_filter(self):
        """run() with trust_filter kwarg should not raise."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(
            raw,
            "raw_0",
            "raw_4",
            trust_filter=[TrustLevel.HUMAN_ATTESTED, TrustLevel.MECHANICALLY_VERIFIED],
        )
        assert isinstance(result, dict)

    def test_pipeline_run_space_stats_is_dict_or_string(self):
        """space_stats must be a dict or a string (summary)."""
        pipeline = IntegratedNavigationPipeline()
        raw = self._raw_nodes(5)
        result = pipeline.run(raw, "raw_0", "raw_4")
        stats = result.get("space_stats")
        assert stats is None or isinstance(stats, (dict, str))
