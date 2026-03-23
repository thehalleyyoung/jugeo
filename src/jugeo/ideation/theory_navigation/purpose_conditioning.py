"""
Purpose-conditioned navigation — aligning theory traversal with research intent.

In the JuGeo ideation layer, theory navigation is never purely topological.
Every search through the theory space is conditioned by a *research purpose*:
a structured description of what the researcher is trying to achieve,
represented as a ``PurposeCondition``.  This module provides the mathematical
and algorithmic machinery for applying that conditioning:

  1. Purposes are turned into ``PurposeVector`` objects — normalised vectors
     in a keyword-weighted space — that support dot products, cosine
     similarity, and text-projection operations.

  2. A ``PurposeWeightMap`` accumulates per-node purpose alignment scores
     across an entire ``TheorySpace``.  Scores can be decayed, merged, and
     filtered so that the navigator focuses on the most relevant region of
     the space.

  3. The ``PurposeConditioner`` is the primary transformation engine: given
     a ``PurposeCondition`` it computes node weights and can return a
     conditioned view of a ``TheorySpace`` that deprioritises low-alignment
     nodes.

  4. The ``HeuristicComputer`` bridges purpose conditioning and graph search:
     it translates purpose alignment into A*-style heuristics and edge costs
     so that purpose-guided search can be implemented as a standard
     best-first search over cost values.

  5. The ``PurposeAligner`` post-processes completed ``NavigationPath``
     objects, scoring their coherence, identifying misaligned nodes, and
     suggesting replacements.

  6. The ``PurposeDriftDetector`` monitors an ongoing navigation session and
     raises a flag when the path has strayed too far from its declared
     purpose.

Module layout::

    PurposeVector           – mathematical representation of purpose as a keyword-weighted vector
    PurposeWeightMap        – accumulates and manages per-node purpose alignment scores
    PurposeConditioner      – applies purpose conditioning to nodes and spaces
    HeuristicComputer       – translates purpose alignment into A*-style search costs
    PurposeAligner          – aligns and scores completed navigation paths
    PurposeDriftDetector    – detects drift from declared research purpose during navigation

References: theory2.tex §4 (Purpose-Conditioned Traversal).
"""
from __future__ import annotations

import logging
import math
import re
import statistics
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from jugeo.ideation.theory_navigation.models import (
    TheoryNode,
    TheorySpace,
    NavigationPath,
    NavigationState,
    PurposeCondition,
    NodeMaturity,
    NavigationStrategy,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _tokenize(text: str) -> list[str]:
    """Return lowercase word tokens from text."""
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division, returning default when denominator is zero."""
    if denominator == 0.0:
        return default
    return numerator / denominator


def _normalize_vector(components: tuple[float, ...]) -> tuple[float, ...]:
    """Return unit-length version of the given vector. Returns zeros if magnitude is 0."""
    mag = math.sqrt(sum(x * x for x in components))
    if mag == 0.0:
        return components
    return tuple(x / mag for x in components)


def _maturity_factor(maturity: NodeMaturity) -> float:
    """Return a float cost factor for maturity. Lower cost = more mature."""
    from jugeo.ideation.theory_navigation.models import NodeMaturity as NM
    mapping = {
        NM.NASCENT: 0.9,
        NM.DEVELOPING: 0.6,
        NM.MATURE: 0.3,
        NM.ESTABLISHED: 0.1,
    }
    return mapping.get(maturity, 0.5)


# ---------------------------------------------------------------------------
# PurposeVector
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PurposeVector:
    """Mathematical representation of a research purpose as a keyword-weighted vector.

    The vector lives in a keyword-indexed space where each dimension corresponds
    to a keyword drawn from the ``PurposeCondition``.  After construction, the
    component tuple is normalised to unit length so that cosine similarity
    calculations are numerically stable.

    Parameters
    ----------
    vector_id : str
        Unique identifier for this vector instance.
    label : str
        Human-readable label describing this purpose vector.
    components : tuple[float, ...]
        Raw component values (will be normalised in ``__post_init__``).
    keywords : tuple[str, ...]
        Keyword labels corresponding to each component dimension.
        Either must have the same length as *components* or be empty.
    weight : float
        Scalar weight applied during dot products and similarity calculations.
        Clamped to ``[0.0, 2.0]``.
    """

    vector_id: str
    label: str
    components: tuple[float, ...]
    keywords: tuple[str, ...]
    weight: float = 1.0

    def __post_init__(self) -> None:
        if len(self.keywords) != 0 and len(self.keywords) != len(self.components):
            raise ValueError(
                "components and keywords must have same length or keywords must be empty"
            )
        object.__setattr__(self, "components", _normalize_vector(self.components))
        object.__setattr__(self, "weight", _clamp(self.weight, 0.0, 2.0))

    # ------------------------------------------------------------------
    # Geometric operations
    # ------------------------------------------------------------------

    def magnitude(self) -> float:
        """Return the Euclidean magnitude of this vector.

        Returns
        -------
        float
            Square-root of sum of squared components.  After normalisation
            this will be approximately 1.0 unless all components are zero.
        """
        return math.sqrt(sum(x * x for x in self.components))

    def dot(self, other: PurposeVector) -> float:
        """Compute the weighted dot product with another ``PurposeVector``.

        Parameters
        ----------
        other : PurposeVector
            The vector to dot-product with.

        Returns
        -------
        float
            Weighted dot product: ``raw_dot * self.weight * other.weight``.
            If the two vectors have different lengths, only the minimum-length
            prefix is used.
        """
        min_len = min(len(self.components), len(other.components))
        raw_dot = sum(
            self.components[i] * other.components[i] for i in range(min_len)
        )
        return raw_dot * self.weight * other.weight

    def cosine_similarity(self, other: PurposeVector) -> float:
        """Compute cosine similarity with another ``PurposeVector``.

        Parameters
        ----------
        other : PurposeVector
            Vector to compare against.

        Returns
        -------
        float
            Value in ``[-1.0, 1.0]``.  A value of 1.0 indicates identical
            direction; -1.0 indicates opposite directions.
        """
        # Use unweighted dot for geometric cosine, then scale by weights separately
        min_len = min(len(self.components), len(other.components))
        raw_dot = sum(self.components[i] * other.components[i] for i in range(min_len))
        denom = self.magnitude() * other.magnitude() + 1e-9
        raw_cosine = raw_dot / denom
        # Apply weight scaling after the geometric cosine
        weighted = raw_cosine * self.weight * other.weight
        return _clamp(weighted, -1.0, 1.0)

    def project_text(self, text: str) -> float:
        """Compute how much *text* aligns with this purpose vector.

        The projection is based on keyword matching: each keyword that appears
        in the tokenised text contributes its corresponding component magnitude
        (scaled by the vector's weight) to the total score.

        Parameters
        ----------
        text : str
            Free-form text to project (typically node name + description).

        Returns
        -------
        float
            Normalised alignment score in ``[0.0, 1.0]``.  Returns ``0.0``
            when no keywords are defined.
        """
        if not self.keywords:
            return 0.0
        text_tokens = set(_tokenize(text))
        score = 0.0
        total_possible = 0.0
        for keyword, component in zip(self.keywords, self.components):
            abs_comp = abs(component)
            total_possible += abs_comp * self.weight
            # Check if keyword (possibly multi-word) appears in the token set
            kw_tokens = _tokenize(keyword)
            if all(tok in text_tokens for tok in kw_tokens if tok):
                score += abs_comp * self.weight
        return _safe_div(score, total_possible + 1e-9, default=0.0)

    def combine(self, other: PurposeVector, alpha: float = 0.5) -> PurposeVector:
        """Create a weighted linear combination of this vector and *other*.

        Parameters
        ----------
        other : PurposeVector
            Second vector in the combination.
        alpha : float
            Weight of *self* in the blend (``1 - alpha`` is applied to *other*).
            Should be in ``[0.0, 1.0]``; values outside this range are allowed
            but may produce unusual results.

        Returns
        -------
        PurposeVector
            A new normalised ``PurposeVector`` whose components are the
            element-wise weighted average of the two input vectors.
        """
        alpha = _clamp(alpha, 0.0, 1.0)
        a_comps = self.components
        b_comps = other.components
        max_len = max(len(a_comps), len(b_comps))
        # Pad shorter vector with zeros
        a_padded = a_comps + (0.0,) * (max_len - len(a_comps))
        b_padded = b_comps + (0.0,) * (max_len - len(b_comps))
        new_components = tuple(
            alpha * a + (1.0 - alpha) * b for a, b in zip(a_padded, b_padded)
        )
        # Keywords: only carry over if same length, otherwise empty
        if len(self.keywords) == len(other.keywords) == max_len:
            new_keywords = self.keywords
        elif len(self.keywords) == max_len:
            new_keywords = self.keywords
        else:
            new_keywords = ()
        new_label = f"{self.label}+{other.label}"
        new_weight = alpha * self.weight + (1.0 - alpha) * other.weight
        return PurposeVector(
            vector_id=str(uuid.uuid4()),
            label=new_label,
            components=new_components,
            keywords=new_keywords,
            weight=new_weight,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict
            All fields represented with JSON-compatible types (tuples
            converted to lists).
        """
        return {
            "vector_id": self.vector_id,
            "label": self.label,
            "components": list(self.components),
            "keywords": list(self.keywords),
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PurposeVector:
        """Reconstruct a ``PurposeVector`` from a serialised dictionary.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PurposeVector
            Reconstructed instance (components will be re-normalised).
        """
        return cls(
            vector_id=data["vector_id"],
            label=data["label"],
            components=tuple(data["components"]),
            keywords=tuple(data.get("keywords", ())),
            weight=float(data.get("weight", 1.0)),
        )

    @classmethod
    def from_condition(cls, condition: PurposeCondition) -> PurposeVector:
        """Build a ``PurposeVector`` directly from a ``PurposeCondition``.

        Each keyword in the condition receives equal weight derived from
        ``condition.weight / len(keywords)``.  If no keywords are present
        a single-component vector is created instead.

        Parameters
        ----------
        condition : PurposeCondition
            The source condition carrying keywords and weight.

        Returns
        -------
        PurposeVector
            A new ``PurposeVector`` ready for projection and similarity work.
        """
        kws = tuple(condition.keywords)
        if kws:
            per_kw = condition.weight / max(1, len(kws))
            comps = tuple(per_kw for _ in kws)
        else:
            comps = (condition.weight,)
            kws = ()
        return cls(
            vector_id=str(uuid.uuid4()),
            label=condition.label,
            components=comps,
            keywords=kws,
            weight=condition.weight,
        )


# ---------------------------------------------------------------------------
# PurposeWeightMap
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PurposeWeightMap:
    """Accumulates and manages per-node purpose alignment scores across a ``TheorySpace``.

    Scores are clamped to ``[0.0, 1.0]`` at write-time and can be decayed,
    merged, filtered, and normalised to keep the map focused on the most
    relevant region of the theory space.
    """

    weights: dict[str, float] = field(default_factory=dict)
    purpose_label: str = ""
    created_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def set_weight(self, node_id: str, weight: float) -> None:
        """Set the alignment weight for a single node.

        Parameters
        ----------
        node_id : str
            Identifier of the theory node.
        weight : float
            Alignment score (clamped to ``[0.0, 1.0]`` on write).
        """
        self.weights[node_id] = _clamp(weight, 0.0, 1.0)

    def get_weight(self, node_id: str, default: float = 0.5) -> float:
        """Retrieve the alignment weight for a node, using *default* if absent.

        Parameters
        ----------
        node_id : str
            Identifier of the theory node.
        default : float
            Fallback value (clamped to ``[0.0, 1.0]``).

        Returns
        -------
        float
            Alignment score in ``[0.0, 1.0]``.
        """
        return self.weights.get(node_id, _clamp(default, 0.0, 1.0))

    def get_top_n(self, n: int = 10) -> list[tuple[str, float]]:
        """Return the *n* highest-weight node entries.

        Parameters
        ----------
        n : int
            Number of entries to return.

        Returns
        -------
        list[tuple[str, float]]
            List of ``(node_id, weight)`` pairs sorted by weight descending.
        """
        sorted_items = sorted(self.weights.items(), key=lambda kv: kv[1], reverse=True)
        return sorted_items[:n]

    def normalize(self) -> None:
        """Rescale all weights so the maximum value is 1.0.

        If the current maximum is 0.0 (all weights are zero) the method
        returns immediately without modification.
        """
        if not self.weights:
            return
        max_val = max(self.weights.values())
        if max_val == 0.0:
            return
        self.weights = {k: v / max_val for k, v in self.weights.items()}

    def filter_by_threshold(self, threshold: float) -> dict[str, float]:
        """Return a filtered view containing only entries at or above *threshold*.

        Parameters
        ----------
        threshold : float
            Minimum weight to include.

        Returns
        -------
        dict[str, float]
            New dictionary; *self* is not modified.
        """
        return {k: v for k, v in self.weights.items() if v >= threshold}

    def apply_decay(self, decay_factor: float = 0.9) -> None:
        """Multiply all weights by *decay_factor*.

        This simulates temporal decay of relevance — nodes not revisited by
        the conditioner will gradually lose salience.

        Parameters
        ----------
        decay_factor : float
            Multiplier in ``[0.0, 1.0]``.  Values outside this range are
            clamped before application.
        """
        decay_factor = _clamp(decay_factor, 0.0, 1.0)
        self.weights = {
            k: _clamp(v * decay_factor, 0.0, 1.0) for k, v in self.weights.items()
        }

    def merge(self, other: PurposeWeightMap, alpha: float = 0.5) -> PurposeWeightMap:
        """Create a new ``PurposeWeightMap`` blending *self* and *other*.

        Parameters
        ----------
        other : PurposeWeightMap
            Second map to blend with.
        alpha : float
            Weight of *self* in the blend; ``1 - alpha`` applied to *other*.

        Returns
        -------
        PurposeWeightMap
            New map with merged weights and a combined label.
        """
        alpha = _clamp(alpha, 0.0, 1.0)
        all_ids = set(self.weights.keys()) | set(other.weights.keys())
        merged_weights: dict[str, float] = {}
        for node_id in all_ids:
            a_val = self.get_weight(node_id, 0.0)
            b_val = other.get_weight(node_id, 0.0)
            merged_weights[node_id] = _clamp(
                alpha * a_val + (1.0 - alpha) * b_val, 0.0, 1.0
            )
        if self.purpose_label and other.purpose_label:
            new_label = f"{self.purpose_label}+{other.purpose_label}"
        elif self.purpose_label:
            new_label = self.purpose_label
        else:
            new_label = other.purpose_label
        result = PurposeWeightMap(purpose_label=new_label)
        result.weights = merged_weights
        return result

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict
            Contains ``weights``, ``purpose_label``, and ``created_at``.
        """
        return {
            "weights": dict(self.weights),
            "purpose_label": self.purpose_label,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PurposeWeightMap:
        """Reconstruct a ``PurposeWeightMap`` from a serialised dictionary.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        PurposeWeightMap
            Reconstructed instance.
        """
        instance = cls(purpose_label=data.get("purpose_label", ""))
        instance.weights = {k: float(v) for k, v in data.get("weights", {}).items()}
        if "created_at" in data:
            instance.created_at = data["created_at"]
        return instance

    def summary(self) -> str:
        """Return a human-readable multi-line summary of this weight map.

        Returns
        -------
        str
            Multi-line description including average weight, top-3 nodes,
            and count of nodes above the 0.7 threshold.
        """
        count = len(self.weights)
        if count == 0:
            avg = 0.0
        else:
            avg = sum(self.weights.values()) / count
        top3 = self.get_top_n(3)
        above_07 = sum(1 for v in self.weights.values() if v >= 0.7)
        lines = [
            f"PurposeWeightMap(label={self.purpose_label}, nodes={count})",
            f"  avg_weight: {avg:.3f}",
            f"  top_3: {top3}",
            f"  threshold_0.7: {above_07} nodes above 0.7",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PurposeConditioner
# ---------------------------------------------------------------------------

class PurposeConditioner:
    """Primary transformation engine that applies a ``PurposeCondition`` to a ``TheorySpace``.

    Given a ``PurposeCondition``, the conditioner can:

    * Compute per-node alignment weights blending textual projection and
      stored purpose-alignment scores.
    * Build a ``PurposeWeightMap`` for a whole space.
    * Score edge traversal costs that favour purpose-aligned destinations.
    * Rank a node's neighbours by alignment.
    * Produce a human-readable conditioning report.
    """

    def __init__(self, condition: PurposeCondition | None = None) -> None:
        self._condition: PurposeCondition | None = condition
        self._vector: PurposeVector | None = None
        if condition is not None:
            self._vector = PurposeVector.from_condition(condition)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_condition(self, condition: PurposeCondition) -> None:
        """Replace the active ``PurposeCondition`` and rebuild the vector.

        Parameters
        ----------
        condition : PurposeCondition
            New research purpose to use for all subsequent conditioning.
        """
        self._condition = condition
        self._vector = PurposeVector.from_condition(condition)
        logger.debug("PurposeConditioner: condition updated to '%s'", condition.label)

    # ------------------------------------------------------------------
    # Per-node scoring
    # ------------------------------------------------------------------

    def compute_node_weight(self, node: TheoryNode) -> float:
        """Compute the purpose-conditioned alignment weight for a single node.

        The algorithm blends text-projection alignment (60 %) with the node's
        stored ``purpose_alignment`` score (40 %), then applies a maturity
        bonus/penalty.

        Parameters
        ----------
        node : TheoryNode
            The node to evaluate.

        Returns
        -------
        float
            Conditioned weight in ``[0.0, 1.0]``.
        """
        base_score = getattr(node, "purpose_alignment", 0.5)
        if self._vector is not None:
            node_text = f"{node.name} {node.description}"
            text_score = self._vector.project_text(node_text)
            combined = 0.6 * text_score + 0.4 * base_score
        else:
            combined = base_score

        maturity = getattr(node, "maturity", None)
        maturity_bonus = 0.0
        if maturity is not None:
            from jugeo.ideation.theory_navigation.models import NodeMaturity as NM
            maturity_bonus_map = {
                NM.ESTABLISHED: 0.05,
                NM.MATURE: 0.02,
                NM.DEVELOPING: 0.0,
                NM.NASCENT: -0.02,
            }
            maturity_bonus = maturity_bonus_map.get(maturity, 0.0)

        return _clamp(combined + maturity_bonus, 0.0, 1.0)

    # ------------------------------------------------------------------
    # Space-level operations
    # ------------------------------------------------------------------

    def compute_weight_map(self, space: TheorySpace) -> PurposeWeightMap:
        """Build a ``PurposeWeightMap`` covering every node in *space*.

        Parameters
        ----------
        space : TheorySpace
            The theory space to score.

        Returns
        -------
        PurposeWeightMap
            Weight map populated with conditioned scores for all nodes.
        """
        label = self._condition.label if self._condition is not None else ""
        weight_map = PurposeWeightMap(purpose_label=label)
        for node in space.nodes.values():
            weight = self.compute_node_weight(node)
            weight_map.set_weight(node.node_id, weight)
        logger.debug(
            "PurposeConditioner: computed weight map for %d nodes", len(space.nodes)
        )
        return weight_map

    def condition_space(self, space: TheorySpace) -> TheorySpace:
        """Return a conditioned view of *space*.

        Conditioning is a read operation: this method does not mutate the
        ``TheorySpace``.  It logs a summary of how many nodes are above the
        0.5 alignment threshold and returns the space object unchanged.

        Parameters
        ----------
        space : TheorySpace
            Theory space to condition.

        Returns
        -------
        TheorySpace
            The same space object (unchanged).
        """
        if not space.nodes:
            logger.info("PurposeConditioner: empty space, nothing to condition")
            return space
        weights = [self.compute_node_weight(n) for n in space.nodes.values()]
        above_half = sum(1 for w in weights if w >= 0.5)
        total = len(weights)
        pct = _safe_div(above_half * 100.0, total, 0.0)
        logger.info(
            "PurposeConditioner: %d/%d nodes (%.1f%%) have alignment >= 0.5 "
            "under condition '%s'",
            above_half,
            total,
            pct,
            self._condition.label if self._condition else "(none)",
        )
        return space

    def transition_weight(self, from_node: TheoryNode, to_node: TheoryNode) -> float:
        """Compute the purpose-conditioned cost of moving from *from_node* to *to_node*.

        A lower cost means the transition is well-aligned with the current
        research purpose.

        Parameters
        ----------
        from_node : TheoryNode
            Node being departed.
        to_node : TheoryNode
            Node being entered.

        Returns
        -------
        float
            Cost value in ``[0.01, 1.0]``.
        """
        to_weight = self.compute_node_weight(to_node)
        from_weight = self.compute_node_weight(from_node)
        base_cost = 1.0 - to_weight
        # Reward uphill movement towards higher alignment
        if to_weight > from_weight:
            base_cost *= 0.8
        return _clamp(base_cost, 0.01, 1.0)

    def rank_neighbors(
        self, node: TheoryNode, neighbors: list[TheoryNode]
    ) -> list[tuple[TheoryNode, float]]:
        """Rank a node's neighbours by decreasing purpose alignment.

        Parameters
        ----------
        node : TheoryNode
            The current node (used for context but not directly scored here).
        neighbors : list[TheoryNode]
            Candidate neighbours to rank.

        Returns
        -------
        list[tuple[TheoryNode, float]]
            Pairs of ``(neighbor, weight)`` sorted by weight descending.
        """
        scored = [(n, self.compute_node_weight(n)) for n in neighbors]
        return sorted(scored, key=lambda pair: pair[1], reverse=True)

    def describe_conditioning(self, space: TheorySpace) -> str:
        """Produce a comprehensive human-readable conditioning report.

        Parameters
        ----------
        space : TheorySpace
            Theory space to report on.

        Returns
        -------
        str
            Multi-line report including alignment distribution and top nodes.
        """
        condition_label = self._condition.label if self._condition else "(none)"
        node_count = len(space.nodes)
        # Count edges by summing neighbor list lengths
        edge_count = sum(
            len(space.get_neighbors(nid)) for nid in space.nodes
        )

        scored_nodes: list[tuple[str, float]] = []
        for node_id, node in space.nodes.items():
            w = self.compute_node_weight(node)
            scored_nodes.append((node_id, w))

        weights_only = [w for _, w in scored_nodes]
        above_07 = sum(1 for w in weights_only if w >= 0.7)
        below_03 = sum(1 for w in weights_only if w < 0.3)
        total = max(1, len(weights_only))
        avg_weight = _safe_div(sum(weights_only), len(weights_only), 0.0)

        top5 = sorted(scored_nodes, key=lambda kv: kv[1], reverse=True)[:5]

        lines = [
            "=== Purpose Conditioning Report ===",
            f"Condition: {condition_label}",
            f"Space: {node_count} nodes, {edge_count} edges",
            f"Nodes above 0.7 alignment: {above_07} ({above_07/total*100:.1f}%)",
            f"Nodes below 0.3 alignment: {below_03} ({below_03/total*100:.1f}%)",
            f"Average conditioned weight: {avg_weight:.3f}",
            "Top 5 nodes by conditioned weight:",
        ]
        for node_id, w in top5:
            lines.append(f"  {node_id}: {w:.3f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# HeuristicComputer
# ---------------------------------------------------------------------------

class HeuristicComputer:
    """Translates purpose alignment into A*-style heuristics and edge costs.

    By expressing purpose alignment as a cost landscape, purpose-guided
    navigation can be implemented as a standard best-first search without
    modifying the underlying graph algorithms.
    """

    def __init__(
        self,
        condition: PurposeCondition | None = None,
        weight_map: PurposeWeightMap | None = None,
    ) -> None:
        self._condition = condition
        self._weight_map = weight_map
        self._conditioner = (
            PurposeConditioner(condition) if condition else PurposeConditioner()
        )

    # ------------------------------------------------------------------
    # A*-style cost functions
    # ------------------------------------------------------------------

    def heuristic(self, node_id: str, goal_id: str, space: TheorySpace) -> float:
        """Estimate the cost from *node_id* to *goal_id* (admissible A* heuristic).

        The estimate combines a topological distance estimate (1-, 2-, or 3-hop)
        with a purpose-alignment penalty on the goal node.

        Parameters
        ----------
        node_id : str
            Current node identifier.
        goal_id : str
            Target node identifier.
        space : TheorySpace
            The space being searched.

        Returns
        -------
        float
            Non-negative estimated cost.  Returns ``0.0`` when already at goal.
        """
        if node_id == goal_id:
            return 0.0

        neighbors_of_current = space.get_neighbors(node_id)
        if goal_id in neighbors_of_current:
            distance_estimate = 1.0
        else:
            # 2-hop BFS check
            two_hop_reachable = False
            for mid_id in neighbors_of_current:
                second_hop = space.get_neighbors(mid_id)
                if goal_id in second_hop:
                    two_hop_reachable = True
                    break
            distance_estimate = 2.0 if two_hop_reachable else 3.0

        goal_node = space.get_node(goal_id)
        if goal_node is None:
            return distance_estimate

        if self._weight_map is not None:
            goal_weight = self._weight_map.get_weight(goal_id, 0.5)
        elif self._conditioner._condition is not None:
            goal_weight = self._conditioner.compute_node_weight(goal_node)
        else:
            goal_weight = getattr(goal_node, "purpose_alignment", 0.5)

        purpose_penalty = (1.0 - goal_weight) * 0.5
        return distance_estimate + purpose_penalty

    def edge_cost(self, from_id: str, to_id: str, space: TheorySpace) -> float:
        """Compute the purpose-conditioned cost of traversing an edge.

        The cost is a blend of inverse purpose alignment (40 %) and
        node maturity cost (60 %), ensuring that well-developed, highly
        aligned nodes are cheap to visit.

        Parameters
        ----------
        from_id : str
            Source node identifier.
        to_id : str
            Destination node identifier.
        space : TheorySpace
            The space containing the edge.

        Returns
        -------
        float
            Cost in ``[0.01, 1.0]``.  Returns ``1.0`` when the destination
            node does not exist in the space.
        """
        to_node = space.get_node(to_id)
        if to_node is None:
            return 1.0

        if self._weight_map is not None:
            dest_weight = self._weight_map.get_weight(
                to_id, getattr(to_node, "purpose_alignment", 0.5)
            )
        elif self._conditioner._condition is not None:
            dest_weight = self._conditioner.compute_node_weight(to_node)
        else:
            dest_weight = getattr(to_node, "purpose_alignment", 0.5)

        maturity_cost = _maturity_factor(getattr(to_node, "maturity", NodeMaturity.DEVELOPING))
        purpose_cost = 1.0 - dest_weight
        return _clamp(0.4 * purpose_cost + 0.6 * maturity_cost, 0.01, 1.0)

    def total_path_cost(self, path: NavigationPath, space: TheorySpace) -> float:
        """Sum edge costs along an entire ``NavigationPath``.

        Parameters
        ----------
        path : NavigationPath
            The path to evaluate.
        space : TheorySpace
            The space the path was computed in.

        Returns
        -------
        float
            Total accumulated cost.  Returns ``0.0`` for paths with fewer
            than two nodes.
        """
        node_ids = path.node_ids
        if len(node_ids) < 2:
            return 0.0
        total = 0.0
        for i in range(len(node_ids) - 1):
            total += self.edge_cost(node_ids[i], node_ids[i + 1], space)
        return total

    def recompute_path_alignment(self, path: NavigationPath, space: TheorySpace) -> float:
        """Compute the mean purpose alignment across all nodes in *path*.

        Parameters
        ----------
        path : NavigationPath
            Path whose alignment is to be (re)computed.
        space : TheorySpace
            Source space for node lookup.

        Returns
        -------
        float
            Mean ``purpose_alignment`` value across all resolvable nodes, or
            ``0.0`` if the path is empty or no nodes can be resolved.
        """
        if not path.node_ids:
            return 0.0
        alignments: list[float] = []
        for node_id in path.node_ids:
            node = space.get_node(node_id)
            if node is not None:
                alignments.append(getattr(node, "purpose_alignment", 0.5))
        if not alignments:
            return 0.0
        return sum(alignments) / len(alignments)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_condition(self, condition: PurposeCondition) -> None:
        """Update the active condition and rebuild the internal conditioner.

        Parameters
        ----------
        condition : PurposeCondition
            New research purpose.
        """
        self._condition = condition
        self._conditioner = PurposeConditioner(condition)
        logger.debug("HeuristicComputer: condition updated to '%s'", condition.label)

    def update_weights(self, weight_map: PurposeWeightMap) -> None:
        """Replace the active ``PurposeWeightMap`` used for cost lookups.

        Parameters
        ----------
        weight_map : PurposeWeightMap
            Pre-computed weight map to use in heuristic and cost functions.
        """
        self._weight_map = weight_map
        logger.debug(
            "HeuristicComputer: weight map updated (%d nodes)",
            len(weight_map.weights),
        )


# ---------------------------------------------------------------------------
# PurposeAligner
# ---------------------------------------------------------------------------

class PurposeAligner:
    """Post-processes completed ``NavigationPath`` objects for purpose alignment.

    Responsibilities:

    * Recompute a path's overall alignment score.
    * Measure per-path coherence (how consistently aligned the path is).
    * Identify poorly-aligned nodes that may be bottlenecks.
    * Suggest better-aligned replacement candidates from the space.
    * Produce a detailed alignment report.
    """

    def __init__(self, condition: PurposeCondition | None = None) -> None:
        self._condition = condition
        self._conditioner = (
            PurposeConditioner(condition) if condition else PurposeConditioner()
        )
        self._heuristic = HeuristicComputer(condition)

    # ------------------------------------------------------------------
    # Core alignment methods
    # ------------------------------------------------------------------

    def align_path(self, path: NavigationPath, space: TheorySpace) -> NavigationPath:
        """Return a copy of *path* with an updated ``purpose_alignment`` score.

        Parameters
        ----------
        path : NavigationPath
            Path to re-align.
        space : TheorySpace
            Space used to resolve node objects.

        Returns
        -------
        NavigationPath
            New path object (original is unchanged) with recalculated
            ``purpose_alignment``.
        """
        new_alignment = self._heuristic.recompute_path_alignment(path, space)
        return replace(path, purpose_alignment=new_alignment)

    def score_path_coherence(self, path: NavigationPath, space: TheorySpace) -> float:
        """Measure how consistently purpose-aligned the nodes along *path* are.

        A coherence of 1.0 means every node has identical alignment; 0.0 means
        extreme variance.

        Parameters
        ----------
        path : NavigationPath
            Path to evaluate.
        space : TheorySpace
            Space used to resolve node objects.

        Returns
        -------
        float
            Coherence score in ``[0.0, 1.0]``.
        """
        alignments: list[float] = []
        for node_id in path.node_ids:
            node = space.get_node(node_id)
            if node is not None:
                w = self._conditioner.compute_node_weight(node)
                alignments.append(w)

        if len(alignments) < 2:
            return 1.0  # trivially coherent

        std_dev = statistics.stdev(alignments)
        coherence = 1.0 - _clamp(std_dev / 0.5, 0.0, 1.0)
        return coherence

    def find_misaligned_nodes(
        self, path: NavigationPath, space: TheorySpace, threshold: float = 0.3
    ) -> list[str]:
        """Identify nodes in *path* whose conditioned weight is below *threshold*.

        Parameters
        ----------
        path : NavigationPath
            Path to inspect.
        space : TheorySpace
            Space used to resolve node objects.
        threshold : float
            Minimum acceptable alignment weight.  Default ``0.3``.

        Returns
        -------
        list[str]
            Node identifiers, in path order, with weight below *threshold*.
        """
        misaligned: list[str] = []
        for node_id in path.node_ids:
            node = space.get_node(node_id)
            if node is None:
                continue
            w = self._conditioner.compute_node_weight(node)
            if w < threshold:
                misaligned.append(node_id)
        return misaligned

    def suggest_alternatives(
        self, node_id: str, space: TheorySpace, n: int = 3
    ) -> list[TheoryNode]:
        """Suggest purpose-aligned alternative nodes that could replace *node_id*.

        The candidates are drawn from the node's immediate neighbours and from
        the rest of the space, then scored and ranked.

        Parameters
        ----------
        node_id : str
            Identifier of the node to replace.
        space : TheorySpace
            The full theory space to search.
        n : int
            Maximum number of alternatives to return.

        Returns
        -------
        list[TheoryNode]
            Up to *n* alternative nodes sorted by conditioned weight descending.
        """
        direct_neighbors = set(space.get_neighbors(node_id))
        candidates: list[TheoryNode] = []

        for nid, node in space.nodes.items():
            if nid == node_id:
                continue
            candidates.append(node)

        # Score and rank
        scored = [(n_obj, self._conditioner.compute_node_weight(n_obj)) for n_obj in candidates]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        # Prefer direct neighbors by bumping their score
        reranked = []
        for node_obj, score in scored:
            bonus = 0.1 if node_obj.node_id in direct_neighbors else 0.0
            reranked.append((node_obj, score + bonus))
        reranked.sort(key=lambda pair: pair[1], reverse=True)
        return [node_obj for node_obj, _ in reranked[:n]]

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def alignment_report(self, path: NavigationPath, space: TheorySpace) -> str:
        """Produce a detailed human-readable alignment report for *path*.

        Parameters
        ----------
        path : NavigationPath
            Path to report on.
        space : TheorySpace
            Space used to resolve nodes and costs.

        Returns
        -------
        str
            Multi-line report with per-node breakdown and suggestions.
        """
        overall_alignment = self._heuristic.recompute_path_alignment(path, space)
        coherence = self.score_path_coherence(path, space)
        misaligned = self.find_misaligned_nodes(path, space)
        total_cost = self._heuristic.total_path_cost(path, space)

        start_id = path.node_ids[0] if path.node_ids else "N/A"
        goal_id = path.node_ids[-1] if path.node_ids else "N/A"
        purpose_str = getattr(path, "purpose", "(none)")

        lines = [
            "=== Path Alignment Report ===",
            f"Path: {path.path_id}",
            f"Start: {start_id} → Goal: {goal_id}",
            f"Nodes: {len(path.node_ids)}",
            f"Purpose: {purpose_str}",
            f"Overall alignment: {overall_alignment:.3f}",
            f"Coherence score: {coherence:.3f}",
            f"Misaligned nodes (< 0.3): {misaligned if misaligned else 'none'}",
        ]

        # Per-node table
        for i, node_id in enumerate(path.node_ids):
            node = space.get_node(node_id)
            if node is not None:
                w = self._conditioner.compute_node_weight(node)
            else:
                w = 0.0
            lines.append(f"  {i}. {node_id}: alignment={w:.3f}")

        lines.append(f"Total path cost: {total_cost:.3f}")

        # Suggestions for worst misaligned node
        if misaligned:
            worst_id = misaligned[0]
            # Find which is actually worst
            worst_weight = 1.0
            for nid in misaligned:
                node = space.get_node(nid)
                if node is not None:
                    w = self._conditioner.compute_node_weight(node)
                    if w < worst_weight:
                        worst_weight = w
                        worst_id = nid
            alts = self.suggest_alternatives(worst_id, space, n=3)
            lines.append(f"Suggested replacements for '{worst_id}':")
            for alt in alts:
                alt_w = self._conditioner.compute_node_weight(alt)
                lines.append(f"  → {alt.node_id} ({alt.name}): {alt_w:.3f}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# PurposeDriftDetector
# ---------------------------------------------------------------------------

class PurposeDriftDetector:
    """Monitors an ongoing navigation session for drift from the declared research purpose.

    Drift is defined as a sustained drop in purpose alignment relative to the
    baseline established at the start of the session.  When
    :meth:`is_drifting` returns ``True``, the navigator should consider
    course-correcting back towards the stated research purpose.
    """

    def __init__(
        self,
        condition: PurposeCondition | None = None,
        drift_threshold: float = 0.2,
    ) -> None:
        self._condition = condition
        self._drift_threshold = _clamp(drift_threshold, 0.0, 1.0)
        self._conditioner = (
            PurposeConditioner(condition) if condition else PurposeConditioner()
        )
        self._history: list[tuple[str, float]] = []
        self._state_objects: list[NavigationState] = []
        self._baseline_alignment: float | None = None

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_state(self, state: NavigationState, space: TheorySpace) -> None:
        """Record the current ``NavigationState`` and its alignment score.

        The first recorded state establishes the baseline alignment against
        which all future drift is measured.

        Parameters
        ----------
        state : NavigationState
            Current navigation state to record.
        space : TheorySpace
            Theory space used to resolve the current node.
        """
        current_node_id = getattr(state, "current_node_id", None)
        node = space.get_node(current_node_id) if current_node_id else None
        if node is None:
            alignment = 0.0
        else:
            alignment = self._conditioner.compute_node_weight(node)

        state_id = getattr(state, "state_id", str(uuid.uuid4()))
        self._history.append((state_id, alignment))
        self._state_objects.append(state)

        if self._baseline_alignment is None:
            self._baseline_alignment = alignment
            logger.debug(
                "PurposeDriftDetector: baseline alignment set to %.3f", alignment
            )

    # ------------------------------------------------------------------
    # Drift computation
    # ------------------------------------------------------------------

    def compute_drift(self) -> float:
        """Compute the current amount of purpose drift.

        Drift is measured as the drop in alignment from the session baseline.
        When at least three states have been recorded the drift is smoothed
        using the mean of the three most recent alignment values.

        Returns
        -------
        float
            Drift magnitude in ``[0.0, 1.0]``.  ``0.0`` means no drift;
            ``1.0`` means complete loss of alignment.
        """
        if not self._history:
            return 0.0
        if len(self._history) == 1:
            return 0.0
        if self._baseline_alignment is None:
            return 0.0

        current_alignment = self._history[-1][1]
        drift = _clamp(self._baseline_alignment - current_alignment, 0.0, 1.0)

        if len(self._history) >= 3:
            recent_alignments = [a for _, a in self._history[-3:]]
            recent_mean = sum(recent_alignments) / 3.0
            drift = _clamp(self._baseline_alignment - recent_mean, 0.0, 1.0)

        return drift

    def is_drifting(self) -> bool:
        """Return ``True`` when drift exceeds the configured threshold.

        Returns
        -------
        bool
            Whether the session has drifted beyond the acceptable threshold.
        """
        return self.compute_drift() > self._drift_threshold

    def last_aligned_state(self) -> NavigationState | None:
        """Return the last ``NavigationState`` that was above the alignment floor.

        The alignment floor is defined as
        ``baseline - drift_threshold / 2``.

        Returns
        -------
        NavigationState or None
            Most recent well-aligned state, or ``None`` if none qualifies.
        """
        if self._baseline_alignment is None or not self._history:
            return None
        floor = self._baseline_alignment - self._drift_threshold / 2.0
        # Walk backwards through history
        for i in range(len(self._history) - 1, -1, -1):
            _, alignment = self._history[i]
            if alignment >= floor:
                if i < len(self._state_objects):
                    return self._state_objects[i]
        return None

    def reset(self) -> None:
        """Clear all recorded history and reset the baseline alignment.

        After calling this method the detector behaves as if it were freshly
        constructed.
        """
        self._history = []
        self._state_objects = []
        self._baseline_alignment = None
        logger.debug("PurposeDriftDetector: reset")

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def drift_report(self) -> str:
        """Produce a human-readable summary of the drift history.

        Returns
        -------
        str
            Multi-line report including baseline, current drift, and alignment
            history of the last ten states.
        """
        condition_label = self._condition.label if self._condition else "(none)"
        state_count = len(self._history)
        if self._baseline_alignment is not None:
            baseline_str = f"{self._baseline_alignment:.3f}"
        else:
            baseline_str = "(not set)"
        drift = self.compute_drift()
        is_drifting = self.is_drifting()

        lines = [
            "=== Purpose Drift Report ===",
            f"Condition: {condition_label}",
            f"Drift threshold: {self._drift_threshold:.2f}",
            f"States recorded: {state_count}",
            f"Baseline alignment: {baseline_str}",
            f"Current drift: {drift:.3f}",
            f"Is drifting: {is_drifting}",
            "Alignment history (last 10):",
        ]
        recent_history = self._history[-10:]
        for state_id, alignment in recent_history:
            short_id = state_id[:8] if len(state_id) >= 8 else state_id
            lines.append(f"  {short_id}...: {alignment:.3f}")

        last_state = self.last_aligned_state()
        if last_state is not None:
            last_sid = getattr(last_state, "state_id", "unknown")
        else:
            last_sid = "none"
        lines.append(f"Last aligned state: {last_sid}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "PurposeVector",
    "PurposeWeightMap",
    "PurposeConditioner",
    "HeuristicComputer",
    "PurposeAligner",
    "PurposeDriftDetector",
]
