"""Semantic compounding algorithms (theory2.tex Ch61 §3).

Module layout::

    CompoundingConfig        – configuration for compounding algorithms
    CompoundingDetector      – detects compounding opportunities
    SynergyEstimator         – estimates synergy between theorem/lemma nodes
    CompoundBuilder          – builds CompoundingEffect instances
    AmplificationCalculator  – calculates amplification factors
    CompoundingEngine        – orchestrates full compounding pipeline

Theory Background
=================

*Semantic compounding* occurs when two or more theorems or lemmas interact
to produce an insight whose value exceeds the sum of their individual
contributions.  Formally, given nodes A and B with individual utilities
u(A) and u(B), the compound A⊕B has value:

    v(A⊕B) = (u(A) + u(B)) × α(A, B)

where α(A, B) ≥ 1.0 is the *amplification factor*.  The amplification
factor depends on the *synergy* between A and B, which in turn depends on
three components:

1. **Semantic synergy** — token-vocabulary overlap between node IDs.
   Moderate overlap (complementary content) yields higher synergy than zero
   overlap (unrelated) or perfect overlap (redundant).

2. **Structural synergy** — shared dependencies between A and B.  Nodes that
   build on the same foundations are more likely to compose productively.

3. **Dependency synergy** — extra synergy awarded when one node directly or
   transitively depends on the other (they form part of a proof chain).

The ``SynergyEstimator`` implements all three components and combines them
via a weighted sum.  The ``AmplificationCalculator`` converts synergy scores
into amplification factors using a superlinear transformation calibrated so
that synergy=1.0 maps to the ``config.min_amplification`` times the size
factor.

Detection in ``CompoundingDetector`` enumerates all pairs and triples whose
combined synergy exceeds ``min_synergy_threshold``.  For chain detection,
the algorithm follows the dependency graph from a starting node up to
``max_chain`` hops, building compound effects along each path segment.

The ``CompoundingEngine`` caches results keyed by ecology ID to avoid
recomputing expensive pairwise synergy matrices on repeated calls.  Callers
may invalidate the cache by calling ``apply_compound``, which modifies the
ecology and forces re-analysis on the next ``analyze`` call.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Callable, Iterable, Mapping, Sequence

from jugeo.ideation.theorem_ecologies.models import (
    CompoundingEffect,
    TheoremEcology,
    LemmaPortfolio,
    EcologyHealth,
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _tokenize(text: str) -> frozenset[str]:
    """Tokenise *text* into lowercase alphabetic words of length >= 2."""
    return frozenset(w for w in re.split(r"[^a-z]+", text.lower()) if len(w) >= 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _uid() -> str:
    return str(uuid.uuid4())


def _sigmoid(x: float) -> float:
    """Standard logistic sigmoid."""
    return 1.0 / (1.0 + math.exp(-x))


def _softplus(x: float) -> float:
    """Softplus activation: log(1 + exp(x)), smooth approximation to ReLU."""
    return math.log1p(math.exp(max(-88.0, x)))


# ---------------------------------------------------------------------------
# CompoundingConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompoundingConfig:
    """Configuration for semantic compounding algorithms.

    Attributes
    ----------
    min_synergy_threshold:
        Minimum synergy for a pair/triple to be considered a compound.
    min_amplification:
        Minimum amplification factor; compounds below this are discarded.
    max_sources:
        Maximum number of source nodes in a single compound.
    confidence_floor:
        Minimum confidence for any detected effect.
    compound_decay:
        Per-hop decay factor for dependency-chain compounds.
    pair_weight:
        Weight applied to pairwise synergy in the group estimate.
    triple_weight:
        Weight applied to triple-wise synergy in the group estimate.
    higher_weight:
        Weight applied to higher-order synergy (n >= 4).
    jaccard_synergy_scale:
        Scaling constant for converting Jaccard overlap to synergy.
        Higher values amplify moderate overlap; lower values flatten it.
    """

    min_synergy_threshold: float = 0.3
    min_amplification: float = 1.1
    max_sources: int = 10
    confidence_floor: float = 0.1
    compound_decay: float = 0.05
    pair_weight: float = 0.4
    triple_weight: float = 0.35
    higher_weight: float = 0.25
    jaccard_synergy_scale: float = 2.0

    def __post_init__(self) -> None:
        if self.min_synergy_threshold < 0 or self.min_synergy_threshold > 1:
            raise ValueError("min_synergy_threshold must be in [0, 1]")
        if self.min_amplification < 1.0:
            raise ValueError("min_amplification must be >= 1.0")
        if self.max_sources < 2:
            raise ValueError("max_sources must be >= 2")
        total_w = self.pair_weight + self.triple_weight + self.higher_weight
        if abs(total_w - 1.0) > 0.05:
            raise ValueError(
                f"pair_weight + triple_weight + higher_weight must sum to ~1.0; got {total_w:.4f}"
            )

    def weight_for_size(self, n: int) -> float:
        """Return the appropriate weight for a compound of size *n*."""
        if n == 2:
            return self.pair_weight
        elif n == 3:
            return self.triple_weight
        else:
            return self.higher_weight

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_synergy_threshold": self.min_synergy_threshold,
            "min_amplification": self.min_amplification,
            "max_sources": self.max_sources,
            "confidence_floor": self.confidence_floor,
            "compound_decay": self.compound_decay,
            "pair_weight": self.pair_weight,
            "triple_weight": self.triple_weight,
            "higher_weight": self.higher_weight,
            "jaccard_synergy_scale": self.jaccard_synergy_scale,
        }


# ---------------------------------------------------------------------------
# CompoundingDetector
# ---------------------------------------------------------------------------

class CompoundingDetector:
    """Detects compounding opportunities in theorem/lemma collections.

    The detector enumerates candidate groups of 2 or 3 nodes from a
    ``TheoremEcology`` and scores each group's synergy using a
    ``SynergyEstimator``.  Groups whose synergy exceeds
    ``config.min_synergy_threshold`` are returned as ``CompoundingEffect``
    instances.

    For chain detection, the dependency graph is traversed from a starting
    node, and compound effects are built for each consecutive pair of nodes
    along the path.

    Parameters
    ----------
    config:
        Configuration controlling detection thresholds.
    """

    def __init__(self, config: CompoundingConfig = CompoundingConfig()) -> None:
        self._config = config
        self._synergy = SynergyEstimator(config)
        self._builder = CompoundBuilder(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self, node_ids: list[str], ecology: TheoremEcology
    ) -> list[CompoundingEffect]:
        """Find all pairs and triples with synergy above threshold.

        Returns detected effects sorted by net_value() descending.
        """
        effects: list[CompoundingEffect] = []
        effects.extend(self.detect_pairs(node_ids, ecology))
        if len(node_ids) >= 3:
            effects.extend(self.detect_triples(node_ids, ecology))
        return self.rank_effects(effects)

    def detect_pairs(
        self, node_ids: list[str], ecology: TheoremEcology
    ) -> list[CompoundingEffect]:
        """Enumerate all pairs and return those meeting the synergy threshold."""
        effects: list[CompoundingEffect] = []
        for a, b in combinations(node_ids, 2):
            synergy = self._synergy.combined_synergy(a, b, ecology)
            if synergy >= self._config.min_synergy_threshold:
                effect = self._builder.build_from_pair(a, b, ecology)
                if effect.amplification_factor >= self._config.min_amplification:
                    effects.append(effect)
        return effects

    def detect_triples(
        self, node_ids: list[str], ecology: TheoremEcology
    ) -> list[CompoundingEffect]:
        """Enumerate all triples and return those meeting the synergy threshold."""
        effects: list[CompoundingEffect] = []
        # Limit to first 20 nodes to bound complexity O(n^3)
        capped = node_ids[:20]
        for a, b, c in combinations(capped, 3):
            synergy = self._synergy.estimate_group([a, b, c], ecology)
            if synergy >= self._config.min_synergy_threshold:
                effect = self._builder.build((a, b, c), ecology, synergy)
                if effect.amplification_factor >= self._config.min_amplification:
                    effects.append(effect)
        return effects

    def detect_chains(
        self,
        start_id: str,
        ecology: TheoremEcology,
        max_chain: int = 5,
    ) -> list[CompoundingEffect]:
        """Follow dependency chains from *start_id* and build compound effects.

        For each consecutive pair (A, B) in the chain where B depends on A,
        a chain-type compound is created.
        """
        adj: dict[str, list[str]] = {k: list(v) for k, v in ecology.dependencies.items()}
        effects: list[CompoundingEffect] = []
        visited: set[str] = set()
        queue: deque[tuple[str, list[str]]] = deque([(start_id, [start_id])])

        while queue:
            node, path = queue.popleft()
            if node in visited or len(path) > max_chain:
                continue
            visited.add(node)

            for neighbour in adj.get(node, []):
                new_path = path + [neighbour]
                if len(new_path) >= 2:
                    # Build a chain compound for the last two nodes
                    effect = self._builder.build_from_chain(new_path[-2:], ecology)
                    if effect.amplification_factor >= self._config.min_amplification:
                        effects.append(effect)
                if len(new_path) <= max_chain:
                    queue.append((neighbour, new_path))

        return effects

    def filter_significant(
        self, effects: list[CompoundingEffect]
    ) -> list[CompoundingEffect]:
        """Keep only effects with synergy >= threshold and confidence above floor."""
        return [
            e for e in effects
            if e.synergy >= self._config.min_synergy_threshold
            and e.confidence >= self._config.confidence_floor
            and e.amplification_factor >= self._config.min_amplification
        ]

    def rank_effects(
        self, effects: list[CompoundingEffect]
    ) -> list[CompoundingEffect]:
        """Sort effects by net_value() descending."""
        return sorted(effects, key=lambda e: e.net_value(), reverse=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _node_similarity(
        self, a: str, b: str, ecology: TheoremEcology
    ) -> float:
        """Token-based Jaccard similarity between two node IDs."""
        return _jaccard(_tokenize(a), _tokenize(b))

    def _generate_compound_result(
        self, sources: list[str], ecology: TheoremEcology
    ) -> str:
        """Generate a human-readable description of the compound result."""
        names = [s.replace("_", " ").title() for s in sources[:3]]
        if len(sources) > 3:
            names.append(f"and {len(sources) - 3} more")
        return "Compound(" + " ⊕ ".join(names) + ")"


# ---------------------------------------------------------------------------
# SynergyEstimator
# ---------------------------------------------------------------------------

class SynergyEstimator:
    """Estimates synergy between theorem and lemma nodes.

    Synergy is a [0, 1] value representing how much the combination of nodes
    exceeds their individual contributions.  Three orthogonal synergy signals
    are combined:

    1. **Semantic synergy** — vocabulary overlap of node IDs.
    2. **Dependency synergy** — extra credit for dependency relationships.
    3. **Structural synergy** — shared dependencies in the ecology.

    Parameters
    ----------
    config:
        Configuration controlling synergy weights and scaling.
    """

    def __init__(self, config: CompoundingConfig = CompoundingConfig()) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate_pair(
        self, id_a: str, id_b: str, ecology: TheoremEcology
    ) -> float:
        """Estimate synergy between exactly two nodes.  Returns [0, 1]."""
        return self.combined_synergy(id_a, id_b, ecology)

    def estimate_group(
        self, ids: list[str], ecology: TheoremEcology
    ) -> float:
        """Estimate synergy for a group by averaging pairwise synergies."""
        if len(ids) < 2:
            return 0.0
        pairs = list(combinations(ids, 2))
        total = sum(self.combined_synergy(a, b, ecology) for a, b in pairs)
        return _clamp(total / len(pairs))

    def dependency_synergy(
        self, id_a: str, id_b: str, ecology: TheoremEcology
    ) -> float:
        """Extra synergy when one node depends on the other.

        Direct dependency: bonus of 0.4.
        Indirect (depth-2) dependency: bonus of 0.2.
        No dependency: 0.0.
        """
        deps_a = set(ecology.dependencies.get(id_a, ()))
        deps_b = set(ecology.dependencies.get(id_b, ()))

        if id_b in deps_a or id_a in deps_b:
            return 0.4

        # Check depth-2 dependency
        deps_of_deps_a: set[str] = set()
        for dep in deps_a:
            deps_of_deps_a.update(ecology.dependencies.get(dep, ()))
        deps_of_deps_b: set[str] = set()
        for dep in deps_b:
            deps_of_deps_b.update(ecology.dependencies.get(dep, ()))

        if id_b in deps_of_deps_a or id_a in deps_of_deps_b:
            return 0.2
        return 0.0

    def semantic_synergy(self, id_a: str, id_b: str) -> float:
        """Synergy based on token-vocabulary similarity of node IDs.

        The synergy function is non-monotone in overlap: maximum synergy is
        achieved at moderate overlap (complementary nodes), not at high
        overlap (redundant nodes) or zero overlap (unrelated nodes).
        The function is modelled as a scaled inverted U-shape.
        """
        overlap = _jaccard(_tokenize(id_a), _tokenize(id_b))
        # Inverted U-shape: peak at overlap ~= 0.35
        peak = 0.35
        width = 0.5
        raw = math.exp(-0.5 * ((overlap - peak) / width) ** 2)
        return _clamp(raw * self._config.jaccard_synergy_scale / 2.0)

    def structural_synergy(
        self, id_a: str, id_b: str, ecology: TheoremEcology
    ) -> float:
        """Synergy from shared dependencies (common ancestors).

        Nodes that share many dependency ancestors are likely to build on
        compatible conceptual foundations.
        """
        deps_a = set(ecology.dependencies.get(id_a, ()))
        deps_b = set(ecology.dependencies.get(id_b, ()))
        if not deps_a and not deps_b:
            return 0.0
        return _jaccard(frozenset(deps_a), frozenset(deps_b))

    def combined_synergy(
        self, id_a: str, id_b: str, ecology: TheoremEcology
    ) -> float:
        """Weighted combination of all three synergy components.

        Weights: semantic 40%, dependency 35%, structural 25%.
        """
        sem = self.semantic_synergy(id_a, id_b)
        dep = self.dependency_synergy(id_a, id_b, ecology)
        struct = self.structural_synergy(id_a, id_b, ecology)
        return _clamp(0.40 * sem + 0.35 * dep + 0.25 * struct)

    def synergy_matrix(
        self, ids: list[str], ecology: TheoremEcology
    ) -> list[list[float]]:
        """Compute a symmetric N×N pairwise synergy matrix."""
        n = len(ids)
        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            matrix[i][i] = 1.0
            for j in range(i + 1, n):
                s = self.combined_synergy(ids[i], ids[j], ecology)
                matrix[i][j] = s
                matrix[j][i] = s
        return matrix

    def top_synergies(
        self,
        ids: list[str],
        ecology: TheoremEcology,
        k: int = 10,
    ) -> list[tuple[str, str, float]]:
        """Return the *k* highest-synergy pairs as (id_a, id_b, synergy) tuples."""
        scored: list[tuple[str, str, float]] = []
        for a, b in combinations(ids, 2):
            s = self.combined_synergy(a, b, ecology)
            scored.append((a, b, s))
        scored.sort(key=lambda x: x[2], reverse=True)
        return scored[:k]


# ---------------------------------------------------------------------------
# CompoundBuilder
# ---------------------------------------------------------------------------

class CompoundBuilder:
    """Builds ``CompoundingEffect`` instances from synergistic sources.

    The builder is responsible for packaging the synergy, amplification, and
    required-condition data into immutable ``CompoundingEffect`` value objects.

    Parameters
    ----------
    config:
        Configuration for amplification bounds and compound decay.
    """

    def __init__(self, config: CompoundingConfig = CompoundingConfig()) -> None:
        self._config = config
        self._amplification = AmplificationCalculator(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        source_ids: tuple[str, ...],
        ecology: TheoremEcology,
        synergy: float,
    ) -> CompoundingEffect:
        """Create a ``CompoundingEffect`` from sources with precomputed synergy."""
        amp = self._amplification.calculate(list(source_ids), synergy, ecology)
        confidence = _clamp(synergy * 0.9 + self._config.confidence_floor)
        result = self._describe_compound(list(source_ids), ecology)
        conditions = self._required_conditions(list(source_ids), ecology)
        return CompoundingEffect(
            source_ids=source_ids,
            compound_result=result,
            synergy=synergy,
            amplification_factor=amp,
            confidence=confidence,
            required_conditions=conditions,
        )

    def build_from_pair(
        self, id_a: str, id_b: str, ecology: TheoremEcology
    ) -> CompoundingEffect:
        """Build a pairwise compound between *id_a* and *id_b*."""
        synergy_est = SynergyEstimator(self._config)
        synergy = synergy_est.combined_synergy(id_a, id_b, ecology)
        return self.build((id_a, id_b), ecology, synergy)

    def build_from_chain(
        self, chain: list[str], ecology: TheoremEcology
    ) -> CompoundingEffect:
        """Build a chain compound from a list of nodes forming a dependency path.

        The synergy decays geometrically along the chain with factor
        ``config.compound_decay``.
        """
        n = len(chain)
        if n < 2:
            raise ValueError("Chain must have at least 2 nodes")

        synergy_est = SynergyEstimator(self._config)
        # Compute pairwise synergies along the chain and apply decay
        chain_synergy = 0.0
        for i in range(n - 1):
            pair_syn = synergy_est.combined_synergy(chain[i], chain[i + 1], ecology)
            decay = (1.0 - self._config.compound_decay) ** i
            chain_synergy += pair_syn * decay
        # Normalise by chain length
        chain_synergy = _clamp(chain_synergy / (n - 1))

        amp = self._amplification.chain_amplification(n)
        confidence = _clamp(chain_synergy * 0.85)
        result = self._describe_compound(chain, ecology)
        conditions = self._required_conditions(chain, ecology)

        return CompoundingEffect(
            source_ids=tuple(chain),
            compound_result=result,
            synergy=chain_synergy,
            amplification_factor=amp,
            confidence=confidence,
            required_conditions=conditions,
        )

    def combine(
        self,
        effect_a: CompoundingEffect,
        effect_b: CompoundingEffect,
    ) -> CompoundingEffect:
        """Create a higher-order compound by combining two existing effects.

        The combined synergy is the geometric mean of the individual synergies,
        scaled by the smaller confidence.
        """
        all_sources = list(
            dict.fromkeys(list(effect_a.source_ids) + list(effect_b.source_ids))
        )
        combined_synergy = math.sqrt(effect_a.synergy * effect_b.synergy)
        combined_amp = max(
            effect_a.amplification_factor * 0.7 + effect_b.amplification_factor * 0.3,
            1.0,
        )
        combined_conf = min(effect_a.confidence, effect_b.confidence) * 0.8
        all_conditions = list(
            dict.fromkeys(
                list(effect_a.required_conditions) + list(effect_b.required_conditions)
            )
        )
        return CompoundingEffect(
            source_ids=tuple(all_sources[:self._config.max_sources]),
            compound_result=f"Compound({effect_a.compound_result} ⊕ {effect_b.compound_result})",
            synergy=_clamp(combined_synergy),
            amplification_factor=_clamp(combined_amp, 1.0, 5.0),
            confidence=_clamp(combined_conf),
            required_conditions=tuple(all_conditions[:10]),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _describe_compound(
        self, sources: list[str], ecology: TheoremEcology
    ) -> str:
        """Build a human-readable compound description."""
        names = [s.replace("_", " ").title() for s in sources[:4]]
        suffix = f" [+{len(sources) - 4} more]" if len(sources) > 4 else ""
        return "Compound(" + " ⊕ ".join(names) + suffix + ")"

    def _required_conditions(
        self, sources: list[str], ecology: TheoremEcology
    ) -> tuple[str, ...]:
        """Derive required preconditions from the sources' dependency sets."""
        conditions: list[str] = []
        for src in sources:
            for dep in ecology.dependencies.get(src, ()):
                cond = f"requires:{dep}"
                if cond not in conditions:
                    conditions.append(cond)
        return tuple(conditions[:8])


# ---------------------------------------------------------------------------
# AmplificationCalculator
# ---------------------------------------------------------------------------

class AmplificationCalculator:
    """Calculates amplification factors for compounding effects.

    The amplification factor quantifies how much a compound exceeds the sum
    of its parts.  It is always >= 1.0 (additive baseline) and is computed
    as the product of several multiplicative components:

    * **Size amplification** — more sources yield (sublinearly) higher amp.
    * **Synergy amplification** — higher synergy maps to higher amplification.
    * **Dependency amplification** — extra boost for dependency-linked nodes.

    Parameters
    ----------
    config:
        Configuration controlling minimum amplification and decay.
    """

    def __init__(self, config: CompoundingConfig = CompoundingConfig()) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate(
        self,
        source_ids: list[str],
        synergy: float,
        ecology: TheoremEcology,
    ) -> float:
        """Compute the overall amplification factor (>= 1.0)."""
        return self.total_amplification(source_ids, synergy, ecology)

    def size_amplification(self, n_sources: int) -> float:
        """Sublinear size amplification: more sources => more amplification.

        Uses square-root scaling: amp ≈ 1 + sqrt(n_sources - 1) * 0.3.
        """
        if n_sources < 2:
            return 1.0
        return 1.0 + math.sqrt(n_sources - 1) * 0.3

    def dependency_amplification(
        self, source_ids: list[str], ecology: TheoremEcology
    ) -> float:
        """Extra amplification for nodes that are in the same dependency chain.

        Counts the number of direct dependency edges among the sources and
        converts to an amplification factor.
        """
        linked = 0
        for a, b in combinations(source_ids, 2):
            deps_a = set(ecology.dependencies.get(a, ()))
            deps_b = set(ecology.dependencies.get(b, ()))
            if b in deps_a or a in deps_b:
                linked += 1
        n_pairs = max(len(source_ids) * (len(source_ids) - 1) // 2, 1)
        link_ratio = linked / n_pairs
        return 1.0 + link_ratio * 0.5

    def synergy_amplification(self, synergy: float) -> float:
        """Convert synergy score to amplification multiplier.

        Uses a softplus-based transformation so that synergy=0 gives ~1.0
        and synergy=1.0 gives approximately 1 + config.min_amplification.
        """
        scale = self._config.min_amplification - 1.0
        return 1.0 + scale * _sigmoid(synergy * 6.0 - 3.0)

    def chain_amplification(self, chain_length: int) -> float:
        """Amplification specific to dependency chains.

        Longer chains provide higher amplification up to a maximum of 2.5.
        Uses a logarithmic scale.
        """
        if chain_length < 2:
            return 1.0
        return _clamp(1.0 + math.log(chain_length) * 0.5, 1.0, 2.5)

    def total_amplification(
        self,
        source_ids: list[str],
        synergy: float,
        ecology: TheoremEcology,
    ) -> float:
        """Combined amplification with decay for large compounds.

        Applies the three multiplicative components, then scales down
        by a decay factor for each source beyond the second to prevent
        unbounded amplification on large groups.
        """
        n = len(source_ids)
        size_amp = self.size_amplification(n)
        syn_amp = self.synergy_amplification(synergy)
        dep_amp = self.dependency_amplification(source_ids, ecology)

        # Raw product
        raw = size_amp * syn_amp * dep_amp

        # Decay for large groups to prevent runaway amplification
        if n > 3:
            decay = (1.0 - self._config.compound_decay) ** (n - 3)
            raw *= decay

        return _clamp(raw, 1.0, 5.0)


# ---------------------------------------------------------------------------
# CompoundingEngine
# ---------------------------------------------------------------------------

class CompoundingEngine:
    """Orchestrates the full semantic compounding pipeline.

    The engine ties together detection, synergy estimation, building, and
    amplification into a single callable interface.  Results are cached by
    ecology ID to avoid repeated computation; the cache is invalidated when
    ``apply_compound`` is called.

    Parameters
    ----------
    config:
        Configuration for all sub-components.
    """

    def __init__(self, config: CompoundingConfig = CompoundingConfig()) -> None:
        self._config = config
        self._detector = CompoundingDetector(config)
        self._synergy_estimator = SynergyEstimator(config)
        self._builder = CompoundBuilder(config)
        self._amplification = AmplificationCalculator(config)
        self._effects_cache: dict[str, list[CompoundingEffect]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, ecology: TheoremEcology) -> list[CompoundingEffect]:
        """Run full compounding analysis on all nodes in the ecology.

        Results are cached by ecology ID.

        Returns effects ranked by net_value() descending.
        """
        eid = ecology.ecology_id
        if eid in self._effects_cache:
            return self._effects_cache[eid]

        all_nodes = list(ecology.all_node_ids)
        # Limit to manageable size for O(n^2) detection
        sample = all_nodes[:50]
        effects = self._detector.detect(sample, ecology)
        effects = self._detector.filter_significant(effects)
        self._effects_cache[eid] = effects
        return effects

    def analyze_portfolio(
        self, portfolio: LemmaPortfolio, ecology: TheoremEcology
    ) -> list[CompoundingEffect]:
        """Analyse compounding potential among lemmas in a portfolio.

        Only lemma IDs that exist in the ecology are considered.
        """
        ecology_nodes = set(ecology.all_node_ids)
        lemma_ids = [lid for lid in portfolio.lemma_ids if lid in ecology_nodes]
        if len(lemma_ids) < 2:
            return []
        return self._detector.detect(lemma_ids, ecology)

    def get_strongest(
        self, ecology: TheoremEcology, k: int = 5
    ) -> list[CompoundingEffect]:
        """Return the *k* strongest compounding effects by net_value()."""
        effects = self.analyze(ecology)
        return effects[:k]

    def compounding_potential(self, ecology: TheoremEcology) -> float:
        """Estimate overall compounding potential of the ecology in [0, 1].

        Computes the average net_value() of all detected effects, scaled
        to [0, 1] using a sigmoid transformation.
        """
        effects = self.analyze(ecology)
        if not effects:
            return 0.0
        avg_net = sum(e.net_value() for e in effects) / len(effects)
        # Sigmoid maps avg_net (which is in [0, 0.5] typically) to [0, 1]
        return _clamp(_sigmoid(avg_net * 10.0 - 3.0))

    def recommend_compounds(
        self, ecology: TheoremEcology, budget: int = 5
    ) -> list[CompoundingEffect]:
        """Recommend the top-*budget* compounds by net_value().

        Deduplicates overlapping effects so that the recommendation set
        covers as many distinct source nodes as possible.
        """
        effects = self.analyze(ecology)
        seen_sources: set[str] = set()
        recommendations: list[CompoundingEffect] = []
        for effect in effects:
            if len(recommendations) >= budget:
                break
            # Skip if this effect's sources are all already covered
            new_sources = set(effect.source_ids) - seen_sources
            if not new_sources and len(recommendations) > 0:
                continue
            recommendations.append(effect)
            seen_sources.update(effect.source_ids)
        return recommendations

    def apply_compound(
        self, effect: CompoundingEffect, ecology: TheoremEcology
    ) -> TheoremEcology:
        """Apply a compound effect to an ecology by adding a synthetic node.

        The synthetic node's ID is derived from the effect ID.  Its
        dependencies are set to the source IDs.  The cache is invalidated
        after application.
        """
        synthetic_id = f"compound_{effect.effect_id[:8]}"
        # Add synthetic node to theorem list
        new_theorems = tuple(list(ecology.theorem_ids) + [synthetic_id])
        # Add dependencies: synthetic node depends on all sources
        new_deps = dict(ecology.dependencies)
        new_deps[synthetic_id] = tuple(effect.source_ids)
        # Re-compute health proxy from existing score
        new_health = _clamp(ecology.health_score * 1.02)  # slight improvement
        new_diversity = _clamp(ecology.diversity_index * 1.01)

        updated = replace(
            ecology,
            theorem_ids=new_theorems,
            dependencies=new_deps,
            health_score=new_health,
            diversity_index=new_diversity,
        )
        # Invalidate cache
        self._effects_cache.pop(ecology.ecology_id, None)
        return updated

    def report(self, ecology: TheoremEcology) -> str:
        """Generate a multi-line human-readable compounding report."""
        effects = self.analyze(ecology)
        potential = self.compounding_potential(ecology)
        recommended = self.recommend_compounds(ecology, 5)

        lines: list[str] = [
            "=== Compounding Analysis Report ===",
            f"  Ecology:              {ecology.name} ({ecology.ecology_id})",
            f"  Total nodes:          {ecology.size}",
            f"  Detected effects:     {len(effects)}",
            f"  Compounding potential:{potential:.4f}",
            "",
            "--- Top 5 Effects ---",
        ]
        for i, eff in enumerate(effects[:5], 1):
            lines.append(
                f"  {i}. [{eff.order}-way] synergy={eff.synergy:.3f}  "
                f"amp={eff.amplification_factor:.3f}  "
                f"conf={eff.confidence:.3f}  "
                f"net={eff.net_value():.4f}"
            )
            lines.append(f"     sources: {', '.join(eff.source_ids[:4])}")
            lines.append(f"     result:  {eff.compound_result}")
        lines.append("")
        lines.append("--- Recommended Compounds ---")
        for i, eff in enumerate(recommended, 1):
            lines.append(
                f"  {i}. {eff.compound_result}  (net={eff.net_value():.4f})"
            )
        lines.append("")
        lines.append(f"  Report generated at: {_now_iso()}")
        return "\n".join(lines)

    def diagnostics(self, ecology: TheoremEcology) -> dict[str, Any]:
        """Return a diagnostics dict for the compounding analysis."""
        effects = self.analyze(ecology)
        potential = self.compounding_potential(ecology)
        synergy_matrix = self._synergy_estimator.synergy_matrix(
            list(ecology.all_node_ids)[:15], ecology
        )
        top_pairs = self._synergy_estimator.top_synergies(
            list(ecology.all_node_ids)[:20], ecology, k=5
        )
        pair_effects = [e for e in effects if e.order == 2]
        triple_effects = [e for e in effects if e.order == 3]
        higher_effects = [e for e in effects if e.order > 3]
        avg_synergy = (
            sum(e.synergy for e in effects) / len(effects) if effects else 0.0
        )
        avg_amp = (
            sum(e.amplification_factor for e in effects) / len(effects)
            if effects else 1.0
        )
        return {
            "ecology_id": ecology.ecology_id,
            "total_nodes": ecology.size,
            "total_effects": len(effects),
            "pair_effects": len(pair_effects),
            "triple_effects": len(triple_effects),
            "higher_effects": len(higher_effects),
            "compounding_potential": potential,
            "average_synergy": avg_synergy,
            "average_amplification": avg_amp,
            "top_synergy_pairs": [
                {"a": a, "b": b, "synergy": s} for a, b, s in top_pairs
            ],
            "synergy_matrix_size": len(synergy_matrix),
            "cache_entries": len(self._effects_cache),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CompoundingConfig",
    "CompoundingDetector",
    "SynergyEstimator",
    "CompoundBuilder",
    "AmplificationCalculator",
    "CompoundingEngine",
]
