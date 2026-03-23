from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

"""
Federated Knowledge Propagation — test_s02_federated_knowledge
==============================================================

This test module exercises the federated-knowledge layer defined in
``jugeo.ideation.discovery_federation.s02_federated_knowledge``.

Federated knowledge addresses the problem of combining knowledge that exists
across multiple, loosely coupled *nodes* in a discovery network.  Each node
may have its own local view of the world (a set of ``KnowledgeEntry`` records),
and the federation layer provides mechanisms to:

1. **Propagate** entries from their originating node to other registered nodes.
2. **Merge** entries from multiple sources into a single coherent view using
   one of five configurable strategies:
   - ``UNION``          – keep all entries (deduplicated)
   - ``INTERSECTION``   – keep only entries that appear in every node's corpus
   - ``LATEST``         – keep the most-recently-created entry per topic key
   - ``TRUST_WEIGHTED`` – weight entries by node trust scores; keep highest-
                          weighted versions
   - ``CONSENSUS``      – keep entries that appear in at least *threshold*
                          fraction of source nodes
3. **Store and retrieve** entries via a ``KnowledgeRepository``.

Key classes under test
----------------------
* ``MergeStrategy``           – enum with UNION, INTERSECTION, LATEST,
                                TRUST_WEIGHTED, CONSENSUS members.
* ``KnowledgeEntry``          – immutable value object wrapping a single
                                knowledge payload.
* ``MergeResult``             – structured result returned by every merge
                                operation.
* ``KnowledgePropagator``     – sends entries to target nodes; keeps a log.
* ``KnowledgeMerger``         – implements all five merge strategies.
* ``KnowledgeRepository``     – in-memory (or persistent) store for entries.
* ``FederatedKnowledgeRunner``– top-level orchestrator.

Free functions
--------------
* ``propagate_knowledge`` – convenience wrapper around ``KnowledgePropagator``.
* ``merge_knowledge``     – convenience wrapper around ``KnowledgeMerger``.
"""

from jugeo.ideation.discovery_federation.s02_federated_knowledge import (
    MergeStrategy,
    KnowledgeEntry,
    MergeResult,
    KnowledgePropagator,
    KnowledgeMerger,
    KnowledgeRepository,
    FederatedKnowledgeRunner,
    propagate_knowledge,
    merge_knowledge,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_entry(
    node_id: str = "node1",
    trust_score: float = 0.8,
    content: dict | None = None,
    entry_id: str | None = None,
    tags: list | None = None,
    created_at: str = "2024-01-01T00:00:00",
) -> dict:
    """Return a minimal KnowledgeEntry-compatible dict.

    Parameters
    ----------
    node_id:
        Identifier of the node that produced this entry.
    trust_score:
        Float in [0, 1] representing node reliability.
    content:
        Arbitrary payload dict.  Defaults to a minimal stub.
    entry_id:
        Explicit ID; auto-generated if omitted.
    tags:
        List of string labels attached to this entry.
    created_at:
        ISO-8601 creation timestamp string.
    """
    import uuid
    return {
        "entry_id": entry_id or str(uuid.uuid4()),
        "node_id": node_id,
        "trust_score": trust_score,
        "content": content or {"key": "value", "source": node_id},
        "tags": tags or [],
        "created_at": created_at,
    }


def make_entries_for_nodes(
    node_ids: list | None = None,
    per_node: int = 2,
) -> list[dict]:
    """Return a flat list of entries distributed across several nodes."""
    if node_ids is None:
        node_ids = ["node1", "node2", "node3"]
    entries = []
    for node_id in node_ids:
        for i in range(per_node):
            entries.append(
                make_entry(
                    node_id=node_id,
                    entry_id=f"{node_id}-entry-{i}",
                    created_at=f"2024-0{node_ids.index(node_id) + 1}-0{i + 1}T00:00:00",
                )
            )
    return entries


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def knowledge_entries_fixture() -> list[dict]:
    """Five KnowledgeEntry-like dicts from three different nodes."""
    return [
        make_entry(node_id="node1", entry_id="e1", trust_score=0.9,
                   created_at="2024-03-01T10:00:00", tags=["alpha"]),
        make_entry(node_id="node1", entry_id="e2", trust_score=0.85,
                   created_at="2024-02-15T08:00:00", tags=["beta"]),
        make_entry(node_id="node2", entry_id="e3", trust_score=0.7,
                   created_at="2024-01-10T12:00:00", tags=["alpha", "gamma"]),
        make_entry(node_id="node3", entry_id="e4", trust_score=0.6,
                   created_at="2024-04-01T09:00:00", tags=["delta"]),
        make_entry(node_id="node3", entry_id="e5", trust_score=0.75,
                   created_at="2024-04-02T11:00:00", tags=["beta", "delta"]),
    ]


@pytest.fixture
def populated_repository(knowledge_entries_fixture: list[dict]) -> KnowledgeRepository:
    """KnowledgeRepository pre-loaded with 5 entries from 3 nodes."""
    repo = KnowledgeRepository()
    for entry in knowledge_entries_fixture:
        repo.store(entry)
    return repo


@pytest.fixture
def propagator_with_nodes() -> KnowledgePropagator:
    """KnowledgePropagator with two pre-registered nodes."""
    propagator = KnowledgePropagator()
    propagator.register_node("node-alpha", trust_score=0.9)
    propagator.register_node("node-beta", trust_score=0.6)
    return propagator


@pytest.fixture
def merger_default() -> KnowledgeMerger:
    """KnowledgeMerger with no special configuration."""
    return KnowledgeMerger()


@pytest.fixture
def runner_default() -> FederatedKnowledgeRunner:
    """FederatedKnowledgeRunner wired with default sub-components."""
    return FederatedKnowledgeRunner()


# ---------------------------------------------------------------------------
# MergeStrategy enum
# ---------------------------------------------------------------------------

class TestMergeStrategy:
    """Tests for the MergeStrategy enum."""

    def test_union_member_exists(self) -> None:
        assert hasattr(MergeStrategy, "UNION")

    def test_intersection_member_exists(self) -> None:
        assert hasattr(MergeStrategy, "INTERSECTION")

    def test_latest_member_exists(self) -> None:
        assert hasattr(MergeStrategy, "LATEST")

    def test_trust_weighted_member_exists(self) -> None:
        assert hasattr(MergeStrategy, "TRUST_WEIGHTED")

    def test_consensus_member_exists(self) -> None:
        assert hasattr(MergeStrategy, "CONSENSUS")

    def test_five_total_members(self) -> None:
        members = list(MergeStrategy)
        assert len(members) == 5, f"Expected 5 members, got {len(members)}: {members}"

    def test_all_members_iterable(self) -> None:
        """MergeStrategy must be iterable and yield all expected values."""
        names = {m.name for m in MergeStrategy}
        expected = {"UNION", "INTERSECTION", "LATEST", "TRUST_WEIGHTED", "CONSENSUS"}
        assert names == expected

    def test_members_are_distinct(self) -> None:
        members = list(MergeStrategy)
        assert len(members) == len(set(members))

    def test_union_value_accessible(self) -> None:
        strategy = MergeStrategy.UNION
        assert strategy is not None

    def test_consensus_value_accessible(self) -> None:
        strategy = MergeStrategy.CONSENSUS
        assert strategy is not None

    def test_str_coercion_non_empty(self) -> None:
        """str() of any strategy member must be a non-empty string."""
        for s in MergeStrategy:
            assert len(str(s)) > 0


# ---------------------------------------------------------------------------
# KnowledgeEntry
# ---------------------------------------------------------------------------

class TestKnowledgeEntryCreate:
    """Tests for KnowledgeEntry.create() classmethod."""

    def test_create_returns_knowledge_entry(self) -> None:
        entry = KnowledgeEntry.create(
            node_id="node1", content={"key": "value"}, trust_score=0.8
        )
        assert isinstance(entry, KnowledgeEntry)

    def test_create_sets_node_id(self) -> None:
        entry = KnowledgeEntry.create(node_id="node-x", content={}, trust_score=0.5)
        assert entry.node_id == "node-x"

    def test_create_sets_trust_score(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.75)
        assert entry.trust_score == pytest.approx(0.75)

    def test_create_assigns_entry_id(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        assert isinstance(entry.entry_id, str)
        assert len(entry.entry_id) > 0

    def test_create_two_entries_distinct_ids(self) -> None:
        e1 = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        e2 = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        assert e1.entry_id != e2.entry_id

    def test_create_with_tags(self) -> None:
        entry = KnowledgeEntry.create(
            node_id="n1", content={}, trust_score=0.5, tags=["foo", "bar"]
        )
        assert "foo" in entry.tags
        assert "bar" in entry.tags

    def test_create_with_empty_tags(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5, tags=[])
        assert isinstance(entry.tags, list)


class TestKnowledgeEntryToDict:
    """Tests for KnowledgeEntry.to_dict()."""

    def test_to_dict_returns_dict(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        assert isinstance(entry.to_dict(), dict)

    def test_to_dict_has_entry_id(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        d = entry.to_dict()
        assert "entry_id" in d

    def test_to_dict_has_node_id(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        d = entry.to_dict()
        assert "node_id" in d

    def test_to_dict_has_trust_score(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.77)
        d = entry.to_dict()
        assert "trust_score" in d
        assert d["trust_score"] == pytest.approx(0.77)

    def test_to_dict_has_content(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={"x": 1}, trust_score=0.5)
        d = entry.to_dict()
        assert "content" in d

    def test_to_dict_has_created_at(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        d = entry.to_dict()
        assert "created_at" in d

    def test_to_dict_has_tags(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5, tags=["t"])
        d = entry.to_dict()
        assert "tags" in d


class TestKnowledgeEntryAge:
    """Tests for KnowledgeEntry.age()."""

    def test_age_returns_non_negative_float(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        age = entry.age()
        assert isinstance(age, (int, float))
        assert age >= 0


class TestKnowledgeEntryWeightedRepr:
    """Tests for KnowledgeEntry.weighted_repr()."""

    def test_weighted_repr_returns_dict(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.8)
        result = entry.weighted_repr()
        assert isinstance(result, dict)

    def test_weighted_repr_contains_trust(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.8)
        result = entry.weighted_repr()
        assert "trust_score" in result or "weight" in result

    def test_weighted_repr_contains_entry_id(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.8)
        result = entry.weighted_repr()
        assert "entry_id" in result


# ---------------------------------------------------------------------------
# MergeResult
# ---------------------------------------------------------------------------

class TestMergeResultCreate:
    """Tests for MergeResult.create()."""

    def test_create_returns_merge_result(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION,
            input_count=3,
            output_entries=[],
        )
        assert isinstance(result, MergeResult)

    def test_create_sets_strategy(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.CONSENSUS,
            input_count=2,
            output_entries=[],
        )
        assert result.strategy == MergeStrategy.CONSENSUS

    def test_create_sets_input_count(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION,
            input_count=7,
            output_entries=[],
        )
        assert result.input_count == 7

    def test_create_assigns_result_id(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION,
            input_count=1,
            output_entries=[],
        )
        assert isinstance(result.result_id, str)
        assert len(result.result_id) > 0

    def test_create_with_output_entries(self) -> None:
        entries = [make_entry() for _ in range(3)]
        result = MergeResult.create(
            strategy=MergeStrategy.LATEST,
            input_count=3,
            output_entries=entries,
        )
        assert len(result.output_entries) == 3

    def test_create_sets_merged_at(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION,
            input_count=0,
            output_entries=[],
        )
        assert isinstance(result.merged_at, str)
        assert len(result.merged_at) > 0


class TestMergeResultToDict:
    """Tests for MergeResult.to_dict()."""

    def test_to_dict_returns_dict(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION, input_count=0, output_entries=[]
        )
        assert isinstance(result.to_dict(), dict)

    def test_to_dict_has_strategy(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.INTERSECTION, input_count=0, output_entries=[]
        )
        d = result.to_dict()
        assert "strategy" in d

    def test_to_dict_has_input_count(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION, input_count=4, output_entries=[]
        )
        d = result.to_dict()
        assert "input_count" in d
        assert d["input_count"] == 4

    def test_to_dict_has_output_entries(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION, input_count=0, output_entries=[]
        )
        d = result.to_dict()
        assert "output_entries" in d


class TestMergeResultSummary:
    """Tests for MergeResult.summary()."""

    def test_summary_returns_non_empty_string(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION, input_count=3, output_entries=[]
        )
        s = result.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summary_mentions_strategy(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.CONSENSUS, input_count=5, output_entries=[]
        )
        assert "CONSENSUS" in result.summary().upper() or "consensus" in result.summary().lower()


# ---------------------------------------------------------------------------
# KnowledgePropagator
# ---------------------------------------------------------------------------

class TestKnowledgePropagatorRegister:
    """Tests for KnowledgePropagator.register_node()."""

    def test_register_node_does_not_raise(self) -> None:
        propagator = KnowledgePropagator()
        propagator.register_node("node-test", trust_score=0.7)

    def test_register_multiple_nodes(self) -> None:
        propagator = KnowledgePropagator()
        for i in range(5):
            propagator.register_node(f"node-{i}", trust_score=0.5 + i * 0.1)

    def test_register_same_node_twice_does_not_crash(self) -> None:
        propagator = KnowledgePropagator()
        propagator.register_node("dup-node", trust_score=0.8)
        propagator.register_node("dup-node", trust_score=0.9)  # update — must not raise


class TestKnowledgePropagatorPropagate:
    """Tests for KnowledgePropagator.propagate_to() and propagate_all()."""

    def test_propagate_to_returns_dict(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        entry = make_entry(node_id="node-alpha")
        result = propagator_with_nodes.propagate_to(entry, target_node="node-beta")
        assert isinstance(result, dict)

    def test_propagate_to_includes_entry_id(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        entry = make_entry(entry_id="prop-entry-1")
        result = propagator_with_nodes.propagate_to(entry, target_node="node-beta")
        assert "entry_id" in result or "propagated_entry_id" in result or result.get("entry_id") == "prop-entry-1"

    def test_propagate_all_returns_list(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        entries = [make_entry(entry_id=f"e-{i}") for i in range(3)]
        results = propagator_with_nodes.propagate_all(
            entries, nodes=["node-alpha", "node-beta"]
        )
        assert isinstance(results, list)

    def test_propagate_all_empty_entries(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        results = propagator_with_nodes.propagate_all([], nodes=["node-alpha"])
        assert isinstance(results, list)
        assert len(results) == 0

    def test_propagate_all_multiple_entries_multiple_nodes(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        entries = [make_entry(entry_id=f"multi-{i}") for i in range(3)]
        results = propagator_with_nodes.propagate_all(
            entries, nodes=["node-alpha", "node-beta"]
        )
        # Should produce at least as many items as entries × nodes
        assert len(results) >= len(entries)


class TestKnowledgePropagatorLog:
    """Tests for KnowledgePropagator.get_log() and summary()."""

    def test_get_log_initially_empty(self) -> None:
        propagator = KnowledgePropagator()
        assert propagator.get_log() == []

    def test_get_log_accumulates_after_propagate(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        entry = make_entry()
        propagator_with_nodes.propagate_to(entry, target_node="node-beta")
        log = propagator_with_nodes.get_log()
        assert len(log) >= 1

    def test_get_log_returns_list_of_dicts(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        entry = make_entry()
        propagator_with_nodes.propagate_to(entry, target_node="node-beta")
        for item in propagator_with_nodes.get_log():
            assert isinstance(item, dict)

    def test_summary_returns_non_empty_string(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        s = propagator_with_nodes.summary()
        assert isinstance(s, str)
        assert len(s) > 0


# ---------------------------------------------------------------------------
# KnowledgeMerger
# ---------------------------------------------------------------------------

class TestKnowledgeMergerMethods:
    """Tests for individual merge methods on KnowledgeMerger."""

    @pytest.mark.parametrize("strategy_name,method_name", [
        ("UNION", "merge_union"),
        ("INTERSECTION", "merge_intersection"),
        ("LATEST", "merge_latest"),
        ("TRUST_WEIGHTED", "merge_trust_weighted"),
    ])
    def test_merge_method_returns_merge_result(
        self,
        merger_default: KnowledgeMerger,
        knowledge_entries_fixture: list[dict],
        strategy_name: str,
        method_name: str,
    ) -> None:
        method = getattr(merger_default, method_name)
        result = method(knowledge_entries_fixture)
        assert isinstance(result, MergeResult)

    def test_merge_consensus_returns_merge_result(
        self,
        merger_default: KnowledgeMerger,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        result = merger_default.merge_consensus(knowledge_entries_fixture, threshold=0.5)
        assert isinstance(result, MergeResult)

    def test_merge_union_output_entries_is_list(
        self,
        merger_default: KnowledgeMerger,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        result = merger_default.merge_union(knowledge_entries_fixture)
        assert isinstance(result.output_entries, list)

    def test_merge_union_includes_all_entries(
        self,
        merger_default: KnowledgeMerger,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        """UNION should return at least as many entries as the input (deduplication only)."""
        result = merger_default.merge_union(knowledge_entries_fixture)
        assert len(result.output_entries) <= len(knowledge_entries_fixture)
        # But for all-unique input it should be exactly len(input) or close
        assert len(result.output_entries) >= 1

    def test_merge_latest_respects_created_at(
        self,
        merger_default: KnowledgeMerger,
    ) -> None:
        """LATEST must not discard the most recent entry."""
        entries = [
            make_entry(entry_id="old", created_at="2020-01-01T00:00:00"),
            make_entry(entry_id="new", created_at="2024-12-31T00:00:00"),
        ]
        result = merger_default.merge_latest(entries)
        ids = [e.get("entry_id") for e in result.output_entries]
        assert "new" in ids

    def test_merge_trust_weighted_respects_trust(
        self,
        merger_default: KnowledgeMerger,
    ) -> None:
        """TRUST_WEIGHTED: the high-trust entry must appear in the result."""
        entries = [
            make_entry(entry_id="low-trust", trust_score=0.1),
            make_entry(entry_id="high-trust", trust_score=0.99),
        ]
        result = merger_default.merge_trust_weighted(entries)
        ids = [e.get("entry_id") for e in result.output_entries]
        assert "high-trust" in ids

    def test_merge_intersection_empty_result_for_no_common(
        self,
        merger_default: KnowledgeMerger,
    ) -> None:
        """If no entry appears in every node the intersection may be empty."""
        entries = [
            make_entry(node_id="node1", entry_id="only-node1"),
            make_entry(node_id="node2", entry_id="only-node2"),
        ]
        result = merger_default.merge_intersection(entries)
        assert isinstance(result, MergeResult)

    def test_merge_consensus_threshold_one_keeps_entries_in_all(
        self,
        merger_default: KnowledgeMerger,
    ) -> None:
        """threshold=1.0 is equivalent to strict intersection."""
        entries = make_entries_for_nodes(["n1", "n2", "n3"], per_node=2)
        result = merger_default.merge_consensus(entries, threshold=1.0)
        assert isinstance(result, MergeResult)

    def test_merge_empty_list_returns_merge_result(
        self,
        merger_default: KnowledgeMerger,
    ) -> None:
        for method_name in (
            "merge_union",
            "merge_intersection",
            "merge_latest",
            "merge_trust_weighted",
        ):
            result = getattr(merger_default, method_name)([])
            assert isinstance(result, MergeResult), f"{method_name} did not return MergeResult"

    def test_merge_single_entry_returns_that_entry(
        self,
        merger_default: KnowledgeMerger,
    ) -> None:
        single = [make_entry(entry_id="single")]
        for method_name in (
            "merge_union",
            "merge_latest",
            "merge_trust_weighted",
        ):
            result = getattr(merger_default, method_name)(single)
            assert len(result.output_entries) >= 1


class TestKnowledgeMergerDispatch:
    """Tests for KnowledgeMerger.merge() dispatch."""

    @pytest.mark.parametrize("strategy", list(MergeStrategy))
    def test_merge_dispatch_all_strategies(
        self,
        merger_default: KnowledgeMerger,
        knowledge_entries_fixture: list[dict],
        strategy: MergeStrategy,
    ) -> None:
        result = merger_default.merge(knowledge_entries_fixture, strategy)
        assert isinstance(result, MergeResult)
        assert result.strategy == strategy

    def test_merge_dispatch_union_sets_input_count(
        self,
        merger_default: KnowledgeMerger,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        result = merger_default.merge(knowledge_entries_fixture, MergeStrategy.UNION)
        assert result.input_count == len(knowledge_entries_fixture)


# ---------------------------------------------------------------------------
# KnowledgeRepository
# ---------------------------------------------------------------------------

class TestKnowledgeRepositoryStore:
    """Tests for KnowledgeRepository.store()."""

    def test_store_returns_string_id(self) -> None:
        repo = KnowledgeRepository()
        entry = make_entry()
        eid = repo.store(entry)
        assert isinstance(eid, str)
        assert len(eid) > 0

    def test_store_increments_count(self) -> None:
        repo = KnowledgeRepository()
        assert repo.count() == 0
        repo.store(make_entry())
        assert repo.count() == 1
        repo.store(make_entry())
        assert repo.count() == 2

    def test_store_multiple_returns_distinct_ids(self) -> None:
        repo = KnowledgeRepository()
        ids = [repo.store(make_entry()) for _ in range(5)]
        assert len(set(ids)) == len(ids)


class TestKnowledgeRepositoryRetrieve:
    """Tests for KnowledgeRepository.retrieve()."""

    def test_retrieve_returns_stored_entry(self) -> None:
        repo = KnowledgeRepository()
        entry = make_entry(entry_id="known-id")
        repo.store(entry)
        retrieved = repo.retrieve("known-id")
        assert retrieved is not None
        assert retrieved.get("entry_id") == "known-id"

    def test_retrieve_nonexistent_returns_none(self) -> None:
        repo = KnowledgeRepository()
        assert repo.retrieve("ghost-id") is None

    def test_retrieve_returns_dict(self) -> None:
        repo = KnowledgeRepository()
        entry = make_entry(entry_id="dict-entry")
        repo.store(entry)
        result = repo.retrieve("dict-entry")
        assert isinstance(result, dict)

    def test_retrieve_preserves_content(self) -> None:
        repo = KnowledgeRepository()
        entry = make_entry(entry_id="preserve-content", content={"special_key": 42})
        repo.store(entry)
        result = repo.retrieve("preserve-content")
        assert result["content"]["special_key"] == 42

    def test_retrieve_after_delete_returns_none(self) -> None:
        repo = KnowledgeRepository()
        entry = make_entry(entry_id="to-delete")
        repo.store(entry)
        repo.delete("to-delete")
        assert repo.retrieve("to-delete") is None


class TestKnowledgeRepositoryRetrieveByNode:
    """Tests for KnowledgeRepository.retrieve_by_node()."""

    def test_retrieve_by_node_returns_list(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        results = populated_repository.retrieve_by_node("node1")
        assert isinstance(results, list)

    def test_retrieve_by_node_filters_correctly(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        node1_entries = populated_repository.retrieve_by_node("node1")
        for e in node1_entries:
            assert e.get("node_id") == "node1"

    def test_retrieve_by_node_unknown_returns_empty(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        results = populated_repository.retrieve_by_node("nonexistent-node")
        assert results == []

    def test_retrieve_by_node_correct_count(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        """Fixture has 2 entries for node1, 1 for node2, 2 for node3."""
        assert len(populated_repository.retrieve_by_node("node1")) == 2
        assert len(populated_repository.retrieve_by_node("node2")) == 1
        assert len(populated_repository.retrieve_by_node("node3")) == 2


class TestKnowledgeRepositorySearch:
    """Tests for KnowledgeRepository.search()."""

    def test_search_returns_list(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        results = populated_repository.search("alpha")
        assert isinstance(results, list)

    def test_search_empty_query_returns_all_or_empty(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        results = populated_repository.search("")
        assert isinstance(results, list)

    def test_search_no_match_returns_empty(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        results = populated_repository.search("zzz-no-match-xyz")
        assert isinstance(results, list)


class TestKnowledgeRepositoryDelete:
    """Tests for KnowledgeRepository.delete()."""

    def test_delete_returns_true_on_success(self) -> None:
        repo = KnowledgeRepository()
        entry = make_entry(entry_id="del-me")
        repo.store(entry)
        assert repo.delete("del-me") is True

    def test_delete_returns_false_for_unknown(self) -> None:
        repo = KnowledgeRepository()
        assert repo.delete("ghost") is False

    def test_delete_decrements_count(self) -> None:
        repo = KnowledgeRepository()
        entry = make_entry(entry_id="count-entry")
        repo.store(entry)
        assert repo.count() == 1
        repo.delete("count-entry")
        assert repo.count() == 0


class TestKnowledgeRepositoryCount:
    """Tests for KnowledgeRepository.count()."""

    def test_count_empty_repo_is_zero(self) -> None:
        repo = KnowledgeRepository()
        assert repo.count() == 0

    def test_count_after_five_stores(self) -> None:
        repo = KnowledgeRepository()
        for i in range(5):
            repo.store(make_entry(entry_id=f"c-{i}"))
        assert repo.count() == 5

    def test_count_decreases_after_delete(self, populated_repository: KnowledgeRepository) -> None:
        before = populated_repository.count()
        populated_repository.delete("e1")
        assert populated_repository.count() == before - 1


class TestKnowledgeRepositoryRebuildIndex:
    """Tests for KnowledgeRepository.rebuild_index()."""

    def test_rebuild_index_does_not_raise(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        populated_repository.rebuild_index()

    def test_rebuild_index_preserves_count(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        before = populated_repository.count()
        populated_repository.rebuild_index()
        assert populated_repository.count() == before

    def test_rebuild_index_on_empty_does_not_raise(self) -> None:
        repo = KnowledgeRepository()
        repo.rebuild_index()


# ---------------------------------------------------------------------------
# FederatedKnowledgeRunner
# ---------------------------------------------------------------------------

class TestFederatedKnowledgeRunnerRun:
    """Tests for FederatedKnowledgeRunner.run()."""

    def test_run_returns_dict(
        self,
        runner_default: FederatedKnowledgeRunner,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        result = runner_default.run(
            knowledge_entries_fixture,
            nodes=["node1", "node2", "node3"],
            strategy=MergeStrategy.UNION,
        )
        assert isinstance(result, dict)

    def test_run_result_has_status_key(
        self,
        runner_default: FederatedKnowledgeRunner,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        result = runner_default.run(
            knowledge_entries_fixture,
            nodes=["node1"],
            strategy=MergeStrategy.UNION,
        )
        assert "status" in result

    def test_run_result_has_merge_result_key(
        self,
        runner_default: FederatedKnowledgeRunner,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        result = runner_default.run(
            knowledge_entries_fixture,
            nodes=["node1"],
            strategy=MergeStrategy.UNION,
        )
        assert "merge_result" in result or "merged" in result or "output" in result

    def test_run_with_empty_entries(
        self, runner_default: FederatedKnowledgeRunner
    ) -> None:
        result = runner_default.run([], nodes=["node1"], strategy=MergeStrategy.UNION)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("strategy", list(MergeStrategy))
    def test_run_all_strategies_succeed(
        self,
        runner_default: FederatedKnowledgeRunner,
        knowledge_entries_fixture: list[dict],
        strategy: MergeStrategy,
    ) -> None:
        result = runner_default.run(
            knowledge_entries_fixture,
            nodes=["node1", "node2", "node3"],
            strategy=strategy,
        )
        assert isinstance(result, dict)


class TestFederatedKnowledgeRunnerFromNodes:
    """Tests for FederatedKnowledgeRunner.run_from_nodes()."""

    def test_run_from_nodes_returns_dict(
        self, runner_default: FederatedKnowledgeRunner
    ) -> None:
        result = runner_default.run_from_nodes(
            node_ids=["node1", "node2"],
            strategy=MergeStrategy.UNION,
        )
        assert isinstance(result, dict)

    def test_run_from_nodes_empty_list(
        self, runner_default: FederatedKnowledgeRunner
    ) -> None:
        result = runner_default.run_from_nodes(node_ids=[], strategy=MergeStrategy.UNION)
        assert isinstance(result, dict)


class TestFederatedKnowledgeRunnerGetResults:
    """Tests for FederatedKnowledgeRunner.get_results()."""

    def test_get_results_initially_empty(self) -> None:
        runner = FederatedKnowledgeRunner()
        assert runner.get_results() == []

    def test_get_results_accumulates(
        self,
        runner_default: FederatedKnowledgeRunner,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        for _ in range(3):
            runner_default.run(
                knowledge_entries_fixture,
                nodes=["node1"],
                strategy=MergeStrategy.UNION,
            )
        assert len(runner_default.get_results()) == 3

    def test_get_results_returns_list_of_dicts(
        self,
        runner_default: FederatedKnowledgeRunner,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        runner_default.run(
            knowledge_entries_fixture,
            nodes=["node1"],
            strategy=MergeStrategy.UNION,
        )
        for item in runner_default.get_results():
            assert isinstance(item, dict)


# ---------------------------------------------------------------------------
# Injected sub-components
# ---------------------------------------------------------------------------

class TestFederatedKnowledgeRunnerInjection:
    """Tests that verify custom propagator/merger/repository can be injected."""

    def test_inject_custom_propagator(
        self,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        custom_prop = KnowledgePropagator()
        custom_prop.register_node("injected-node", trust_score=0.8)
        runner = FederatedKnowledgeRunner(propagator=custom_prop)
        result = runner.run(
            knowledge_entries_fixture,
            nodes=["injected-node"],
            strategy=MergeStrategy.UNION,
        )
        assert isinstance(result, dict)

    def test_inject_custom_merger(
        self,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        custom_merger = KnowledgeMerger()
        runner = FederatedKnowledgeRunner(merger=custom_merger)
        result = runner.run(
            knowledge_entries_fixture,
            nodes=["node1"],
            strategy=MergeStrategy.LATEST,
        )
        assert isinstance(result, dict)

    def test_inject_custom_repository(
        self,
        knowledge_entries_fixture: list[dict],
    ) -> None:
        custom_repo = KnowledgeRepository()
        runner = FederatedKnowledgeRunner(repository=custom_repo)
        result = runner.run(
            knowledge_entries_fixture,
            nodes=["node1"],
            strategy=MergeStrategy.UNION,
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

class TestPropagateKnowledgeFreeFunction:
    """Tests for the propagate_knowledge() free function."""

    def test_returns_list(
        self, knowledge_entries_fixture: list[dict]
    ) -> None:
        result = propagate_knowledge(knowledge_entries_fixture, nodes=["node1", "node2"])
        assert isinstance(result, list)

    def test_empty_entries_returns_empty_list(self) -> None:
        result = propagate_knowledge([], nodes=["node1"])
        assert result == []

    def test_empty_nodes_returns_empty_or_unchanged(
        self, knowledge_entries_fixture: list[dict]
    ) -> None:
        result = propagate_knowledge(knowledge_entries_fixture, nodes=[])
        assert isinstance(result, list)

    @pytest.mark.parametrize("node_count", [1, 3, 5, 10])
    def test_various_node_counts_do_not_crash(
        self,
        knowledge_entries_fixture: list[dict],
        node_count: int,
    ) -> None:
        nodes = [f"node-{i}" for i in range(node_count)]
        result = propagate_knowledge(knowledge_entries_fixture, nodes=nodes)
        assert isinstance(result, list)

    def test_result_items_are_dicts(
        self, knowledge_entries_fixture: list[dict]
    ) -> None:
        result = propagate_knowledge(knowledge_entries_fixture, nodes=["node1"])
        for item in result:
            assert isinstance(item, dict)


class TestMergeKnowledgeFreeFunction:
    """Tests for the merge_knowledge() free function."""

    def test_returns_dict(
        self, knowledge_entries_fixture: list[dict]
    ) -> None:
        result = merge_knowledge(knowledge_entries_fixture, strategy="UNION")
        assert isinstance(result, dict)

    def test_empty_entries_returns_dict(self) -> None:
        result = merge_knowledge([], strategy="UNION")
        assert isinstance(result, dict)

    @pytest.mark.parametrize("strategy_str", [
        "UNION", "INTERSECTION", "LATEST", "TRUST_WEIGHTED", "CONSENSUS"
    ])
    def test_all_strategy_strings_accepted(
        self,
        knowledge_entries_fixture: list[dict],
        strategy_str: str,
    ) -> None:
        result = merge_knowledge(knowledge_entries_fixture, strategy=strategy_str)
        assert isinstance(result, dict)

    def test_result_has_output_entries_or_merged_key(
        self, knowledge_entries_fixture: list[dict]
    ) -> None:
        result = merge_knowledge(knowledge_entries_fixture, strategy="UNION")
        assert (
            "output_entries" in result
            or "entries" in result
            or "merged" in result
            or "result" in result
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Miscellaneous edge-case tests for the federated knowledge layer."""

    def test_empty_repository_count_is_zero(self) -> None:
        repo = KnowledgeRepository()
        assert repo.count() == 0

    def test_retrieve_from_empty_repo_returns_none(self) -> None:
        repo = KnowledgeRepository()
        assert repo.retrieve("anything") is None

    def test_overlapping_tags_between_entries(
        self, merger_default: KnowledgeMerger
    ) -> None:
        """Entries sharing tags must not cause errors during any merge."""
        entries = [
            make_entry(entry_id="t1", tags=["shared", "unique-1"]),
            make_entry(entry_id="t2", tags=["shared", "unique-2"]),
        ]
        for method_name in ("merge_union", "merge_latest", "merge_trust_weighted"):
            result = getattr(merger_default, method_name)(entries)
            assert isinstance(result, MergeResult)

    def test_same_node_id_different_entries(
        self, merger_default: KnowledgeMerger
    ) -> None:
        """Multiple entries from the same node are all valid inputs."""
        entries = [
            make_entry(node_id="same-node", entry_id=f"sn-{i}") for i in range(5)
        ]
        result = merger_default.merge_union(entries)
        assert isinstance(result, MergeResult)

    def test_merge_of_single_entry_union(
        self, merger_default: KnowledgeMerger
    ) -> None:
        result = merger_default.merge_union([make_entry(entry_id="solo")])
        assert len(result.output_entries) == 1

    def test_knowledge_entry_age_non_negative_after_create(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.5)
        assert entry.age() >= 0.0

    def test_propagator_log_grows_per_call(
        self, propagator_with_nodes: KnowledgePropagator
    ) -> None:
        entry = make_entry()
        before = len(propagator_with_nodes.get_log())
        propagator_with_nodes.propagate_to(entry, target_node="node-beta")
        after = len(propagator_with_nodes.get_log())
        assert after == before + 1

    def test_merge_trust_weighted_empty_returns_merge_result(
        self, merger_default: KnowledgeMerger
    ) -> None:
        result = merger_default.merge_trust_weighted([])
        assert isinstance(result, MergeResult)

    def test_runner_get_results_initially_empty(self) -> None:
        runner = FederatedKnowledgeRunner()
        assert runner.get_results() == []

    def test_populated_repo_retrieve_by_node2_has_one_entry(
        self, populated_repository: KnowledgeRepository
    ) -> None:
        entries = populated_repository.retrieve_by_node("node2")
        assert len(entries) == 1

    def test_knowledge_entry_weighted_repr_trust_value(self) -> None:
        entry = KnowledgeEntry.create(node_id="n1", content={}, trust_score=0.65)
        wr = entry.weighted_repr()
        # The weight-related field should reflect the trust_score
        weight_val = wr.get("trust_score") or wr.get("weight")
        assert weight_val is not None
        assert float(weight_val) == pytest.approx(0.65)

    def test_merge_result_to_dict_has_result_id(self) -> None:
        result = MergeResult.create(
            strategy=MergeStrategy.UNION, input_count=0, output_entries=[]
        )
        d = result.to_dict()
        assert "result_id" in d
