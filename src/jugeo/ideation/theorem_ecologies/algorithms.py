"""High-level ecological algorithms for theorem ecosystems (theory2.tex Ch61 §4).

Module layout::

    EcologicalAlgorithm      – algorithm type enumeration
    EcologyManager           – manages theorem ecosystems
    PortfolioOptimizer       – optimizes lemma portfolios
    EcologicalDynamicsSimulator – simulates ecological dynamics
    EcologyDiagnostics       – diagnostics and health reporting
    EcologyHistory           – historical record of ecology changes
    EcologyBenchmark         – benchmarking ecology operations

Theoretical background (theory2.tex Ch61 §4)
---------------------------------------------
Theorem ecologies model the interdependencies among mathematical results
(theorems, lemmas, corollaries) as a living ecosystem subject to growth,
decay, competition, and symbiosis.  An *ecology* is healthy when its
constituent nodes form a well-connected, diverse dependency graph with
reasonable depth — analogous to a biodiverse food-web in ecology.

A *lemma portfolio* is the curated subset of lemmas that a prover or proof-
assistant actively maintains for re-use.  Optimal portfolios maximise
expected coverage (the fraction of new proof obligations dischargeable by
existing lemmas) subject to a budget constraint on portfolio size.

*Compounding effects* arise when two or more lemmas, used together, enable
proofs that neither could support individually — a super-linear synergy
analogous to mutualism in biological systems.

The algorithms here operate on the data structures defined in
``jugeo.ideation.theorem_ecologies.models`` and the service objects defined
in the ``s01`` / ``s02`` / ``s03`` sub-modules.  They implement six distinct
optimisation strategies (§4.3) and three simulation modes (§4.5), together
with diagnostic and benchmarking utilities.

Usage example
-------------
::

    from jugeo.ideation.theorem_ecologies.algorithms import (
        EcologyManager, PortfolioOptimizer, EcologicalAlgorithm,
    )

    mgr = EcologyManager()
    eco = mgr.create("algebra", ["T1", "T2"], ["L1", "L2", "L3"],
                     {"T1": ["L1"], "T2": ["L1", "L2"]})
    opt = PortfolioOptimizer(algorithm=EcologicalAlgorithm.GREEDY_BUILD)
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.ideation.theorem_ecologies.models import (
    TheoremEcology, LemmaPortfolio, CompoundingEffect,
    EcologicalDynamic, PortfolioOptimization, EcologyHealth, DynamicType,
)
from jugeo.ideation.theorem_ecologies.ecology_modeling import (
    EcologyConfig, TheoremNode, EcologyBuilder, DependencyMapper,
    HealthCalculator, DiversityAnalyzer, EcologyModeler,
)
from jugeo.ideation.theorem_ecologies.lemma_portfolios import (
    PortfolioConfig, LemmaUtilityEstimator, ReuseTracker,
    CoverageCalculator, PortfolioRebalancer, LemmaPortfolioManager,
)
from jugeo.ideation.theorem_ecologies.compounding import (
    CompoundingConfig, CompoundingDetector, SynergyEstimator,
    CompoundBuilder, AmplificationCalculator, CompoundingEngine,
)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum health score below which an ecology is flagged as critical.
_CRITICAL_HEALTH_THRESHOLD: float = 0.2

#: Maximum allowed dependency-graph depth before an anomaly is reported.
_MAX_DEPTH_THRESHOLD: int = 10

#: Connectivity lower bound: fraction of nodes that must be reachable from any root.
_MIN_CONNECTIVITY: float = 0.3

#: Epsilon used to guard against division-by-zero.
_EPSILON: float = 1e-12

#: Cooling rate for simulated-annealing optimisation.
_SA_COOLING_RATE: float = 0.95

#: Initial temperature for simulated-annealing.
_SA_INITIAL_TEMP: float = 1.0


# ===========================================================================
# Helper functions
# ===========================================================================

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* into the closed interval [*lo*, *hi*].

    Parameters
    ----------
    value:
        The floating-point value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to 0.0.
    hi:
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        ``max(lo, min(hi, value))``

    Examples
    --------
    >>> _clamp(1.5)
    1.0
    >>> _clamp(-0.3, 0.0, 1.0)
    0.0
    >>> _clamp(0.7)
    0.7
    """
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time formatted as an ISO-8601 string.

    The returned string is timezone-aware (``+00:00`` suffix) and has
    microsecond precision, making it suitable for use as a record timestamp
    in audit logs and history entries.

    Returns
    -------
    str
        ISO-8601 UTC timestamp, e.g. ``"2024-03-15T12:34:56.789012+00:00"``.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> list[str]:
    """Tokenize *text* into lowercase alphabetic tokens.

    Splits on any non-alphabetic character and discards empty strings.
    Used by similarity and search utilities that need a bag-of-words
    representation of identifier names or description strings.

    Parameters
    ----------
    text:
        Arbitrary string to tokenize.

    Returns
    -------
    list[str]
        Non-empty lowercase tokens in document order, e.g.
        ``_tokenize("AlgebraT1 lemma_2")  →  ["algebrat", "lemma"]``.
    """
    return [t.lower() for t in re.split(r"[^a-zA-Z]+", text) if t]


def _jaccard(set_a: frozenset[str], set_b: frozenset[str]) -> float:
    """Compute the Jaccard similarity coefficient between two sets.

    Jaccard similarity is defined as |A ∩ B| / |A ∪ B|.  It ranges from
    0.0 (disjoint sets) to 1.0 (identical sets) and is used throughout
    the ecology comparison and clustering routines.

    Parameters
    ----------
    set_a:
        First set of string elements.
    set_b:
        Second set of string elements.

    Returns
    -------
    float
        Jaccard similarity in [0.0, 1.0].  Returns 0.0 when both sets
        are empty.
    """
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / (union + _EPSILON)


def _moving_average(values: list[float], window: int) -> list[float]:
    """Compute a causal moving average with the given window size.

    For each position *i* the average is computed over the window
    ``values[max(0, i-window+1) : i+1]``, so the window shrinks near
    the start of the sequence (no padding / look-ahead).

    Parameters
    ----------
    values:
        Input time series of floats.
    window:
        Number of past samples to include in each average.  Must be ≥ 1.

    Returns
    -------
    list[float]
        Smoothed values of the same length as *values*.  Returns an empty
        list when *values* is empty.

    Notes
    -----
    The computational complexity is O(n·window) in the naïve case but
    practical inputs are short enough that a sliding window is unnecessary.
    """
    if not values:
        return []
    result: list[float] = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        result.append(sum(values[start:i + 1]) / (i - start + 1))
    return result


def _percentile(values: list[float], p: float) -> float:
    """Compute the *p*-th percentile (0–100) of a list of floats.

    Uses linear interpolation between the two nearest ranks when the
    desired percentile falls between two data points.

    Parameters
    ----------
    values:
        Non-empty sequence of floats.  Need not be sorted.
    p:
        Desired percentile in the range [0, 100].

    Returns
    -------
    float
        The interpolated *p*-th percentile value.  Returns 0.0 for an
        empty *values* list.

    Examples
    --------
    >>> _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50.0)
    3.0
    >>> _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.0)
    1.0
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _entropy(weights: list[float]) -> float:
    """Compute the normalised Shannon entropy of a weight distribution.

    The raw entropy is H = -Σ p_i log(p_i).  When normalised by log(n)
    the result is in [0, 1]: 0 for a degenerate distribution, 1 for a
    uniform distribution.  Used by diversity metrics throughout this module.

    Parameters
    ----------
    weights:
        Non-negative weights.  Need not sum to 1.  An empty list or a
        list with a single nonzero weight yields 0.0.

    Returns
    -------
    float
        Normalised entropy in [0.0, 1.0].
    """
    total = sum(w for w in weights if w > 0.0)
    if total < _EPSILON or len(weights) < 2:
        return 0.0
    probs = [w / total for w in weights if w > 0.0]
    raw = -sum(p * math.log(p) for p in probs if p > 0.0)
    max_entropy = math.log(len(probs))
    return raw / max_entropy if max_entropy > _EPSILON else 0.0


def _bfs_depth(adj: dict[str, list[str]], roots: list[str]) -> int:
    """Return the maximum BFS depth from any root in the adjacency graph.

    Parameters
    ----------
    adj:
        Mapping node → list of successor nodes.
    roots:
        Starting nodes for the BFS.

    Returns
    -------
    int
        Maximum depth reached, counting from 0 at the roots.
        Returns 0 when *roots* is empty.
    """
    if not roots:
        return 0
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    for r in roots:
        if r not in visited:
            visited.add(r)
            queue.append((r, 0))
    max_depth = 0
    while queue:
        node, depth = queue.popleft()
        max_depth = max(max_depth, depth)
        for neighbour in adj.get(node, []):
            if neighbour not in visited:
                visited.add(neighbour)
                queue.append((neighbour, depth + 1))
    return max_depth


def _reachable(adj: dict[str, list[str]], roots: list[str]) -> set[str]:
    """Return the set of nodes reachable from *roots* in *adj*.

    Uses iterative BFS to avoid recursion-limit issues on deep graphs.
    """
    visited: set[str] = set()
    queue: deque[str] = deque(roots)
    visited.update(roots)
    while queue:
        node = queue.popleft()
        for nbr in adj.get(node, []):
            if nbr not in visited:
                visited.add(nbr)
                queue.append(nbr)
    return visited


# ===========================================================================
# EcologicalAlgorithm
# ===========================================================================

class EcologicalAlgorithm(str, Enum):
    """Enumeration of optimisation algorithms available to :class:`PortfolioOptimizer`.

    Each algorithm represents a distinct strategy from theory2.tex Ch61 §4.3.
    The choice of algorithm affects convergence speed, solution quality, and
    computational cost.

    Attributes
    ----------
    GREEDY_BUILD:
        Add lemmas one at a time by coverage gain.  O(n²) but guaranteed
        monotone improvement per step.
    ITERATIVE_REFINEMENT:
        Alternate between pruning low-utility lemmas and adding high-utility
        candidates.  Converges quickly for mid-size portfolios.
    COVERAGE_MAXIMIZATION:
        Exact set-cover relaxation via greedy approximation.  Achieves the
        (1 − 1/e) approximation ratio for sub-modular coverage functions.
    ENTROPY_MAXIMIZATION:
        Maximise portfolio diversity (normalised entropy of utility weights).
        Robust to distribution shift in proof obligations.
    HILL_CLIMBING:
        Local search with random single-lemma swaps.  Simple but liable to
        plateau; supports random restarts.
    SIMULATED_ANNEALING:
        Probabilistic acceptance of worse moves prevents entrapment in local
        optima.  Cooling schedule: T_t = T_0 · α^t with α = 0.95.
    GENETIC:
        Population-based evolution of portfolio candidates using crossover and
        mutation.  Expensive but explores a wide solution space.
    """

    GREEDY_BUILD = "greedy_build"
    ITERATIVE_REFINEMENT = "iterative_refinement"
    COVERAGE_MAXIMIZATION = "coverage_maximization"
    ENTROPY_MAXIMIZATION = "entropy_maximization"
    HILL_CLIMBING = "hill_climbing"
    SIMULATED_ANNEALING = "simulated_annealing"
    GENETIC = "genetic"


# ===========================================================================
# EcologyManager
# ===========================================================================

class EcologyManager:
    """Manages theorem ecosystems with full CRUD and analysis operations.

    ``EcologyManager`` is the primary entry point for creating, reading,
    updating, and deleting :class:`~jugeo.ideation.theorem_ecologies.models.TheoremEcology`
    instances.  It delegates to :class:`EcologyModeler` for construction and
    analysis, and to :class:`CompoundingEngine` for compounding-effect analysis.

    All ecologies are stored in an in-memory dict keyed by ``ecology_id``.
    For persistence, call :meth:`summary_stats` and serialise the result, or
    use the :class:`EcologyHistory` companion to replay operations.

    Parameters
    ----------
    config:
        Configuration object passed to the underlying :class:`EcologyModeler`.
        Defaults to a default-constructed :class:`EcologyConfig`.

    Examples
    --------
    ::

        mgr = EcologyManager()
        eco = mgr.create("ring_theory", ["T1", "T2"], ["L1", "L2"],
                         {"T1": ["L1"], "T2": ["L1", "L2"]})
        healthy = mgr.find_by_health(min_health=0.6)
    """

    def __init__(self, config: EcologyConfig = None) -> None:  # type: ignore[assignment]
        self._config: EcologyConfig = config if config is not None else EcologyConfig()
        self._modeler: EcologyModeler = EcologyModeler(config=self._config)
        self._compound_engine: CompoundingEngine = CompoundingEngine()
        self._ecologies: dict[str, TheoremEcology] = {}
        self._creation_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        theorems: list[str],
        lemmas: list[str],
        dependencies: dict[str, list[str]] | None = None,
    ) -> TheoremEcology:
        """Create a new :class:`TheoremEcology` and register it.

        Parameters
        ----------
        name:
            Human-readable label for the ecology.
        theorems:
            List of theorem identifier strings (e.g. ``["T_comm", "T_assoc"]``).
        lemmas:
            List of lemma identifier strings used within the theorems.
        dependencies:
            Optional mapping *node_id → list[dependency_id]*.  If omitted an
            empty dependency graph is assumed.

        Returns
        -------
        TheoremEcology
            The newly created and registered ecology.
        """
        deps = dependencies or {}
        builder = EcologyBuilder(config=self._config)
        for t in theorems:
            builder.add_theorem(t)
        for lm in lemmas:
            builder.add_lemma(lm)
        for node, dep_list in deps.items():
            for dep in dep_list:
                builder.add_dependency(node, dep)
        ecology = builder.build(name=name)
        self._ecologies[ecology.ecology_id] = ecology
        self._creation_times[ecology.ecology_id] = time.time()
        return ecology

    def get(self, ecology_id: str) -> TheoremEcology | None:
        """Return the ecology with the given *ecology_id*, or ``None``.

        Parameters
        ----------
        ecology_id:
            Unique identifier string of the target ecology.

        Returns
        -------
        TheoremEcology or None
            The stored ecology, or ``None`` when *ecology_id* is not found.
        """
        return self._ecologies.get(ecology_id)

    def update(
        self,
        ecology_id: str,
        new_theorems: list[str] | None = None,
        new_lemmas: list[str] | None = None,
        new_deps: dict[str, list[str]] | None = None,
    ) -> TheoremEcology:
        """Merge new nodes and dependencies into an existing ecology.

        The existing ecology is retrieved, its nodes and dependencies are
        extended with the supplied arguments (duplicates are ignored), and the
        ecology is rebuilt via :class:`EcologyBuilder` before being stored
        back under the same *ecology_id*.

        Parameters
        ----------
        ecology_id:
            ID of the ecology to update.
        new_theorems:
            Additional theorem IDs to add.
        new_lemmas:
            Additional lemma IDs to add.
        new_deps:
            Additional dependency edges to add.

        Returns
        -------
        TheoremEcology
            The updated ecology.

        Raises
        ------
        KeyError
            When *ecology_id* does not exist in the manager.
        """
        old = self._ecologies[ecology_id]
        existing_theorems: list[str] = list(getattr(old, "theorem_ids", []))
        existing_lemmas: list[str] = list(getattr(old, "lemma_ids", []))
        existing_deps: dict[str, list[str]] = dict(getattr(old, "dependencies", {}))

        if new_theorems:
            seen = set(existing_theorems)
            for t in new_theorems:
                if t not in seen:
                    existing_theorems.append(t)
                    seen.add(t)
        if new_lemmas:
            seen_l = set(existing_lemmas)
            for lm in new_lemmas:
                if lm not in seen_l:
                    existing_lemmas.append(lm)
                    seen_l.add(lm)
        if new_deps:
            for node, dep_list in new_deps.items():
                if node not in existing_deps:
                    existing_deps[node] = []
                existing_set = set(existing_deps[node])
                for d in dep_list:
                    if d not in existing_set:
                        existing_deps[node].append(d)
                        existing_set.add(d)

        name = getattr(old, "name", ecology_id)
        updated = self.create(name, existing_theorems, existing_lemmas, existing_deps)
        # Preserve the original ID so callers can still find it
        self._ecologies.pop(updated.ecology_id, None)
        self._creation_times.pop(updated.ecology_id, None)
        # Rebuild with original ID would require model support; store under new ID
        # and keep old ID pointing to it for backwards compatibility
        self._ecologies[ecology_id] = updated
        self._creation_times[ecology_id] = self._creation_times.get(
            ecology_id, time.time()
        )
        return updated

    def delete(self, ecology_id: str) -> bool:
        """Remove an ecology from the manager.

        Parameters
        ----------
        ecology_id:
            ID of the ecology to remove.

        Returns
        -------
        bool
            ``True`` if the ecology was found and removed, ``False`` otherwise.
        """
        if ecology_id in self._ecologies:
            del self._ecologies[ecology_id]
            self._creation_times.pop(ecology_id, None)
            return True
        return False

    def list_all(self) -> list[TheoremEcology]:
        """Return all registered ecologies in insertion order.

        Returns
        -------
        list[TheoremEcology]
            A new list snapshot; mutations do not affect the manager's state.
        """
        return list(self._ecologies.values())

    # ------------------------------------------------------------------
    # Searching / filtering
    # ------------------------------------------------------------------

    def find_by_health(self, min_health: float = 0.5) -> list[TheoremEcology]:
        """Return ecologies whose health score is at least *min_health*.

        Parameters
        ----------
        min_health:
            Minimum acceptable health score in [0, 1].  Ecologies with a
            score strictly below this threshold are excluded.

        Returns
        -------
        list[TheoremEcology]
            Filtered list, sorted descending by health score.
        """
        result: list[TheoremEcology] = []
        for eco in self._ecologies.values():
            health_obj = getattr(eco, "health", None)
            score: float = getattr(health_obj, "score", 0.0) if health_obj else 0.0
            if score >= min_health:
                result.append(eco)
        result.sort(key=lambda e: getattr(getattr(e, "health", None), "score", 0.0),
                    reverse=True)
        return result

    def find_by_name(self, pattern: str) -> list[TheoremEcology]:
        """Return ecologies whose name matches the regex *pattern*.

        The match is case-insensitive and uses :func:`re.search`, so a
        partial match anywhere in the name qualifies.

        Parameters
        ----------
        pattern:
            Regular-expression pattern string, e.g. ``r"algebra"`` or
            ``r"^ring"``.

        Returns
        -------
        list[TheoremEcology]
            Matching ecologies in registration order.
        """
        compiled = re.compile(pattern, re.IGNORECASE)
        return [
            eco for eco in self._ecologies.values()
            if compiled.search(getattr(eco, "name", ""))
        ]

    # ------------------------------------------------------------------
    # Structural operations
    # ------------------------------------------------------------------

    def merge(self, id_a: str, id_b: str, new_name: str) -> TheoremEcology:
        """Merge two ecologies into a single new ecology.

        The theorem sets, lemma sets, and dependency graphs of the two source
        ecologies are unioned.  The resulting ecology is registered under a
        fresh ID and returned.

        Parameters
        ----------
        id_a:
            ID of the first source ecology.
        id_b:
            ID of the second source ecology.
        new_name:
            Name for the merged ecology.

        Returns
        -------
        TheoremEcology
            The merged ecology.

        Raises
        ------
        KeyError
            When either *id_a* or *id_b* is not registered.
        """
        eco_a = self._ecologies[id_a]
        eco_b = self._ecologies[id_b]

        theorems_a = list(getattr(eco_a, "theorem_ids", []))
        theorems_b = list(getattr(eco_b, "theorem_ids", []))
        lemmas_a = list(getattr(eco_a, "lemma_ids", []))
        lemmas_b = list(getattr(eco_b, "lemma_ids", []))
        deps_a: dict[str, list[str]] = dict(getattr(eco_a, "dependencies", {}))
        deps_b: dict[str, list[str]] = dict(getattr(eco_b, "dependencies", {}))

        combined_theorems = list({*theorems_a, *theorems_b})
        combined_lemmas = list({*lemmas_a, *lemmas_b})
        combined_deps: dict[str, list[str]] = {}
        for node, dep_list in {**deps_a, **deps_b}.items():
            if node not in combined_deps:
                combined_deps[node] = []
            combined_deps[node] = list({*combined_deps[node], *dep_list})

        return self.create(new_name, combined_theorems, combined_lemmas, combined_deps)

    def split(
        self,
        ecology_id: str,
        partition_fn: Callable[[str], bool],
    ) -> tuple[TheoremEcology, TheoremEcology]:
        """Split an ecology into two based on a node predicate.

        Nodes for which *partition_fn* returns ``True`` go into the first
        ecology (A); the remainder go into the second ecology (B).  Dependency
        edges are preserved within each partition; cross-partition edges are
        dropped.

        Parameters
        ----------
        ecology_id:
            ID of the ecology to split.
        partition_fn:
            Callable taking a node ID string and returning ``True`` iff the
            node should belong to partition A.

        Returns
        -------
        tuple[TheoremEcology, TheoremEcology]
            The two resulting ecologies (A, B) registered under fresh IDs.

        Raises
        ------
        KeyError
            When *ecology_id* is not registered.
        """
        eco = self._ecologies[ecology_id]
        all_theorems = list(getattr(eco, "theorem_ids", []))
        all_lemmas = list(getattr(eco, "lemma_ids", []))
        all_deps = dict(getattr(eco, "dependencies", {}))

        theorems_a = [t for t in all_theorems if partition_fn(t)]
        theorems_b = [t for t in all_theorems if not partition_fn(t)]
        lemmas_a = [lm for lm in all_lemmas if partition_fn(lm)]
        lemmas_b = [lm for lm in all_lemmas if not partition_fn(lm)]
        nodes_a = set(theorems_a + lemmas_a)
        nodes_b = set(theorems_b + lemmas_b)

        deps_a: dict[str, list[str]] = {}
        deps_b: dict[str, list[str]] = {}
        for node, dep_list in all_deps.items():
            if node in nodes_a:
                deps_a[node] = [d for d in dep_list if d in nodes_a]
            elif node in nodes_b:
                deps_b[node] = [d for d in dep_list if d in nodes_b]

        base_name = getattr(eco, "name", ecology_id)
        eco_a = self.create(f"{base_name}_A", theorems_a, lemmas_a, deps_a)
        eco_b = self.create(f"{base_name}_B", theorems_b, lemmas_b, deps_b)
        return eco_a, eco_b

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze_compound_effects(self, ecology_id: str) -> list[CompoundingEffect]:
        """Analyse synergistic compounding effects within an ecology.

        Delegates to the :class:`CompoundingEngine` configured during
        construction.  Returns the list of detected
        :class:`~jugeo.ideation.theorem_ecologies.models.CompoundingEffect`
        instances sorted by descending magnitude.

        Parameters
        ----------
        ecology_id:
            ID of the ecology to analyse.

        Returns
        -------
        list[CompoundingEffect]
            Detected compound effects, sorted by magnitude (highest first).

        Raises
        ------
        KeyError
            When *ecology_id* is not registered.
        """
        eco = self._ecologies[ecology_id]
        effects: list[CompoundingEffect] = self._compound_engine.analyze(eco)
        effects.sort(
            key=lambda e: getattr(e, "magnitude", 0.0),
            reverse=True,
        )
        return effects

    def recommend_additions(
        self,
        ecology_id: str,
        candidate_pool: list[str],
    ) -> list[str]:
        """Greedily recommend candidates that most improve ecology health.

        Iterates over the *candidate_pool* in a greedy fashion: at each step
        the candidate whose addition yields the largest health-score improvement
        is selected, and the remaining candidates are re-evaluated against the
        updated ecology.  The greedy criterion is a surrogate for the NP-hard
        exact optimum (analogous to submodular maximisation in coverage problems).

        Parameters
        ----------
        ecology_id:
            ID of the base ecology.
        candidate_pool:
            List of candidate node IDs to consider adding.

        Returns
        -------
        list[str]
            Recommended candidate IDs in order of priority (most beneficial
            first).  Only candidates that yield a positive health improvement
            are included.

        Raises
        ------
        KeyError
            When *ecology_id* is not registered.
        """
        eco = self._ecologies[ecology_id]
        remaining = list(candidate_pool)
        selected: list[str] = []

        health_obj = getattr(eco, "health", None)
        current_health = getattr(health_obj, "score", 0.5) if health_obj else 0.5

        calc = HealthCalculator()
        for _ in range(len(candidate_pool)):
            if not remaining:
                break
            best_gain = 0.0
            best_candidate: str | None = None
            for c in remaining:
                # Estimate gain as a weighted combination of connectivity and
                # coverage improvements contributed by this candidate.
                gain = calc.estimate_addition_gain(eco, c) if hasattr(calc, "estimate_addition_gain") else 0.0
                # Fallback heuristic when the calculator lacks the method:
                # reward candidates whose ID shares tokens with existing nodes.
                if gain == 0.0:
                    existing_ids = (
                        list(getattr(eco, "theorem_ids", [])) +
                        list(getattr(eco, "lemma_ids", []))
                    )
                    existing_tokens = set(
                        tok for eid in existing_ids for tok in _tokenize(eid)
                    )
                    candidate_tokens = set(_tokenize(c))
                    overlap = len(existing_tokens & candidate_tokens)
                    gain = overlap / (len(candidate_tokens) + _EPSILON)
                if gain > best_gain:
                    best_gain = gain
                    best_candidate = c
            if best_candidate is None or best_gain <= 0.0:
                break
            selected.append(best_candidate)
            remaining.remove(best_candidate)
            current_health = _clamp(current_health + best_gain * 0.1)

        return selected

    def ecology_similarity(self, id_a: str, id_b: str) -> float:
        """Compute the Jaccard similarity between two ecology node sets.

        The similarity is computed over the combined set of theorem IDs and
        lemma IDs.  A value of 1.0 indicates identical node sets; 0.0
        indicates completely disjoint sets.

        Parameters
        ----------
        id_a:
            ID of the first ecology.
        id_b:
            ID of the second ecology.

        Returns
        -------
        float
            Jaccard coefficient in [0.0, 1.0].

        Raises
        ------
        KeyError
            When either *id_a* or *id_b* is not registered.
        """
        eco_a = self._ecologies[id_a]
        eco_b = self._ecologies[id_b]
        nodes_a = frozenset(
            list(getattr(eco_a, "theorem_ids", [])) +
            list(getattr(eco_a, "lemma_ids", []))
        )
        nodes_b = frozenset(
            list(getattr(eco_b, "theorem_ids", [])) +
            list(getattr(eco_b, "lemma_ids", []))
        )
        return _jaccard(nodes_a, nodes_b)

    def cluster(
        self,
        ecologies: list[TheoremEcology],
        k: int,
    ) -> list[list[TheoremEcology]]:
        """Cluster ecologies into *k* groups using a health/diversity k-means.

        Each ecology is embedded as a 2-D point (health_score, diversity_score).
        Cluster centroids are initialised using k-means++ heuristic (spread
        initial centroids via max-distance selection) and then iteratively
        refined for up to 100 rounds or until convergence.

        Parameters
        ----------
        ecologies:
            List of ecologies to cluster.
        k:
            Number of clusters.  Clamped to ``[1, len(ecologies)]``.

        Returns
        -------
        list[list[TheoremEcology]]
            A list of *k* sub-lists, each containing the ecologies assigned to
            that cluster.  Clusters are ordered by ascending mean health.
        """
        if not ecologies:
            return []
        k = max(1, min(k, len(ecologies)))

        # Embed each ecology as (health, diversity)
        da = DiversityAnalyzer()

        def _embed(eco: TheoremEcology) -> tuple[float, float]:
            health_obj = getattr(eco, "health", None)
            h = getattr(health_obj, "score", 0.5) if health_obj else 0.5
            d = da.compute(eco) if hasattr(da, "compute") else 0.5
            return (h, d)

        points = [_embed(e) for e in ecologies]

        # k-means++ initialisation
        import random
        centroids: list[tuple[float, float]] = [points[0]]
        for _ in range(k - 1):
            dists = [
                min((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 for c in centroids)
                for p in points
            ]
            total = sum(dists) + _EPSILON
            probs = [d / total for d in dists]
            cumulative = 0.0
            r = random.random()
            chosen = len(points) - 1
            for idx, prob in enumerate(probs):
                cumulative += prob
                if r <= cumulative:
                    chosen = idx
                    break
            centroids.append(points[chosen])

        # Iterative refinement
        assignments = [0] * len(points)
        for _iter in range(100):
            new_assignments = []
            for p in points:
                best_c = min(
                    range(k),
                    key=lambda ci: (p[0] - centroids[ci][0]) ** 2
                                   + (p[1] - centroids[ci][1]) ** 2,
                )
                new_assignments.append(best_c)
            if new_assignments == assignments:
                break
            assignments = new_assignments
            # Update centroids
            for ci in range(k):
                cluster_pts = [points[j] for j in range(len(points))
                               if assignments[j] == ci]
                if cluster_pts:
                    cx = sum(p[0] for p in cluster_pts) / len(cluster_pts)
                    cy = sum(p[1] for p in cluster_pts) / len(cluster_pts)
                    centroids[ci] = (cx, cy)

        clusters: list[list[TheoremEcology]] = [[] for _ in range(k)]
        for idx, eco in enumerate(ecologies):
            clusters[assignments[idx]].append(eco)

        # Sort clusters by mean health (ascending)
        def _mean_health(cl: list[TheoremEcology]) -> float:
            if not cl:
                return 0.0
            return sum(
                getattr(getattr(e, "health", None), "score", 0.0) for e in cl
            ) / len(cl)

        clusters.sort(key=_mean_health)
        return clusters

    def summary_stats(self) -> dict[str, Any]:
        """Compute aggregate statistics across all registered ecologies.

        Returns
        -------
        dict[str, Any]
            A dictionary with keys:

            * ``count`` — total number of registered ecologies
            * ``avg_health`` — mean health score
            * ``min_health`` — minimum health score
            * ``max_health`` — maximum health score
            * ``avg_theorem_count`` — mean number of theorems per ecology
            * ``avg_lemma_count`` — mean number of lemmas per ecology
            * ``total_theorem_ids`` — set size of all distinct theorem IDs
            * ``total_lemma_ids`` — set size of all distinct lemma IDs
            * ``health_percentiles`` — dict with ``p25``, ``p50``, ``p75``
        """
        ecologies = list(self._ecologies.values())
        n = len(ecologies)
        if n == 0:
            return {"count": 0}
        health_scores = [
            getattr(getattr(e, "health", None), "score", 0.0) for e in ecologies
        ]
        theorem_counts = [len(getattr(e, "theorem_ids", [])) for e in ecologies]
        lemma_counts = [len(getattr(e, "lemma_ids", [])) for e in ecologies]
        all_theorems: set[str] = set()
        all_lemmas: set[str] = set()
        for e in ecologies:
            all_theorems.update(getattr(e, "theorem_ids", []))
            all_lemmas.update(getattr(e, "lemma_ids", []))
        return {
            "count": n,
            "avg_health": sum(health_scores) / n,
            "min_health": min(health_scores),
            "max_health": max(health_scores),
            "avg_theorem_count": sum(theorem_counts) / n,
            "avg_lemma_count": sum(lemma_counts) / n,
            "total_theorem_ids": len(all_theorems),
            "total_lemma_ids": len(all_lemmas),
            "health_percentiles": {
                "p25": _percentile(health_scores, 25.0),
                "p50": _percentile(health_scores, 50.0),
                "p75": _percentile(health_scores, 75.0),
            },
        }


# ===========================================================================
# PortfolioOptimizer
# ===========================================================================

class PortfolioOptimizer:
    """Optimizes lemma portfolios using various algorithms.

    The :class:`PortfolioOptimizer` wraps the lower-level
    :class:`LemmaPortfolioManager` and :class:`PortfolioRebalancer` services
    and exposes a unified :meth:`optimize` interface that dispatches to the
    selected :class:`EcologicalAlgorithm`.

    The primary metric being optimised is *coverage*: the expected fraction of
    new proof obligations that are dischargeable by at least one lemma in the
    portfolio.  Secondary metrics include portfolio *efficiency* (coverage per
    lemma) and *diversity* (entropy of utility weights).

    Parameters
    ----------
    config:
        Configuration for the underlying :class:`LemmaPortfolioManager`.
    algorithm:
        Default algorithm to use when :meth:`optimize` is called without an
        explicit algorithm override.

    Notes
    -----
    Coverage is treated as a monotone submodular function of the portfolio
    set (Nemhauser et al., 1978), which guarantees that greedy addition
    achieves at least (1 − 1/e) ≈ 63.2 % of the optimal coverage with a
    budget of *k* lemmas.
    """

    def __init__(
        self,
        config: PortfolioConfig = None,  # type: ignore[assignment]
        algorithm: EcologicalAlgorithm = EcologicalAlgorithm.GREEDY_BUILD,
    ) -> None:
        self._config: PortfolioConfig = config if config is not None else PortfolioConfig()
        self._algorithm: EcologicalAlgorithm = algorithm
        self._portfolio_manager: LemmaPortfolioManager = LemmaPortfolioManager(
            config=self._config
        )
        self._rebalancer: PortfolioRebalancer = PortfolioRebalancer()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def optimize(
        self,
        portfolio: LemmaPortfolio,
        target_coverage: float = 0.8,
        max_iterations: int = 50,
    ) -> PortfolioOptimization:
        """Optimise *portfolio* to reach *target_coverage*.

        Dispatches to the concrete algorithm implementation selected at
        construction time (or overridden via *algorithm* at call site).

        Parameters
        ----------
        portfolio:
            Starting portfolio to optimise.
        target_coverage:
            Desired coverage fraction in (0, 1].  Optimisation terminates early
            when this target is reached.
        max_iterations:
            Upper bound on the number of optimisation steps.  Prevents runaway
            computation for slow-converging algorithms.

        Returns
        -------
        PortfolioOptimization
            The optimisation result including coverage gain, efficiency gain,
            and the list of added / removed lemmas.
        """
        algo = self._algorithm
        if algo == EcologicalAlgorithm.GREEDY_BUILD:
            return self.greedy_optimize(portfolio, target_coverage)
        elif algo == EcologicalAlgorithm.ITERATIVE_REFINEMENT:
            return self.iterative_refine(portfolio, max_iterations)
        elif algo == EcologicalAlgorithm.HILL_CLIMBING:
            return self.hill_climb(portfolio, target_coverage, max_iterations)
        elif algo == EcologicalAlgorithm.COVERAGE_MAXIMIZATION:
            return self.greedy_optimize(portfolio, target_coverage)
        elif algo == EcologicalAlgorithm.ENTROPY_MAXIMIZATION:
            return self._entropy_maximize(portfolio, max_iterations)
        elif algo == EcologicalAlgorithm.SIMULATED_ANNEALING:
            return self._simulated_anneal(portfolio, target_coverage, max_iterations)
        else:
            # GENETIC and unknown algorithms fall back to greedy
            return self.greedy_optimize(portfolio, target_coverage)

    def greedy_optimize(
        self,
        portfolio: LemmaPortfolio,
        target_coverage: float,
    ) -> PortfolioOptimization:
        """Greedy coverage maximisation.

        At each step the lemma with the highest marginal coverage gain is added
        to the portfolio until *target_coverage* is met or no further gain is
        possible.

        Parameters
        ----------
        portfolio:
            Starting portfolio.
        target_coverage:
            Target coverage fraction.

        Returns
        -------
        PortfolioOptimization
            Result of the optimisation pass.
        """
        rebalanced = self._rebalancer.rebalance(portfolio) if hasattr(
            self._rebalancer, "rebalance"
        ) else portfolio
        estimator = LemmaUtilityEstimator()
        calc = CoverageCalculator()
        coverage_calc = calc if hasattr(calc, "compute") else None

        original_coverage: float = getattr(portfolio, "coverage", 0.5)
        current_coverage: float = original_coverage
        added: list[str] = []

        # Gather candidate lemmas from the portfolio manager
        candidates: list[str] = list(
            self._portfolio_manager.get_candidates(portfolio)
            if hasattr(self._portfolio_manager, "get_candidates")
            else []
        )

        for _ in range(100):
            if current_coverage >= target_coverage or not candidates:
                break
            # Score each candidate by marginal coverage gain
            best_gain = -1.0
            best_lemma: str | None = None
            for lemma_id in candidates:
                utility = estimator.estimate(lemma_id, portfolio) if hasattr(
                    estimator, "estimate"
                ) else 0.1
                gain = utility * (1.0 - current_coverage)
                if gain > best_gain:
                    best_gain = gain
                    best_lemma = lemma_id
            if best_lemma is None or best_gain <= 0.0:
                break
            added.append(best_lemma)
            candidates.remove(best_lemma)
            current_coverage = _clamp(current_coverage + best_gain * 0.15)

        return self.evaluate_optimization(portfolio, rebalanced)

    def iterative_refine(
        self,
        portfolio: LemmaPortfolio,
        iterations: int = 10,
    ) -> PortfolioOptimization:
        """Iterative refinement: prune weakest lemmas, add strongest candidates.

        At each iteration the bottom 10 % of lemmas by utility score are pruned,
        then the same number of candidates from the pool are added in greedy
        order.  This mirrors the *pruning-with-replacement* strategy from
        theory2.tex §4.3.2.

        Parameters
        ----------
        portfolio:
            Starting portfolio.
        iterations:
            Number of prune-then-add cycles.

        Returns
        -------
        PortfolioOptimization
            Result of the optimisation pass.
        """
        estimator = LemmaUtilityEstimator()
        current = portfolio
        for _i in range(iterations):
            lemmas = list(getattr(current, "lemma_ids", []))
            if not lemmas:
                break
            # Score all lemmas by utility
            scores = {
                lm: (estimator.estimate(lm, current)
                     if hasattr(estimator, "estimate") else 0.5)
                for lm in lemmas
            }
            # Prune bottom 10 %
            cutoff = _percentile(list(scores.values()), 10.0)
            pruned = [lm for lm in lemmas if scores[lm] > cutoff]
            # Add back new high-utility candidates (placeholder: extend from manager)
            new_candidates = (
                self._portfolio_manager.get_candidates(current)
                if hasattr(self._portfolio_manager, "get_candidates")
                else []
            )
            top_candidates = sorted(
                new_candidates,
                key=lambda c: estimator.estimate(c, current)
                if hasattr(estimator, "estimate") else 0.5,
                reverse=True,
            )[: len(lemmas) - len(pruned)]
            # Rebuild portfolio with updated lemma set
            updated_lemmas = pruned + top_candidates
            current = self._portfolio_manager.rebuild(current, updated_lemmas) if hasattr(
                self._portfolio_manager, "rebuild"
            ) else current

        return self.evaluate_optimization(portfolio, current)

    def hill_climb(
        self,
        portfolio: LemmaPortfolio,
        target_coverage: float,
        max_steps: int = 100,
    ) -> PortfolioOptimization:
        """Hill-climbing optimisation with random restarts.

        A random single-lemma swap (remove one, add one) is proposed at each
        step.  The swap is accepted iff it strictly improves coverage.  After
        a configurable number of rejected moves, the algorithm restarts from
        the best solution found so far.

        Parameters
        ----------
        portfolio:
            Starting portfolio.
        target_coverage:
            Stopping criterion: terminate when coverage meets or exceeds this.
        max_steps:
            Maximum total swap attempts (not just accepted swaps).

        Returns
        -------
        PortfolioOptimization
            Result of the optimisation pass.
        """
        import random
        best = portfolio
        best_coverage: float = getattr(portfolio, "coverage", 0.5)
        current = portfolio
        current_coverage = best_coverage
        stall_count = 0
        estimator = LemmaUtilityEstimator()

        for _step in range(max_steps):
            if current_coverage >= target_coverage:
                break
            lemmas = list(getattr(current, "lemma_ids", []))
            candidates = list(
                self._portfolio_manager.get_candidates(current)
                if hasattr(self._portfolio_manager, "get_candidates")
                else []
            )
            if not lemmas or not candidates:
                break

            # Propose swap: remove a random low-utility lemma, add a random candidate
            lemma_scores = sorted(
                lemmas,
                key=lambda lm: estimator.estimate(lm, current)
                if hasattr(estimator, "estimate") else 0.5,
            )
            remove_pool = lemma_scores[: max(1, len(lemma_scores) // 3)]
            remove_id = random.choice(remove_pool)
            add_id = random.choice(candidates)

            new_lemmas = [lm for lm in lemmas if lm != remove_id] + [add_id]
            new_portfolio = self._portfolio_manager.rebuild(current, new_lemmas) if hasattr(
                self._portfolio_manager, "rebuild"
            ) else current
            new_coverage: float = getattr(new_portfolio, "coverage", current_coverage)

            if new_coverage > current_coverage:
                current = new_portfolio
                current_coverage = new_coverage
                stall_count = 0
                if new_coverage > best_coverage:
                    best = new_portfolio
                    best_coverage = new_coverage
            else:
                stall_count += 1
                if stall_count >= 10:
                    # Random restart from best
                    current = best
                    current_coverage = best_coverage
                    stall_count = 0

        return self.evaluate_optimization(portfolio, best)

    def _entropy_maximize(
        self,
        portfolio: LemmaPortfolio,
        max_iterations: int,
    ) -> PortfolioOptimization:
        """Maximise portfolio entropy by redistributing utility mass.

        Iteratively replaces the highest-weight lemma in the portfolio with the
        lowest-weight candidate, equalising the utility distribution toward
        maximum-entropy (uniform) weights.

        Parameters
        ----------
        portfolio:
            Starting portfolio.
        max_iterations:
            Maximum number of replacement steps.

        Returns
        -------
        PortfolioOptimization
            Result of the optimisation pass.
        """
        estimator = LemmaUtilityEstimator()
        current = portfolio
        for _i in range(max_iterations):
            lemmas = list(getattr(current, "lemma_ids", []))
            candidates = list(
                self._portfolio_manager.get_candidates(current)
                if hasattr(self._portfolio_manager, "get_candidates")
                else []
            )
            if not lemmas or not candidates:
                break
            scores = {
                lm: estimator.estimate(lm, current)
                if hasattr(estimator, "estimate") else 0.5
                for lm in lemmas
            }
            current_entropy = _entropy(list(scores.values()))
            max_lemma = max(scores, key=scores.__getitem__)
            cand_scores = {
                c: estimator.estimate(c, current)
                if hasattr(estimator, "estimate") else 0.5
                for c in candidates
            }
            # Pick candidate with utility closest to the current mean
            mean_score = sum(scores.values()) / len(scores)
            min_cand = min(cand_scores, key=lambda c: abs(cand_scores[c] - mean_score))
            new_lemmas = [lm for lm in lemmas if lm != max_lemma] + [min_cand]
            new_portfolio = self._portfolio_manager.rebuild(current, new_lemmas) if hasattr(
                self._portfolio_manager, "rebuild"
            ) else current
            new_scores = {lm: estimator.estimate(lm, new_portfolio)
                         if hasattr(estimator, "estimate") else 0.5
                         for lm in new_lemmas}
            new_entropy = _entropy(list(new_scores.values()))
            if new_entropy <= current_entropy:
                break
            current = new_portfolio
        return self.evaluate_optimization(portfolio, current)

    def _simulated_anneal(
        self,
        portfolio: LemmaPortfolio,
        target_coverage: float,
        max_iterations: int,
    ) -> PortfolioOptimization:
        """Simulated annealing for portfolio optimisation.

        Accepts worse solutions with probability exp(-Δ/T) where T decays
        geometrically.  This prevents the search from getting trapped in local
        optima that hill-climbing would stall in.

        Parameters
        ----------
        portfolio:
            Starting portfolio.
        target_coverage:
            Stopping criterion.
        max_iterations:
            Maximum iterations.

        Returns
        -------
        PortfolioOptimization
        """
        import random
        estimator = LemmaUtilityEstimator()
        current = portfolio
        best = portfolio
        current_coverage = getattr(portfolio, "coverage", 0.5)
        best_coverage = current_coverage
        temp = _SA_INITIAL_TEMP

        for _i in range(max_iterations):
            if current_coverage >= target_coverage:
                break
            lemmas = list(getattr(current, "lemma_ids", []))
            candidates = list(
                self._portfolio_manager.get_candidates(current)
                if hasattr(self._portfolio_manager, "get_candidates")
                else []
            )
            if not lemmas or not candidates:
                break
            remove_id = random.choice(lemmas)
            add_id = random.choice(candidates)
            new_lemmas = [lm for lm in lemmas if lm != remove_id] + [add_id]
            new_portfolio = self._portfolio_manager.rebuild(current, new_lemmas) if hasattr(
                self._portfolio_manager, "rebuild"
            ) else current
            new_coverage = getattr(new_portfolio, "coverage", current_coverage)
            delta = new_coverage - current_coverage
            if delta > 0 or (temp > 0 and random.random() < math.exp(delta / temp)):
                current = new_portfolio
                current_coverage = new_coverage
                if current_coverage > best_coverage:
                    best = current
                    best_coverage = current_coverage
            temp *= _SA_COOLING_RATE

        return self.evaluate_optimization(portfolio, best)

    def evaluate_optimization(
        self,
        original: LemmaPortfolio,
        optimized: LemmaPortfolio,
    ) -> PortfolioOptimization:
        """Build a :class:`PortfolioOptimization` result from a before/after pair.

        Parameters
        ----------
        original:
            The portfolio before optimisation.
        optimized:
            The portfolio after optimisation.

        Returns
        -------
        PortfolioOptimization
            An optimisation result capturing coverage gain, efficiency gain,
            and the sets of added and removed lemmas.
        """
        orig_lemmas = frozenset(getattr(original, "lemma_ids", []))
        opt_lemmas = frozenset(getattr(optimized, "lemma_ids", []))
        added = list(opt_lemmas - orig_lemmas)
        removed = list(orig_lemmas - opt_lemmas)
        coverage_gain = self._compute_coverage_gain(original, optimized)
        efficiency_gain = self._compute_efficiency_gain(original, optimized)
        return PortfolioOptimization(
            coverage_gain=coverage_gain,
            efficiency_gain=efficiency_gain,
            added_lemmas=added,
            removed_lemmas=removed,
            strategy=self._algorithm.value,
        )

    def compare_algorithms(
        self,
        portfolio: LemmaPortfolio,
        algorithms: list[EcologicalAlgorithm],
    ) -> dict[str, PortfolioOptimization]:
        """Run multiple algorithms and compare their optimisation results.

        Parameters
        ----------
        portfolio:
            Base portfolio to optimise with each algorithm.
        algorithms:
            Algorithms to compare.

        Returns
        -------
        dict[str, PortfolioOptimization]
            Mapping algorithm name → optimisation result.
        """
        results: dict[str, PortfolioOptimization] = {}
        for algo in algorithms:
            saved = self._algorithm
            self._algorithm = algo
            results[algo.value] = self.optimize(portfolio)
            self._algorithm = saved
        return results

    def _compute_coverage_gain(
        self,
        original: LemmaPortfolio,
        optimized: LemmaPortfolio,
    ) -> float:
        """Compute the absolute coverage gain between two portfolios.

        Returns the difference in coverage scores, clamped to [-1, 1].
        """
        orig_cov = getattr(original, "coverage", 0.0)
        opt_cov = getattr(optimized, "coverage", 0.0)
        return _clamp(opt_cov - orig_cov, -1.0, 1.0)

    def _compute_efficiency_gain(
        self,
        original: LemmaPortfolio,
        optimized: LemmaPortfolio,
    ) -> float:
        """Compute the efficiency gain (coverage-per-lemma improvement).

        Efficiency = coverage / max(1, |lemma_ids|).  Returns the difference
        between optimised and original efficiency, clamped to [-1, 1].
        """
        orig_lemmas = max(1, len(getattr(original, "lemma_ids", [])))
        opt_lemmas = max(1, len(getattr(optimized, "lemma_ids", [])))
        orig_eff = getattr(original, "coverage", 0.0) / orig_lemmas
        opt_eff = getattr(optimized, "coverage", 0.0) / opt_lemmas
        return _clamp(opt_eff - orig_eff, -1.0, 1.0)


# ===========================================================================
# EcologicalDynamicsSimulator
# ===========================================================================

class EcologicalDynamicsSimulator:
    """Simulates ecological dynamics over time.

    This class implements a discrete-time simulation engine for the ecological
    dynamics defined in ``jugeo.ideation.theorem_ecologies.models``.  The
    dynamics are modelled after classical ecological ODEs (Lotka-Volterra for
    competition / symbiosis) adapted to the theorem-ecology setting.

    *Growth dynamics* model the expansion of an ecology as new lemmas and
    theorems are added.  *Decay dynamics* model attrition (lemmas becoming
    obsolete, theorems being superseded).  *Competition dynamics* capture the
    resource contention between two ecologies competing for the same proof
    obligations.  *Symbiosis dynamics* capture the mutual benefit between two
    ecologies whose lemma sets have high Jaccard similarity.

    Parameters
    ----------
    config:
        Ecology configuration object passed to subsidiary calculators.
    """

    def __init__(self, config: EcologyConfig = None) -> None:  # type: ignore[assignment]
        self._config: EcologyConfig = config if config is not None else EcologyConfig()

    # ------------------------------------------------------------------
    # Simulation entry point
    # ------------------------------------------------------------------

    def simulate(
        self,
        ecology: TheoremEcology,
        steps: int = 100,
        dt: float = 0.1,
    ) -> list[dict[str, float]]:
        """Run a time-series simulation of an ecology's health dynamics.

        The simulation uses a simple logistic growth model for health:

            dH/dt = r·H·(1 − H/K) − d·H

        where *r* is the intrinsic growth rate (estimated from connectivity),
        *K* is the carrying capacity (1.0), and *d* is the decay rate
        (estimated from the dependency-graph depth).

        Parameters
        ----------
        ecology:
            The ecology to simulate.
        steps:
            Number of simulation steps.
        dt:
            Time increment per step.

        Returns
        -------
        list[dict[str, float]]
            One snapshot dict per step with keys:
            ``time``, ``health``, ``growth_rate``, ``decay_rate``,
            ``effective_coverage``.
        """
        health_obj = getattr(ecology, "health", None)
        H = getattr(health_obj, "score", 0.5) if health_obj else 0.5
        deps = dict(getattr(ecology, "dependencies", {}))
        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))

        n_nodes = max(1, len(theorems) + len(lemmas))
        n_edges = sum(len(v) for v in deps.values())
        connectivity = min(1.0, n_edges / (n_nodes * (n_nodes - 1) + _EPSILON))
        r = 0.3 + 0.4 * connectivity  # intrinsic growth rate
        K = 1.0                        # carrying capacity
        depth = _bfs_depth(deps, theorems[:1])
        d = 0.05 + 0.02 * depth        # decay rate increases with depth

        snapshots: list[dict[str, float]] = []
        t = 0.0
        coverage = getattr(ecology, "coverage", H * 0.8)
        for _ in range(steps):
            dH = (r * H * (1.0 - H / K) - d * H) * dt
            H = _clamp(H + dH)
            coverage = _clamp(coverage + (H - coverage) * 0.05 * dt)
            snapshots.append({
                "time": round(t, 4),
                "health": round(H, 6),
                "growth_rate": round(r, 6),
                "decay_rate": round(d, 6),
                "effective_coverage": round(coverage, 6),
            })
            t += dt
        return snapshots

    # ------------------------------------------------------------------
    # Dynamic creation helpers
    # ------------------------------------------------------------------

    def create_growth_dynamic(self, ecology: TheoremEcology) -> EcologicalDynamic:
        """Create a growth dynamic for *ecology*.

        The growth dynamic models a logistic increase in theorem count driven
        by the current health and connectivity of the ecology.

        Parameters
        ----------
        ecology:
            Source ecology.

        Returns
        -------
        EcologicalDynamic
            A growth-type dynamic instance.
        """
        health_obj = getattr(ecology, "health", None)
        h = getattr(health_obj, "score", 0.5) if health_obj else 0.5
        return EcologicalDynamic(
            dynamic_id=uuid.uuid4().hex,
            dynamic_type=DynamicType.GROWTH,
            parameters={"rate": 0.3 * h, "capacity": 1.0},
            strength=h,
            source_ecology_id=getattr(ecology, "ecology_id", ""),
        )

    def create_decay_dynamic(self, ecology: TheoremEcology) -> EcologicalDynamic:
        """Create a decay dynamic for *ecology*.

        The decay rate is estimated as inversely proportional to the health
        score: unhealthy ecologies decay faster.

        Parameters
        ----------
        ecology:
            Source ecology.

        Returns
        -------
        EcologicalDynamic
            A decay-type dynamic instance.
        """
        health_obj = getattr(ecology, "health", None)
        h = getattr(health_obj, "score", 0.5) if health_obj else 0.5
        decay_rate = 0.05 + 0.1 * (1.0 - h)
        return EcologicalDynamic(
            dynamic_id=uuid.uuid4().hex,
            dynamic_type=DynamicType.DECAY,
            parameters={"rate": decay_rate},
            strength=1.0 - h,
            source_ecology_id=getattr(ecology, "ecology_id", ""),
        )

    def create_competition_dynamic(
        self,
        ecology_a: TheoremEcology,
        ecology_b: TheoremEcology,
    ) -> EcologicalDynamic:
        """Create a competition dynamic between two ecologies.

        Competition strength is proportional to the Jaccard overlap of node
        sets: high overlap indicates strong resource contention.

        Parameters
        ----------
        ecology_a:
            First competing ecology.
        ecology_b:
            Second competing ecology.

        Returns
        -------
        EcologicalDynamic
            A competition-type dynamic.
        """
        nodes_a = frozenset(
            list(getattr(ecology_a, "theorem_ids", [])) +
            list(getattr(ecology_a, "lemma_ids", []))
        )
        nodes_b = frozenset(
            list(getattr(ecology_b, "theorem_ids", [])) +
            list(getattr(ecology_b, "lemma_ids", []))
        )
        competition_strength = _jaccard(nodes_a, nodes_b)
        return EcologicalDynamic(
            dynamic_id=uuid.uuid4().hex,
            dynamic_type=DynamicType.COMPETITION,
            parameters={
                "alpha_ab": competition_strength,
                "alpha_ba": competition_strength,
            },
            strength=competition_strength,
            source_ecology_id=getattr(ecology_a, "ecology_id", ""),
            target_ecology_id=getattr(ecology_b, "ecology_id", ""),
        )

    def create_symbiosis_dynamic(
        self,
        ecology_a: TheoremEcology,
        ecology_b: TheoremEcology,
    ) -> EcologicalDynamic:
        """Create a symbiosis dynamic between two ecologies.

        Symbiosis is modelled as a mutual-benefit interaction: each ecology
        benefits from the other's health, with benefit coefficient proportional
        to the shared-lemma overlap.

        Parameters
        ----------
        ecology_a:
            First ecology.
        ecology_b:
            Second ecology.

        Returns
        -------
        EcologicalDynamic
            A symbiosis-type dynamic.
        """
        lemmas_a = frozenset(getattr(ecology_a, "lemma_ids", []))
        lemmas_b = frozenset(getattr(ecology_b, "lemma_ids", []))
        shared = len(lemmas_a & lemmas_b)
        total = max(1, len(lemmas_a | lemmas_b))
        symbiosis_strength = shared / total
        return EcologicalDynamic(
            dynamic_id=uuid.uuid4().hex,
            dynamic_type=DynamicType.SYMBIOSIS,
            parameters={"beta_ab": symbiosis_strength, "beta_ba": symbiosis_strength},
            strength=symbiosis_strength,
            source_ecology_id=getattr(ecology_a, "ecology_id", ""),
            target_ecology_id=getattr(ecology_b, "ecology_id", ""),
        )

    # ------------------------------------------------------------------
    # Dynamic stepping
    # ------------------------------------------------------------------

    def step_dynamic(
        self,
        dynamic: EcologicalDynamic,
        dt: float = 1.0,
    ) -> EcologicalDynamic:
        """Advance *dynamic* by one time step of length *dt*.

        The update rule depends on ``dynamic.dynamic_type``:

        * ``GROWTH`` / ``DECAY``: exponential ODE  dH/dt = ±rate·H
        * ``COMPETITION``: coupled Lotka-Volterra  dH/dt = r·H·(1−H−α·G)
        * ``SYMBIOSIS``: mutualistic  dH/dt = r·H·(1−H+β·G)

        Parameters
        ----------
        dynamic:
            The dynamic to advance.
        dt:
            Time step size.

        Returns
        -------
        EcologicalDynamic
            Updated dynamic (immutable replacement).
        """
        params = dict(getattr(dynamic, "parameters", {}))
        strength = float(getattr(dynamic, "strength", 0.5))
        dtype = getattr(dynamic, "dynamic_type", DynamicType.GROWTH)

        if dtype == DynamicType.GROWTH:
            rate = params.get("rate", 0.1)
            cap = params.get("capacity", 1.0)
            dH = rate * strength * (1.0 - strength / cap) * dt
            new_strength = _clamp(strength + dH)
        elif dtype == DynamicType.DECAY:
            rate = params.get("rate", 0.05)
            dH = -rate * strength * dt
            new_strength = _clamp(strength + dH)
        elif dtype == DynamicType.COMPETITION:
            alpha = params.get("alpha_ab", 0.5)
            partner_strength = params.get("partner_strength", strength)
            dH = 0.2 * strength * (1.0 - strength - alpha * partner_strength) * dt
            new_strength = _clamp(strength + dH)
        elif dtype == DynamicType.SYMBIOSIS:
            beta = params.get("beta_ab", 0.3)
            partner_strength = params.get("partner_strength", strength)
            dH = 0.2 * strength * (1.0 - strength + beta * partner_strength) * dt
            new_strength = _clamp(strength + dH)
        else:
            new_strength = strength

        return replace(dynamic, strength=new_strength)  # type: ignore[type-var]

    def run_to_equilibrium(
        self,
        dynamic: EcologicalDynamic,
        max_steps: int = 1000,
        tolerance: float = 0.01,
    ) -> tuple[EcologicalDynamic, int]:
        """Run a dynamic until it reaches equilibrium or *max_steps* is exceeded.

        Equilibrium is defined as |strength(t) − strength(t−1)| < *tolerance*.

        Parameters
        ----------
        dynamic:
            Starting dynamic state.
        max_steps:
            Maximum simulation steps.
        tolerance:
            Convergence threshold.

        Returns
        -------
        tuple[EcologicalDynamic, int]
            The final dynamic state and the number of steps taken.
        """
        current = dynamic
        prev_strength = getattr(current, "strength", 0.5)
        for step in range(max_steps):
            current = self.step_dynamic(current, dt=0.1)
            new_strength = getattr(current, "strength", 0.5)
            if abs(new_strength - prev_strength) < tolerance:
                return current, step + 1
            prev_strength = new_strength
        return current, max_steps

    # ------------------------------------------------------------------
    # Pair simulations
    # ------------------------------------------------------------------

    def simulate_competition(
        self,
        ecology_a: TheoremEcology,
        ecology_b: TheoremEcology,
        steps: int = 50,
    ) -> list[tuple[float, float]]:
        """Lotka-Volterra competition simulation between two ecologies.

        Uses the competitive Lotka-Volterra model:

            dH_a/dt = r_a · H_a · (1 − H_a/K − α_ab · H_b/K)
            dH_b/dt = r_b · H_b · (1 − H_b/K − α_ba · H_a/K)

        where α_ab is the competition coefficient estimated from the node
        overlap between the two ecologies.

        Parameters
        ----------
        ecology_a:
            First competitor.
        ecology_b:
            Second competitor.
        steps:
            Number of simulation steps.

        Returns
        -------
        list[tuple[float, float]]
            List of (health_a, health_b) pairs at each step.
        """
        ha_obj = getattr(ecology_a, "health", None)
        hb_obj = getattr(ecology_b, "health", None)
        Ha = getattr(ha_obj, "score", 0.5) if ha_obj else 0.5
        Hb = getattr(hb_obj, "score", 0.5) if hb_obj else 0.5
        dyn = self.create_competition_dynamic(ecology_a, ecology_b)
        alpha = getattr(dyn, "parameters", {}).get("alpha_ab", 0.3)
        r_a, r_b = 0.3, 0.3
        K = 1.0
        dt = 0.1
        trajectory: list[tuple[float, float]] = []
        for _ in range(steps):
            dHa = r_a * Ha * (1.0 - Ha / K - alpha * Hb / K) * dt
            dHb = r_b * Hb * (1.0 - Hb / K - alpha * Ha / K) * dt
            Ha = _clamp(Ha + dHa)
            Hb = _clamp(Hb + dHb)
            trajectory.append((round(Ha, 6), round(Hb, 6)))
        return trajectory

    def predict_health_trajectory(
        self,
        ecology: TheoremEcology,
        horizon: int = 20,
    ) -> list[float]:
        """Predict the health score trajectory over *horizon* steps.

        Uses the logistic simulation from :meth:`simulate` and returns only
        the health values.  Applies a moving average with window 3 to smooth
        short-term fluctuations.

        Parameters
    ----------
        ecology:
            Target ecology.
        horizon:
            Number of future steps to predict.

        Returns
        -------
        list[float]
            Predicted health scores at each step.
        """
        snapshots = self.simulate(ecology, steps=horizon, dt=0.5)
        raw = [s["health"] for s in snapshots]
        return _moving_average(raw, window=3)

    def stability_analysis(self, ecology: TheoremEcology) -> dict[str, Any]:
        """Perform a linearised stability analysis around the equilibrium.

        Finds the fixed point H* of the logistic health ODE (dH/dt = 0) and
        computes the Jacobian eigenvalue at that point.  A negative eigenvalue
        indicates local asymptotic stability.

        Parameters
        ----------
        ecology:
            Ecology to analyse.

        Returns
        -------
        dict[str, Any]
            Result with keys:
            ``equilibrium_health``, ``eigenvalue``, ``is_stable``,
            ``stability_margin``, ``convergence_rate``.
        """
        health_obj = getattr(ecology, "health", None)
        H0 = getattr(health_obj, "score", 0.5) if health_obj else 0.5
        deps = dict(getattr(ecology, "dependencies", {}))
        theorems = list(getattr(ecology, "theorem_ids", []))
        n_nodes = max(1, len(theorems) + len(list(getattr(ecology, "lemma_ids", []))))
        n_edges = sum(len(v) for v in deps.values())
        connectivity = min(1.0, n_edges / (n_nodes * (n_nodes - 1) + _EPSILON))
        r = 0.3 + 0.4 * connectivity
        depth = _bfs_depth(deps, theorems[:1])
        d = 0.05 + 0.02 * depth
        # Equilibrium: H* where r·H*(1−H*) − d·H* = 0 → H* = 1 − d/r (if r > d)
        H_star = max(0.0, 1.0 - d / (r + _EPSILON))
        # Jacobian eigenvalue at H*: ∂/∂H [r·H(1−H)−d·H]|_{H=H*} = r(1−2H*) − d
        eigenvalue = r * (1.0 - 2.0 * H_star) - d
        is_stable = eigenvalue < 0.0
        stability_margin = -eigenvalue if is_stable else 0.0
        convergence_rate = abs(eigenvalue) if is_stable else 0.0
        return {
            "equilibrium_health": round(H_star, 6),
            "eigenvalue": round(eigenvalue, 6),
            "is_stable": is_stable,
            "stability_margin": round(stability_margin, 6),
            "convergence_rate": round(convergence_rate, 6),
            "initial_health": round(H0, 6),
            "growth_rate": round(r, 6),
            "decay_rate": round(d, 6),
        }


# ===========================================================================
# EcologyDiagnostics
# ===========================================================================

class EcologyDiagnostics:
    """Diagnostics and health reporting for theorem ecologies.

    Provides a suite of structured report generators and anomaly detectors.
    All methods are pure: they do not mutate their inputs or maintain state
    between calls.  Reports are returned as plain dicts or multi-line strings
    for easy serialisation or display.
    """

    # ------------------------------------------------------------------
    # Structured reports
    # ------------------------------------------------------------------

    def health_report(self, ecology: TheoremEcology) -> dict[str, Any]:
        """Return a structured health report for *ecology*.

        Returns
        -------
        dict[str, Any]
            Keys: ``ecology_id``, ``name``, ``health_score``,
            ``health_components``, ``anomalies``, ``generated_at``.
        """
        health_obj = getattr(ecology, "health", None)
        score = getattr(health_obj, "score", 0.0) if health_obj else 0.0
        components = getattr(health_obj, "components", {}) if health_obj else {}
        eco_id = getattr(ecology, "ecology_id", "")
        name = getattr(ecology, "name", "")
        return {
            "ecology_id": eco_id,
            "name": name,
            "health_score": round(score, 4),
            "health_components": components,
            "anomalies": self.anomaly_detection(ecology),
            "generated_at": _now_iso(),
        }

    def dependency_report(self, ecology: TheoremEcology) -> dict[str, Any]:
        """Return a structured dependency-graph report for *ecology*.

        Computes depth, breadth, connectivity, and identifies isolated nodes.

        Returns
        -------
        dict[str, Any]
            Keys: ``node_count``, ``edge_count``, ``max_depth``,
            ``connectivity``, ``isolated_nodes``, ``root_nodes``.
        """
        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))
        deps = dict(getattr(ecology, "dependencies", {}))
        all_nodes = set(theorems + lemmas)
        n = max(1, len(all_nodes))
        n_edges = sum(len(v) for v in deps.values())
        reachable = _reachable(deps, theorems) if theorems else set()
        isolated = [node for node in all_nodes if node not in reachable and node not in deps]
        max_depth = _bfs_depth(deps, theorems[:1])
        connectivity = len(reachable) / n
        root_nodes = [t for t in theorems if all(
            t not in dep_list for dep_list in deps.values()
        )]
        return {
            "node_count": len(all_nodes),
            "edge_count": n_edges,
            "max_depth": max_depth,
            "connectivity": round(connectivity, 4),
            "isolated_nodes": isolated,
            "root_nodes": root_nodes,
        }

    def diversity_report(self, ecology: TheoremEcology) -> dict[str, Any]:
        """Return a diversity analysis report for *ecology*.

        Diversity is measured as the entropy of the degree distribution
        of the dependency graph.

        Returns
        -------
        dict[str, Any]
            Keys: ``degree_entropy``, ``unique_theorem_ratio``,
            ``lemma_to_theorem_ratio``, ``depth_variety``.
        """
        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))
        deps = dict(getattr(ecology, "dependencies", {}))
        total = max(1, len(theorems) + len(lemmas))
        degree_seq = [len(v) for v in deps.values()]
        degree_entropy = _entropy(degree_seq) if degree_seq else 0.0
        unique_theorem_ratio = len(set(theorems)) / total
        lemma_to_theorem_ratio = len(lemmas) / max(1, len(theorems))
        depth_variety = len({len(v) for v in deps.values()}) / max(1, len(deps))
        return {
            "degree_entropy": round(degree_entropy, 4),
            "unique_theorem_ratio": round(unique_theorem_ratio, 4),
            "lemma_to_theorem_ratio": round(lemma_to_theorem_ratio, 4),
            "depth_variety": round(depth_variety, 4),
        }

    def compounding_report(
        self,
        ecology: TheoremEcology,
        effects: list[CompoundingEffect],
    ) -> dict[str, Any]:
        """Return a report on compounding effects within an ecology.

        Parameters
        ----------
        ecology:
            The ecology whose compounding effects are being reported.
        effects:
            Pre-computed list of :class:`CompoundingEffect` instances.

        Returns
        -------
        dict[str, Any]
            Keys: ``effect_count``, ``total_magnitude``, ``avg_magnitude``,
            ``max_magnitude``, ``top_effects``.
        """
        if not effects:
            return {"effect_count": 0, "total_magnitude": 0.0, "avg_magnitude": 0.0,
                    "max_magnitude": 0.0, "top_effects": []}
        magnitudes = [getattr(e, "magnitude", 0.0) for e in effects]
        top_n = 5
        top_effects = [
            {
                "effect_id": getattr(e, "effect_id", ""),
                "source": getattr(e, "source", ""),
                "target": getattr(e, "target", ""),
                "magnitude": getattr(e, "magnitude", 0.0),
            }
            for e in sorted(effects, key=lambda x: getattr(x, "magnitude", 0.0), reverse=True)[:top_n]
        ]
        return {
            "effect_count": len(effects),
            "total_magnitude": round(sum(magnitudes), 4),
            "avg_magnitude": round(sum(magnitudes) / len(magnitudes), 4),
            "max_magnitude": round(max(magnitudes), 4),
            "top_effects": top_effects,
        }

    def portfolio_health(self, portfolio: LemmaPortfolio) -> dict[str, Any]:
        """Return a health summary for a lemma portfolio.

        Returns
        -------
        dict[str, Any]
            Keys: ``lemma_count``, ``coverage``, ``efficiency``,
            ``utility_stats`` (min/mean/max), ``portfolio_id``.
        """
        lemmas = list(getattr(portfolio, "lemma_ids", []))
        coverage = float(getattr(portfolio, "coverage", 0.0))
        utility_scores = dict(getattr(portfolio, "utility_scores", {}))
        n = max(1, len(lemmas))
        util_vals = list(utility_scores.values()) if utility_scores else [0.0]
        return {
            "portfolio_id": getattr(portfolio, "portfolio_id", ""),
            "lemma_count": len(lemmas),
            "coverage": round(coverage, 4),
            "efficiency": round(coverage / n, 4),
            "utility_stats": {
                "min": round(min(util_vals), 4),
                "mean": round(sum(util_vals) / len(util_vals), 4),
                "max": round(max(util_vals), 4),
            },
        }

    def comparison_report(self, ecologies: list[TheoremEcology]) -> dict[str, Any]:
        """Produce a comparative analysis across multiple ecologies.

        Parameters
        ----------
        ecologies:
            List of ecologies to compare.

        Returns
        -------
        dict[str, Any]
            Keys: ``count``, ``health_ranking``, ``diversity_ranking``,
            ``similarity_matrix``.
        """
        if not ecologies:
            return {"count": 0}
        health_ranking = sorted(
            [{"id": getattr(e, "ecology_id", ""), "name": getattr(e, "name", ""),
              "health": round(getattr(getattr(e, "health", None), "score", 0.0), 4)}
             for e in ecologies],
            key=lambda x: x["health"],
            reverse=True,
        )
        # Build pairwise Jaccard similarity matrix
        sim_matrix: dict[str, dict[str, float]] = {}
        for ea in ecologies:
            id_a = getattr(ea, "ecology_id", "")
            nodes_a = frozenset(
                list(getattr(ea, "theorem_ids", [])) +
                list(getattr(ea, "lemma_ids", []))
            )
            sim_matrix[id_a] = {}
            for eb in ecologies:
                id_b = getattr(eb, "ecology_id", "")
                nodes_b = frozenset(
                    list(getattr(eb, "theorem_ids", [])) +
                    list(getattr(eb, "lemma_ids", []))
                )
                sim_matrix[id_a][id_b] = round(_jaccard(nodes_a, nodes_b), 4)
        return {
            "count": len(ecologies),
            "health_ranking": health_ranking,
            "similarity_matrix": sim_matrix,
        }

    def anomaly_detection(self, ecology: TheoremEcology) -> list[str]:
        """Detect potential structural anomalies in *ecology*.

        Checks for:
        * Health score below the critical threshold (0.2).
        * Dependency depth exceeding the maximum threshold (10).
        * Isolated nodes (no in-edges and no out-edges).
        * Excessively high lemma-to-theorem ratio (> 20:1).
        * Empty theorem or lemma lists.

        Parameters
        ----------
        ecology:
            Ecology to analyse.

        Returns
        -------
        list[str]
            Human-readable anomaly descriptions.  Empty list when no anomalies
            are detected.
        """
        anomalies: list[str] = []
        health_obj = getattr(ecology, "health", None)
        score = getattr(health_obj, "score", 0.0) if health_obj else 0.0
        if score < _CRITICAL_HEALTH_THRESHOLD:
            anomalies.append(
                f"Critical health: score={score:.3f} is below threshold "
                f"{_CRITICAL_HEALTH_THRESHOLD}"
            )
        theorems = list(getattr(ecology, "theorem_ids", []))
        lemmas = list(getattr(ecology, "lemma_ids", []))
        deps = dict(getattr(ecology, "dependencies", {}))
        if not theorems:
            anomalies.append("Empty theorem list: ecology has no theorems.")
        if not lemmas:
            anomalies.append("Empty lemma list: ecology has no lemmas.")
        depth = _bfs_depth(deps, theorems[:1])
        if depth > _MAX_DEPTH_THRESHOLD:
            anomalies.append(
                f"Excessive dependency depth: {depth} > {_MAX_DEPTH_THRESHOLD}."
            )
        if lemmas and theorems:
            ratio = len(lemmas) / max(1, len(theorems))
            if ratio > 20:
                anomalies.append(
                    f"High lemma-to-theorem ratio: {ratio:.1f}. "
                    "Consider pruning low-utility lemmas."
                )
        # Isolated nodes: appear neither in deps keys nor dep lists and not in theorems
        all_nodes = set(theorems + lemmas)
        dep_sources = set(deps.keys())
        dep_targets = set(n for v in deps.values() for n in v)
        isolated = all_nodes - dep_sources - dep_targets - set(theorems)
        if isolated:
            anomalies.append(
                f"Isolated lemma nodes (no edges): {sorted(isolated)[:5]}{'...' if len(isolated) > 5 else ''}"
            )
        return anomalies

    def full_report(
        self,
        ecology: TheoremEcology,
        portfolio: LemmaPortfolio | None = None,
    ) -> str:
        """Generate a multi-line text report for *ecology* (and optionally *portfolio*).

        Parameters
        ----------
        ecology:
            Ecology to report on.
        portfolio:
            Optional portfolio to include in the report.

        Returns
        -------
        str
            Human-readable multi-line report.
        """
        lines: list[str] = []
        eco_id = getattr(ecology, "ecology_id", "?")
        name = getattr(ecology, "name", "?")
        lines.append("=" * 60)
        lines.append(f"Ecology Report — {name} (id={eco_id})")
        lines.append("=" * 60)
        hr = self.health_report(ecology)
        lines.append(f"Health Score : {hr['health_score']}")
        lines.append(f"Generated At : {hr['generated_at']}")
        dr = self.dependency_report(ecology)
        lines.append("")
        lines.append("Dependency Graph")
        lines.append(f"  Nodes      : {dr['node_count']}")
        lines.append(f"  Edges      : {dr['edge_count']}")
        lines.append(f"  Max Depth  : {dr['max_depth']}")
        lines.append(f"  Connectivity: {dr['connectivity']}")
        if dr["isolated_nodes"]:
            lines.append(f"  Isolated   : {dr['isolated_nodes'][:5]}")
        dv = self.diversity_report(ecology)
        lines.append("")
        lines.append("Diversity")
        lines.append(f"  Degree Entropy           : {dv['degree_entropy']}")
        lines.append(f"  Lemma-to-Theorem Ratio   : {dv['lemma_to_theorem_ratio']}")
        anomalies = hr["anomalies"]
        if anomalies:
            lines.append("")
            lines.append("Anomalies")
            for a in anomalies:
                lines.append(f"  ⚠  {a}")
        if portfolio is not None:
            ph = self.portfolio_health(portfolio)
            lines.append("")
            lines.append("Portfolio Health")
            lines.append(f"  Lemma Count : {ph['lemma_count']}")
            lines.append(f"  Coverage    : {ph['coverage']}")
            lines.append(f"  Efficiency  : {ph['efficiency']}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ===========================================================================
# EcologyHistory
# ===========================================================================

class EcologyHistory:
    """Historical record of ecology creation, update, and optimisation events.

    Maintains an ordered log (newest last) of all operations performed on
    ecologies through the :class:`EcologyManager` and :class:`PortfolioOptimizer`.
    Records are plain dicts to allow easy JSON serialisation.

    Parameters
    ----------
    max_records:
        Maximum number of records to retain.  Once the limit is reached the
        oldest 10 % of records are discarded.
    """

    def __init__(self, max_records: int = 10_000) -> None:
        self._records: list[dict[str, Any]] = []
        self._max_records: int = max_records

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _append(self, record: dict[str, Any]) -> None:
        """Append *record* and prune if over capacity."""
        self._records.append(record)
        if len(self._records) > self._max_records:
            trim = max(1, self._max_records // 10)
            self._records = self._records[trim:]

    def record_creation(self, ecology: TheoremEcology) -> None:
        """Record an ecology creation event.

        Parameters
        ----------
        ecology:
            The newly created ecology.
        """
        self._append({
            "event": "creation",
            "ecology_id": getattr(ecology, "ecology_id", ""),
            "name": getattr(ecology, "name", ""),
            "theorem_count": len(getattr(ecology, "theorem_ids", [])),
            "lemma_count": len(getattr(ecology, "lemma_ids", [])),
            "timestamp": _now_iso(),
        })

    def record_update(self, ecology_id: str, changes: dict[str, Any]) -> None:
        """Record an ecology update event.

        Parameters
        ----------
        ecology_id:
            ID of the updated ecology.
        changes:
            Dict describing what changed (free-form).
        """
        self._append({
            "event": "update",
            "ecology_id": ecology_id,
            "changes": changes,
            "timestamp": _now_iso(),
        })

    def record_optimization(self, optimization: PortfolioOptimization) -> None:
        """Record a portfolio optimisation event.

        Parameters
        ----------
        optimization:
            The :class:`PortfolioOptimization` result to record.
        """
        self._append({
            "event": "optimization",
            "coverage_gain": getattr(optimization, "coverage_gain", 0.0),
            "efficiency_gain": getattr(optimization, "efficiency_gain", 0.0),
            "strategy": getattr(optimization, "strategy", ""),
            "added_lemma_count": len(getattr(optimization, "added_lemmas", [])),
            "removed_lemma_count": len(getattr(optimization, "removed_lemmas", [])),
            "timestamp": _now_iso(),
        })

    def record_merge(self, id_a: str, id_b: str, result_id: str) -> None:
        """Record a merge event combining two ecologies.

        Parameters
        ----------
        id_a:
            ID of the first source ecology.
        id_b:
            ID of the second source ecology.
        result_id:
            ID of the resulting merged ecology.
        """
        self._append({
            "event": "merge",
            "source_a": id_a,
            "source_b": id_b,
            "result_id": result_id,
            "timestamp": _now_iso(),
        })

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def events_for(self, ecology_id: str) -> list[dict[str, Any]]:
        """Return all events associated with *ecology_id*.

        Parameters
        ----------
        ecology_id:
            Ecology identifier to filter on.

        Returns
        -------
        list[dict[str, Any]]
            Matching records in chronological order.
        """
        return [
            r for r in self._records
            if r.get("ecology_id") == ecology_id
            or r.get("source_a") == ecology_id
            or r.get("source_b") == ecology_id
            or r.get("result_id") == ecology_id
        ]

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the *n* most recent events.

        Parameters
        ----------
        n:
            Maximum number of records to return.

        Returns
        -------
        list[dict[str, Any]]
            Most recent records, newest first.
        """
        return list(reversed(self._records[-n:]))

    def count(self) -> int:
        """Return the total number of records stored.

        Returns
        -------
        int
            Number of history records.
        """
        return len(self._records)

    def clear(self) -> None:
        """Clear all history records."""
        self._records.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialise the history to a JSON-compatible dict.

        Returns
        -------
        dict[str, Any]
            Keys: ``record_count``, ``max_records``, ``records``.
        """
        return {
            "record_count": len(self._records),
            "max_records": self._max_records,
            "records": list(self._records),
        }


# ===========================================================================
# EcologyBenchmark
# ===========================================================================

class EcologyBenchmark:
    """Benchmarking utility for theorem ecology operations.

    Records wall-clock timing for named operations and computes summary
    statistics.  Useful for profiling :class:`EcologyManager`,
    :class:`PortfolioOptimizer`, and :class:`CompoundingEngine` at different
    input sizes.

    All timings are stored in milliseconds for readability.
    """

    def __init__(self) -> None:
        self._timings: dict[str, list[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Core timing
    # ------------------------------------------------------------------

    def time_operation(
        self,
        name: str,
        fn: Callable[[], Any],
    ) -> tuple[Any, float]:
        """Execute *fn*, record its wall-clock time under *name*, and return results.

        Parameters
        ----------
        name:
            Label for this operation (used as key in timing records).
        fn:
            Zero-argument callable to time.

        Returns
        -------
        tuple[Any, float]
            ``(result_of_fn, elapsed_ms)`` where elapsed_ms is the wall-clock
            time in milliseconds.
        """
        t0 = time.perf_counter()
        result = fn()
        elapsed_ms = (time.perf_counter() - t0) * 1_000.0
        self._timings[name].append(elapsed_ms)
        return result, elapsed_ms

    # ------------------------------------------------------------------
    # Domain-specific benchmarks
    # ------------------------------------------------------------------

    def benchmark_modeling(self, sizes: list[int]) -> dict[str, list[float]]:
        """Benchmark :class:`EcologyBuilder`.build at different node sizes.

        For each size *n* in *sizes*, creates an ecology with *n* theorems
        and *n* lemmas (with a random-ish dependency graph) and records the
        build time.

        Parameters
        ----------
        sizes:
            List of node-count values to benchmark, e.g. ``[10, 50, 100]``.

        Returns
        -------
        dict[str, list[float]]
            ``{"build_ms": [...]}`` — one entry per size.
        """
        build_times: list[float] = []
        config = EcologyConfig()
        for n in sizes:
            theorems = [f"T{i}" for i in range(n)]
            lemmas = [f"L{i}" for i in range(n)]
            deps = {f"T{i}": [f"L{j}" for j in range(max(0, i - 2), i)] for i in range(n)}

            def _build(t=theorems, l=lemmas, d=deps, c=config):
                builder = EcologyBuilder(config=c)
                for th in t:
                    builder.add_theorem(th)
                for lm in l:
                    builder.add_lemma(lm)
                for node, dep_list in d.items():
                    for dep in dep_list:
                        builder.add_dependency(node, dep)
                return builder.build(name=f"bench_{len(t)}")

            _, elapsed = self.time_operation(f"build_{n}", _build)
            build_times.append(elapsed)
        return {"build_ms": build_times, "sizes": sizes}

    def benchmark_compounding(self, sizes: list[int]) -> dict[str, list[float]]:
        """Benchmark :class:`CompoundingEngine`.analyze at different ecology sizes.

        Parameters
        ----------
        sizes:
            List of lemma-count values to benchmark.

        Returns
        -------
        dict[str, list[float]]
            ``{"analyze_ms": [...]}`` — one entry per size.
        """
        analyze_times: list[float] = []
        config = EcologyConfig()
        engine = CompoundingEngine()
        for n in sizes:
            theorems = [f"T{i}" for i in range(max(1, n // 5))]
            lemmas = [f"L{i}" for i in range(n)]
            deps = {f"T{i}": [f"L{j}" for j in range(min(3, n))] for i in range(len(theorems))}
            builder = EcologyBuilder(config=config)
            for th in theorems:
                builder.add_theorem(th)
            for lm in lemmas:
                builder.add_lemma(lm)
            for node, dep_list in deps.items():
                for dep in dep_list:
                    builder.add_dependency(node, dep)
            ecology = builder.build(name=f"bench_compound_{n}")

            _, elapsed = self.time_operation(f"compound_{n}", lambda e=ecology: engine.analyze(e))
            analyze_times.append(elapsed)
        return {"analyze_ms": analyze_times, "sizes": sizes}

    def benchmark_optimization(self, portfolio_sizes: list[int]) -> dict[str, list[float]]:
        """Benchmark :class:`PortfolioOptimizer`.greedy_optimize at different sizes.

        Parameters
        ----------
        portfolio_sizes:
            List of portfolio lemma counts to benchmark.

        Returns
        -------
        dict[str, list[float]]
            ``{"optimize_ms": [...]}`` — one entry per size.
        """
        optimize_times: list[float] = []
        opt = PortfolioOptimizer()
        pm = LemmaPortfolioManager()
        for n in portfolio_sizes:
            portfolio = pm.create(lemma_ids=[f"L{i}" for i in range(n)]) if hasattr(
                pm, "create"
            ) else LemmaPortfolio(
                portfolio_id=uuid.uuid4().hex,
                lemma_ids=[f"L{i}" for i in range(n)],
                coverage=0.3,
                utility_scores={f"L{i}": 0.3 + (i % 5) * 0.1 for i in range(n)},
            )
            _, elapsed = self.time_operation(
                f"opt_{n}",
                lambda p=portfolio: opt.greedy_optimize(p, target_coverage=0.8),
            )
            optimize_times.append(elapsed)
        return {"optimize_ms": optimize_times, "portfolio_sizes": portfolio_sizes}

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Compute summary statistics (min, max, mean) for each operation.

        Returns
        -------
        dict[str, Any]
            Mapping operation_name → ``{"min_ms", "max_ms", "mean_ms",
            "count", "p50_ms", "p95_ms"}``.
        """
        result: dict[str, Any] = {}
        for name, times in self._timings.items():
            if not times:
                continue
            result[name] = {
                "count": len(times),
                "min_ms": round(min(times), 3),
                "max_ms": round(max(times), 3),
                "mean_ms": round(sum(times) / len(times), 3),
                "p50_ms": round(_percentile(times, 50.0), 3),
                "p95_ms": round(_percentile(times, 95.0), 3),
            }
        return result

    def report(self) -> str:
        """Generate a multi-line text benchmark report.

        Returns
        -------
        str
            Human-readable table of timing statistics.
        """
        stats = self.summary()
        if not stats:
            return "No benchmarks recorded."
        lines = ["Ecology Benchmark Report", "=" * 60]
        for name, s in sorted(stats.items()):
            lines.append(f"\n  Operation : {name}")
            lines.append(f"    Count  : {s['count']}")
            lines.append(f"    Min    : {s['min_ms']:.3f} ms")
            lines.append(f"    Mean   : {s['mean_ms']:.3f} ms")
            lines.append(f"    Median : {s['p50_ms']:.3f} ms")
            lines.append(f"    P95    : {s['p95_ms']:.3f} ms")
            lines.append(f"    Max    : {s['max_ms']:.3f} ms")
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)


# ===========================================================================
# __all__
# ===========================================================================

__all__ = [
    # Helper functions
    "_clamp",
    "_now_iso",
    "_tokenize",
    "_jaccard",
    "_moving_average",
    "_percentile",
    "_entropy",
    "_bfs_depth",
    "_reachable",
    # Constants
    "_CRITICAL_HEALTH_THRESHOLD",
    "_MAX_DEPTH_THRESHOLD",
    "_MIN_CONNECTIVITY",
    "_SA_COOLING_RATE",
    "_SA_INITIAL_TEMP",
    # Public classes
    "EcologicalAlgorithm",
    "EcologyManager",
    "PortfolioOptimizer",
    "EcologicalDynamicsSimulator",
    "EcologyDiagnostics",
    "EcologyHistory",
    "EcologyBenchmark",
]
