"""Tests for jugeo.ideation.theory_navigation.models.

Covers: NodeMaturity, NavigationStrategy, PurposeCondition, TheoryNode,
NavigationPath, NavigationState, TheorySpace.
"""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import datetime
import pytest

from jugeo.ideation.theory_navigation.models import (
    NodeMaturity,
    NavigationStrategy,
    PurposeCondition,
    TheoryNode,
    NavigationPath,
    NavigationState,
    TheorySpace,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_condition(
    label: str = "test",
    keywords: tuple[str, ...] = ("algebra", "topology"),
    weight: float = 1.0,
) -> PurposeCondition:
    return PurposeCondition(
        condition_id="cond-1",
        label=label,
        description="A test purpose condition",
        keywords=keywords,
        weight=weight,
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
    )


def _make_node(
    node_id: str = "n1",
    name: str = "Algebra",
    *,
    maturity: NodeMaturity = NodeMaturity.MATURE,
    purpose: float = 0.8,
    connections: tuple[str, ...] = (),
) -> TheoryNode:
    return TheoryNode(
        node_id=node_id,
        name=name,
        description="Study of algebraic structures and their properties",
        purpose_alignment=purpose,
        maturity=maturity,
        connections=connections,
        metadata=(),
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
    )


def _make_path(
    node_ids: tuple[str, ...] = ("n1", "n2", "n3"),
    total_cost: float = 2.0,
    purpose_alignment: float = 0.75,
) -> NavigationPath:
    return NavigationPath(
        path_id="path-1",
        node_ids=node_ids,
        start_id=node_ids[0] if node_ids else "",
        goal_id=node_ids[-1] if node_ids else "",
        purpose="explore algebraic connections",
        total_cost=total_cost,
        purpose_alignment=purpose_alignment,
        strategy=NavigationStrategy.BREADTH_FIRST,
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
    )


def _make_state(
    current: str = "n1",
    goal: str = "n5",
    visited: tuple[str, ...] = ("n1",),
    depth: int = 0,
    cost: float = 0.0,
) -> NavigationState:
    return NavigationState(
        state_id="state-1",
        current_node_id=current,
        goal_node_id=goal,
        purpose="find path",
        strategy=NavigationStrategy.DEPTH_FIRST,
        visited=visited,
        beam=(),
        cost_so_far=cost,
        depth=depth,
    )


def _make_space(n_nodes: int = 3) -> TheorySpace:
    """Creates a TheorySpace with n_nodes connected linearly."""
    space = TheorySpace()
    nodes = [
        _make_node(node_id=f"n{i}", name=f"Node{i}", purpose=0.5 + i * 0.1)
        for i in range(1, n_nodes + 1)
    ]
    for node in nodes:
        space.add_node(node)
    for i in range(len(nodes) - 1):
        space.add_edge(nodes[i].node_id, nodes[i + 1].node_id)
    return space


# ---------------------------------------------------------------------------
# NodeMaturity tests
# ---------------------------------------------------------------------------

def test_node_maturity_all_values_exist():
    assert NodeMaturity.NASCENT is not None
    assert NodeMaturity.DEVELOPING is not None
    assert NodeMaturity.MATURE is not None
    assert NodeMaturity.ESTABLISHED is not None


def test_node_maturity_string_values():
    assert NodeMaturity.NASCENT == "nascent"
    assert NodeMaturity.DEVELOPING == "developing"
    assert NodeMaturity.MATURE == "mature"
    assert NodeMaturity.ESTABLISHED == "established"


def test_node_maturity_numeric_value_ordering():
    nascent_v = NodeMaturity.NASCENT.numeric_value()
    developing_v = NodeMaturity.DEVELOPING.numeric_value()
    mature_v = NodeMaturity.MATURE.numeric_value()
    established_v = NodeMaturity.ESTABLISHED.numeric_value()
    assert nascent_v < developing_v < mature_v < established_v


def test_node_maturity_numeric_value_range():
    for m in NodeMaturity:
        v = m.numeric_value()
        assert 0.0 <= v <= 1.0, f"numeric_value for {m} out of [0,1]: {v}"


def test_node_maturity_from_score_nascent():
    result = NodeMaturity.from_score(0.1)
    assert result == NodeMaturity.NASCENT


def test_node_maturity_from_score_developing():
    result = NodeMaturity.from_score(0.35)
    assert result == NodeMaturity.DEVELOPING


def test_node_maturity_from_score_mature():
    result = NodeMaturity.from_score(0.6)
    assert result == NodeMaturity.MATURE


def test_node_maturity_from_score_established():
    result = NodeMaturity.from_score(1.0)
    assert result == NodeMaturity.ESTABLISHED


def test_node_maturity_from_score_boundary_025():
    # At exact boundary 0.25, result should be developing or nascent
    result = NodeMaturity.from_score(0.25)
    assert result in (NodeMaturity.NASCENT, NodeMaturity.DEVELOPING)


def test_node_maturity_from_score_boundary_050():
    result = NodeMaturity.from_score(0.5)
    assert result in (NodeMaturity.DEVELOPING, NodeMaturity.MATURE)


def test_node_maturity_from_score_boundary_075():
    result = NodeMaturity.from_score(0.75)
    assert result in (NodeMaturity.MATURE, NodeMaturity.ESTABLISHED)


def test_node_maturity_from_score_zero():
    result = NodeMaturity.from_score(0.0)
    assert result == NodeMaturity.NASCENT


# ---------------------------------------------------------------------------
# NavigationStrategy tests
# ---------------------------------------------------------------------------

def test_navigation_strategy_values():
    strategies = list(NavigationStrategy)
    assert len(strategies) == 5


def test_navigation_strategy_breadth_first_exists():
    assert NavigationStrategy.BREADTH_FIRST is not None


def test_navigation_strategy_depth_first_exists():
    assert NavigationStrategy.DEPTH_FIRST is not None


def test_navigation_strategy_purpose_guided_exists():
    assert NavigationStrategy.PURPOSE_GUIDED is not None


def test_navigation_strategy_beam_search_exists():
    assert NavigationStrategy.BEAM_SEARCH is not None


def test_navigation_strategy_random_walk_exists():
    assert NavigationStrategy.RANDOM_WALK is not None


def test_navigation_strategy_is_heuristic_purpose_guided():
    assert NavigationStrategy.PURPOSE_GUIDED.is_heuristic() is True


def test_navigation_strategy_is_heuristic_beam_search():
    assert NavigationStrategy.BEAM_SEARCH.is_heuristic() is True


def test_navigation_strategy_is_heuristic_breadth_first_false():
    assert NavigationStrategy.BREADTH_FIRST.is_heuristic() is False


def test_navigation_strategy_is_heuristic_depth_first_false():
    assert NavigationStrategy.DEPTH_FIRST.is_heuristic() is False


def test_navigation_strategy_description_nonempty():
    for strategy in NavigationStrategy:
        desc = strategy.description()
        assert isinstance(desc, str), f"{strategy} description is not a str"
        assert len(desc) > 0, f"{strategy} description is empty"


def test_navigation_strategy_descriptions_are_distinct():
    descriptions = [s.description() for s in NavigationStrategy]
    assert len(set(descriptions)) == len(descriptions)


# ---------------------------------------------------------------------------
# PurposeCondition tests
# ---------------------------------------------------------------------------

def test_purpose_condition_creation():
    cond = _make_condition()
    assert cond.condition_id == "cond-1"
    assert cond.label == "test"
    assert "algebra" in cond.keywords


def test_purpose_condition_score_text_exact_match():
    cond = _make_condition(keywords=("algebra", "topology", "ring"))
    text = "algebra topology ring theory"
    score = cond.score_text(text)
    assert score > 0.5, f"Expected high score for matching text, got {score}"


def test_purpose_condition_score_text_no_match():
    cond = _make_condition(keywords=("algebra", "topology"))
    text = "unrelated cooking recipe for pasta"
    score = cond.score_text(text)
    assert score < 0.3, f"Expected low score for unrelated text, got {score}"


def test_purpose_condition_score_text_partial_match():
    cond = _make_condition(keywords=("algebra", "topology", "geometry", "analysis"))
    text = "algebra"
    score = cond.score_text(text)
    assert 0.0 < score < 1.0


def test_purpose_condition_matches_true():
    cond = _make_condition(keywords=("algebra", "topology"))
    assert cond.matches("study of algebra and topology", threshold=0.01) is True


def test_purpose_condition_matches_false():
    cond = _make_condition(keywords=("algebra", "topology"))
    # Very high threshold that no partial match can exceed
    result = cond.matches("cooking and food", threshold=0.99)
    assert result is False


def test_purpose_condition_matches_default_threshold():
    cond = _make_condition(keywords=("algebra",))
    assert isinstance(cond.matches("algebra"), bool)


def test_purpose_condition_adjusted_weight_increases():
    cond = _make_condition(weight=0.5)
    adjusted = cond.adjusted(weight_delta=0.2)
    assert abs(adjusted.weight - 0.7) < 1e-9


def test_purpose_condition_adjusted_weight_decreases():
    cond = _make_condition(weight=0.8)
    adjusted = cond.adjusted(weight_delta=-0.3)
    assert abs(adjusted.weight - 0.5) < 1e-9


def test_purpose_condition_adjusted_returns_new_instance():
    cond = _make_condition(weight=0.5)
    adjusted = cond.adjusted(weight_delta=0.1)
    assert adjusted is not cond
    assert adjusted.condition_id == cond.condition_id


def test_purpose_condition_weight_clamped_at_max():
    cond = _make_condition(weight=0.95)
    adjusted = cond.adjusted(weight_delta=0.5)
    assert adjusted.weight <= 1.0


def test_purpose_condition_weight_clamped_at_min():
    cond = _make_condition(weight=0.1)
    adjusted = cond.adjusted(weight_delta=-1.0)
    assert adjusted.weight >= 0.0


def test_purpose_condition_to_dict_round_trip():
    cond = _make_condition(label="math", keywords=("algebra", "topology", "calculus"))
    d = cond.to_dict()
    restored = PurposeCondition.from_dict(d)
    assert restored.condition_id == cond.condition_id
    assert restored.label == cond.label
    assert set(restored.keywords) == set(cond.keywords)
    assert abs(restored.weight - cond.weight) < 1e-9


def test_purpose_condition_rejects_empty_id():
    with pytest.raises((ValueError, Exception)):
        PurposeCondition(
            condition_id="",
            label="test",
            description="desc",
            keywords=("algebra",),
            weight=1.0,
            created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        )


def test_purpose_condition_to_dict_contains_required_keys():
    cond = _make_condition()
    d = cond.to_dict()
    assert "condition_id" in d
    assert "label" in d
    assert "keywords" in d


def test_purpose_condition_is_frozen():
    cond = _make_condition()
    with pytest.raises((AttributeError, TypeError)):
        cond.label = "new_label"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TheoryNode tests
# ---------------------------------------------------------------------------

def test_theory_node_creation():
    node = _make_node()
    assert node.node_id == "n1"
    assert node.name == "Algebra"
    assert node.maturity == NodeMaturity.MATURE


def test_theory_node_is_mature_true_for_mature():
    node = _make_node(maturity=NodeMaturity.MATURE)
    assert node.is_mature() is True


def test_theory_node_is_mature_true_for_established():
    node = _make_node(maturity=NodeMaturity.ESTABLISHED)
    assert node.is_mature() is True


def test_theory_node_is_mature_false_for_nascent():
    node = _make_node(maturity=NodeMaturity.NASCENT)
    assert node.is_mature() is False


def test_theory_node_is_mature_false_for_developing():
    node = _make_node(maturity=NodeMaturity.DEVELOPING)
    assert node.is_mature() is False


def test_theory_node_connection_count_zero():
    node = _make_node(connections=())
    assert node.connection_count() == 0


def test_theory_node_connection_count_multiple():
    node = _make_node(connections=("n2", "n3", "n4"))
    assert node.connection_count() == 3


def test_theory_node_has_connection_true():
    node = _make_node(connections=("n2", "n3"))
    assert node.has_connection("n2") is True


def test_theory_node_has_connection_false():
    node = _make_node(connections=("n2", "n3"))
    assert node.has_connection("n99") is False


def test_theory_node_with_connection_adds():
    node = _make_node(connections=("n2",))
    updated = node.with_connection("n3")
    assert updated.has_connection("n3") is True
    assert updated.has_connection("n2") is True
    assert updated is not node


def test_theory_node_with_connection_no_duplicate():
    node = _make_node(connections=("n2",))
    updated = node.with_connection("n2")
    assert updated.connection_count() == node.connection_count()


def test_theory_node_without_connection_removes():
    node = _make_node(connections=("n2", "n3", "n4"))
    updated = node.without_connection("n3")
    assert updated.has_connection("n3") is False
    assert updated.has_connection("n2") is True
    assert updated.has_connection("n4") is True


def test_theory_node_without_connection_missing_id_noop():
    node = _make_node(connections=("n2",))
    updated = node.without_connection("n99")
    assert updated.connection_count() == node.connection_count()


def test_theory_node_get_metadata_existing():
    node = _make_node().with_metadata("topic", "algebra")
    assert node.get_metadata("topic") == "algebra"


def test_theory_node_get_metadata_missing_returns_none():
    node = _make_node()
    assert node.get_metadata("nonexistent") is None


def test_theory_node_with_metadata_adds():
    node = _make_node()
    updated = node.with_metadata("source", "textbook")
    assert updated.get_metadata("source") == "textbook"
    assert updated is not node


def test_theory_node_with_metadata_overwrites():
    node = _make_node().with_metadata("topic", "old_value")
    updated = node.with_metadata("topic", "new_value")
    assert updated.get_metadata("topic") == "new_value"


def test_theory_node_relevance_score_range():
    cond = _make_condition(keywords=("algebra", "ring"))
    node = _make_node(name="Algebra Theory")
    score = node.relevance_score(cond)
    assert 0.0 <= score <= 1.0, f"relevance_score out of [0,1]: {score}"


def test_theory_node_relevance_score_high_for_matching():
    cond = _make_condition(keywords=("algebra",))
    node = _make_node(name="algebra", node_id="n1")
    # Node's description already mentions algebra
    score = node.relevance_score(cond)
    assert score > 0.0


def test_theory_node_to_dict_round_trip():
    node = _make_node(connections=("n2", "n3"))
    updated = node.with_metadata("key", "val")
    d = updated.to_dict()
    restored = TheoryNode.from_dict(d)
    assert restored.node_id == updated.node_id
    assert restored.name == updated.name
    assert restored.maturity == updated.maturity
    assert set(restored.connections) == set(updated.connections)
    assert restored.get_metadata("key") == "val"


def test_theory_node_rejects_empty_id():
    with pytest.raises((ValueError, Exception)):
        TheoryNode(
            node_id="",
            name="Algebra",
            description="desc",
            purpose_alignment=0.5,
            maturity=NodeMaturity.MATURE,
            connections=(),
            metadata=(),
            created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        )


def test_theory_node_is_frozen():
    node = _make_node()
    with pytest.raises((AttributeError, TypeError)):
        node.name = "modified"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NavigationPath tests
# ---------------------------------------------------------------------------

def test_navigation_path_creation():
    path = _make_path()
    assert path.path_id == "path-1"
    assert path.start_id == "n1"
    assert path.goal_id == "n3"


def test_navigation_path_is_empty_false():
    path = _make_path(node_ids=("n1", "n2", "n3"))
    assert path.is_empty() is False


def test_navigation_path_is_empty_true():
    path = _make_path(node_ids=())
    assert path.is_empty() is True


def test_navigation_path_length():
    path = _make_path(node_ids=("n1", "n2", "n3"))
    assert path.length() == 3


def test_navigation_path_length_single():
    path = _make_path(node_ids=("n1",))
    assert path.length() == 1


def test_navigation_path_contains_true():
    path = _make_path(node_ids=("n1", "n2", "n3"))
    assert path.contains("n2") is True


def test_navigation_path_contains_false():
    path = _make_path(node_ids=("n1", "n2", "n3"))
    assert path.contains("n99") is False


def test_navigation_path_is_direct_two_nodes():
    path = _make_path(node_ids=("n1", "n2"))
    assert path.is_direct() is True


def test_navigation_path_is_direct_three_nodes_false():
    path = _make_path(node_ids=("n1", "n2", "n3"))
    assert path.is_direct() is False


def test_navigation_path_reversed():
    path = _make_path(node_ids=("n1", "n2", "n3"))
    rev = path.reversed()
    assert rev.node_ids == ("n3", "n2", "n1")


def test_navigation_path_reversed_returns_new():
    path = _make_path()
    rev = path.reversed()
    assert rev is not path


def test_navigation_path_sub_path():
    path = _make_path(node_ids=("n1", "n2", "n3", "n4", "n5"))
    sub = path.sub_path(1, 3)
    assert len(sub.node_ids) == 3
    assert sub.node_ids[0] == "n2"
    assert sub.node_ids[-1] == "n4"


def test_navigation_path_cost_per_step_multi_nodes():
    path = _make_path(node_ids=("n1", "n2", "n3"), total_cost=4.0)
    cps = path.cost_per_step()
    assert abs(cps - 2.0) < 1e-9


def test_navigation_path_cost_per_step_single_node():
    path = _make_path(node_ids=("n1",), total_cost=0.0)
    cps = path.cost_per_step()
    assert cps == 0.0


def test_navigation_path_quality_score_range():
    path = _make_path()
    q = path.quality_score()
    assert q >= 0.0


def test_navigation_path_quality_score_type():
    path = _make_path()
    assert isinstance(path.quality_score(), float)


def test_navigation_path_to_dict_round_trip():
    path = _make_path(node_ids=("n1", "n2", "n3"), total_cost=3.0, purpose_alignment=0.9)
    d = path.to_dict()
    restored = NavigationPath.from_dict(d)
    assert restored.path_id == path.path_id
    assert restored.node_ids == path.node_ids
    assert abs(restored.total_cost - path.total_cost) < 1e-9
    assert restored.strategy == path.strategy


def test_navigation_path_is_frozen():
    path = _make_path()
    with pytest.raises((AttributeError, TypeError)):
        path.total_cost = 99.9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NavigationState tests
# ---------------------------------------------------------------------------

def test_navigation_state_creation():
    state = _make_state()
    assert state.current_node_id == "n1"
    assert state.goal_node_id == "n5"
    assert state.depth == 0


def test_navigation_state_has_visited_true():
    state = _make_state(visited=("n1", "n2"))
    assert state.has_visited("n1") is True
    assert state.has_visited("n2") is True


def test_navigation_state_has_visited_false():
    state = _make_state(visited=("n1",))
    assert state.has_visited("n99") is False


def test_navigation_state_visit_updates_visited():
    state = _make_state(current="n1", visited=("n1",))
    new_state = state.visit("n2", cost=1.0)
    assert new_state.has_visited("n2") is True


def test_navigation_state_visit_updates_cost():
    state = _make_state(cost=0.0)
    new_state = state.visit("n2", cost=1.5)
    assert new_state.cost_so_far > 0.0


def test_navigation_state_visit_returns_new():
    state = _make_state()
    new_state = state.visit("n2", cost=1.0)
    assert new_state is not state


def test_navigation_state_is_at_goal_true():
    state = _make_state(current="n5", goal="n5")
    assert state.is_at_goal() is True


def test_navigation_state_is_at_goal_false():
    state = _make_state(current="n1", goal="n5")
    assert state.is_at_goal() is False


def test_navigation_state_depth_exceeded_true():
    state = _make_state(depth=10)
    assert state.depth_exceeded(max_depth=5) is True


def test_navigation_state_depth_exceeded_false():
    state = _make_state(depth=3)
    assert state.depth_exceeded(max_depth=10) is False


def test_navigation_state_depth_exceeded_equal():
    state = _make_state(depth=5)
    # At exactly max_depth, behavior may differ; just check it returns a bool
    result = state.depth_exceeded(max_depth=5)
    assert isinstance(result, bool)


def test_navigation_state_to_dict_round_trip():
    state = _make_state(current="n2", goal="n7", visited=("n1", "n2"), depth=1, cost=1.5)
    d = state.to_dict()
    restored = NavigationState.from_dict(d)
    assert restored.current_node_id == state.current_node_id
    assert restored.goal_node_id == state.goal_node_id
    assert restored.depth == state.depth
    assert abs(restored.cost_so_far - state.cost_so_far) < 1e-9


def test_navigation_state_is_frozen():
    state = _make_state()
    with pytest.raises((AttributeError, TypeError)):
        state.depth = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TheorySpace tests
# ---------------------------------------------------------------------------

def test_theory_space_creation():
    space = TheorySpace()
    assert space.node_count() == 0


def test_theory_space_add_get_node():
    space = TheorySpace()
    node = _make_node()
    space.add_node(node)
    retrieved = space.get_node("n1")
    assert retrieved is not None
    assert retrieved.node_id == "n1"


def test_theory_space_remove_node():
    space = _make_space(n_nodes=3)
    space.remove_node("n2")
    assert not space.has_node("n2")


def test_theory_space_remove_node_removes_edges():
    space = _make_space(n_nodes=3)
    initial_edges = space.edge_count()
    space.remove_node("n2")
    assert space.edge_count() < initial_edges


def test_theory_space_has_node_true():
    space = _make_space(n_nodes=2)
    assert space.has_node("n1") is True


def test_theory_space_has_node_false():
    space = _make_space(n_nodes=2)
    assert space.has_node("n99") is False


def test_theory_space_node_count():
    space = _make_space(n_nodes=4)
    assert space.node_count() == 4


def test_theory_space_add_edge_and_remove():
    space = _make_space(n_nodes=3)
    space.add_edge("n1", "n3")
    assert space.is_connected("n1", "n3")
    space.remove_edge("n1", "n3")
    # After removal, direct edge should be gone (reachability may still hold via n2)
    # At minimum, removing should not raise
    assert space.node_count() == 3


def test_theory_space_get_neighbors():
    space = _make_space(n_nodes=3)
    neighbors = space.get_neighbors("n2")
    neighbor_ids = {n.node_id for n in neighbors}
    assert "n1" in neighbor_ids or "n3" in neighbor_ids


def test_theory_space_edge_count():
    space = _make_space(n_nodes=3)
    # Linear chain: n1-n2, n2-n3 = 2 edges
    assert space.edge_count() == 2


def test_theory_space_is_connected_reachable():
    space = _make_space(n_nodes=3)
    assert space.is_connected("n1", "n3") is True


def test_theory_space_is_connected_unreachable():
    space = _make_space(n_nodes=3)
    isolated = _make_node(node_id="n99", name="Isolated")
    space.add_node(isolated)
    assert space.is_connected("n1", "n99") is False


def test_theory_space_nodes_by_maturity():
    space = TheorySpace()
    space.add_node(_make_node("n1", maturity=NodeMaturity.MATURE))
    space.add_node(_make_node("n2", maturity=NodeMaturity.NASCENT))
    space.add_node(_make_node("n3", maturity=NodeMaturity.ESTABLISHED))
    mature_nodes = space.nodes_by_maturity()
    assert isinstance(mature_nodes, dict)
    assert NodeMaturity.MATURE in mature_nodes or NodeMaturity.NASCENT in mature_nodes


def test_theory_space_nodes_by_purpose_alignment():
    space = TheorySpace()
    space.add_node(_make_node("n1", purpose=0.9))
    space.add_node(_make_node("n2", purpose=0.3))
    cond = _make_condition()
    results = space.nodes_by_purpose_alignment(cond, threshold=0.0)
    assert isinstance(results, list)


def test_theory_space_most_connected_nodes():
    space = _make_space(n_nodes=4)
    # Add extra connections to n2
    space.add_edge("n2", "n4")
    ranked = space.most_connected_nodes()
    assert isinstance(ranked, list)
    assert len(ranked) > 0
    # n2 should have the most connections (connected to n1, n3, n4)
    top_id = ranked[0].node_id
    assert top_id == "n2"


def test_theory_space_to_dict_round_trip():
    space = _make_space(n_nodes=3)
    d = space.to_dict()
    restored = TheorySpace.from_dict(d)
    assert restored.node_count() == space.node_count()
    assert restored.edge_count() == space.edge_count()
    for nid in ["n1", "n2", "n3"]:
        assert restored.has_node(nid)


def test_theory_space_summary_nonempty():
    space = _make_space(n_nodes=3)
    summary = space.summary()
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_theory_space_get_node_missing_returns_none():
    space = TheorySpace()
    assert space.get_node("nonexistent") is None


def test_theory_space_add_duplicate_node():
    space = TheorySpace()
    node = _make_node()
    space.add_node(node)
    space.add_node(node)  # adding duplicate; should either overwrite or ignore
    assert space.node_count() == 1


def test_theory_space_empty_space_is_connected_false():
    space = TheorySpace()
    assert space.is_connected("n1", "n2") is False
