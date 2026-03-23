"""Core domain models for the theory_navigation package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex and
provides the fundamental value objects and service class used throughout the
``jugeo.ideation.theory_navigation`` sub-package.

The central abstraction is a *theory space*: a directed graph whose nodes are
mathematical theory elements (definitions, lemmas, constructions, conjectures)
and whose edges represent dependency, implication, or analogy relationships.
Navigation through this space is purpose-conditioned — the
:class:`PurposeCondition` assigns relevance scores to nodes so that
path-finding algorithms can favour purpose-aligned routes.

Reference: theory2.tex — theory-space navigation, purpose-conditioned search,
and path-finding chapters.

Module layout::

    NodeMaturity               – enum: nascent / developing / mature / established
    NavigationStrategy         – enum: bfs / dfs / purpose_guided / beam / random
    PurposeCondition           – frozen: condition_id, label, keywords, weight
    TheoryNode                 – frozen: node_id, name, maturity, connections
    NavigationPath             – frozen: ordered tuple of node IDs with cost/alignment
    NavigationState            – frozen: current position in an active search
    TheorySpace                – mutable service: node/edge graph with query methods
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *value* clamped to [*lo*, *hi*].

    Parameters
    ----------
    value:
        Float to clamp.
    lo:
        Lower bound, inclusive.  Default 0.0.
    hi:
        Upper bound, inclusive.  Default 1.0.

    Returns
    -------
    float
        The clamped value.
    """
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        UTC timestamp in ISO-8601 format.
    """
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> set[str]:
    """Tokenise *text* into a set of lowercase word tokens.

    Strips punctuation, splits on whitespace/underscores, lower-cases all
    tokens, and discards single-character tokens.

    Parameters
    ----------
    text:
        Raw text to tokenise.

    Returns
    -------
    set[str]
        Set of normalised word tokens with length > 1.
    """
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9]*", text.lower())
    return {t for t in tokens if len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets.

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        Similarity in [0, 1].  Returns 0.0 when both sets are empty.
    """
    if not a and not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _normalize_score(raw: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Linearly normalise *raw* from [*lo*, *hi*] to [0, 1], then clamp.

    When ``hi == lo`` the function returns 0.5 to avoid division by zero.

    Parameters
    ----------
    raw:
        The raw score to normalise.
    lo:
        Minimum of the input range.
    hi:
        Maximum of the input range.

    Returns
    -------
    float
        Normalised score in [0, 1].
    """
    if hi == lo:
        return 0.5
    normalised = (raw - lo) / (hi - lo)
    return _clamp(normalised)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeMaturity(str, Enum):
    """Maturity level of a theory node, encoding how well-developed it is.

    The maturity level affects how path-finding and purpose alignment treat a
    node: nascent nodes are speculative, established nodes are foundational.
    """

    NASCENT = "nascent"
    DEVELOPING = "developing"
    MATURE = "mature"
    ESTABLISHED = "established"

    def numeric_value(self) -> float:
        """Return a numeric maturity score in (0, 1].

        Returns
        -------
        float
            ``0.25`` for :attr:`NASCENT`, ``0.5`` for :attr:`DEVELOPING`,
            ``0.75`` for :attr:`MATURE`, ``1.0`` for :attr:`ESTABLISHED`.
        """
        _values: dict[str, float] = {
            "nascent": 0.25,
            "developing": 0.50,
            "mature": 0.75,
            "established": 1.00,
        }
        return _values[self.value]

    @classmethod
    def from_score(cls, score: float) -> NodeMaturity:
        """Return the :class:`NodeMaturity` that best matches a numeric *score*.

        Thresholds:
        - score < 0.25 → :attr:`NASCENT`
        - score < 0.50 → :attr:`DEVELOPING`
        - score < 0.75 → :attr:`MATURE`
        - score ≥ 0.75 → :attr:`ESTABLISHED`

        Parameters
        ----------
        score:
            Numeric maturity score, typically in [0, 1].

        Returns
        -------
        NodeMaturity
            The closest maturity level.
        """
        clamped = _clamp(score)
        if clamped < 0.25:
            return cls.NASCENT
        if clamped < 0.50:
            return cls.DEVELOPING
        if clamped < 0.75:
            return cls.MATURE
        return cls.ESTABLISHED

    def description(self) -> str:
        """Return a human-readable description of this maturity level.

        Returns
        -------
        str
            Short prose description.
        """
        _desc: dict[str, str] = {
            "nascent": "Speculative or newly proposed; not yet formalised",
            "developing": "Partially formalised; proofs or constructions incomplete",
            "mature": "Well-formalised with proofs; ready for use in larger arguments",
            "established": "Foundational; widely used and fully verified",
        }
        return _desc[self.value]


class NavigationStrategy(str, Enum):
    """Strategy used to navigate a :class:`TheorySpace`.

    Different strategies trade off completeness, efficiency, and
    purpose-alignment differently.  The :meth:`is_heuristic` method
    distinguishes exact from heuristic strategies.
    """

    BREADTH_FIRST = "breadth_first"
    DEPTH_FIRST = "depth_first"
    PURPOSE_GUIDED = "purpose_guided"
    BEAM_SEARCH = "beam_search"
    RANDOM_WALK = "random_walk"

    def is_heuristic(self) -> bool:
        """Return ``True`` for strategies that use a heuristic function.

        Returns
        -------
        bool
            ``True`` for :attr:`PURPOSE_GUIDED` and :attr:`BEAM_SEARCH`.
        """
        return self in (NavigationStrategy.PURPOSE_GUIDED, NavigationStrategy.BEAM_SEARCH)

    def description(self) -> str:
        """Return a human-readable description of this navigation strategy.

        Returns
        -------
        str
            Short prose description.
        """
        _desc: dict[str, str] = {
            "breadth_first": (
                "Explores all nodes at the current depth before going deeper; "
                "guarantees shortest-path in unweighted graphs"
            ),
            "depth_first": (
                "Explores as far as possible along a branch before backtracking; "
                "memory-efficient but may miss shorter paths"
            ),
            "purpose_guided": (
                "Uses a purpose-alignment heuristic to prioritise nodes most "
                "relevant to the declared research purpose; best for directed search"
            ),
            "beam_search": (
                "Maintains a fixed-width beam of top candidates; balances "
                "exploration quality with computational cost"
            ),
            "random_walk": (
                "Samples paths by random neighbour selection; useful for "
                "diversity and exploring uncharted regions"
            ),
        }
        return _desc[self.value]


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PurposeCondition:
    """An immutable descriptor encoding a research purpose for navigation.

    A :class:`PurposeCondition` specifies *what the researcher is looking for*:
    a labelled purpose with associated keywords and an importance weight.  It
    is used by path-finding algorithms to score nodes and prioritise
    purpose-relevant routes through a :class:`TheorySpace`.

    Attributes
    ----------
    condition_id:
        Unique snake_case identifier for this condition.
    label:
        Short human-readable label, e.g. ``"algebraic_closure"``.
    description:
        Prose description of the research purpose.
    keywords:
        Tuple of keywords that characterise purpose-relevant nodes.
    weight:
        Importance weight in [0, 1].  Higher means this condition matters
        more relative to other conditions.  Clamped on construction.
    created_at:
        ISO-8601 UTC timestamp of condition creation.
    """

    condition_id: str
    label: str
    description: str
    keywords: tuple[str, ...]
    weight: float = 1.0
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        normalised_id = str(self.condition_id).strip()
        if not normalised_id:
            raise ValueError("PurposeCondition.condition_id must be a non-empty string")
        object.__setattr__(self, "condition_id", normalised_id)

        # Validate label.
        if not self.label or not self.label.strip():
            raise ValueError("PurposeCondition.label must be a non-empty string")

        # Validate description.
        if not self.description or not self.description.strip():
            raise ValueError("PurposeCondition.description must be a non-empty string")

        # Clamp weight to [0, 1].
        object.__setattr__(self, "weight", _clamp(self.weight))

        # Ensure keywords is a tuple of stripped, non-empty strings.
        kw = tuple(k.strip().lower() for k in self.keywords if k.strip())
        object.__setattr__(self, "keywords", kw)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score_text(self, text: str) -> float:
        """Compute a Jaccard-based relevance score for *text* against this condition.

        Tokenises *text* and computes the Jaccard similarity with the
        condition's keywords.  The result is scaled by :attr:`weight`.

        Parameters
        ----------
        text:
            Arbitrary text to score, typically a node name + description.

        Returns
        -------
        float
            Relevance score in [0, 1], scaled by ``self.weight``.
        """
        keyword_set = set(self.keywords)
        text_tokens = _tokenize(text)
        raw = _jaccard(keyword_set, text_tokens)
        return _clamp(raw * self.weight)

    def matches(self, text: str, threshold: float = 0.1) -> bool:
        """Return ``True`` when this condition scores *text* above *threshold*.

        Parameters
        ----------
        text:
            Text to evaluate.
        threshold:
            Minimum score to consider a match.  Default 0.1.

        Returns
        -------
        bool
            ``True`` when ``score_text(text) >= threshold``.
        """
        return self.score_text(text) >= threshold

    def keyword_overlap(self, other: "PurposeCondition") -> float:
        """Return Jaccard similarity between this condition's keywords and another's.

        Parameters
        ----------
        other:
            The other :class:`PurposeCondition` to compare keywords with.

        Returns
        -------
        float
            Jaccard similarity of keyword sets.
        """
        return _jaccard(set(self.keywords), set(other.keywords))

    # ------------------------------------------------------------------
    # Mutation (returns new instance via replace())
    # ------------------------------------------------------------------

    def adjusted(self, *, weight_delta: float = 0.0) -> PurposeCondition:
        """Return a new :class:`PurposeCondition` with adjusted weight.

        Parameters
        ----------
        weight_delta:
            Signed change to apply to :attr:`weight`.  The result is clamped
            to [0, 1].

        Returns
        -------
        PurposeCondition
            New instance with ``weight = clamp(self.weight + weight_delta)``.
        """
        new_weight = _clamp(self.weight + weight_delta)
        return replace(self, weight=new_weight)

    def with_keyword(self, keyword: str) -> PurposeCondition:
        """Return a new :class:`PurposeCondition` with an additional keyword.

        Parameters
        ----------
        keyword:
            New keyword to add.  Stripped and lower-cased.

        Returns
        -------
        PurposeCondition
            New instance with the keyword appended (if not already present).
        """
        kw = keyword.strip().lower()
        if not kw or kw in self.keywords:
            return self
        return replace(self, keywords=self.keywords + (kw,))

    def without_keyword(self, keyword: str) -> PurposeCondition:
        """Return a new :class:`PurposeCondition` with *keyword* removed.

        Parameters
        ----------
        keyword:
            Keyword to remove (case-insensitive).

        Returns
        -------
        PurposeCondition
            New instance without the keyword.  If the keyword was not present,
            returns ``self``.
        """
        kw = keyword.strip().lower()
        new_kws = tuple(k for k in self.keywords if k != kw)
        if new_kws == self.keywords:
            return self
        return replace(self, keywords=new_kws)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this condition to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation.
        """
        return {
            "condition_id": self.condition_id,
            "label": self.label,
            "description": self.description,
            "keywords": list(self.keywords),
            "weight": self.weight,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PurposeCondition:
        """Deserialise a :class:`PurposeCondition` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PurposeCondition
            Reconstructed condition.
        """
        return cls(
            condition_id=data["condition_id"],
            label=data["label"],
            description=data["description"],
            keywords=tuple(data.get("keywords", [])),
            weight=float(data.get("weight", 1.0)),
            created_at=data.get("created_at", _now_iso()),
        )

    def __repr__(self) -> str:
        kw_preview = ", ".join(self.keywords[:5])
        if len(self.keywords) > 5:
            kw_preview += f", ... (+{len(self.keywords) - 5})"
        return (
            f"PurposeCondition(id={self.condition_id!r}, label={self.label!r}, "
            f"weight={self.weight:.2f}, keywords=[{kw_preview}])"
        )


@dataclass(frozen=True, slots=True)
class TheoryNode:
    """An immutable representation of a single node in a theory space.

    A :class:`TheoryNode` encodes one mathematical entity (a definition,
    lemma, construction, or conjecture) together with its purpose alignment
    score, maturity level, and set of outgoing connection IDs.

    Attributes
    ----------
    node_id:
        Unique identifier for this node within a :class:`TheorySpace`.
    name:
        Short human-readable name, e.g. ``"Algebraic Closure Theorem"``.
    description:
        Prose description of what this node represents.
    purpose_alignment:
        Pre-computed alignment with the overarching research purpose, in
        [0, 1].  Clamped on construction.
    maturity:
        :class:`NodeMaturity` level of this node.
    connections:
        Tuple of ``node_id`` strings this node has a directed edge to.
    metadata:
        Tuple of ``(key, value)`` string pairs carrying arbitrary annotations.
    created_at:
        ISO-8601 UTC timestamp of node creation.
    """

    node_id: str
    name: str
    description: str
    purpose_alignment: float
    maturity: NodeMaturity
    connections: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if not self.node_id or not self.node_id.strip():
            raise ValueError("TheoryNode.node_id must be a non-empty string")
        if not self.name or not self.name.strip():
            raise ValueError("TheoryNode.name must be a non-empty string")
        # Clamp purpose_alignment.
        object.__setattr__(self, "purpose_alignment", _clamp(self.purpose_alignment))
        # Ensure connections is a tuple of non-empty strings.
        conns = tuple(c for c in self.connections if isinstance(c, str) and c.strip())
        object.__setattr__(self, "connections", conns)
        # Ensure metadata is a tuple of (str, str) pairs.
        meta = tuple(
            (str(k), str(v))
            for k, v in self.metadata
            if k and str(k).strip()
        )
        object.__setattr__(self, "metadata", meta)

    # ------------------------------------------------------------------
    # Property helpers
    # ------------------------------------------------------------------

    def is_mature(self) -> bool:
        """Return ``True`` when this node's maturity is :attr:`~NodeMaturity.MATURE` or higher.

        Returns
        -------
        bool
            ``True`` for :attr:`~NodeMaturity.MATURE` and
            :attr:`~NodeMaturity.ESTABLISHED`.
        """
        return self.maturity in (NodeMaturity.MATURE, NodeMaturity.ESTABLISHED)

    def connection_count(self) -> int:
        """Return the number of outgoing connections from this node.

        Returns
        -------
        int
            Length of :attr:`connections`.
        """
        return len(self.connections)

    def has_connection(self, other_id: str) -> bool:
        """Return ``True`` when this node has a direct connection to *other_id*.

        Parameters
        ----------
        other_id:
            The target node ID to check.

        Returns
        -------
        bool
            ``True`` when *other_id* is in :attr:`connections`.
        """
        return other_id in self.connections

    # ------------------------------------------------------------------
    # Mutation (returns new instance via replace())
    # ------------------------------------------------------------------

    def with_connection(self, other_id: str) -> TheoryNode:
        """Return a new :class:`TheoryNode` with *other_id* added to connections.

        If *other_id* is already in :attr:`connections`, returns ``self``.

        Parameters
        ----------
        other_id:
            The target node ID to add.

        Returns
        -------
        TheoryNode
            New node instance with the connection added.
        """
        if not other_id or not other_id.strip():
            return self
        if other_id in self.connections:
            return self
        return replace(self, connections=self.connections + (other_id,))

    def without_connection(self, other_id: str) -> TheoryNode:
        """Return a new :class:`TheoryNode` with *other_id* removed from connections.

        Parameters
        ----------
        other_id:
            The target node ID to remove.

        Returns
        -------
        TheoryNode
            New node instance without the connection.  If *other_id* was not
            present, returns ``self``.
        """
        if other_id not in self.connections:
            return self
        new_conns = tuple(c for c in self.connections if c != other_id)
        return replace(self, connections=new_conns)

    def with_metadata(self, key: str, value: str) -> TheoryNode:
        """Return a new :class:`TheoryNode` with a metadata entry set.

        Replaces an existing entry with *key* if one exists.

        Parameters
        ----------
        key:
            Metadata key.
        value:
            Metadata value.

        Returns
        -------
        TheoryNode
            New node with the metadata entry added or updated.
        """
        if not key or not key.strip():
            return self
        existing = {k: v for k, v in self.metadata}
        existing[key.strip()] = value
        new_meta = tuple(existing.items())
        return replace(self, metadata=new_meta)

    def get_metadata(self, key: str) -> str | None:
        """Return the metadata value for *key*, or ``None`` if not present.

        Parameters
        ----------
        key:
            The metadata key to look up.

        Returns
        -------
        str | None
            The stored value, or ``None``.
        """
        for k, v in self.metadata:
            if k == key:
                return v
        return None

    def with_maturity(self, maturity: NodeMaturity) -> TheoryNode:
        """Return a new :class:`TheoryNode` with updated maturity.

        Parameters
        ----------
        maturity:
            The new :class:`NodeMaturity` level.

        Returns
        -------
        TheoryNode
            New node instance.
        """
        return replace(self, maturity=maturity)

    def with_purpose_alignment(self, alignment: float) -> TheoryNode:
        """Return a new :class:`TheoryNode` with updated purpose alignment.

        Parameters
        ----------
        alignment:
            New alignment score; will be clamped to [0, 1].

        Returns
        -------
        TheoryNode
            New node instance.
        """
        return replace(self, purpose_alignment=_clamp(alignment))

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def relevance_score(self, condition: PurposeCondition) -> float:
        """Compute a composite relevance score for this node under *condition*.

        Combines :attr:`purpose_alignment` (60 % weight) with the Jaccard
        similarity of the condition's keywords against this node's name and
        description (40 % weight), scaled by the condition's own weight.

        Parameters
        ----------
        condition:
            The :class:`PurposeCondition` to score this node against.

        Returns
        -------
        float
            Composite relevance score in [0, 1].
        """
        text = f"{self.name} {self.description}"
        text_score = condition.score_text(text)
        composite = 0.6 * self.purpose_alignment + 0.4 * text_score
        return _clamp(composite)

    def maturity_weighted_score(self, condition: PurposeCondition) -> float:
        """Return relevance score further weighted by node maturity.

        Mature / established nodes receive a small bonus; nascent nodes a
        small penalty.  This reflects the intuition that mature theory nodes
        are more reliably useful.

        Parameters
        ----------
        condition:
            The purpose condition to score against.

        Returns
        -------
        float
            Maturity-weighted relevance score in [0, 1].
        """
        base = self.relevance_score(condition)
        maturity_factor = self.maturity.numeric_value()
        # Blend: 80% base relevance, 20% maturity bonus
        blended = 0.8 * base + 0.2 * maturity_factor
        return _clamp(blended)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this node to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation.
        """
        return {
            "node_id": self.node_id,
            "name": self.name,
            "description": self.description,
            "purpose_alignment": self.purpose_alignment,
            "maturity": self.maturity.value,
            "connections": list(self.connections),
            "metadata": {k: v for k, v in self.metadata},
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TheoryNode:
        """Deserialise a :class:`TheoryNode` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        TheoryNode
            Reconstructed node instance.
        """
        raw_meta = data.get("metadata", {})
        if isinstance(raw_meta, dict):
            meta: tuple[tuple[str, str], ...] = tuple(
                (str(k), str(v)) for k, v in raw_meta.items()
            )
        else:
            meta = tuple(
                (str(k), str(v)) for k, v in raw_meta
            )
        return cls(
            node_id=data["node_id"],
            name=data["name"],
            description=data["description"],
            purpose_alignment=float(data.get("purpose_alignment", 0.0)),
            maturity=NodeMaturity(data.get("maturity", "nascent")),
            connections=tuple(data.get("connections", [])),
            metadata=meta,
            created_at=data.get("created_at", _now_iso()),
        )

    def __repr__(self) -> str:
        return (
            f"TheoryNode(id={self.node_id!r}, name={self.name!r}, "
            f"maturity={self.maturity.value}, align={self.purpose_alignment:.2f}, "
            f"connections={self.connection_count()})"
        )


@dataclass(frozen=True, slots=True)
class NavigationPath:
    """An immutable record of a completed navigation path through a theory space.

    A :class:`NavigationPath` captures the result of a pathfinding algorithm:
    the ordered sequence of node IDs visited, together with cost and
    purpose-alignment metrics.

    Attributes
    ----------
    path_id:
        Unique identifier for this path.
    node_ids:
        Tuple of node IDs in traversal order, from start to goal.
    start_id:
        ID of the path's starting node.
    goal_id:
        ID of the path's goal node.
    purpose:
        Short string describing the research purpose that guided this path.
    total_cost:
        Accumulated traversal cost (lower is better).  Non-negative.
    purpose_alignment:
        Mean purpose-alignment of nodes along the path, in [0, 1].
    strategy:
        The :class:`NavigationStrategy` used to find this path.
    created_at:
        ISO-8601 UTC timestamp of path creation.
    """

    path_id: str
    node_ids: tuple[str, ...]
    start_id: str
    goal_id: str
    purpose: str
    total_cost: float
    purpose_alignment: float
    strategy: NavigationStrategy
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if not isinstance(self.node_ids, tuple):
            object.__setattr__(self, "node_ids", tuple(self.node_ids))
        if self.node_ids and (not self.start_id or not self.start_id.strip()):
            raise ValueError("NavigationPath.start_id must be a non-empty string")
        if self.node_ids and (not self.goal_id or not self.goal_id.strip()):
            raise ValueError("NavigationPath.goal_id must be a non-empty string")
        # Clamp scores.
        object.__setattr__(self, "purpose_alignment", _clamp(self.purpose_alignment))
        object.__setattr__(self, "total_cost", max(0.0, self.total_cost))

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """Return ``True`` when the path contains no nodes.

        Returns
        -------
        bool
            Always ``False`` for valid paths (``__post_init__`` ensures non-empty).
        """
        return len(self.node_ids) == 0

    def length(self) -> int:
        """Return the number of nodes in this path.

        Returns
        -------
        int
            ``len(self.node_ids)``.
        """
        return len(self.node_ids)

    def contains(self, node_id: str) -> bool:
        """Return ``True`` when *node_id* appears in this path.

        Parameters
        ----------
        node_id:
            The node ID to test.

        Returns
        -------
        bool
            ``True`` when *node_id* is in :attr:`node_ids`.
        """
        return node_id in self.node_ids

    def is_direct(self) -> bool:
        """Return ``True`` when this path is a single-hop (start → goal).

        Returns
        -------
        bool
            ``True`` when :meth:`length` == 2.
        """
        return self.length() == 2

    def cost_per_step(self) -> float:
        """Return the average cost per traversal step.

        Returns
        -------
        float
            ``total_cost / (length - 1)`` for paths with ≥ 2 nodes,
            or ``total_cost`` for single-node paths.
        """
        steps = max(1, self.length() - 1)
        return self.total_cost / steps

    def quality_score(self) -> float:
        """Compute a composite quality score for this path.

        The quality score combines :attr:`purpose_alignment` (70 % weight)
        with an inverted normalised cost penalty (30 % weight).  Cost is
        penalised on a soft exponential scale: very high costs asymptotically
        reduce the cost contribution to 0.

        Returns
        -------
        float
            Quality score in [0, 1].  Higher is better.
        """
        alignment_part = 0.7 * self.purpose_alignment
        # Cost penalty: use exponential decay so cost in [0, inf) maps to (0, 1].
        # cost_score = exp(-cost_per_step / 10); scale factor 10 is heuristic.
        import math
        cost_score = math.exp(-self.cost_per_step() / 10.0)
        cost_part = 0.3 * cost_score
        return _clamp(alignment_part + cost_part)

    # ------------------------------------------------------------------
    # Mutation (returns new instance via replace())
    # ------------------------------------------------------------------

    def reversed(self) -> NavigationPath:
        """Return a new :class:`NavigationPath` with the node order reversed.

        The returned path swaps :attr:`start_id` and :attr:`goal_id` and
        reverses :attr:`node_ids`.

        Returns
        -------
        NavigationPath
            Reversed path instance.
        """
        return replace(
            self,
            path_id=str(uuid.uuid4()),
            node_ids=tuple(reversed(self.node_ids)),
            start_id=self.goal_id,
            goal_id=self.start_id,
        )

    def sub_path(self, start_idx: int, end_idx: int) -> NavigationPath:
        """Return a sub-path spanning ``node_ids[start_idx:end_idx + 1]``.

        Parameters
        ----------
        start_idx:
            Start index (inclusive) into :attr:`node_ids`.
        end_idx:
            End index (inclusive) into :attr:`node_ids`.

        Returns
        -------
        NavigationPath
            New path covering the requested slice.

        Raises
        ------
        ValueError
            When the slice is empty or indices are out of range.
        """
        sliced = self.node_ids[start_idx:end_idx + 1]
        if not sliced:
            raise ValueError(
                f"sub_path({start_idx}, {end_idx}) produces an empty path; "
                f"node_ids has length {len(self.node_ids)}"
            )
        steps = max(1, len(sliced) - 1)
        fraction = steps / max(1, self.length() - 1)
        return replace(
            self,
            path_id=str(uuid.uuid4()),
            node_ids=sliced,
            start_id=sliced[0],
            goal_id=sliced[-1],
            total_cost=self.total_cost * fraction,
        )

    def extended(self, node_id: str, extra_cost: float = 1.0) -> NavigationPath:
        """Return a new path extended by *node_id*.

        Parameters
        ----------
        node_id:
            The node ID to append.
        extra_cost:
            Additional cost incurred by this step.  Non-negative.

        Returns
        -------
        NavigationPath
            New path with the node appended.
        """
        return replace(
            self,
            path_id=str(uuid.uuid4()),
            node_ids=self.node_ids + (node_id,),
            goal_id=node_id,
            total_cost=self.total_cost + max(0.0, extra_cost),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this path to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation.
        """
        return {
            "path_id": self.path_id,
            "node_ids": list(self.node_ids),
            "start_id": self.start_id,
            "goal_id": self.goal_id,
            "purpose": self.purpose,
            "total_cost": self.total_cost,
            "purpose_alignment": self.purpose_alignment,
            "strategy": self.strategy.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NavigationPath:
        """Deserialise a :class:`NavigationPath` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        NavigationPath
            Reconstructed path instance.
        """
        return cls(
            path_id=data.get("path_id", str(uuid.uuid4())),
            node_ids=tuple(data["node_ids"]),
            start_id=data["start_id"],
            goal_id=data["goal_id"],
            purpose=data.get("purpose", ""),
            total_cost=float(data.get("total_cost", 0.0)),
            purpose_alignment=float(data.get("purpose_alignment", 0.0)),
            strategy=NavigationStrategy(data.get("strategy", "breadth_first")),
            created_at=data.get("created_at", _now_iso()),
        )

    def __repr__(self) -> str:
        return (
            f"NavigationPath(id={self.path_id[:8]!r}, "
            f"{self.start_id!r}→{self.goal_id!r}, "
            f"len={self.length()}, cost={self.total_cost:.2f}, "
            f"align={self.purpose_alignment:.2f})"
        )


@dataclass(frozen=True, slots=True)
class NavigationState:
    """Immutable snapshot of the state of an active navigation session.

    A :class:`NavigationState` is updated (by producing a new instance via
    :meth:`visit`) at each step of a navigation algorithm.  Because it is
    frozen it can be safely stored in priority queues and sets.

    Attributes
    ----------
    state_id:
        Unique ID for this state snapshot.
    current_node_id:
        ID of the node the navigator is currently at.
    goal_node_id:
        ID of the target node.
    purpose:
        Short description of the navigation purpose.
    strategy:
        :class:`NavigationStrategy` in use for this session.
    visited:
        Tuple of node IDs that have been visited (in order).
    beam:
        Tuple of candidate node IDs in the current beam (for beam search).
    cost_so_far:
        Accumulated traversal cost up to this state.
    depth:
        Current search depth (number of steps taken from the start node).
    created_at:
        ISO-8601 UTC timestamp of this state's creation.
    """

    state_id: str
    current_node_id: str
    goal_node_id: str
    purpose: str
    strategy: NavigationStrategy
    visited: tuple[str, ...] = ()
    beam: tuple[str, ...] = ()
    cost_so_far: float = 0.0
    depth: int = 0
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        for attr in ("state_id", "current_node_id", "goal_node_id"):
            val = object.__getattribute__(self, attr)
            if not val or not str(val).strip():
                raise ValueError(f"NavigationState.{attr} must be a non-empty string")
        object.__setattr__(self, "cost_so_far", max(0.0, self.cost_so_far))
        object.__setattr__(self, "depth", max(0, self.depth))
        if not isinstance(self.visited, tuple):
            object.__setattr__(self, "visited", tuple(self.visited))
        if not isinstance(self.beam, tuple):
            object.__setattr__(self, "beam", tuple(self.beam))

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def has_visited(self, node_id: str) -> bool:
        """Return ``True`` when *node_id* has been visited in this session.

        Parameters
        ----------
        node_id:
            The node ID to check.

        Returns
        -------
        bool
            ``True`` when *node_id* is in :attr:`visited`.
        """
        return node_id in self.visited

    def is_at_goal(self) -> bool:
        """Return ``True`` when the navigator has reached the goal node.

        Returns
        -------
        bool
            ``True`` when :attr:`current_node_id` == :attr:`goal_node_id`.
        """
        return self.current_node_id == self.goal_node_id

    def depth_exceeded(self, max_depth: int) -> bool:
        """Return ``True`` when the current depth exceeds *max_depth*.

        Parameters
        ----------
        max_depth:
            The maximum allowed depth.

        Returns
        -------
        bool
            ``True`` when ``self.depth > max_depth``.
        """
        return self.depth > max_depth

    def step_count(self) -> int:
        """Return the number of steps taken so far.

        Returns
        -------
        int
            Same as :attr:`depth`.
        """
        return self.depth

    # ------------------------------------------------------------------
    # Mutation (returns new instance via replace())
    # ------------------------------------------------------------------

    def visit(self, node_id: str, cost: float = 0.0) -> NavigationState:
        """Return a new :class:`NavigationState` representing a move to *node_id*.

        Parameters
        ----------
        node_id:
            The node to move to.
        cost:
            Cost incurred by this step.  Non-negative; clamped if negative.

        Returns
        -------
        NavigationState
            New state with :attr:`current_node_id` set to *node_id*,
            *node_id* appended to :attr:`visited`, :attr:`cost_so_far`
            incremented, and :attr:`depth` incremented.
        """
        new_visited = self.visited + (node_id,)
        return replace(
            self,
            state_id=str(uuid.uuid4()),
            current_node_id=node_id,
            visited=new_visited,
            cost_so_far=self.cost_so_far + max(0.0, cost),
            depth=self.depth + 1,
        )

    def with_beam(self, beam: tuple[str, ...]) -> NavigationState:
        """Return a new state with the beam updated.

        Parameters
        ----------
        beam:
            New beam of candidate node IDs.

        Returns
        -------
        NavigationState
            New state with updated :attr:`beam`.
        """
        return replace(self, state_id=str(uuid.uuid4()), beam=tuple(beam))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this state to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation.
        """
        return {
            "state_id": self.state_id,
            "current_node_id": self.current_node_id,
            "goal_node_id": self.goal_node_id,
            "purpose": self.purpose,
            "strategy": self.strategy.value,
            "visited": list(self.visited),
            "beam": list(self.beam),
            "cost_so_far": self.cost_so_far,
            "depth": self.depth,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NavigationState:
        """Deserialise a :class:`NavigationState` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        NavigationState
            Reconstructed state.
        """
        return cls(
            state_id=data.get("state_id", str(uuid.uuid4())),
            current_node_id=data["current_node_id"],
            goal_node_id=data["goal_node_id"],
            purpose=data.get("purpose", ""),
            strategy=NavigationStrategy(data.get("strategy", "breadth_first")),
            visited=tuple(data.get("visited", [])),
            beam=tuple(data.get("beam", [])),
            cost_so_far=float(data.get("cost_so_far", 0.0)),
            depth=int(data.get("depth", 0)),
            created_at=data.get("created_at", _now_iso()),
        )

    def __repr__(self) -> str:
        return (
            f"NavigationState(at={self.current_node_id!r}, "
            f"goal={self.goal_node_id!r}, depth={self.depth}, "
            f"cost={self.cost_so_far:.2f}, "
            f"visited={len(self.visited)})"
        )


# ---------------------------------------------------------------------------
# Mutable service class: TheorySpace
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheorySpace:
    """A mutable directed graph of :class:`TheoryNode` objects.

    :class:`TheorySpace` is the core data structure of the theory-navigation
    package.  It stores nodes in a dictionary keyed by ``node_id`` and
    edges in an adjacency dict (``from_id → set[to_id]``).

    The space supports standard graph operations (add/remove nodes and edges,
    neighbour lookup, connectivity testing), as well as purpose-conditioned
    queries (e.g. finding nodes that match a :class:`PurposeCondition`).

    Attributes
    ----------
    nodes:
        Dictionary mapping ``node_id`` → :class:`TheoryNode`.
    edges:
        Adjacency mapping ``from_id`` → ``set[to_id]``.
    space_id:
        UUID-4 uniquely identifying this space instance.
    created_at:
        ISO-8601 UTC timestamp of space creation.
    """

    nodes: dict[str, TheoryNode] = field(default_factory=dict)
    edges: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    space_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Node CRUD
    # ------------------------------------------------------------------

    def add_node(self, node: TheoryNode) -> None:
        """Add *node* to the space.

        If a node with the same ``node_id`` already exists it is replaced.

        Parameters
        ----------
        node:
            The :class:`TheoryNode` to add.
        """
        self.nodes[node.node_id] = node

    def remove_node(self, node_id: str) -> bool:
        """Remove the node with *node_id* and all its incident edges.

        Parameters
        ----------
        node_id:
            ID of the node to remove.

        Returns
        -------
        bool
            ``True`` when the node was present and removed; ``False``
            otherwise.
        """
        if node_id not in self.nodes:
            return False
        del self.nodes[node_id]
        # Remove outgoing edges from this node.
        if node_id in self.edges:
            del self.edges[node_id]
        # Remove incoming edges referencing this node.
        for src in list(self.edges):
            self.edges[src].discard(node_id)
        return True

    def get_node(self, node_id: str) -> TheoryNode | None:
        """Return the node with *node_id*, or ``None`` if not found.

        Parameters
        ----------
        node_id:
            The node ID to look up.

        Returns
        -------
        TheoryNode | None
            The matching node, or ``None``.
        """
        return self.nodes.get(node_id)

    def has_node(self, node_id: str) -> bool:
        """Return ``True`` when this space contains a node with *node_id*.

        Parameters
        ----------
        node_id:
            The node ID to test.

        Returns
        -------
        bool
            ``True`` when *node_id* is in :attr:`nodes`.
        """
        return node_id in self.nodes

    def node_count(self) -> int:
        """Return the number of nodes in this space.

        Returns
        -------
        int
            ``len(self.nodes)``.
        """
        return len(self.nodes)

    def iter_nodes(self) -> Iterator[TheoryNode]:
        """Iterate over all nodes in this space.

        Returns
        -------
        Iterator[TheoryNode]
            Iterator over all :class:`TheoryNode` values.
        """
        return iter(self.nodes.values())

    # ------------------------------------------------------------------
    # Edge CRUD
    # ------------------------------------------------------------------

    def add_edge(
        self, from_id: str, to_id: str, bidirectional: bool = True
    ) -> None:
        """Add a directed edge from *from_id* to *to_id*.

        If *bidirectional* is ``True``, also adds the reverse edge.
        Silently does nothing when either node is not in the space.

        Parameters
        ----------
        from_id:
            Source node ID.
        to_id:
            Target node ID.
        bidirectional:
            When ``True``, adds both ``from→to`` and ``to→from``.
        """
        if from_id not in self.nodes or to_id not in self.nodes:
            return
        if from_id == to_id:
            return
        self.edges[from_id].add(to_id)
        if bidirectional:
            self.edges[to_id].add(from_id)

    def remove_edge(
        self, from_id: str, to_id: str, bidirectional: bool = True
    ) -> bool:
        """Remove the directed edge from *from_id* to *to_id*.

        Parameters
        ----------
        from_id:
            Source node ID.
        to_id:
            Target node ID.
        bidirectional:
            When ``True``, also removes the reverse edge.

        Returns
        -------
        bool
            ``True`` when the ``from→to`` edge was present and removed.
        """
        removed = to_id in self.edges.get(from_id, set())
        if from_id in self.edges:
            self.edges[from_id].discard(to_id)
        if bidirectional and to_id in self.edges:
            self.edges[to_id].discard(from_id)
        return removed

    def get_neighbors(self, node_id: str) -> list[TheoryNode]:
        """Return the list of :class:`TheoryNode` objects adjacent to *node_id*.

        Parameters
        ----------
        node_id:
            Source node ID.

        Returns
        -------
        list[TheoryNode]
            All nodes reachable via a single directed edge from *node_id*,
            sorted by ``node_id`` for deterministic ordering.
        """
        neighbor_ids = self.edges.get(node_id, set())
        result = [
            self.nodes[nid]
            for nid in sorted(neighbor_ids)
            if nid in self.nodes
        ]
        return result

    def edge_count(self) -> int:
        """Return the total number of logical edges in this space.

        Returns
        -------
        int
            Count of unique source/target pairs, treating bidirectional links as
            a single edge.
        """
        unique_edges: set[frozenset[str]] = set()
        for src, targets in self.edges.items():
            for dst in targets:
                unique_edges.add(frozenset((src, dst)))
        return len(unique_edges)

    def out_degree(self, node_id: str) -> int:
        """Return the number of outgoing edges from *node_id*.

        Parameters
        ----------
        node_id:
            The node to query.

        Returns
        -------
        int
            Out-degree; 0 if the node has no outgoing edges.
        """
        return len(self.edges.get(node_id, set()))

    def in_degree(self, node_id: str) -> int:
        """Return the number of incoming edges to *node_id*.

        Parameters
        ----------
        node_id:
            The node to query.

        Returns
        -------
        int
            In-degree; 0 if no edges point to this node.
        """
        count = 0
        for src, targets in self.edges.items():
            if node_id in targets:
                count += 1
        return count

    # ------------------------------------------------------------------
    # Graph algorithms
    # ------------------------------------------------------------------

    def is_connected(self, from_id: str, to_id: str) -> bool:
        """Return ``True`` when *to_id* is reachable from *from_id* via BFS.

        Parameters
        ----------
        from_id:
            Starting node ID.
        to_id:
            Target node ID.

        Returns
        -------
        bool
            ``True`` when there is a directed path from *from_id* to *to_id*.
            Returns ``True`` trivially when ``from_id == to_id``.
        """
        if from_id == to_id:
            return True
        if from_id not in self.nodes or to_id not in self.nodes:
            return False

        visited: set[str] = set()
        queue: deque[str] = deque([from_id])
        visited.add(from_id)

        while queue:
            current = queue.popleft()
            if current == to_id:
                return True
            for neighbor_id in self.edges.get(current, set()):
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append(neighbor_id)

        return False

    def shortest_path(
        self, from_id: str, to_id: str
    ) -> list[str] | None:
        """Find the shortest directed path from *from_id* to *to_id* using BFS.

        Parameters
        ----------
        from_id:
            Starting node ID.
        to_id:
            Target node ID.

        Returns
        -------
        list[str] | None
            Ordered list of node IDs from *from_id* to *to_id* (inclusive),
            or ``None`` when no path exists.
        """
        if from_id not in self.nodes or to_id not in self.nodes:
            return None
        if from_id == to_id:
            return [from_id]

        visited: set[str] = {from_id}
        # parent dict maps node_id → predecessor
        parent: dict[str, str | None] = {from_id: None}
        queue: deque[str] = deque([from_id])

        while queue:
            current = queue.popleft()
            if current == to_id:
                # Reconstruct path.
                path: list[str] = []
                node: str | None = to_id
                while node is not None:
                    path.append(node)
                    node = parent[node]
                path.reverse()
                return path
            for neighbor_id in sorted(self.edges.get(current, set())):
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    parent[neighbor_id] = current
                    queue.append(neighbor_id)

        return None

    def reachable_from(self, node_id: str) -> set[str]:
        """Return all node IDs reachable from *node_id* (including itself).

        Parameters
        ----------
        node_id:
            Starting node ID.

        Returns
        -------
        set[str]
            Set of all reachable node IDs.
        """
        if node_id not in self.nodes:
            return set()

        visited: set[str] = set()
        queue: deque[str] = deque([node_id])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for neighbor_id in self.edges.get(current, set()):
                if neighbor_id not in visited:
                    queue.append(neighbor_id)

        return visited

    # ------------------------------------------------------------------
    # Purpose-conditioned queries
    # ------------------------------------------------------------------

    def nodes_by_maturity(
        self, maturity: NodeMaturity | None = None
    ) -> list[TheoryNode] | dict[NodeMaturity, list[TheoryNode]]:
        """Return nodes grouped by maturity, or filtered when *maturity* is given.

        Parameters
        ----------
        maturity:
            Optional :class:`NodeMaturity` level to filter by.

        Returns
        -------
        list[TheoryNode] | dict[NodeMaturity, list[TheoryNode]]
            Matching nodes in ascending ``node_id`` order, or a grouped mapping
            for all maturity levels when *maturity* is omitted.
        """
        if maturity is None:
            grouped: dict[NodeMaturity, list[TheoryNode]] = {
                level: [] for level in NodeMaturity
            }
            for node in sorted(self.nodes.values(), key=lambda n: n.node_id):
                grouped[node.maturity].append(node)
            return grouped
        return sorted(
            (n for n in self.nodes.values() if n.maturity == maturity),
            key=lambda n: n.node_id,
        )

    def nodes_by_purpose_alignment(
        self,
        condition: PurposeCondition,
        threshold: float = 0.3,
    ) -> list[TheoryNode]:
        """Return nodes that score above *threshold* under *condition*.

        Nodes are scored using :meth:`TheoryNode.relevance_score` and
        returned sorted by descending score.

        Parameters
        ----------
        condition:
            The :class:`PurposeCondition` to score nodes against.
        threshold:
            Minimum relevance score, in [0, 1].  Default 0.3.

        Returns
        -------
        list[TheoryNode]
            Nodes with ``relevance_score(condition) >= threshold``, sorted by
            descending relevance.
        """
        scored = [
            (n.relevance_score(condition), n)
            for n in self.nodes.values()
            if n.relevance_score(condition) >= _clamp(threshold)
        ]
        scored.sort(key=lambda t: (-t[0], t[1].node_id))
        return [n for _, n in scored]

    def most_connected_nodes(self, limit: int = 10) -> list[TheoryNode]:
        """Return the *limit* nodes with the highest out-degree.

        Parameters
        ----------
        limit:
            Maximum number of nodes to return.  Default 10.

        Returns
        -------
        list[TheoryNode]
            Top-*limit* nodes by out-degree, sorted descending.
        """
        all_nodes = list(self.nodes.values())
        all_nodes.sort(
            key=lambda n: (-self.out_degree(n.node_id), n.node_id)
        )
        return all_nodes[:max(1, limit)]

    def nodes_matching_text(self, query: str, limit: int = 20) -> list[TheoryNode]:
        """Return nodes whose name or description best matches *query* by Jaccard.

        Parameters
        ----------
        query:
            Free-text search query.
        limit:
            Maximum number of results to return.

        Returns
        -------
        list[TheoryNode]
            Top matching nodes in descending relevance order.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return list(self.nodes.values())[:limit]

        scored: list[tuple[float, TheoryNode]] = []
        for n in self.nodes.values():
            text = f"{n.name} {n.description}"
            score = _jaccard(query_tokens, _tokenize(text))
            if score > 0.0:
                scored.append((score, n))

        scored.sort(key=lambda t: (-t[0], t[1].node_id))
        return [n for _, n in scored[:limit]]

    def mature_nodes_sorted_by_alignment(self) -> list[TheoryNode]:
        """Return all mature/established nodes sorted by descending purpose alignment.

        Returns
        -------
        list[TheoryNode]
            All nodes with maturity ≥ :attr:`~NodeMaturity.MATURE`, sorted by
            :attr:`~TheoryNode.purpose_alignment` descending.
        """
        mature = [
            n
            for n in self.nodes.values()
            if n.maturity in (NodeMaturity.MATURE, NodeMaturity.ESTABLISHED)
        ]
        mature.sort(key=lambda n: (-n.purpose_alignment, n.node_id))
        return mature

    def isolated_nodes(self) -> list[TheoryNode]:
        """Return nodes with no outgoing and no incoming edges.

        Returns
        -------
        list[TheoryNode]
            Isolated (degree-0) nodes sorted by ``node_id``.
        """
        result = []
        all_target_ids: set[str] = set()
        for targets in self.edges.values():
            all_target_ids |= targets

        for n in self.nodes.values():
            has_out = bool(self.edges.get(n.node_id))
            has_in = n.node_id in all_target_ids
            if not has_out and not has_in:
                result.append(n)

        return sorted(result, key=lambda n: n.node_id)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a multi-line human-readable summary of this theory space.

        Returns
        -------
        str
            Summary including node count, edge count, maturity distribution,
            and a listing of the most-connected nodes.
        """
        sep = "=" * 72
        thin = "-" * 72
        lines: list[str] = [
            sep,
            f"  TheorySpace [{self.space_id[:8]}]",
            f"  Created: {self.created_at}",
            thin,
            f"  Nodes : {self.node_count()}",
            f"  Edges : {self.edge_count()} directed",
            thin,
            "  Maturity distribution:",
        ]

        maturity_counts: dict[NodeMaturity, int] = {m: 0 for m in NodeMaturity}
        for n in self.nodes.values():
            maturity_counts[n.maturity] += 1

        for m in NodeMaturity:
            count = maturity_counts[m]
            bar = "█" * min(count, 40)
            lines.append(f"    {m.value:12s} {bar} ({count})")

        lines.append(thin)
        top_nodes = self.most_connected_nodes(limit=5)
        lines.append("  Most connected nodes (top 5):")
        for n in top_nodes:
            deg = self.out_degree(n.node_id)
            lines.append(
                f"    [{deg:3d} edges] {n.node_id:30s}  {n.maturity.value}"
            )

        isolated = self.isolated_nodes()
        lines.append(thin)
        lines.append(f"  Isolated nodes: {len(isolated)}")

        lines.append(sep)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theory space to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-compatible representation with all nodes and edges.
        """
        edges_serializable = {
            from_id: sorted(targets)
            for from_id, targets in self.edges.items()
        }
        return {
            "space_id": self.space_id,
            "created_at": self.created_at,
            "nodes": [n.to_dict() for n in sorted(self.nodes.values(), key=lambda n: n.node_id)],
            "edges": edges_serializable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TheorySpace:
        """Reconstruct a :class:`TheorySpace` from a plain dictionary.

        Parameters
        ----------
        data:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        TheorySpace
            Reconstructed space with all nodes and edges loaded.
        """
        space = cls(
            space_id=data.get("space_id", str(uuid.uuid4())),
            created_at=data.get("created_at", _now_iso()),
        )
        for ndata in data.get("nodes", []):
            space.add_node(TheoryNode.from_dict(ndata))
        raw_edges: dict[str, list[str]] = data.get("edges", {})
        for from_id, targets in raw_edges.items():
            for to_id in targets:
                if from_id in space.nodes and to_id in space.nodes:
                    space.edges[from_id].add(to_id)
        return space

    def __repr__(self) -> str:
        return (
            f"TheorySpace(id={self.space_id[:8]!r}, "
            f"nodes={self.node_count()}, edges={self.edge_count()})"
        )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "NodeMaturity",
    "NavigationStrategy",
    "PurposeCondition",
    "TheoryNode",
    "NavigationPath",
    "NavigationState",
    "TheorySpace",
]
