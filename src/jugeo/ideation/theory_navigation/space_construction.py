"""
Space Construction — build, index, and incrementally update theory spaces.

This module provides the complete pipeline for constructing a
:class:`~jugeo.ideation.theory_navigation.models.TheorySpace` from raw
input data.  The pipeline is intentionally split into composable stages so
that each stage can be tested and replaced independently.

A theory space is a directed graph whose nodes represent mathematical or
conceptual elements (lemmas, definitions, examples, conjectures) and whose
edges represent semantic similarity or explicit citation links.  The
construction pipeline proceeds as follows:

1. **Extraction** — :class:`NodeExtractor` converts raw ``dict``-like inputs
   or free-form text strings into validated :class:`TheoryNode` instances.
   Duplicate nodes (by ``node_id``) are dropped; filtering by maturity and
   purpose-alignment is applied before the graph is assembled.

2. **Edge building** — :class:`EdgeBuilder` computes pairwise Jaccard
   similarity between node descriptions and names.  Edges whose similarity
   exceeds :attr:`SpaceConstructionConfig.similarity_threshold` are added.
   Explicit ``connections`` declared in the raw data are also honoured.
   The two edge sets are merged and weak edges pruned to keep the graph
   tractable.

3. **Assembly** — :class:`SpaceConstructor` drives the full pipeline,
   producing a :class:`TheorySpace` ready for navigation.  It also stores a
   human-readable construction report and delegates index-building to
   :class:`SpaceIndexer`.

4. **Indexing** — :class:`SpaceIndexer` builds in-memory keyword, maturity,
   and purpose-alignment indexes over a completed space so that later
   navigation steps can perform sub-linear lookups.

5. **Incremental updates** — :class:`IncrementalSpaceUpdater` supports adding,
   removing, and modifying individual nodes without rebuilding the whole space
   from scratch.

Module layout::

    SpaceConstructionConfig   – frozen configuration for the construction pipeline
    NodeExtractor             – extracts and filters TheoryNode instances from raw input
    EdgeBuilder               – computes similarity edges and merges edge sets
    SpaceIndexer              – builds keyword / maturity / purpose indexes over a space
    SpaceConstructor          – orchestrates the full construction pipeline
    IncrementalSpaceUpdater   – adds / removes / updates nodes in an existing space

All classes are fully self-contained and depend only on
``jugeo.ideation.theory_navigation.models``.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from jugeo.ideation.theory_navigation.models import (
    NavigationPath,
    NavigationState,
    NavigationStrategy,
    NodeMaturity,
    PurposeCondition,
    TheoryNode,
    TheorySpace,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """Tokenize text into lowercase words, stripping punctuation."""
    return set(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets.

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        ``|a ∩ b| / |a ∪ b|``, or ``0.0`` when both sets are empty.
    """
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi].

    Parameters
    ----------
    value:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        ``max(lo, min(hi, value))``.
    """
    return max(lo, min(hi, value))


def _maturity_rank(maturity: NodeMaturity) -> int:
    """Return integer rank for maturity (higher = more mature).

    Parameters
    ----------
    maturity:
        A :class:`NodeMaturity` enum member.

    Returns
    -------
    int
        Integer rank where ``ESTABLISHED`` is highest (3) and
        ``NASCENT`` is lowest (0).
    """
    order = {
        NodeMaturity.NASCENT: 0,
        NodeMaturity.DEVELOPING: 1,
        NodeMaturity.MATURE: 2,
        NodeMaturity.ESTABLISHED: 3,
    }
    return order.get(maturity, 0)


# ---------------------------------------------------------------------------
# SpaceConstructionConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpaceConstructionConfig:
    """Immutable configuration controlling the space-construction pipeline.

    Parameters
    ----------
    max_nodes:
        Maximum number of nodes to include in the constructed space.
        Must be > 0.
    max_edges_per_node:
        Maximum number of outgoing edges retained per node after pruning.
    similarity_threshold:
        Minimum Jaccard similarity for an automatic edge to be created.
        Must be in ``[0.0, 1.0]``.
    include_nascent:
        When ``False``, nodes whose maturity is ``NASCENT`` are excluded.
    min_purpose_alignment:
        Minimum ``purpose_alignment`` a node must have to be included.
        Clamped to ``[0.0, 1.0]``.
    extraction_depth:
        Depth of recursive extraction (used by higher-level callers).
        Clamped to ``>= 1``.
    use_bidirectional_edges:
        When ``True``, similarity edges are added in both directions.
    config_id:
        UUID-4 identifier for this configuration instance.
    """

    max_nodes: int = 500
    max_edges_per_node: int = 20
    similarity_threshold: float = 0.15
    include_nascent: bool = True
    min_purpose_alignment: float = 0.0
    extraction_depth: int = 3
    use_bidirectional_edges: bool = True
    config_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if self.max_nodes <= 0:
            raise ValueError(
                f"max_nodes must be > 0, got {self.max_nodes}"
            )
        if not (0.0 <= self.similarity_threshold <= 1.0):
            raise ValueError(
                f"similarity_threshold must be in [0.0, 1.0], "
                f"got {self.similarity_threshold}"
            )
        # Clamp min_purpose_alignment to [0, 1]
        clamped_pa = _clamp(self.min_purpose_alignment, 0.0, 1.0)
        object.__setattr__(self, "min_purpose_alignment", clamped_pa)
        # Clamp extraction_depth to >= 1
        clamped_depth = max(1, self.extraction_depth)
        object.__setattr__(self, "extraction_depth", clamped_depth)

    def with_threshold(self, t: float) -> "SpaceConstructionConfig":
        """Return a copy with *similarity_threshold* set to clamped *t*.

        Parameters
        ----------
        t:
            New threshold value; clamped to ``[0.0, 1.0]``.

        Returns
        -------
        SpaceConstructionConfig
            New configuration with updated threshold.
        """
        return replace(self, similarity_threshold=_clamp(t, 0.0, 1.0))

    def with_max_nodes(self, n: int) -> "SpaceConstructionConfig":
        """Return a copy with *max_nodes* set to *n*.

        Parameters
        ----------
        n:
            New maximum node count.  Must be > 0.

        Returns
        -------
        SpaceConstructionConfig
            New configuration with updated max_nodes.

        Raises
        ------
        ValueError
            When *n* is not > 0.
        """
        if n <= 0:
            raise ValueError(f"max_nodes must be > 0, got {n}")
        return replace(self, max_nodes=n)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this configuration to a plain dict.

        Returns
        -------
        dict
            All fields as JSON-serialisable values.
        """
        return {
            "config_id": self.config_id,
            "max_nodes": self.max_nodes,
            "max_edges_per_node": self.max_edges_per_node,
            "similarity_threshold": self.similarity_threshold,
            "include_nascent": self.include_nascent,
            "min_purpose_alignment": self.min_purpose_alignment,
            "extraction_depth": self.extraction_depth,
            "use_bidirectional_edges": self.use_bidirectional_edges,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpaceConstructionConfig":
        """Reconstruct a :class:`SpaceConstructionConfig` from a plain dict.

        Parameters
        ----------
        data:
            Dict with optional keys matching the dataclass fields.  Missing
            keys fall back to class defaults.

        Returns
        -------
        SpaceConstructionConfig
            Reconstructed instance.
        """
        return cls(
            max_nodes=int(data.get("max_nodes", 500)),
            max_edges_per_node=int(data.get("max_edges_per_node", 20)),
            similarity_threshold=float(data.get("similarity_threshold", 0.15)),
            include_nascent=bool(data.get("include_nascent", True)),
            min_purpose_alignment=float(data.get("min_purpose_alignment", 0.0)),
            extraction_depth=int(data.get("extraction_depth", 3)),
            use_bidirectional_edges=bool(data.get("use_bidirectional_edges", True)),
            config_id=str(data.get("config_id", str(uuid.uuid4()))),
        )


# ---------------------------------------------------------------------------
# NodeExtractor
# ---------------------------------------------------------------------------


class NodeExtractor:
    """Converts raw dict-like inputs or free-form text into :class:`TheoryNode` objects.

    Parameters
    ----------
    config:
        The :class:`SpaceConstructionConfig` governing extraction behaviour.
    """

    _MATURITY_MAP: dict[str, NodeMaturity] = {
        "nascent": NodeMaturity.NASCENT,
        "developing": NodeMaturity.DEVELOPING,
        "mature": NodeMaturity.MATURE,
        "established": NodeMaturity.ESTABLISHED,
    }

    def __init__(self, config: SpaceConstructionConfig | None = None) -> None:
        self.config = config or SpaceConstructionConfig()
        self._seen_ids: set[str] = set()

    def extract_node(self, raw: Mapping[str, Any]) -> TheoryNode:
        """Extract a single :class:`TheoryNode` from a raw mapping.

        Parameters
        ----------
        raw:
            Dict-like with optional keys: ``id``, ``name``, ``description``,
            ``maturity``, ``purpose_alignment``, ``connections``,
            ``metadata``.

        Returns
        -------
        TheoryNode
            Fully constructed node.  Missing fields receive safe defaults.
        """
        if isinstance(raw, TheoryNode):
            return raw

        node_id = str(raw.get("id") or raw.get("node_id") or uuid.uuid4())
        name = str(raw.get("name", "")).strip() or node_id[:16]
        description = str(raw.get("description", "")).strip()

        maturity_raw = str(raw.get("maturity", "")).lower().strip()
        maturity = self._MATURITY_MAP.get(maturity_raw, NodeMaturity.NASCENT)

        try:
            purpose_alignment = _clamp(float(raw.get("purpose_alignment", 0.0)), 0.0, 1.0)
        except (TypeError, ValueError):
            purpose_alignment = 0.0

        raw_connections = raw.get("connections", [])
        if isinstance(raw_connections, (list, tuple, set, frozenset)):
            connections = frozenset(str(c) for c in raw_connections if c)
        else:
            connections = frozenset()

        raw_metadata = raw.get("metadata", {})
        if isinstance(raw_metadata, dict):
            metadata = frozenset(
                (str(k), str(v)) for k, v in raw_metadata.items()
            )
        elif isinstance(raw_metadata, (list, tuple, frozenset, set)):
            metadata = frozenset(
                (str(pair[0]), str(pair[1]))
                for pair in raw_metadata
                if len(pair) == 2
            )
        else:
            metadata = frozenset()

        return TheoryNode(
            node_id=node_id,
            name=name,
            description=description,
            purpose_alignment=purpose_alignment,
            maturity=maturity,
            connections=connections,
            metadata=metadata,
            created_at=_now_iso(),
        )

    def extract_nodes(self, raws: Iterable[Mapping[str, Any]]) -> list[TheoryNode]:
        """Extract and deduplicate nodes from an iterable of raw mappings.

        The first occurrence of a ``node_id`` wins; duplicates are discarded.
        :attr:`_seen_ids` is reset after extraction completes so that this
        method is idempotent across successive calls.

        Parameters
        ----------
        raws:
            Iterable of dict-like objects to convert.

        Returns
        -------
        list[TheoryNode]
            Deduplicated list of extracted nodes.
        """
        self._seen_ids = set()
        result: list[TheoryNode] = []
        for raw in raws:
            node = self.extract_node(raw)
            if node.node_id in self._seen_ids:
                logger.debug("Skipping duplicate node_id=%s", node.node_id)
                continue
            self._seen_ids.add(node.node_id)
            result.append(node)
        self._seen_ids = set()
        return result

    def extract_from_text(
        self, text: str, node_id: str | None = None
    ) -> TheoryNode:
        """Parse a free-form text string into a :class:`TheoryNode`.

        The first non-empty line (or first 50 characters if the text is a
        single long line) is used as the node name; the remainder becomes
        the description (truncated to 500 characters).

        Parameters
        ----------
        text:
            Free-form text representing a theory element.
        node_id:
            Optional explicit ``node_id``.  A UUID-4 is generated when
            ``None``.

        Returns
        -------
        TheoryNode
            Node with ``maturity=NASCENT`` and ``purpose_alignment=0.0``.
        """
        effective_id = node_id if node_id is not None else str(uuid.uuid4())
        stripped = text.strip()
        lines = stripped.splitlines()

        # Derive name from first non-empty line, or from initial characters
        name = ""
        remaining_lines: list[str] = []
        for i, line in enumerate(lines):
            if line.strip():
                name = line.strip()[:100]
                remaining_lines = lines[i + 1 :]
                break
        if not name:
            name = stripped[:50] if stripped else effective_id[:16]

        description = "\n".join(remaining_lines).strip()[:500]
        if not description:
            description = stripped[:500]

        metadata = frozenset(
            [
                ("source", "text"),
                ("char_count", str(len(text))),
            ]
        )

        return TheoryNode(
            node_id=effective_id,
            name=name,
            description=description,
            purpose_alignment=0.0,
            maturity=NodeMaturity.NASCENT,
            connections=frozenset(),
            metadata=metadata,
            created_at=_now_iso(),
        )

    def filter_by_config(
        self,
        nodes: list[TheoryNode],
        config: SpaceConstructionConfig | None = None,
    ) -> list[TheoryNode]:
        """Apply configuration-based filters and truncation to *nodes*.

        Applies three successive filters then sorts and truncates:

        1. Remove ``NASCENT`` nodes when
           :attr:`SpaceConstructionConfig.include_nascent` is ``False``.
        2. Remove nodes whose ``purpose_alignment`` is below
           :attr:`SpaceConstructionConfig.min_purpose_alignment`.
        3. Sort by ``(purpose_alignment DESC, maturity_rank DESC)`` and
           truncate to :attr:`SpaceConstructionConfig.max_nodes`.

        Parameters
        ----------
        nodes:
            Input list of :class:`TheoryNode` objects.

        Returns
        -------
        list[TheoryNode]
            Filtered and possibly truncated list.
        """
        active_config = config or self.config
        filtered = list(nodes)

        # Step 1: optional nascent exclusion
        if not active_config.include_nascent:
            before = len(filtered)
            filtered = [
                n for n in filtered if n.maturity != NodeMaturity.NASCENT
            ]
            removed = before - len(filtered)
            if removed:
                logger.debug("Excluded %d NASCENT nodes", removed)

        # Step 2: minimum purpose alignment
        if active_config.min_purpose_alignment > 0.0:
            before = len(filtered)
            filtered = [
                n
                for n in filtered
                if n.purpose_alignment >= active_config.min_purpose_alignment
            ]
            removed = before - len(filtered)
            if removed:
                logger.debug(
                    "Excluded %d nodes below min_purpose_alignment=%.3f",
                    removed,
                    active_config.min_purpose_alignment,
                )

        # Step 3: sort and truncate
        filtered.sort(
            key=lambda n: (n.purpose_alignment, _maturity_rank(n.maturity)),
            reverse=True,
        )
        if len(filtered) > active_config.max_nodes:
            logger.debug(
                "Truncating node list from %d to %d (max_nodes)",
                len(filtered),
                active_config.max_nodes,
            )
            filtered = filtered[: active_config.max_nodes]

        return filtered

    def extraction_stats(self, nodes: list[TheoryNode]) -> dict[str, Any]:
        """Compute descriptive statistics over an extracted node list.

        Parameters
        ----------
        nodes:
            List of :class:`TheoryNode` objects to analyse.

        Returns
        -------
        dict
            Keys: ``total``, ``by_maturity``, ``avg_purpose_alignment``,
            ``min_purpose_alignment``, ``max_purpose_alignment``,
            ``with_connections``, ``avg_connections``.
        """
        total = len(nodes)
        if total == 0:
            return {
                "total": 0,
                "by_maturity": {m.value: 0 for m in NodeMaturity},
                "avg_purpose_alignment": 0.0,
                "min_purpose_alignment": 0.0,
                "max_purpose_alignment": 0.0,
                "with_connections": 0,
                "avg_connections": 0.0,
            }

        by_maturity: dict[str, int] = {m.value: 0 for m in NodeMaturity}
        alignments: list[float] = []
        connection_counts: list[int] = []

        for node in nodes:
            by_maturity[node.maturity.value] = (
                by_maturity.get(node.maturity.value, 0) + 1
            )
            alignments.append(node.purpose_alignment)
            connection_counts.append(len(node.connections))

        with_connections = sum(1 for c in connection_counts if c > 0)
        avg_connections = sum(connection_counts) / total if total else 0.0

        return {
            "total": total,
            "by_maturity": by_maturity,
            "avg_purpose_alignment": sum(alignments) / total,
            "min_purpose_alignment": min(alignments),
            "max_purpose_alignment": max(alignments),
            "with_connections": with_connections,
            "avg_connections": avg_connections,
        }


# ---------------------------------------------------------------------------
# EdgeBuilder
# ---------------------------------------------------------------------------


class EdgeBuilder:
    """Builds edges between :class:`TheoryNode` objects based on similarity or
    explicit connection declarations.

    Parameters
    ----------
    config:
        The :class:`SpaceConstructionConfig` governing edge-building behaviour.
    """

    def __init__(self, config: SpaceConstructionConfig | None = None) -> None:
        self.config = config or SpaceConstructionConfig()

    def compute_similarity(self, a: TheoryNode, b: TheoryNode) -> float:
        """Compute a composite similarity score between two nodes.

        The score combines:

        * Jaccard similarity on the description tokens (primary signal).
        * 30 % of the Jaccard similarity on the name tokens (secondary signal).

        The result is clamped to ``[0.0, 1.0]``.

        Parameters
        ----------
        a:
            First node.
        b:
            Second node.

        Returns
        -------
        float
            Similarity score in ``[0.0, 1.0]``.
        """
        desc_tokens_a = _tokenize(a.description)
        desc_tokens_b = _tokenize(b.description)
        jaccard_desc = _jaccard(desc_tokens_a, desc_tokens_b)

        name_tokens_a = _tokenize(a.name)
        name_tokens_b = _tokenize(b.name)
        name_bonus = _jaccard(name_tokens_a, name_tokens_b) * 0.3

        return min(1.0, jaccard_desc + name_bonus)

    def build_edges(
        self,
        nodes: list[TheoryNode],
        config: SpaceConstructionConfig | None = None,
    ) -> dict[str, set[str]] | list[tuple[str, str]]:
        """Compute similarity-based edges for all pairs in *nodes*.

        Only pairs whose :meth:`compute_similarity` score exceeds
        :attr:`SpaceConstructionConfig.similarity_threshold` receive an edge.
        After computing all candidate edges, outgoing edges per node are
        pruned to keep at most
        :attr:`SpaceConstructionConfig.max_edges_per_node` neighbours
        (highest similarity first).

        Parameters
        ----------
        nodes:
            List of :class:`TheoryNode` objects to connect.

        Returns
        -------
        dict[str, set[str]]
            Adjacency dict ``node_id → set of neighbour node_ids``.
        """
        # scored_edges[node_id] = list of (similarity, neighbour_id)
        active_config = config or self.config
        scored_edges: dict[str, list[tuple[float, str]]] = defaultdict(list)

        n = len(nodes)
        for i in range(n):
            for j in range(i + 1, n):
                a = nodes[i]
                b = nodes[j]
                sim = self.compute_similarity(a, b)
                if sim >= active_config.similarity_threshold:
                    scored_edges[a.node_id].append((sim, b.node_id))
                    if active_config.use_bidirectional_edges:
                        scored_edges[b.node_id].append((sim, a.node_id))
                    else:
                        # Still need reverse direction in the scored list for
                        # pruning to work correctly, but only add the forward
                        # direction to the output.
                        scored_edges[b.node_id].append((sim, a.node_id))

        result: dict[str, set[str]] = {}
        for node_id, candidates in scored_edges.items():
            # Sort by similarity descending, then take top max_edges_per_node
            candidates.sort(key=lambda x: x[0], reverse=True)
            top = candidates[: active_config.max_edges_per_node]
            result[node_id] = {nbr for _, nbr in top}

        if config is not None:
            edge_pairs: list[tuple[str, str]] = []
            seen: set[frozenset[str]] = set()
            for src, neighbors in result.items():
                for dst in sorted(neighbors):
                    pair_key = frozenset((src, dst))
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)
                    edge_pairs.append((src, dst))
            return edge_pairs
        return result

    def build_from_connections(
        self, nodes: list[TheoryNode]
    ) -> list[tuple[str, str]] | dict[str, set[str]]:
        """Build edges from the explicit ``connections`` field of each node.

        Only edges pointing to nodes that are actually in *nodes* are
        included.  If :attr:`SpaceConstructionConfig.use_bidirectional_edges`
        is ``True``, reverse edges are also added.

        Parameters
        ----------
        nodes:
            List of :class:`TheoryNode` objects whose ``connections`` fields
            will be read.

        Returns
        -------
        dict[str, set[str]]
            Adjacency dict ``node_id → set of explicit neighbour node_ids``.
        """
        valid_ids: set[str] = {n.node_id for n in nodes}
        result: dict[str, set[str]] = defaultdict(set)

        for node in nodes:
            for target_id in node.connections:
                if target_id not in valid_ids:
                    continue
                if target_id == node.node_id:
                    continue
                result[node.node_id].add(target_id)
                if self.config.use_bidirectional_edges:
                    result[target_id].add(node.node_id)

        edge_pairs: list[tuple[str, str]] = []
        seen: set[frozenset[str]] = set()
        for src, targets in dict(result).items():
            for dst in sorted(targets):
                pair_key = frozenset((src, dst))
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                edge_pairs.append((src, dst))
        return edge_pairs

    def merge_edges(
        self,
        explicit: dict[str, set[str]] | list[tuple[str, str]],
        computed: dict[str, set[str]] | list[tuple[str, str]],
    ) -> dict[str, set[str]] | list[tuple[str, str]]:
        """Return the union of *explicit* and *computed* edge dicts.

        Explicit edges take priority when both sources reference the same node:
        explicit edges are not subject to the ``max_edges_per_node`` limit,
        but computed edges that would push a node over the limit are dropped
        (lowest-priority computed edges dropped first, which here means
        alphabetically last, since we don't have scores at this stage).

        Parameters
        ----------
        explicit:
            Edges derived from :meth:`build_from_connections`.
        computed:
            Edges derived from :meth:`build_edges`.

        Returns
        -------
        dict[str, set[str]]
            Merged adjacency dict.
        """
        explicit_is_list = isinstance(explicit, list)
        computed_is_list = isinstance(computed, list)

        if explicit_is_list:
            explicit_dict: dict[str, set[str]] = defaultdict(set)
            for src, dst in explicit:
                explicit_dict[src].add(dst)
                explicit_dict[dst].add(src)
            explicit = dict(explicit_dict)
        if computed_is_list:
            computed_dict: dict[str, set[str]] = defaultdict(set)
            for src, dst in computed:
                computed_dict[src].add(dst)
                computed_dict[dst].add(src)
            computed = dict(computed_dict)

        all_ids: set[str] = set(explicit) | set(computed)
        merged: dict[str, set[str]] = {}

        for node_id in all_ids:
            exp_nbrs = explicit.get(node_id, set())
            comp_nbrs = computed.get(node_id, set())

            # Explicit edges always included; fill remaining slots with computed
            combined = set(exp_nbrs)
            remaining_capacity = max(0, self.config.max_edges_per_node - len(combined))

            # Add computed edges sorted to have deterministic output
            extra_computed = sorted(comp_nbrs - combined)[:remaining_capacity]
            combined.update(extra_computed)
            merged[node_id] = combined

        if explicit_is_list or computed_is_list:
            edge_pairs: list[tuple[str, str]] = []
            seen: set[frozenset[str]] = set()
            for src, targets in merged.items():
                for dst in sorted(targets):
                    pair_key = frozenset((src, dst))
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)
                    edge_pairs.append((src, dst))
            return edge_pairs
        return merged

    def prune_weak_edges(
        self,
        edges: dict[str, set[str]] | list[tuple[str, str]],
        nodes_by_id: dict[str, TheoryNode] | None = None,
        keep_top_n: int = 10,
        max_per_node: int | None = None,
    ) -> dict[str, set[str]] | list[tuple[str, str]]:
        """Re-rank each node's neighbours by similarity and keep only the top *n*.

        Parameters
        ----------
        edges:
            Adjacency dict to prune.
        nodes_by_id:
            Mapping from ``node_id`` to :class:`TheoryNode`.
        keep_top_n:
            Maximum neighbours to retain per node.

        Returns
        -------
        dict[str, set[str]]
            Pruned adjacency dict.
        """
        if max_per_node is not None:
            keep_top_n = max_per_node

        edges_is_list = isinstance(edges, list)
        if edges_is_list:
            adjacency: dict[str, set[str]] = defaultdict(set)
            for src, dst in edges:
                adjacency[src].add(dst)
                adjacency[dst].add(src)
            edges = dict(adjacency)

        pruned: dict[str, set[str]] = {}
        for node_id, neighbours in edges.items():
            source = nodes_by_id.get(node_id) if nodes_by_id is not None else None
            if source is None or nodes_by_id is None:
                pruned[node_id] = set(neighbours)
                continue

            scored: list[tuple[float, str]] = []
            for nbr_id in neighbours:
                target = nodes_by_id.get(nbr_id)
                if target is None:
                    continue
                sim = self.compute_similarity(source, target)
                scored.append((sim, nbr_id))

            scored.sort(key=lambda x: x[0], reverse=True)
            pruned[node_id] = {nbr_id for _, nbr_id in scored[:keep_top_n]}

        if edges_is_list:
            edge_pairs: list[tuple[str, str]] = []
            seen: set[frozenset[str]] = set()
            for src, targets in pruned.items():
                for dst in sorted(targets):
                    pair_key = frozenset((src, dst))
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)
                    edge_pairs.append((src, dst))
            return edge_pairs
        return pruned

    def edge_stats(self, edges: dict[str, set[str]] | list[tuple[str, str]]) -> dict[str, Any]:
        """Compute descriptive statistics over an edge dict.

        Parameters
        ----------
        edges:
            Adjacency dict to analyse.

        Returns
        -------
        dict
            Keys: ``total_directed_edges``, ``total_undirected_edges``,
            ``avg_degree``, ``max_degree``, ``max_degree_node``,
            ``isolated_nodes``, ``degree_distribution``.
        """
        degree_distribution: dict[int, int] = defaultdict(int)
        max_degree = 0
        max_degree_node = ""
        total_directed = 0
        isolated = 0

        if isinstance(edges, list):
            adjacency: dict[str, set[str]] = defaultdict(set)
            for src, dst in edges:
                adjacency[src].add(dst)
                adjacency[dst].add(src)
            edges = dict(adjacency)

        for node_id, neighbours in edges.items():
            deg = len(neighbours)
            total_directed += deg
            degree_distribution[deg] += 1
            if deg > max_degree:
                max_degree = deg
                max_degree_node = node_id
            if deg == 0:
                isolated += 1

        n = len(edges)
        avg_degree = total_directed / n if n > 0 else 0.0
        total_undirected = total_directed // 2

        return {
            "total_directed_edges": total_directed,
            "total_undirected_edges": total_undirected,
            "avg_degree": avg_degree,
            "max_degree": max_degree,
            "max_degree_node": max_degree_node,
            "isolated_nodes": isolated,
            "degree_distribution": dict(degree_distribution),
        }


# ---------------------------------------------------------------------------
# SpaceIndexer
# ---------------------------------------------------------------------------


class SpaceIndexer:
    """In-memory keyword, maturity, and purpose-alignment indexes over a
    :class:`TheorySpace`.

    The indexer must be rebuilt via :meth:`build_index` whenever the underlying
    space changes.  All lookup methods return empty results when the index has
    not been built.
    """

    def __init__(self) -> None:
        self._keyword_index: dict[str, set[str]] = defaultdict(set)
        self._maturity_index: dict[NodeMaturity, set[str]] = defaultdict(set)
        self._purpose_index: list[tuple[float, str]] = []
        self._node_tokens: dict[str, set[str]] = {}
        self._space: TheorySpace | None = None
        self._built: bool = False

    def build_index(self, space: TheorySpace) -> None:
        """Build all indexes from the nodes in *space*.

        Clears any existing index data before rebuilding.  After this method
        returns, :attr:`_built` is ``True``.

        Parameters
        ----------
        space:
            The :class:`TheorySpace` to index.
        """
        self.clear()
        self._space = space

        for node in space.iter_nodes():
            tokens = _tokenize(node.name + " " + node.description)
            self._node_tokens[node.node_id] = tokens

            for token in tokens:
                self._keyword_index[token].add(node.node_id)

            self._maturity_index[node.maturity].add(node.node_id)
            self._purpose_index.append((node.purpose_alignment, node.node_id))

        # Sort ascending by alignment so we can binary-search efficiently
        self._purpose_index.sort(key=lambda x: x[0])
        self._built = True
        logger.debug(
            "SpaceIndexer: indexed %d nodes, %d keywords",
            len(self._node_tokens),
            len(self._keyword_index),
        )

    def build(self, space: TheorySpace) -> None:
        """Compatibility alias for :meth:`build_index`."""
        self.build_index(space)

    def lookup_by_keyword(self, keyword: str) -> list[str]:
        """Return node_ids whose tokens contain *keyword*.

        Parameters
        ----------
        keyword:
            Keyword to search for (case-insensitive).

        Returns
        -------
        list[str]
            Sorted list of matching node_ids, or ``[]`` if not built.
        """
        if not self._built:
            return []
        normalised = keyword.lower().strip()
        return sorted(self._keyword_index.get(normalised, set()))

    def lookup_by_maturity(self, maturity: NodeMaturity) -> list[str]:
        """Return node_ids with the given *maturity*.

        Parameters
        ----------
        maturity:
            The :class:`NodeMaturity` level to filter on.

        Returns
        -------
        list[str]
            Sorted list of matching node_ids, or ``[]`` if not built.
        """
        if not self._built:
            return []
        return sorted(self._maturity_index.get(maturity, set()))

    def lookup_by_purpose_range(
        self,
        lo: float | None = None,
        hi: float | None = None,
        *,
        min_alignment: float | None = None,
        max_alignment: float | None = None,
    ) -> list[str]:
        """Return node_ids whose ``purpose_alignment`` is in ``[lo, hi]``.

        Uses binary search on the sorted ``_purpose_index`` list for
        efficiency on large spaces.

        Parameters
        ----------
        lo:
            Lower bound (inclusive).
        hi:
            Upper bound (inclusive).

        Returns
        -------
        list[str]
            Node_ids with alignment in range, sorted by alignment descending.
        """
        if lo is None:
            lo = 0.0 if min_alignment is None else min_alignment
        if hi is None:
            hi = 1.0 if max_alignment is None else max_alignment
        if not self._built or not self._purpose_index:
            return []

        # Binary search for left boundary (first index >= lo)
        left = 0
        right = len(self._purpose_index)
        while left < right:
            mid = (left + right) // 2
            if self._purpose_index[mid][0] < lo:
                left = mid + 1
            else:
                right = mid

        # Collect all entries with alignment <= hi
        in_range: list[tuple[float, str]] = []
        for i in range(left, len(self._purpose_index)):
            alignment, node_id = self._purpose_index[i]
            if alignment > hi:
                break
            in_range.append((alignment, node_id))

        # Return sorted highest-alignment first
        in_range.sort(key=lambda x: x[0], reverse=True)
        return [node_id for _, node_id in in_range]

    def top_n_by_purpose(self, n: int = 10) -> list[str]:
        """Return the top *n* node_ids by ``purpose_alignment``.

        Parameters
        ----------
        n:
            Number of top nodes to return.

        Returns
        -------
        list[str]
            Node_ids sorted by purpose_alignment descending (highest first).
        """
        if not self._built or not self._purpose_index:
            return []
        # _purpose_index is sorted ascending; take from the end
        top = self._purpose_index[-n:][::-1]
        return [node_id for _, node_id in top]

    def nearest_neighbors(
        self, node_id: str, space: TheorySpace | None = None, n: int = 5
    ) -> list[str]:
        """Return the *n* most similar nodes to *node_id* based on token overlap.

        Similarity is computed as Jaccard similarity between the token sets
        stored in :attr:`_node_tokens`.

        Parameters
        ----------
        node_id:
            The query node.
        space:
            The :class:`TheorySpace` providing neighbour candidates.
        n:
            Number of nearest neighbours to return.

        Returns
        -------
        list[str]
            Up to *n* most similar node_ids (excluding *node_id* itself),
            sorted by similarity descending.  Returns ``[]`` when *node_id*
            is not in the index.
        """
        if not self._built:
            return []
        space = space or self._space
        if space is None:
            return []
        query_tokens = self._node_tokens.get(node_id)
        if query_tokens is None:
            return []

        scored: list[tuple[float, str]] = []
        for other_id, other_tokens in self._node_tokens.items():
            if other_id == node_id:
                continue
            sim = _jaccard(query_tokens, other_tokens)
            if sim > 0.0:
                scored.append((sim, other_id))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [nid for _, nid in scored[:n]]

    def index_stats(self) -> dict[str, Any]:
        """Return summary statistics about the current index state.

        Returns
        -------
        dict
            Keys: ``built``, ``keyword_count``, ``maturity_buckets``,
            ``indexed_nodes``, ``purpose_index_size``.
        """
        maturity_buckets = {
            m.value: len(self._maturity_index.get(m, set()))
            for m in NodeMaturity
        }
        return {
            "built": self._built,
            "keyword_count": len(self._keyword_index),
            "maturity_buckets": maturity_buckets,
            "indexed_nodes": len(self._node_tokens),
            "purpose_index_size": len(self._purpose_index),
        }

    def clear(self) -> None:
        """Reset all indexes and mark as not built."""
        self._keyword_index = defaultdict(set)
        self._maturity_index = defaultdict(set)
        self._purpose_index = []
        self._node_tokens = {}
        self._space = None
        self._built = False


# ---------------------------------------------------------------------------
# SpaceConstructor
# ---------------------------------------------------------------------------


class SpaceConstructor:
    """Orchestrates the full theory-space construction pipeline.

    The pipeline is:

    1. Extract nodes from raw input.
    2. Filter by configuration.
    3. Build explicit edges from ``connections`` fields.
    4. Build similarity edges.
    5. Merge and prune the two edge sets.
    6. Assemble into a :class:`TheorySpace`.
    7. Build the index.
    8. Store a construction report.

    Parameters
    ----------
    config:
        Optional :class:`SpaceConstructionConfig`.  A default instance is
        created when ``None``.
    """

    def __init__(self, config: SpaceConstructionConfig | None = None) -> None:
        self.config = config or SpaceConstructionConfig()
        self._extractor = NodeExtractor(self.config)
        self._edge_builder = EdgeBuilder(self.config)
        self._indexer = SpaceIndexer()
        self._last_report: str = ""

    def construct(
        self, raw_nodes: Iterable[Mapping[str, Any]]
    ) -> TheorySpace:
        """Run the full construction pipeline on *raw_nodes*.

        Parameters
        ----------
        raw_nodes:
            Iterable of dict-like objects describing theory nodes.

        Returns
        -------
        TheorySpace
            Fully constructed, indexed theory space.
        """
        logger.info("SpaceConstructor: starting construction pipeline")

        # Stage 1: Extract
        nodes = self._extractor.extract_nodes(raw_nodes)
        logger.debug("Extracted %d nodes", len(nodes))

        # Stage 2: Filter
        nodes = self._extractor.filter_by_config(nodes)
        logger.debug("After filtering: %d nodes", len(nodes))

        # Stage 3: Explicit edges
        explicit_edges = self._edge_builder.build_from_connections(nodes)
        logger.debug(
            "Explicit edges cover %d source nodes", len(explicit_edges)
        )

        # Stage 4: Similarity edges
        similarity_edges = self._edge_builder.build_edges(nodes)
        logger.debug(
            "Similarity edges cover %d source nodes", len(similarity_edges)
        )

        # Stage 5: Merge and prune
        merged = self._edge_builder.merge_edges(explicit_edges, similarity_edges)
        nodes_by_id: dict[str, TheoryNode] = {n.node_id: n for n in nodes}
        pruned = self._edge_builder.prune_weak_edges(
            merged,
            nodes_by_id,
            keep_top_n=self.config.max_edges_per_node,
        )

        # Stage 6: Assemble
        space = self.assemble_space(nodes, pruned)
        logger.debug(
            "Assembled space: %d nodes, %d directed edges",
            space.node_count(),
            sum(len(v) for v in space.edges.values()),
        )

        # Stage 7: Index
        self._indexer.build_index(space)

        # Stage 8: Report
        self._last_report = self.construction_report(space)
        logger.info("SpaceConstructor: construction complete")

        return space

    def construct_from_texts(self, texts: list[str]) -> TheorySpace:
        """Build a theory space from a list of free-form text strings.

        Each text is converted to a :class:`TheoryNode` via
        :meth:`NodeExtractor.extract_from_text`, and then the standard
        pipeline (filter, edges, assemble, index) is run.

        Parameters
        ----------
        texts:
            List of text strings, one per node.

        Returns
        -------
        TheorySpace
            Constructed space.
        """
        logger.info(
            "SpaceConstructor: constructing from %d text strings", len(texts)
        )
        nodes: list[TheoryNode] = []
        for text in texts:
            node = self._extractor.extract_from_text(text)
            nodes.append(node)

        # Deduplicate by node_id (should all be unique since UUIDs are generated)
        seen: set[str] = set()
        unique_nodes: list[TheoryNode] = []
        for node in nodes:
            if node.node_id not in seen:
                seen.add(node.node_id)
                unique_nodes.append(node)

        filtered = self._extractor.filter_by_config(unique_nodes)

        explicit_edges = self._edge_builder.build_from_connections(filtered)
        similarity_edges = self._edge_builder.build_edges(filtered)
        merged = self._edge_builder.merge_edges(explicit_edges, similarity_edges)
        nodes_by_id: dict[str, TheoryNode] = {n.node_id: n for n in filtered}
        pruned = self._edge_builder.prune_weak_edges(
            merged,
            nodes_by_id,
            keep_top_n=self.config.max_edges_per_node,
        )

        space = self.assemble_space(filtered, pruned)
        self._indexer.build_index(space)
        self._last_report = self.construction_report(space)

        return space

    def assemble_space(
        self,
        nodes: list[TheoryNode],
        edges: dict[str, set[str]] | list[tuple[str, str]],
    ) -> TheorySpace:
        """Assemble a :class:`TheorySpace` from a node list and an edge dict.

        Parameters
        ----------
        nodes:
            Nodes to add to the space.
        edges:
            Either an adjacency dict ``from_id → set[to_id]`` or a list of
            edge pairs.

        Returns
        -------
        TheorySpace
            Populated space instance.
        """
        space = TheorySpace()

        for node in nodes:
            space.add_node(node)

        edge_items = (
            edges.items()
            if isinstance(edges, dict)
            else ((from_id, {to_id}) for from_id, to_id in edges)
        )
        for from_id, neighbours in edge_items:
            for to_id in neighbours:
                if space.has_node(from_id) and space.has_node(to_id):
                    # Use non-bidirectional add because bidirectionality is
                    # already encoded in the edge dict by EdgeBuilder
                    space.add_edge(from_id, to_id, bidirectional=False)

        return space

    def validate_space(self, space: TheorySpace) -> list[str]:
        """Check the constructed space for common problems.

        Parameters
        ----------
        space:
            The space to validate.

        Returns
        -------
        list[str]
            List of validation error strings.  Empty when no issues are found.
        """
        errors: list[str] = []
        node_count = space.node_count()

        if node_count == 0:
            errors.append("Space is empty")
            return errors  # No further checks make sense

        if node_count > self.config.max_nodes:
            errors.append(
                f"Space exceeds max_nodes limit "
                f"({node_count} > {self.config.max_nodes})"
            )

        # Isolated nodes
        isolated_reported = 0
        for node_id in space.nodes:
            nbrs = space.edges.get(node_id, set())
            if not nbrs:
                if isolated_reported < 10:
                    errors.append(
                        f"Node '{node_id}' is isolated (no edges)"
                    )
                isolated_reported += 1

        # Dangling edge references
        all_node_ids = set(space.nodes.keys())
        for from_id, neighbours in space.edges.items():
            if from_id not in all_node_ids:
                errors.append(
                    f"Edge source '{from_id}' references non-existent node"
                )
            for to_id in neighbours:
                if to_id not in all_node_ids:
                    errors.append(
                        f"Edge target '{to_id}' (from '{from_id}') "
                        f"references non-existent node"
                    )

        return errors

    def construction_report(self, space: TheorySpace) -> str:
        """Produce a human-readable multi-line construction report.

        Parameters
        ----------
        space:
            The constructed :class:`TheorySpace` to report on.

        Returns
        -------
        str
            Multi-line report string.
        """
        lines: list[str] = []
        lines.append("=== Theory Space Construction Report ===")

        node_count = space.node_count()
        lines.append(f"Nodes: {node_count} (target max: {self.config.max_nodes})")

        edge_total = sum(len(v) for v in space.edges.values())
        lines.append(f"Edges (directed): {edge_total}")

        avg_degree = edge_total / node_count if node_count > 0 else 0.0
        lines.append(f"Average degree: {avg_degree:.2f}")

        # Maturity breakdown
        by_maturity: dict[str, int] = {m.value: 0 for m in NodeMaturity}
        alignments: list[float] = []
        for node in space.iter_nodes():
            by_maturity[node.maturity.value] = (
                by_maturity.get(node.maturity.value, 0) + 1
            )
            alignments.append(node.purpose_alignment)

        lines.append("Maturity breakdown:")
        for level, count in by_maturity.items():
            lines.append(f"  {level}: {count}")

        if alignments:
            avg_pa = sum(alignments) / len(alignments)
            min_pa = min(alignments)
            max_pa = max(alignments)
        else:
            avg_pa = min_pa = max_pa = 0.0
        lines.append(
            f"Purpose alignment: avg={avg_pa:.3f}, "
            f"min={min_pa:.3f}, max={max_pa:.3f}"
        )

        # Validation
        issues = self.validate_space(space)
        if issues:
            lines.append(f"Validation: {len(issues)} issues found")
            for issue in issues[:5]:
                lines.append(f"  - {issue}")
        else:
            lines.append("Validation: OK")

        # Config summary
        lines.append(
            f"Config: max_nodes={self.config.max_nodes}, "
            f"threshold={self.config.similarity_threshold:.3f}, "
            f"bidirectional={self.config.use_bidirectional_edges}, "
            f"include_nascent={self.config.include_nascent}"
        )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# IncrementalSpaceUpdater
# ---------------------------------------------------------------------------


class IncrementalSpaceUpdater:
    """Supports incremental add / remove / update of nodes in a live
    :class:`TheorySpace` without a full reconstruction.

    Parameters
    ----------
    config:
        Optional :class:`SpaceConstructionConfig`.  A default instance is
        created when ``None``.
    """

    def __init__(self, config: SpaceConstructionConfig | None = None) -> None:
        self.config = config or SpaceConstructionConfig()
        self._extractor = NodeExtractor(self.config)
        self._edge_builder = EdgeBuilder(self.config)

    def _clone_space(self, space: TheorySpace) -> TheorySpace:
        cloned = TheorySpace()
        for node in space.iter_nodes():
            cloned.add_node(node)
        for from_id, neighbours in space.edges.items():
            for to_id in neighbours:
                if cloned.has_node(from_id) and cloned.has_node(to_id):
                    cloned.add_edge(from_id, to_id, bidirectional=False)
        return cloned

    def add_node(
        self,
        space: TheorySpace,
        raw: Mapping[str, Any] | TheoryNode,
    ) -> tuple[TheorySpace, list[str]] | TheorySpace:
        """Add a new node to *space* and connect it by similarity.

        If a node with the same ``node_id`` already exists in the space, the
        space is returned unchanged with an empty edge-description list.

        Parameters
        ----------
        space:
            The :class:`TheorySpace` to mutate.
        raw:
            Dict-like describing the new node.

        Returns
        -------
        tuple[TheorySpace, list[str]]
            Updated space and a list of human-readable edge-creation messages.
        """
        raw_is_node = isinstance(raw, TheoryNode)
        working_space = self._clone_space(space)
        new_node = self._extractor.extract_node(raw)

        if working_space.has_node(new_node.node_id):
            logger.debug(
                "IncrementalUpdater.add_node: node %s already exists",
                new_node.node_id,
            )
            return working_space if raw_is_node else (working_space, [])

        working_space.add_node(new_node)
        edge_descriptions: list[str] = []

        # Compute outgoing edge budget: how many edges can we still add?
        outgoing_capacity = self.config.max_edges_per_node
        candidate_sims: list[tuple[float, TheoryNode]] = []

        for existing_node in working_space.iter_nodes():
            if existing_node.node_id == new_node.node_id:
                continue
            sim = self._edge_builder.compute_similarity(new_node, existing_node)
            if sim >= self.config.similarity_threshold:
                candidate_sims.append((sim, existing_node))

        # Sort by similarity descending; respect outgoing capacity
        candidate_sims.sort(key=lambda x: x[0], reverse=True)

        for sim, existing_node in candidate_sims[:outgoing_capacity]:
            if self.config.use_bidirectional_edges:
                working_space.add_edge(
                    new_node.node_id, existing_node.node_id, bidirectional=True
                )
                edge_descriptions.append(
                    f"Added edge {new_node.node_id} <-> "
                    f"{existing_node.node_id} (sim={sim:.2f})"
                )
            else:
                working_space.add_edge(
                    new_node.node_id, existing_node.node_id, bidirectional=False
                )
                edge_descriptions.append(
                    f"Added edge {new_node.node_id} -> "
                    f"{existing_node.node_id} (sim={sim:.2f})"
                )

        logger.debug(
            "IncrementalUpdater.add_node: added node %s with %d edges",
            new_node.node_id,
            len(edge_descriptions),
        )
        return working_space if raw_is_node else (working_space, edge_descriptions)

    def remove_node(self, space: TheorySpace, node_id: str) -> TheorySpace:
        """Remove *node_id* and all its incident edges from *space*.

        If *node_id* is not in the space, returns the space unchanged.

        Parameters
        ----------
        space:
            The :class:`TheorySpace` to mutate.
        node_id:
            ID of the node to remove.

        Returns
        -------
        TheorySpace
            Updated space (same object, mutated in place).
        """
        working_space = self._clone_space(space)
        if not working_space.has_node(node_id):
            logger.debug(
                "IncrementalUpdater.remove_node: node %s not found", node_id
            )
            return working_space

        working_space.remove_node(node_id)
        # remove_node on TheorySpace already cleans up all incident edges,
        # but we explicitly clean up the edges dict entry for node_id
        if node_id in working_space.edges:
            del working_space.edges[node_id]
        for src in list(working_space.edges):
            working_space.edges[src].discard(node_id)

        logger.debug("IncrementalUpdater.remove_node: removed node %s", node_id)
        return working_space

    def update_node(
        self,
        space: TheorySpace,
        node_id: str | TheoryNode,
        updates: Mapping[str, Any] | None = None,
    ) -> TheorySpace:
        """Update an existing node by merging *updates* into its fields.

        If *node_id* is not found in *space*, the space is returned unchanged.
        The update procedure is:

        1. Convert the existing node to a raw dict.
        2. Merge *updates* over the existing fields.
        3. Preserve the original ``node_id``.
        4. Remove the old node (clearing its edges).
        5. Re-add the updated node (recomputing edges via :meth:`add_node`).

        Parameters
        ----------
        space:
            The :class:`TheorySpace` to mutate.
        node_id:
            ID of the node to update.
        updates:
            Partial mapping of fields to update.

        Returns
        -------
        TheorySpace
            Updated space.
        """
        if isinstance(node_id, TheoryNode):
            replacement = node_id
            node_id = replacement.node_id
            updates = {
                "name": replacement.name,
                "description": replacement.description,
                "maturity": replacement.maturity.value,
                "purpose_alignment": replacement.purpose_alignment,
                "connections": list(replacement.connections),
                "metadata": dict(replacement.metadata),
            }

        existing_node = space.get_node(node_id)
        if existing_node is None:
            logger.debug(
                "IncrementalUpdater.update_node: node %s not found", node_id
            )
            return space

        # Convert existing node to raw dict
        existing_raw: dict[str, Any] = {
            "id": existing_node.node_id,
            "name": existing_node.name,
            "description": existing_node.description,
            "maturity": existing_node.maturity.value,
            "purpose_alignment": existing_node.purpose_alignment,
            "connections": list(existing_node.connections),
            "metadata": dict(existing_node.metadata),
        }

        # Merge updates (shallow)
        merged_raw: dict[str, Any] = {**existing_raw, **dict(updates or {})}
        # Always preserve the original node_id
        merged_raw["id"] = node_id

        # Remove old node
        space = self.remove_node(space, node_id)

        # Re-add updated node
        add_result = self.add_node(space, merged_raw)
        if isinstance(add_result, tuple):
            space, _ = add_result
        else:
            space = add_result

        logger.debug(
            "IncrementalUpdater.update_node: updated node %s", node_id
        )
        return space

    def reindex(
        self,
        space: TheorySpace,
        config: SpaceConstructionConfig | None = None,
    ) -> TheorySpace:
        """Rebuild the entire space from scratch using the current nodes.

        All existing edges are discarded; new edges are recomputed from
        explicit connections and pairwise similarity.

        Parameters
        ----------
        space:
            The source :class:`TheorySpace`.

        Returns
        -------
        TheorySpace
            New :class:`TheorySpace` with freshly computed edges.
        """
        active_config = config or self.config
        if config is not None:
            self.config = active_config
            self._extractor = NodeExtractor(active_config)
            self._edge_builder = EdgeBuilder(active_config)

        nodes = list(space.iter_nodes())

        explicit_edges = self._edge_builder.build_from_connections(nodes)
        similarity_edges = self._edge_builder.build_edges(nodes)
        merged = self._edge_builder.merge_edges(explicit_edges, similarity_edges)
        nodes_by_id: dict[str, TheoryNode] = {n.node_id: n for n in nodes}
        pruned = self._edge_builder.prune_weak_edges(
            merged,
            nodes_by_id,
            keep_top_n=active_config.max_edges_per_node,
        )

        new_space = TheorySpace()
        for node in nodes:
            new_space.add_node(node)
        edge_items = (
            pruned.items()
            if isinstance(pruned, dict)
            else ((from_id, {to_id}) for from_id, to_id in pruned)
        )
        for from_id, neighbours in edge_items:
            for to_id in neighbours:
                if new_space.has_node(from_id) and new_space.has_node(to_id):
                    new_space.add_edge(from_id, to_id, bidirectional=False)

        logger.debug(
            "IncrementalUpdater.reindex: rebuilt space with %d nodes, "
            "%d directed edges",
            new_space.node_count(),
            sum(len(v) for v in new_space.edges.values()),
        )
        return new_space

    def merge_spaces(self, a: TheorySpace, b: TheorySpace) -> TheorySpace:
        """Produce a new space that is the union of *a* and *b*.

        Nodes from *a* are added first; nodes from *b* are added only when
        their ``node_id`` is not already present (i.e. *a* wins on conflicts).
        All original edges from both spaces are included.  Additionally,
        cross-space edges are computed via :meth:`EdgeBuilder.build_edges` on
        the combined node list.

        Parameters
        ----------
        a:
            First source space.
        b:
            Second source space.

        Returns
        -------
        TheorySpace
            Merged space.
        """
        merged_space = TheorySpace()

        # Add all nodes from a
        for node in a.iter_nodes():
            merged_space.add_node(node)

        # Add nodes from b (skip duplicates)
        for node in b.iter_nodes():
            if not merged_space.has_node(node.node_id):
                merged_space.add_node(node)

        # Restore original edges from a
        for from_id, neighbours in a.edges.items():
            for to_id in neighbours:
                if merged_space.has_node(from_id) and merged_space.has_node(to_id):
                    merged_space.add_edge(from_id, to_id, bidirectional=False)

        # Restore original edges from b
        for from_id, neighbours in b.edges.items():
            for to_id in neighbours:
                if merged_space.has_node(from_id) and merged_space.has_node(to_id):
                    merged_space.add_edge(from_id, to_id, bidirectional=False)

        # Compute cross-space edges
        all_nodes = list(merged_space.iter_nodes())
        cross_edges = self._edge_builder.build_edges(all_nodes)
        for from_id, neighbours in cross_edges.items():
            for to_id in neighbours:
                if merged_space.has_node(from_id) and merged_space.has_node(to_id):
                    # Only add if not already present to avoid duplicate work
                    existing_nbrs = merged_space.edges.get(from_id, set())
                    if to_id not in existing_nbrs:
                        merged_space.add_edge(from_id, to_id, bidirectional=False)

        logger.debug(
            "IncrementalUpdater.merge_spaces: merged into %d nodes, "
            "%d directed edges",
            merged_space.node_count(),
            sum(len(v) for v in merged_space.edges.values()),
        )
        return merged_space


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "SpaceConstructionConfig",
    "NodeExtractor",
    "EdgeBuilder",
    "SpaceIndexer",
    "SpaceConstructor",
    "IncrementalSpaceUpdater",
]
