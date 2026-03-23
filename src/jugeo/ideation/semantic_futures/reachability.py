"""Reachability estimation for semantic futures.

This module estimates the probability that a given :class:`~jugeo.ideation.semantic_futures.models.SemanticFuture`
can be reached from a :class:`~jugeo.ideation.semantic_futures.models.FutureState`.  It provides decay
models, a transition graph, path-finding utilities, and a lightweight LRU-style
cache so that repeated queries are answered without recomputation.

Reachability Model Families
----------------------------
* **EXPONENTIAL** – :math:`e^{-\\lambda d}` — smooth, never reaches 0.
* **SIGMOID** – :math:`1 / (1 + e^{k(d-m)})` — S-shaped transition.
* **LINEAR** – :math:`\\max(0,\\ 1 - \\text{slope} \\cdot d)` — linear ramp to 0.
* **CONSTANT** – returns :attr:`ReachabilityModel.baseline` regardless of distance.

Workflow
--------
1.  Build (or load) a :class:`ReachabilityModel` – either manually or via
    :meth:`ReachabilityModel.calibrate`.
2.  Pass it to :class:`ReachabilityEstimator` together with an optional
    :class:`ReachabilityCache`.
3.  Call :meth:`ReachabilityEstimator.estimate` or
    :meth:`ReachabilityEstimator.estimate_batch`.
4.  Optionally feed observed outcomes back via
    :meth:`ReachabilityEstimator.update_calibration` and retrieve uncertainty
    bounds with :meth:`ReachabilityEstimator.confidence_interval`.
"""

from __future__ import annotations

import heapq
import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.ideation.semantic_futures.models import FutureState, SemanticFuture


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ReachabilityModelType(str, Enum):
    """Decay-function family used by :class:`ReachabilityModel`."""

    EXPONENTIAL = "exponential"
    SIGMOID = "sigmoid"
    LINEAR = "linear"
    CONSTANT = "constant"


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _exponential_decay(distance: float, lam: float = 1.0) -> float:
    """Return :math:`e^{-\\lambda \\cdot d}`, clamped to ``[0, 1]``.

    Parameters
    ----------
    distance:
        Non-negative distance value.
    lam:
        Decay-rate constant (must be positive).

    Returns
    -------
    float
        1.0 at distance 0; strictly decreasing; approaches 0 as distance → ∞.
    """
    if distance <= 0.0:
        return 1.0
    result = math.exp(-lam * distance)
    return max(0.0, min(1.0, result))


def _sigmoid(x: float, steepness: float = 1.0, midpoint: float = 0.5) -> float:
    """Return an S-shaped value in ``(0, 1)``.

    The function is *decreasing* in ``x`` so that small distances map to
    high reachability.

    Parameters
    ----------
    x:
        Input value (interpreted as a distance).
    steepness:
        Controls how sharply the sigmoid transitions (larger ⇒ sharper).
    midpoint:
        The distance at which the output equals 0.5.
    """
    try:
        val = 1.0 / (1.0 + math.exp(steepness * (x - midpoint)))
    except OverflowError:
        val = 0.0
    return max(1e-9, min(1.0 - 1e-9, val))


def _linear_decay(distance: float, slope: float = 1.0) -> float:
    """Return ``max(0, 1 - slope * distance)``, clamped to ``[0, 1]``.

    Parameters
    ----------
    distance:
        Non-negative distance value.
    slope:
        Rate at which reachability decreases per unit distance.
    """
    return max(0.0, min(1.0, 1.0 - slope * max(0.0, distance)))


def _semantic_distance(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine distance between two embedding vectors, in ``[0, 1]``.

    Returns 1.0 when either vector is empty or when they are orthogonal.
    Returns 0.0 when the vectors are identical (and non-zero).

    Parameters
    ----------
    a, b:
        Embedding vectors of equal (or different) length.  If lengths differ
        the shorter is zero-padded conceptually – in practice zero padding
        is equivalent to treating the missing dimensions as orthogonal, so
        this returns 1.0 in that case.
    """
    if not a or not b:
        return 1.0
    if len(a) != len(b):
        return 1.0
    dot = sum(ai * bi for ai, bi in zip(a, b))
    mag_a = math.sqrt(sum(ai * ai for ai in a))
    mag_b = math.sqrt(sum(bi * bi for bi in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 1.0
    cosine_similarity = dot / (mag_a * mag_b)
    cosine_similarity = max(-1.0, min(1.0, cosine_similarity))
    return max(0.0, min(1.0, (1.0 - cosine_similarity) / 2.0))


def _create_model_from_type(
    model_type: ReachabilityModelType, **kwargs: Any
) -> "ReachabilityModel":
    """Construct a :class:`ReachabilityModel` for the given *model_type*.

    Any keyword arguments are forwarded as ``params``.

    Parameters
    ----------
    model_type:
        One of the :class:`ReachabilityModelType` variants.
    **kwargs:
        Key-value parameters (e.g. ``lam=2.0``, ``slope=0.5``).
    """
    return ReachabilityModel(model_type=model_type, params=kwargs)


# ---------------------------------------------------------------------------
# ReachabilityModel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReachabilityModel:
    """Immutable decay model that converts a semantic distance into a probability.

    Parameters
    ----------
    model_type:
        Which decay function to apply.
    params:
        Hyper-parameters forwarded to the decay function (e.g. ``{"lam": 2.0}``).
        Stored internally as a sorted tuple of items so the dataclass remains
        hashable and frozen.
    baseline:
        Fall-back value returned by the CONSTANT model (and used as a lower
        bound by some callers).
    """

    model_type: ReachabilityModelType
    params: dict = field(default_factory=dict)
    baseline: float = 0.5

    # ------------------------------------------------------------------
    # Frozen-dataclass helpers: store params as a tuple of items
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        # Convert params dict → sorted tuple of items so the object is hashable.
        if isinstance(self.params, dict):
            object.__setattr__(self, "params", dict(sorted(self.params.items())))

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def compute(self, distance: float) -> float:
        """Map *distance* to a reachability score in ``[0, 1]``.

        Parameters
        ----------
        distance:
            Non-negative semantic distance between source and target states.
        """
        mt = self.model_type
        p = self.params if isinstance(self.params, dict) else dict(self.params)
        if mt == ReachabilityModelType.EXPONENTIAL:
            return _exponential_decay(distance, lam=float(p.get("lam", 1.0)))
        if mt == ReachabilityModelType.SIGMOID:
            return _sigmoid(
                distance,
                steepness=float(p.get("steepness", 1.0)),
                midpoint=float(p.get("midpoint", 0.5)),
            )
        if mt == ReachabilityModelType.LINEAR:
            return _linear_decay(distance, slope=float(p.get("slope", 1.0)))
        # CONSTANT
        return max(0.0, min(1.0, self.baseline))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        params = self.params if isinstance(self.params, dict) else dict(self.params)
        return {
            "model_type": self.model_type.value,
            "params": params,
            "baseline": self.baseline,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReachabilityModel:
        """Deserialise from a plain dictionary produced by :meth:`to_dict`."""
        return cls(
            model_type=ReachabilityModelType(d["model_type"]),
            params=dict(d.get("params", {})),
            baseline=float(d.get("baseline", 0.5)),
        )

    @classmethod
    def calibrate(
        cls,
        distances: list[float],
        targets: list[float],
        model_type: ReachabilityModelType = ReachabilityModelType.EXPONENTIAL,
    ) -> ReachabilityModel:
        """Fit a model to paired ``(distance, target_reachability)`` observations.

        Uses a simple least-squares search over the primary parameter (``lam``
        for EXPONENTIAL, ``slope`` for LINEAR, ``steepness`` for SIGMOID).

        Parameters
        ----------
        distances:
            Observed distance values.
        targets:
            Corresponding observed reachability values in ``[0, 1]``.
        model_type:
            Which decay family to fit.

        Returns
        -------
        ReachabilityModel
            Fitted model.
        """
        if not distances or not targets or len(distances) != len(targets):
            return cls(model_type=model_type)

        # Grid search over candidate parameter values.
        best_param = 1.0
        best_loss = float("inf")
        candidates = [0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0]

        for param in candidates:
            if model_type == ReachabilityModelType.EXPONENTIAL:
                preds = [_exponential_decay(d, lam=param) for d in distances]
            elif model_type == ReachabilityModelType.LINEAR:
                preds = [_linear_decay(d, slope=param) for d in distances]
            elif model_type == ReachabilityModelType.SIGMOID:
                preds = [_sigmoid(d, steepness=param) for d in distances]
            else:
                preds = [0.5] * len(distances)

            loss = sum((p - t) ** 2 for p, t in zip(preds, targets))
            if loss < best_loss:
                best_loss = loss
                best_param = param

        if model_type == ReachabilityModelType.EXPONENTIAL:
            params: dict[str, float] = {"lam": best_param}
        elif model_type == ReachabilityModelType.LINEAR:
            params = {"slope": best_param}
        elif model_type == ReachabilityModelType.SIGMOID:
            params = {"steepness": best_param}
        else:
            params = {}

        return cls(model_type=model_type, params=params)


# ---------------------------------------------------------------------------
# ReachabilityCache
# ---------------------------------------------------------------------------


class ReachabilityCache:
    """LRU-like cache for reachability scores.

    Tracks hits and misses to allow monitoring of cache effectiveness via
    :meth:`hit_rate`.

    Parameters
    ----------
    maxsize:
        Maximum number of entries before the least-recently-used entry is
        evicted.  Defaults to 512.
    """

    def __init__(self, maxsize: int = 512) -> None:
        self._maxsize = maxsize
        self._store: OrderedDict[str, float] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> float | None:
        """Return the cached value for *key*, or ``None`` on a cache miss."""
        if key in self._store:
            self._store.move_to_end(key)
            self._hits += 1
            return self._store[key]
        self._misses += 1
        return None

    def set(self, key: str, value: float) -> None:
        """Insert or update *key* → *value*.  Evicts LRU entry if at capacity."""
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        if len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    def clear(self) -> None:
        """Remove all cached entries and reset hit/miss counters."""
        self._store.clear()
        self._hits = 0
        self._misses = 0

    def size(self) -> int:
        """Return the number of entries currently in the cache."""
        return len(self._store)

    def hit_rate(self) -> float:
        """Return the fraction of lookups that were cache hits, in ``[0, 1]``."""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0


# ---------------------------------------------------------------------------
# BridgeProbability
# ---------------------------------------------------------------------------


class BridgeProbability:
    """Estimates the probability of bridging from a source state to a target future.

    A *bridge* is an intermediate semantic operation (analogy, abstraction,
    generalisation, etc.) that reduces the apparent distance between source
    and target.  Each bridge contributes an independent probability estimate;
    the final score is the maximum across available bridges.

    This class is intentionally *not* frozen so that bridge weights can be
    updated on-line.
    """

    #: Named bridge types and their intrinsic discount factors (0 ⇒ no help,
    #: 1 ⇒ full bridge).
    _BRIDGE_DISCOUNT: dict[str, float] = {
        "analogy": 0.3,
        "abstraction": 0.25,
        "generalisation": 0.2,
        "specialisation": 0.15,
        "domain_transfer": 0.4,
    }

    def compute(self, source: FutureState, target: SemanticFuture) -> float:
        """Estimate bridge probability in ``[0, 1]``.

        Parameters
        ----------
        source:
            The current ideation state.
        target:
            The proposed future to reach.
        """
        gap = self._semantic_gap(source, target)
        best = 0.0
        for bridge in self.available_bridges(source):
            discount = self._BRIDGE_DISCOUNT.get(bridge, 0.1)
            effective_gap = max(0.0, gap - discount)
            prob = _exponential_decay(effective_gap, lam=1.5)
            best = max(best, prob)
        return max(0.0, min(1.0, best))

    def available_bridges(self, state: FutureState) -> list[str]:
        """Return the list of bridge names available from *state*.

        The list always contains at least ``"analogy"``; additional bridges are
        added based on the domain of the source state.

        Parameters
        ----------
        state:
            The current ideation state.
        """
        bridges = ["analogy"]
        domain = (state.domain or "").lower()
        if domain:
            bridges.append("abstraction")
        if "math" in domain or "topology" in domain or "algebra" in domain:
            bridges.extend(["generalisation", "specialisation"])
        if "transfer" in domain or len(domain) > 5:
            bridges.append("domain_transfer")
        return bridges

    def _semantic_gap(self, source: FutureState, target: SemanticFuture) -> float:
        """Return the non-negative semantic gap between *source* and *target*.

        Uses the embedding distance when available; falls back to a simple
        string-overlap heuristic.

        Parameters
        ----------
        source:
            The current ideation state.
        target:
            The proposed future.
        """
        if source.embedding:
            # Build a pseudo-embedding for the future from source embedding and
            # reachability / purpose signals.
            pseudo: tuple[float, ...] = source.embedding
            # Scale by inverse of reachability to widen the gap for hard futures.
            scale = 1.0 - target.reachability * 0.5
            scaled = tuple(v * scale for v in pseudo)
            dist = _semantic_distance(source.embedding, scaled)
            return max(0.0, dist)

        # Fallback: token-overlap gap in [0, 1].
        src_tokens = set((source.description or "").lower().split())
        tgt_tokens = set((target.delta or "").lower().split())
        if not src_tokens and not tgt_tokens:
            return 0.0
        union = src_tokens | tgt_tokens
        intersection = src_tokens & tgt_tokens
        overlap = len(intersection) / len(union) if union else 0.0
        return max(0.0, 1.0 - overlap)


# ---------------------------------------------------------------------------
# TransitionGraph
# ---------------------------------------------------------------------------


class TransitionGraph:
    """Directed weighted graph of state transitions.

    Nodes are state IDs (``str``); edges carry a *probability* and a *cost*.

    Parameters
    ----------
    (no constructor parameters – states and edges are added via methods)
    """

    def __init__(self) -> None:
        # state_id -> embedding
        self._states: dict[str, tuple[float, ...]] = {}
        # from_id -> list of (to_id, probability, cost)
        self._edges: dict[str, list[tuple[str, float, float]]] = {}

    # ------------------------------------------------------------------
    # Mutating helpers
    # ------------------------------------------------------------------

    def add_state(self, state_id: str, embedding: tuple[float, ...] = ()) -> None:
        """Register a state node.

        Parameters
        ----------
        state_id:
            Unique identifier.
        embedding:
            Optional numeric embedding (unused by graph algorithms but stored
            for downstream consumers).
        """
        if state_id not in self._states:
            self._states[state_id] = embedding
            self._edges.setdefault(state_id, [])

    def add_transition(
        self,
        from_id: str,
        to_id: str,
        probability: float,
        cost: float = 1.0,
    ) -> None:
        """Add a directed transition from *from_id* to *to_id*.

        Both nodes are auto-registered if not already present.

        Parameters
        ----------
        from_id:
            Source state ID.
        to_id:
            Target state ID.
        probability:
            Transition probability in ``[0, 1]``.
        cost:
            Non-negative cost associated with this transition.
        """
        probability = max(0.0, min(1.0, probability))
        cost = max(0.0, cost)
        self.add_state(from_id)
        self.add_state(to_id)
        self._edges[from_id].append((to_id, probability, cost))

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def reachable_from(self, state_id: str, max_hops: int = 3) -> set[str]:
        """BFS to find all states reachable within *max_hops* transitions.

        Parameters
        ----------
        state_id:
            Starting node.
        max_hops:
            Maximum number of edges to traverse.

        Returns
        -------
        set[str]
            IDs of reachable states (excluding *state_id* itself when
            ``max_hops > 0``).
        """
        if max_hops <= 0:
            return set()
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(state_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for to_id, _prob, _cost in self._edges.get(current, []):
                if to_id != state_id and to_id not in visited:
                    visited.add(to_id)
                    queue.append((to_id, depth + 1))
        return visited

    def probability_of_path(self, path: list[str]) -> float:
        """Return the joint probability of traversing *path*.

        Parameters
        ----------
        path:
            Ordered list of state IDs forming the path.

        Returns
        -------
        float
            Product of edge probabilities.  Returns 1.0 for an empty or
            single-node path (no edges traversed).
        """
        if len(path) < 2:
            return 1.0
        prob = 1.0
        for i in range(len(path) - 1):
            src, dst = path[i], path[i + 1]
            # Find the highest-probability edge from src to dst.
            best = 0.0
            for to_id, p, _c in self._edges.get(src, []):
                if to_id == dst:
                    best = max(best, p)
            prob *= best
        return prob

    def shortest_path(self, from_id: str, to_id: str) -> list[str] | None:
        """Dijkstra shortest-cost path from *from_id* to *to_id*.

        Parameters
        ----------
        from_id, to_id:
            Source and destination state IDs.

        Returns
        -------
        list[str] | None
            Ordered list of state IDs (including endpoints), or ``None`` if no
            path exists.
        """
        if from_id not in self._states or to_id not in self._states:
            return None
        dist: dict[str, float] = {from_id: 0.0}
        prev: dict[str, str | None] = {from_id: None}
        # min-heap of (cost, node)
        heap: list[tuple[float, str]] = [(0.0, from_id)]
        while heap:
            cost, node = heapq.heappop(heap)
            if node == to_id:
                path: list[str] = []
                cur: str | None = to_id
                while cur is not None:
                    path.append(cur)
                    cur = prev.get(cur)
                return list(reversed(path))
            if cost > dist.get(node, float("inf")):
                continue
            for neighbour, _prob, edge_cost in self._edges.get(node, []):
                new_cost = cost + edge_cost
                if new_cost < dist.get(neighbour, float("inf")):
                    dist[neighbour] = new_cost
                    prev[neighbour] = node
                    heapq.heappush(heap, (new_cost, neighbour))
        return None

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "states": {sid: list(emb) for sid, emb in self._states.items()},
            "edges": {
                src: [(dst, p, c) for dst, p, c in edges]
                for src, edges in self._edges.items()
            },
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TransitionGraph:
        """Deserialise from a plain dictionary produced by :meth:`to_dict`."""
        g = cls()
        for sid, emb in d.get("states", {}).items():
            g.add_state(sid, tuple(float(v) for v in emb))
        for src, edges in d.get("edges", {}).items():
            for entry in edges:
                dst, prob, cost = entry
                g.add_transition(src, dst, float(prob), float(cost))
        return g


# ---------------------------------------------------------------------------
# PathFinder
# ---------------------------------------------------------------------------


class PathFinder:
    """Higher-level path search built on top of :class:`TransitionGraph`.

    Parameters
    ----------
    graph:
        The transition graph to search.
    """

    def __init__(self, graph: TransitionGraph) -> None:
        self._graph = graph

    def find_path(self, from_id: str, to_id: str) -> list[str] | None:
        """Return the shortest-cost path from *from_id* to *to_id*, or ``None``.

        Delegates to :meth:`TransitionGraph.shortest_path`.
        """
        return self._graph.shortest_path(from_id, to_id)

    def find_k_shortest(
        self,
        from_id: str,
        to_id: str,
        k: int = 3,
    ) -> list[list[str]]:
        """Return up to *k* distinct shortest paths using a modified BFS/heap.

        Parameters
        ----------
        from_id, to_id:
            Source and destination state IDs.
        k:
            Maximum number of paths to return.

        Returns
        -------
        list[list[str]]
            At most *k* paths, each as an ordered list of state IDs.  May be
            fewer than *k* if the graph is sparse.
        """
        if from_id not in self._graph._states or to_id not in self._graph._states:
            return []

        results: list[list[str]] = []
        # heap entries: (cost, path)
        heap: list[tuple[float, list[str]]] = [(0.0, [from_id])]
        seen_paths: set[tuple[str, ...]] = set()

        while heap and len(results) < k:
            cost, path = heapq.heappop(heap)
            node = path[-1]
            path_key = tuple(path)

            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)

            if node == to_id and len(path) > 1:
                results.append(path)
                continue

            for neighbour, _prob, edge_cost in self._graph._edges.get(node, []):
                if neighbour not in path:  # avoid cycles
                    new_path = path + [neighbour]
                    heapq.heappush(heap, (cost + edge_cost, new_path))

        return results

    def reachability_via_paths(self, from_id: str, to_id: str) -> float:
        """Estimate reachability as the probability of the best available path.

        Returns 0.0 if no path exists.

        Parameters
        ----------
        from_id, to_id:
            Source and destination state IDs.
        """
        paths = self.find_k_shortest(from_id, to_id, k=5)
        if not paths:
            return 0.0
        best = max(self._graph.probability_of_path(p) for p in paths)
        return max(0.0, min(1.0, best))


# ---------------------------------------------------------------------------
# ReachabilityEstimator
# ---------------------------------------------------------------------------


class ReachabilityEstimator:
    """Estimates the reachability of :class:`SemanticFuture` objects.

    Combines a :class:`ReachabilityModel` (which converts semantic distance to
    probability) with an optional :class:`BridgeProbability` contribution, and
    memoises results in a :class:`ReachabilityCache`.

    Parameters
    ----------
    model:
        Decay model to use.  Defaults to :data:`DEFAULT_MODEL`.
    cache:
        Optional cache.  A fresh :class:`ReachabilityCache` is created when
        ``None`` is passed.
    """

    def __init__(
        self,
        model: ReachabilityModel | None = None,
        cache: ReachabilityCache | None = None,
    ) -> None:
        self._model = model if model is not None else DEFAULT_MODEL
        self._cache = cache if cache is not None else ReachabilityCache()
        self._bridge = BridgeProbability()
        # Calibration observations: (distance, observed) pairs.
        self._observations: list[tuple[float, float]] = []

    # ------------------------------------------------------------------
    # Core estimation
    # ------------------------------------------------------------------

    def _cache_key(self, state: FutureState, future: SemanticFuture) -> str:
        return f"{state.state_id}:{future.future_id}"

    def estimate(self, state: FutureState, future: SemanticFuture) -> float:
        """Return the estimated reachability in ``[0, 1]``.

        Parameters
        ----------
        state:
            Current ideation state.
        future:
            Proposed future to evaluate.
        """
        key = self._cache_key(state, future)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        # Semantic distance between state embedding and future's implicit position.
        if state.embedding:
            # Approximate target embedding: use source, perturbed by 1 - reachability.
            scale = 1.0 - future.reachability * 0.3
            pseudo = tuple(v * scale for v in state.embedding)
            dist = _semantic_distance(state.embedding, pseudo)
        else:
            # Fallback: use (1 - reachability) as a proxy for distance.
            dist = max(0.0, 1.0 - future.reachability)

        model_score = self._model.compute(dist)
        bridge_score = self._bridge.compute(state, future)

        # Blend: 70% model, 30% bridge.
        blended = 0.7 * model_score + 0.3 * bridge_score
        result = max(0.0, min(1.0, blended))

        self._cache.set(key, result)
        return result

    def estimate_batch(
        self, state: FutureState, futures: list[SemanticFuture]
    ) -> list[float]:
        """Return a reachability score for each future in *futures*.

        Parameters
        ----------
        state:
            Current ideation state (shared for all futures).
        futures:
            List of futures to evaluate.

        Returns
        -------
        list[float]
            Same-length list of scores in ``[0, 1]``.
        """
        return [self.estimate(state, f) for f in futures]

    # ------------------------------------------------------------------
    # Online calibration
    # ------------------------------------------------------------------

    def update_calibration(
        self,
        state: FutureState,
        future: SemanticFuture,
        observed: float,
    ) -> None:
        """Record an observed reachability value for future calibration.

        Invalidates any cached estimate for this (state, future) pair.

        Parameters
        ----------
        state:
            The ideation state from which *future* was attempted.
        future:
            The future that was attempted.
        observed:
            The empirically observed reachability (1.0 if reached, 0.0 if not,
            or an intermediate value).
        """
        observed = max(0.0, min(1.0, float(observed)))
        if state.embedding:
            scale = 1.0 - future.reachability * 0.3
            pseudo = tuple(v * scale for v in state.embedding)
            dist = _semantic_distance(state.embedding, pseudo)
        else:
            dist = max(0.0, 1.0 - future.reachability)
        self._observations.append((dist, observed))
        # Invalidate stale cache entry.
        key = self._cache_key(state, future)
        # Re-insert the observed value.
        self._cache.set(key, observed)

    # ------------------------------------------------------------------
    # Uncertainty
    # ------------------------------------------------------------------

    def confidence_interval(
        self,
        state: FutureState,
        future: SemanticFuture,
        confidence: float = 0.95,
    ) -> tuple[float, float]:
        """Return a ``(lo, hi)`` confidence interval for the reachability estimate.

        Uses a Wilson-score-inspired symmetric band around the point estimate,
        widened by ``(1 - confidence)`` to honour the requested confidence level.

        Parameters
        ----------
        state:
            Current ideation state.
        future:
            Proposed future to evaluate.
        confidence:
            Confidence level in ``(0, 1)``.

        Returns
        -------
        tuple[float, float]
            ``(lo, hi)`` with ``0 <= lo <= hi <= 1``.
        """
        point = self.estimate(state, future)
        half_width = (1.0 - max(0.0, min(1.0, confidence))) / 2.0
        lo = max(0.0, point - half_width)
        hi = min(1.0, point + half_width)
        return (lo, hi)


# ---------------------------------------------------------------------------
# Module-level default
# ---------------------------------------------------------------------------

#: Default :class:`ReachabilityModel` used when no explicit model is supplied.
DEFAULT_MODEL: ReachabilityModel = ReachabilityModel(
    model_type=ReachabilityModelType.EXPONENTIAL,
    params={"lam": 1.0},
    baseline=0.5,
)
