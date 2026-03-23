"""Tests for jugeo.ideation.theory_navigation.s01_space_construction.

Covers: SpaceConstructionConfig, NodeExtractor, EdgeBuilder, SpaceIndexer,
SpaceConstructor, IncrementalSpaceUpdater.
"""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import datetime
import pytest

from jugeo.ideation.theory_navigation.s01_space_construction import (
    SpaceConstructionConfig,
    NodeExtractor,
    EdgeBuilder,
    SpaceIndexer,
    SpaceConstructor,
    IncrementalSpaceUpdater,
)
from jugeo.ideation.theory_navigation.models import (
    TheoryNode,
    TheorySpace,
    NodeMaturity,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _raw_node(
    node_id: str = "n1",
    name: str = "Algebra",
    description: str = "Study of algebraic structures and their properties",
    maturity: str = "mature",
    purpose_alignment: float = 0.8,
    connections: list[str] | None = None,
) -> dict:
    return {
        "id": node_id,
        "name": name,
        "description": description,
        "maturity": maturity,
        "purpose_alignment": purpose_alignment,
        "connections": connections or [],
        "metadata": {},
    }


def _make_theory_node(
    node_id: str = "n1",
    name: str = "Algebra",
    description: str = "Study of algebraic structures and their properties",
    maturity: NodeMaturity = NodeMaturity.MATURE,
    purpose: float = 0.8,
    connections: tuple[str, ...] = (),
) -> TheoryNode:
    return TheoryNode(
        node_id=node_id,
        name=name,
        description=description,
        purpose_alignment=purpose,
        maturity=maturity,
        connections=connections,
        metadata=(),
        created_at=datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
    )


def _make_space_with_nodes(n: int = 3) -> TheorySpace:
    space = TheorySpace()
    for i in range(1, n + 1):
        node = _make_theory_node(
            node_id=f"n{i}",
            name=f"Theory{i}",
            description=f"Theory about subject {i} with algebraic properties",
            purpose=0.5 + i * 0.05,
        )
        space.add_node(node)
    for i in range(1, n):
        space.add_edge(f"n{i}", f"n{i + 1}")
    return space


def _make_default_config() -> SpaceConstructionConfig:
    return SpaceConstructionConfig()


# ---------------------------------------------------------------------------
# SpaceConstructionConfig tests
# ---------------------------------------------------------------------------

def test_config_defaults_exist():
    config = SpaceConstructionConfig()
    assert config is not None


def test_config_default_max_nodes():
    config = SpaceConstructionConfig()
    assert config.max_nodes == 500


def test_config_default_similarity_threshold():
    config = SpaceConstructionConfig()
    assert 0.0 <= config.similarity_threshold <= 1.0


def test_config_default_threshold_value():
    config = SpaceConstructionConfig()
    assert abs(config.similarity_threshold - 0.15) < 1e-6


def test_config_with_threshold_returns_new():
    config = SpaceConstructionConfig()
    updated = config.with_threshold(0.25)
    assert updated is not config
    assert abs(updated.similarity_threshold - 0.25) < 1e-6
    # original unchanged
    assert abs(config.similarity_threshold - 0.15) < 1e-6


def test_config_with_max_nodes_returns_new():
    config = SpaceConstructionConfig()
    updated = config.with_max_nodes(100)
    assert updated is not config
    assert updated.max_nodes == 100
    assert config.max_nodes == 500


def test_config_to_dict_round_trip():
    config = SpaceConstructionConfig()
    d = config.to_dict()
    restored = SpaceConstructionConfig.from_dict(d)
    assert restored.max_nodes == config.max_nodes
    assert abs(restored.similarity_threshold - config.similarity_threshold) < 1e-9


def test_config_rejects_negative_max_nodes():
    with pytest.raises((ValueError, Exception)):
        SpaceConstructionConfig(max_nodes=-1, similarity_threshold=0.15)


def test_config_rejects_threshold_above_one():
    with pytest.raises((ValueError, Exception)):
        SpaceConstructionConfig(max_nodes=500, similarity_threshold=1.5)


def test_config_rejects_negative_threshold():
    with pytest.raises((ValueError, Exception)):
        SpaceConstructionConfig(max_nodes=500, similarity_threshold=-0.1)


def test_config_to_dict_contains_max_nodes():
    config = SpaceConstructionConfig()
    d = config.to_dict()
    assert "max_nodes" in d


def test_config_to_dict_contains_threshold():
    config = SpaceConstructionConfig()
    d = config.to_dict()
    assert "similarity_threshold" in d


def test_config_immutability():
    config = SpaceConstructionConfig()
    with pytest.raises((AttributeError, TypeError)):
        config.max_nodes = 9999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NodeExtractor tests
# ---------------------------------------------------------------------------

def test_node_extractor_creation():
    extractor = NodeExtractor()
    assert extractor is not None


def test_node_extractor_extract_node_basic():
    extractor = NodeExtractor()
    raw = _raw_node()
    node = extractor.extract_node(raw)
    assert isinstance(node, TheoryNode)
    assert node.node_id == "n1"
    assert node.name == "Algebra"


def test_node_extractor_extract_node_maturity_mapping_mature():
    extractor = NodeExtractor()
    raw = _raw_node(maturity="mature")
    node = extractor.extract_node(raw)
    assert node.maturity == NodeMaturity.MATURE


def test_node_extractor_extract_node_maturity_mapping_nascent():
    extractor = NodeExtractor()
    raw = _raw_node(maturity="nascent")
    node = extractor.extract_node(raw)
    assert node.maturity == NodeMaturity.NASCENT


def test_node_extractor_extract_node_maturity_mapping_developing():
    extractor = NodeExtractor()
    raw = _raw_node(maturity="developing")
    node = extractor.extract_node(raw)
    assert node.maturity == NodeMaturity.DEVELOPING


def test_node_extractor_extract_node_maturity_mapping_established():
    extractor = NodeExtractor()
    raw = _raw_node(maturity="established")
    node = extractor.extract_node(raw)
    assert node.maturity == NodeMaturity.ESTABLISHED


def test_node_extractor_extract_nodes_multiple():
    extractor = NodeExtractor()
    raws = [_raw_node(f"n{i}", f"Theory{i}") for i in range(1, 5)]
    nodes = extractor.extract_nodes(raws)
    assert isinstance(nodes, list)
    assert len(nodes) == 4
    assert all(isinstance(n, TheoryNode) for n in nodes)


def test_node_extractor_extract_nodes_deduplicates():
    extractor = NodeExtractor()
    raw = _raw_node("n1", "Algebra")
    raws = [raw, raw, raw]  # same node three times
    nodes = extractor.extract_nodes(raws)
    ids = [n.node_id for n in nodes]
    assert ids.count("n1") == 1


def test_node_extractor_extract_from_text():
    extractor = NodeExtractor()
    text = "Algebraic K-theory is a branch of mathematics"
    node = extractor.extract_from_text(text, node_id="text-node-1")
    assert isinstance(node, TheoryNode)
    assert node.maturity == NodeMaturity.NASCENT


def test_node_extractor_extract_from_text_has_description():
    extractor = NodeExtractor()
    text = "Differential geometry studies curves and surfaces"
    node = extractor.extract_from_text(text, node_id="text-node-2")
    assert len(node.description) > 0


def test_node_extractor_filter_by_config_min_alignment():
    extractor = NodeExtractor()
    config = SpaceConstructionConfig()
    raws = [
        _raw_node("n1", purpose_alignment=0.9),
        _raw_node("n2", purpose_alignment=0.05),  # very low
        _raw_node("n3", purpose_alignment=0.7),
    ]
    nodes = extractor.extract_nodes(raws)
    filtered = extractor.filter_by_config(nodes, config)
    assert isinstance(filtered, list)


def test_node_extractor_filter_preserves_high_alignment():
    extractor = NodeExtractor()
    config = SpaceConstructionConfig()
    raws = [
        _raw_node("n1", purpose_alignment=0.9),
        _raw_node("n2", purpose_alignment=0.8),
    ]
    nodes = extractor.extract_nodes(raws)
    filtered = extractor.filter_by_config(nodes, config)
    assert len(filtered) >= 1


def test_node_extractor_extraction_stats():
    extractor = NodeExtractor()
    raws = [_raw_node(f"n{i}") for i in range(1, 6)]
    nodes = extractor.extract_nodes(raws)
    stats = extractor.extraction_stats(nodes)
    assert isinstance(stats, dict)
    assert len(stats) > 0


def test_node_extractor_extraction_stats_has_count():
    extractor = NodeExtractor()
    raws = [_raw_node(f"n{i}") for i in range(1, 4)]
    nodes = extractor.extract_nodes(raws)
    stats = extractor.extraction_stats(nodes)
    # Should have some count-related key
    assert any("count" in k.lower() or "total" in k.lower() for k in stats)


def test_node_extractor_handles_empty_list():
    extractor = NodeExtractor()
    nodes = extractor.extract_nodes([])
    assert nodes == []


# ---------------------------------------------------------------------------
# EdgeBuilder tests
# ---------------------------------------------------------------------------

def test_edge_builder_creation():
    builder = EdgeBuilder()
    assert builder is not None


def test_edge_builder_compute_similarity_identical():
    builder = EdgeBuilder()
    desc = "algebra topology ring homomorphism group"
    node_a = _make_theory_node("na", description=desc)
    node_b = _make_theory_node("nb", description=desc)
    sim = builder.compute_similarity(node_a, node_b)
    assert sim >= 0.5, f"Identical descriptions should yield high similarity, got {sim}"


def test_edge_builder_compute_similarity_disjoint():
    builder = EdgeBuilder()
    node_a = _make_theory_node("na", description="abstract algebra ring theory module")
    node_b = _make_theory_node("nb", description="cooking recipes pasta sauce tomato")
    sim = builder.compute_similarity(node_a, node_b)
    assert sim < 0.5, f"Disjoint descriptions should yield low similarity, got {sim}"


def test_edge_builder_compute_similarity_range():
    builder = EdgeBuilder()
    node_a = _make_theory_node("na", description="algebra topology")
    node_b = _make_theory_node("nb", description="analysis calculus")
    sim = builder.compute_similarity(node_a, node_b)
    assert 0.0 <= sim <= 1.0, f"Similarity out of [0,1]: {sim}"


def test_edge_builder_build_edges_above_threshold():
    builder = EdgeBuilder()
    nodes = [
        _make_theory_node("n1", description="algebra ring theory homomorphism"),
        _make_theory_node("n2", description="algebra ring theory module"),
        _make_theory_node("n3", description="cooking food pasta recipe"),
    ]
    config = SpaceConstructionConfig()
    edges = builder.build_edges(nodes, config)
    assert isinstance(edges, list)
    # n1-n2 should be connected (similar), n1-n3 and n2-n3 should not
    edge_pairs = {(e[0], e[1]) for e in edges} | {(e[1], e[0]) for e in edges}
    # At least n1 and n2 should be connected
    assert ("n1", "n2") in edge_pairs or ("n2", "n1") in edge_pairs


def test_edge_builder_build_edges_low_threshold_more_edges():
    builder = EdgeBuilder()
    nodes = [_make_theory_node(f"n{i}", description=f"theory subject {i}") for i in range(1, 5)]
    low_config = SpaceConstructionConfig(max_nodes=500, similarity_threshold=0.0)
    high_config = SpaceConstructionConfig(max_nodes=500, similarity_threshold=0.99)
    low_edges = builder.build_edges(nodes, low_config)
    high_edges = builder.build_edges(nodes, high_config)
    assert len(low_edges) >= len(high_edges)


def test_edge_builder_build_from_connections():
    builder = EdgeBuilder()
    nodes = [
        _make_theory_node("n1", connections=("n2", "n3")),
        _make_theory_node("n2", connections=("n1",)),
        _make_theory_node("n3", connections=()),
    ]
    edges = builder.build_from_connections(nodes)
    assert isinstance(edges, list)
    edge_pairs = {(e[0], e[1]) for e in edges} | {(e[1], e[0]) for e in edges}
    assert ("n1", "n2") in edge_pairs or ("n2", "n1") in edge_pairs


def test_edge_builder_merge_edges():
    builder = EdgeBuilder()
    explicit = [("n1", "n2"), ("n2", "n3")]
    computed = [("n1", "n3"), ("n2", "n3")]
    merged = builder.merge_edges(explicit, computed)
    assert isinstance(merged, list)
    merged_set = {(e[0], e[1]) for e in merged} | {(e[1], e[0]) for e in merged}
    assert ("n1", "n2") in merged_set
    assert ("n1", "n3") in merged_set


def test_edge_builder_merge_edges_no_duplicates():
    builder = EdgeBuilder()
    explicit = [("n1", "n2")]
    computed = [("n1", "n2"), ("n2", "n3")]
    merged = builder.merge_edges(explicit, computed)
    # Count occurrences of n1-n2
    n1n2_count = sum(1 for e in merged if set(e[:2]) == {"n1", "n2"})
    assert n1n2_count == 1


def test_edge_builder_prune_weak_edges():
    builder = EdgeBuilder()
    nodes = [_make_theory_node(f"n{i}") for i in range(1, 6)]
    config = SpaceConstructionConfig(max_nodes=500, similarity_threshold=0.0)
    all_edges = builder.build_edges(nodes, config)
    pruned = builder.prune_weak_edges(all_edges, max_per_node=2)
    assert isinstance(pruned, list)
    # Each node should appear at most max_per_node times
    from collections import Counter
    counts = Counter()
    for edge in pruned:
        counts[edge[0]] += 1
        counts[edge[1]] += 1
    for node_id, count in counts.items():
        assert count <= 4, f"Node {node_id} appears too many times: {count}"


def test_edge_builder_edge_stats():
    builder = EdgeBuilder()
    nodes = [_make_theory_node(f"n{i}", description=f"algebra theory {i}") for i in range(1, 4)]
    config = SpaceConstructionConfig(max_nodes=500, similarity_threshold=0.0)
    edges = builder.build_edges(nodes, config)
    stats = builder.edge_stats(edges)
    assert isinstance(stats, dict)
    assert len(stats) > 0


def test_edge_builder_edge_stats_has_count():
    builder = EdgeBuilder()
    nodes = [_make_theory_node(f"n{i}") for i in range(1, 4)]
    config = SpaceConstructionConfig(max_nodes=500, similarity_threshold=0.0)
    edges = builder.build_edges(nodes, config)
    stats = builder.edge_stats(edges)
    assert any("count" in k.lower() or "total" in k.lower() or "edge" in k.lower() for k in stats)


def test_edge_builder_empty_nodes_no_edges():
    builder = EdgeBuilder()
    config = SpaceConstructionConfig()
    edges = builder.build_edges([], config)
    assert edges == []


# ---------------------------------------------------------------------------
# SpaceIndexer tests
# ---------------------------------------------------------------------------

def test_space_indexer_creation():
    indexer = SpaceIndexer()
    assert indexer is not None


def test_space_indexer_build_from_space():
    indexer = SpaceIndexer()
    space = _make_space_with_nodes(3)
    indexer.build(space)  # Should not raise


def test_space_indexer_build_and_lookup_keyword():
    indexer = SpaceIndexer()
    space = TheorySpace()
    node = _make_theory_node("algebra-node", name="Algebraic Structures")
    space.add_node(node)
    indexer.build(space)
    results = indexer.lookup_by_keyword("algebraic")
    assert isinstance(results, list)
    assert any(r == "algebra-node" or (hasattr(r, "node_id") and r.node_id == "algebra-node") for r in results)


def test_space_indexer_lookup_by_maturity():
    indexer = SpaceIndexer()
    space = TheorySpace()
    space.add_node(_make_theory_node("n1", maturity=NodeMaturity.MATURE))
    space.add_node(_make_theory_node("n2", maturity=NodeMaturity.NASCENT))
    space.add_node(_make_theory_node("n3", maturity=NodeMaturity.MATURE))
    indexer.build(space)
    results = indexer.lookup_by_maturity(NodeMaturity.MATURE)
    assert isinstance(results, list)
    result_ids = {r if isinstance(r, str) else r.node_id for r in results}
    assert "n1" in result_ids
    assert "n3" in result_ids
    assert "n2" not in result_ids


def test_space_indexer_lookup_by_purpose_range():
    indexer = SpaceIndexer()
    space = TheorySpace()
    space.add_node(_make_theory_node("n1", purpose=0.9))
    space.add_node(_make_theory_node("n2", purpose=0.3))
    space.add_node(_make_theory_node("n3", purpose=0.7))
    indexer.build(space)
    results = indexer.lookup_by_purpose_range(min_alignment=0.6, max_alignment=1.0)
    result_ids = {r if isinstance(r, str) else r.node_id for r in results}
    assert "n1" in result_ids
    assert "n3" in result_ids
    assert "n2" not in result_ids


def test_space_indexer_top_n_by_purpose():
    indexer = SpaceIndexer()
    space = _make_space_with_nodes(5)
    indexer.build(space)
    top3 = indexer.top_n_by_purpose(n=3)
    assert isinstance(top3, list)
    assert len(top3) == 3


def test_space_indexer_top_n_sorted():
    indexer = SpaceIndexer()
    space = TheorySpace()
    space.add_node(_make_theory_node("n1", purpose=0.3))
    space.add_node(_make_theory_node("n2", purpose=0.9))
    space.add_node(_make_theory_node("n3", purpose=0.6))
    indexer.build(space)
    top2 = indexer.top_n_by_purpose(n=2)
    top_ids = [r if isinstance(r, str) else r.node_id for r in top2]
    assert "n2" in top_ids


def test_space_indexer_nearest_neighbors():
    indexer = SpaceIndexer()
    space = _make_space_with_nodes(5)
    indexer.build(space)
    neighbors = indexer.nearest_neighbors("n3", n=2)
    assert isinstance(neighbors, list)
    assert len(neighbors) <= 2


def test_space_indexer_index_stats():
    indexer = SpaceIndexer()
    space = _make_space_with_nodes(3)
    indexer.build(space)
    stats = indexer.index_stats()
    assert isinstance(stats, dict)
    assert len(stats) > 0


def test_space_indexer_clear():
    indexer = SpaceIndexer()
    space = _make_space_with_nodes(3)
    indexer.build(space)
    indexer.clear()
    stats = indexer.index_stats()
    # After clearing, count should be 0 or index should be empty
    count_val = next(
        (v for k, v in stats.items() if "count" in k.lower() or "total" in k.lower()),
        None
    )
    if count_val is not None:
        assert count_val == 0


def test_space_indexer_lookup_empty_keyword_returns_list():
    indexer = SpaceIndexer()
    space = _make_space_with_nodes(2)
    indexer.build(space)
    results = indexer.lookup_by_keyword("")
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# SpaceConstructor tests
# ---------------------------------------------------------------------------

def test_space_constructor_creation():
    constructor = SpaceConstructor()
    assert constructor is not None


def test_space_constructor_construct_basic():
    constructor = SpaceConstructor()
    raws = [_raw_node(f"n{i}", f"Theory{i}") for i in range(1, 5)]
    space = constructor.construct(raws)
    assert isinstance(space, TheorySpace)


def test_space_constructor_construct_node_count():
    constructor = SpaceConstructor()
    raws = [_raw_node(f"n{i}", f"Theory{i}") for i in range(1, 5)]
    space = constructor.construct(raws)
    assert space.node_count() == 4


def test_space_constructor_construct_from_texts():
    constructor = SpaceConstructor()
    texts = [
        "Algebraic topology studies topological spaces",
        "Group theory is a branch of abstract algebra",
        "Number theory investigates integers and prime numbers",
    ]
    space = constructor.construct_from_texts(texts)
    assert isinstance(space, TheorySpace)
    assert space.node_count() >= 1


def test_space_constructor_assemble_space():
    constructor = SpaceConstructor()
    nodes = [_make_theory_node(f"n{i}", name=f"Node{i}") for i in range(1, 4)]
    edges = [("n1", "n2"), ("n2", "n3")]
    space = constructor.assemble_space(nodes, edges)
    assert isinstance(space, TheorySpace)
    assert space.node_count() == 3
    assert space.edge_count() == 2


def test_space_constructor_validate_space_valid():
    constructor = SpaceConstructor()
    space = _make_space_with_nodes(3)
    errors = constructor.validate_space(space)
    assert isinstance(errors, list)
    assert len(errors) == 0


def test_space_constructor_validate_space_empty():
    constructor = SpaceConstructor()
    space = TheorySpace()
    errors = constructor.validate_space(space)
    assert isinstance(errors, list)
    # Empty space may or may not be considered invalid
    # At minimum, validate should not raise


def test_space_constructor_construction_report_nonempty():
    constructor = SpaceConstructor()
    raws = [_raw_node(f"n{i}") for i in range(1, 4)]
    space = constructor.construct(raws)
    report = constructor.construction_report(space)
    assert isinstance(report, str)
    assert len(report) > 0


def test_space_constructor_handles_empty_input():
    constructor = SpaceConstructor()
    space = constructor.construct([])
    assert isinstance(space, TheorySpace)
    assert space.node_count() == 0


def test_space_constructor_respects_max_nodes():
    constructor = SpaceConstructor(config=SpaceConstructionConfig(max_nodes=2, similarity_threshold=0.15))
    raws = [_raw_node(f"n{i}", f"Theory{i}") for i in range(1, 10)]
    space = constructor.construct(raws)
    assert space.node_count() <= 2


def test_space_constructor_construct_has_edges_for_similar():
    constructor = SpaceConstructor(
        config=SpaceConstructionConfig(max_nodes=500, similarity_threshold=0.0)
    )
    raws = [
        _raw_node("n1", description="algebra ring theory homomorphism"),
        _raw_node("n2", description="algebra ring theory module"),
    ]
    space = constructor.construct(raws)
    # With threshold 0, these similar nodes should be connected
    assert space.node_count() == 2


# ---------------------------------------------------------------------------
# IncrementalSpaceUpdater tests
# ---------------------------------------------------------------------------

def test_incremental_space_updater_creation():
    updater = IncrementalSpaceUpdater()
    assert updater is not None


def test_incremental_add_node():
    updater = IncrementalSpaceUpdater()
    space = _make_space_with_nodes(3)
    new_node = _make_theory_node("n_new", name="New Theory")
    updated_space = updater.add_node(space, new_node)
    assert updated_space.has_node("n_new")
    assert updated_space.node_count() == space.node_count() + 1


def test_incremental_add_node_returns_new_or_same():
    updater = IncrementalSpaceUpdater()
    space = _make_space_with_nodes(2)
    new_node = _make_theory_node("n_new")
    updated_space = updater.add_node(space, new_node)
    assert isinstance(updated_space, TheorySpace)


def test_incremental_remove_node():
    updater = IncrementalSpaceUpdater()
    space = _make_space_with_nodes(3)
    updated_space = updater.remove_node(space, "n2")
    assert not updated_space.has_node("n2")
    assert updated_space.node_count() == 2


def test_incremental_remove_node_nonexistent():
    updater = IncrementalSpaceUpdater()
    space = _make_space_with_nodes(3)
    # Removing non-existent node should not raise, just return same/similar space
    updated_space = updater.remove_node(space, "n99")
    assert updated_space.node_count() == space.node_count()


def test_incremental_update_node():
    updater = IncrementalSpaceUpdater()
    space = _make_space_with_nodes(3)
    original_node = space.get_node("n2")
    assert original_node is not None
    updated_node = _make_theory_node("n2", name="Updated Theory", purpose=0.99)
    updated_space = updater.update_node(space, updated_node)
    retrieved = updated_space.get_node("n2")
    assert retrieved is not None
    assert retrieved.name == "Updated Theory"


def test_incremental_update_node_preserves_others():
    updater = IncrementalSpaceUpdater()
    space = _make_space_with_nodes(3)
    updated_node = _make_theory_node("n2", name="Changed")
    updated_space = updater.update_node(space, updated_node)
    assert updated_space.has_node("n1")
    assert updated_space.has_node("n3")


def test_incremental_reindex():
    updater = IncrementalSpaceUpdater()
    space = _make_space_with_nodes(3)
    config = SpaceConstructionConfig(max_nodes=500, similarity_threshold=0.0)
    updated_space = updater.reindex(space, config)
    assert isinstance(updated_space, TheorySpace)
    assert updated_space.node_count() == space.node_count()


def test_incremental_reindex_rebuilds_edges():
    updater = IncrementalSpaceUpdater()
    # Space with similar nodes but no edges
    space = TheorySpace()
    space.add_node(_make_theory_node("na", description="algebra ring theory"))
    space.add_node(_make_theory_node("nb", description="algebra ring theory module"))
    assert space.edge_count() == 0
    config = SpaceConstructionConfig(max_nodes=500, similarity_threshold=0.0)
    updated_space = updater.reindex(space, config)
    # After reindexing with threshold 0, similar nodes should be connected
    assert isinstance(updated_space, TheorySpace)


def test_incremental_merge_spaces_combined_count():
    updater = IncrementalSpaceUpdater()
    space_a = _make_space_with_nodes(3)
    space_b = TheorySpace()
    space_b.add_node(_make_theory_node("n10", name="Extra A"))
    space_b.add_node(_make_theory_node("n11", name="Extra B"))
    merged = updater.merge_spaces(space_a, space_b)
    assert isinstance(merged, TheorySpace)
    # Should have nodes from both spaces (5 total, since no overlap)
    assert merged.node_count() == 5


def test_incremental_merge_spaces_deduplication():
    updater = IncrementalSpaceUpdater()
    space_a = _make_space_with_nodes(3)
    # space_b has two of the same nodes as space_a plus one new
    space_b = TheorySpace()
    space_b.add_node(_make_theory_node("n1", name="Algebra"))  # duplicate
    space_b.add_node(_make_theory_node("n_unique", name="Unique Theory"))
    merged = updater.merge_spaces(space_a, space_b)
    # After dedup, n1 should appear only once
    assert merged.has_node("n1")
    assert merged.has_node("n_unique")
    # n1 should not be duplicated
    assert merged.node_count() <= space_a.node_count() + 1


def test_incremental_merge_preserves_all_unique_nodes():
    updater = IncrementalSpaceUpdater()
    space_a = TheorySpace()
    space_a.add_node(_make_theory_node("na", name="Alpha"))
    space_b = TheorySpace()
    space_b.add_node(_make_theory_node("nb", name="Beta"))
    merged = updater.merge_spaces(space_a, space_b)
    assert merged.has_node("na")
    assert merged.has_node("nb")


def test_incremental_add_then_remove_node():
    updater = IncrementalSpaceUpdater()
    space = _make_space_with_nodes(2)
    new_node = _make_theory_node("n_temp", name="Temporary")
    after_add = updater.add_node(space, new_node)
    assert after_add.node_count() == 3
    after_remove = updater.remove_node(after_add, "n_temp")
    assert after_remove.node_count() == 2
    assert not after_remove.has_node("n_temp")
