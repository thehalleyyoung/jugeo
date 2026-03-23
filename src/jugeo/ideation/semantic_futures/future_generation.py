"""
jugeo.ideation.semantic_futures.future_generation
======================================================

Semantic-future generation layer for the JuGeo ideation system
(Theory Ch. 49 — Ideation as Search over Semantic Futures, §1: Generation).

Theory
------
Generation is the process of *proposing* semantic futures from the current
ideation state :math:`S_{\\text{now}}`.  A set of semantic operators
:math:`O = \\{o_1, \\ldots, o_m\\}` act on :math:`S_{\\text{now}}` to produce
a candidate set of futures:

.. math::

   F = \\{(\\Delta_i, \\rho_i) : i = 1 \\ldots n\\}

where each :math:`\\Delta_i = o_j(S_{\\text{now}})` for some
:math:`o_j \\in O`, and :math:`\\rho_i \\in [0, 1]` is a heuristic
reachability estimate computed from the semantic distance between
:math:`S_{\\text{now}}` and the proposed change.

The generation pipeline has three stages:

1. **Expansion** — apply semantic operators to produce a raw candidate
   set :math:`F_{\\text{raw}}` of size
   :math:`n \\cdot \\text{expansion\\_factor}`.

2. **Diversification** — deduplicate and promote diversity using a
   pairwise Jaccard distance over delta tokens.

3. **Pruning** — remove candidates dominated in the
   :math:`(\\rho, \\text{value})` space, or with composite value below the
   pruning threshold :math:`\\tau`.

Semantic Operators
------------------
Each operator :math:`o_i` is characterised by:

* **input\\_kind** — the semantic category it consumes.
* **output\\_kind** — the semantic category it produces.
* **cost\\_multiplier** — scaling factor applied to the base cost estimate.

The eight built-in operators cover the standard moves in mathematical
knowledge engineering:

==============  ============================================================
GENERALIZE      Lift a specific result to a more general setting.
SPECIALIZE      Instantiate a general result in a concrete special case.
BRIDGE          Connect two disjoint areas of the portfolio.
ANALOGIZE       Transport structure from a known domain by analogy.
DECOMPOSE       Split a complex statement into simpler sub-problems.
COMPOSE         Combine multiple results into a unified theorem.
REFINE          Sharpen the conditions or conclusion of an existing result.
EXTEND          Extend the range of validity of an existing result.
==============  ============================================================

Usage
-----
::

    from jugeo.ideation.semantic_futures.models import FutureState, PurposeFunction
    from jugeo.ideation.semantic_futures.future_generation import (
        FutureGenerator, GenerationConfig, GenerationStrategy,
        FutureExpander, FuturePruner,
    )

    state = FutureState(
        state_id="s0", description="Compact operators on Hilbert spaces.",
        domain="functional-analysis",
    )
    config = GenerationConfig(n_futures=8, strategy=GenerationStrategy.HYBRID)
    generator = FutureGenerator(config)
    futures = generator.generate(state, n=8)

    expander = FutureExpander(expansion_factor=2)
    expanded = expander.expand_batch(futures, state)

    purpose = PurposeFunction(domain="functional-analysis", keywords=("spectrum",))
    pruner = FuturePruner(threshold=0.1)
    pruned = pruner.prune(expanded, purpose)
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.ideation.semantic_futures.models import (
    FutureState,
    FutureTag,
    IdeationState,
    PurposeFunction,
    SemanticFuture,
)

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.ideas import IdeaProposal
    from jugeo.ideation.regimes import IdeationRegime
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.ideation.scheduling import IdeationSchedule
except ImportError:
    IdeaProposal = None  # type: ignore[assignment,misc]
    IdeationRegime = None  # type: ignore[assignment,misc]
    NoveltyScore = None  # type: ignore[assignment,misc]
    IdeationSchedule = None  # type: ignore[assignment,misc]

__all__ = [
    "GenerationStrategy",
    "GenerationConfig",
    "FutureGenerator",
    "FutureExpander",
    "FuturePruner",
    "SemanticOperator",
    "DEFAULT_OPERATORS",
]

_log = logging.getLogger(__name__)

# Qualifier adjectives used when producing delta variants.
_VARIANT_QUALIFIERS: tuple[str, ...] = (
    "generalized",
    "restricted",
    "extended",
    "approximate",
    "constructive",
    "quantitative",
    "topological",
    "asymptotic",
)


# ---------------------------------------------------------------------------
# GenerationStrategy
# ---------------------------------------------------------------------------


class GenerationStrategy(str, Enum):
    """Strategy used by FutureGenerator when producing futures."""

    RANDOM = "random"
    ANALOGICAL = "analogical"
    SEMANTIC_SHIFT = "semantic_shift"
    BRIDGE = "bridge"
    REFINEMENT = "refinement"


# ---------------------------------------------------------------------------
# GenerationConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Configuration for future generation.

    Parameters
    ----------
    n_futures:
        Number of futures to generate per call.
    expansion_factor:
        Number of variants produced per seed in FutureExpander.
    min_reachability:
        Minimum reachability threshold below which futures are considered
        non-viable.
    max_cost:
        Maximum cost allowed for generated futures.
    strategy:
        Default GenerationStrategy.
    """

    n_futures: int = 10
    expansion_factor: int = 3
    min_reachability: float = 0.1
    max_cost: float = 10.0
    strategy: GenerationStrategy = GenerationStrategy.RANDOM

    def __post_init__(self) -> None:
        if self.n_futures <= 0:
            raise ValueError(f"n_futures must be positive, got {self.n_futures}")
        if self.expansion_factor <= 0:
            raise ValueError(
                f"expansion_factor must be positive, got {self.expansion_factor}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_futures": self.n_futures,
            "expansion_factor": self.expansion_factor,
            "min_reachability": self.min_reachability,
            "max_cost": self.max_cost,
            "strategy": self.strategy.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GenerationConfig:
        return cls(
            n_futures=int(d.get("n_futures", 10)),
            expansion_factor=int(d.get("expansion_factor", 3)),
            min_reachability=float(d.get("min_reachability", 0.1)),
            max_cost=float(d.get("max_cost", 10.0)),
            strategy=GenerationStrategy(d.get("strategy", GenerationStrategy.RANDOM)),
        )


# ---------------------------------------------------------------------------
# SemanticOperator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticOperator:
    """An immutable transformation that produces a new delta from an existing one.

    Parameters
    ----------
    op_id:
        Unique operator identifier.
    name:
        Human-readable name.
    description:
        What the operator does conceptually.
    cost_multiplier:
        Scales the cost of futures produced by this operator.
    """

    op_id: str
    name: str
    description: str
    cost_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.cost_multiplier <= 0:
            raise ValueError(
                f"cost_multiplier must be positive, got {self.cost_multiplier}"
            )

    def apply(self, delta: str, state: FutureState) -> str:
        """Produce a transformed delta string.

        The transformation is determined by the operator's op_id so that
        results are consistent given the same inputs.

        Parameters
        ----------
        delta:
            The base delta to transform.
        state:
            The FutureState providing domain context.

        Returns
        -------
        str
            A non-empty transformed delta.
        """
        domain = state.domain
        base = delta.strip() or state.description

        if self.op_id == "negate":
            return f"Instead of {base}, explore the opposite: avoid or invert {base} within {domain}."
        if self.op_id == "abstract":
            return f"Generalise '{base}' to its abstract principle in the context of {domain}."
        if self.op_id == "analogy":
            return (
                f"Find an analogy to '{base}' from outside {domain} "
                f"and map it back to advance {domain}."
            )
        if self.op_id == "refine":
            return f"Refine and narrow the scope of '{base}' by adding precision in {domain}."
        if self.op_id == "extend":
            return f"Extend '{base}' by identifying the next logical step in {domain}."
        if self.op_id == "combine":
            return f"Combine '{base}' with a complementary concept from {domain}."
        if self.op_id == "bridge":
            return (
                f"Bridge '{base}' to a neighbouring domain and return insights to {domain}."
            )

        # Generic fallback for custom operators
        h = _hash_delta(f"{self.op_id}:{base}:{domain}")[:8]
        return f"[{self.name}] Apply {self.description} to '{base}' in {domain} (ref:{h})."

    def to_dict(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "name": self.name,
            "description": self.description,
            "cost_multiplier": self.cost_multiplier,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SemanticOperator:
        return cls(
            op_id=str(d["op_id"]),
            name=str(d["name"]),
            description=str(d["description"]),
            cost_multiplier=float(d.get("cost_multiplier", 1.0)),
        )


# ---------------------------------------------------------------------------
# DEFAULT_OPERATORS
# ---------------------------------------------------------------------------

DEFAULT_OPERATORS: list[SemanticOperator] = [
    SemanticOperator(
        op_id="negate",
        name="Negation",
        description="Invert or oppose the current direction to surface hidden alternatives.",
        cost_multiplier=1.0,
    ),
    SemanticOperator(
        op_id="abstract",
        name="Abstraction",
        description="Lift the delta to a higher level of generality.",
        cost_multiplier=0.8,
    ),
    SemanticOperator(
        op_id="analogy",
        name="Analogical Transfer",
        description="Map structure from an outside domain back to the target domain.",
        cost_multiplier=1.2,
    ),
    SemanticOperator(
        op_id="refine",
        name="Refinement",
        description="Narrow and sharpen the delta for precision and specificity.",
        cost_multiplier=0.9,
    ),
    SemanticOperator(
        op_id="extend",
        name="Extension",
        description="Progress the delta to its natural next step.",
        cost_multiplier=1.1,
    ),
    SemanticOperator(
        op_id="combine",
        name="Combination",
        description="Merge the delta with a complementary concept.",
        cost_multiplier=1.3,
    ),
    SemanticOperator(
        op_id="bridge",
        name="Bridge",
        description="Connect the delta to a neighbouring domain for cross-pollination.",
        cost_multiplier=1.5,
    ),
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _hash_delta(delta: str) -> str:
    """Return a deterministic hex digest for *delta*."""
    return hashlib.sha256(delta.encode("utf-8")).hexdigest()


def _diversity_score(futures: list[SemanticFuture]) -> float:
    """Return a score in [0, 1] reflecting lexical diversity of the futures.

    Uses mean pairwise Jaccard distance over unigram token sets.
    Returns 0.0 for an empty or single-element list.
    """
    if len(futures) < 2:
        return 0.0

    token_sets = [set(f.delta.lower().split()) for f in futures]

    # Check for degenerate case: all identical token sets
    total_pairs = 0
    total_jaccard = 0.0
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            a, b = token_sets[i], token_sets[j]
            union = a | b
            if not union:
                jaccard = 0.0
            else:
                jaccard = len(a & b) / len(union)
            total_jaccard += 1.0 - jaccard  # distance, not similarity
            total_pairs += 1

    return total_jaccard / total_pairs if total_pairs > 0 else 0.0


def _deduplicate_futures(futures: list[SemanticFuture]) -> list[SemanticFuture]:
    """Remove futures with duplicate delta values, keeping the first occurrence."""
    seen: set[str] = set()
    result: list[SemanticFuture] = []
    for f in futures:
        if f.delta not in seen:
            seen.add(f.delta)
            result.append(f)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _pseudo_float(seed: str, lo: float = 0.0, hi: float = 1.0) -> float:
    """Derive a pseudo-random float in [lo, hi] from a seed string."""
    digest = int(_hash_delta(seed)[:8], 16)
    return lo + (digest / 0xFFFFFFFF) * (hi - lo)


def _make_future(
    *,
    delta: str,
    source_state_id: str,
    operator: SemanticOperator | None,
    index: int,
    base_cost: float = 1.0,
    tags: tuple[FutureTag, ...] = (),
    explanation: str = "",
) -> SemanticFuture:
    """Construct a SemanticFuture with hash-derived scores."""
    reachability = _pseudo_float(f"reach:{delta}:{source_state_id}:{index}")
    alignment = _pseudo_float(f"align:{delta}:{source_state_id}:{index}")
    value = _pseudo_float(f"value:{delta}:{source_state_id}:{index}")
    cost = base_cost * (operator.cost_multiplier if operator else 1.0)
    cost = round(cost * (0.5 + _pseudo_float(f"cost:{delta}:{index}") * 1.5), 4)
    op_id = operator.op_id if operator else ""
    return SemanticFuture(
        future_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{delta}:{source_state_id}:{index}")),
        delta=delta,
        source_state_id=source_state_id,
        reachability=round(reachability, 6),
        purpose_alignment=round(alignment, 6),
        cost=round(cost, 6),
        value=round(value, 6),
        tags=tags,
        operator_id=op_id,
        explanation=explanation or f"Generated via operator '{op_id}' from state {source_state_id}.",
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# FutureGenerator
# ---------------------------------------------------------------------------


class FutureGenerator:
    """Generates SemanticFuture proposals from a FutureState.

    Parameters
    ----------
    config:
        Generation configuration; defaults to GenerationConfig().
    operators:
        Operators available for generating deltas; defaults to DEFAULT_OPERATORS.
    """

    def __init__(
        self,
        config: GenerationConfig | None = None,
        operators: list[SemanticOperator] | None = None,
    ) -> None:
        self.config = config or GenerationConfig()
        self.operators: list[SemanticOperator] = (
            list(operators) if operators is not None else list(DEFAULT_OPERATORS)
        )

    def generate(
        self,
        state: FutureState,
        n: int | None = None,
    ) -> list[SemanticFuture]:
        """Generate exactly *n* (or config.n_futures) SemanticFutures.

        Parameters
        ----------
        state:
            The FutureState from which to generate futures.
        n:
            Number of futures to produce; if ``None`` uses config.n_futures;
            if ``0`` returns an empty list.

        Returns
        -------
        list[SemanticFuture]
        """
        if n is None:
            n = self.config.n_futures
        if n == 0:
            return []

        ops = self.operators or DEFAULT_OPERATORS
        futures: list[SemanticFuture] = []
        description = state.description

        for i in range(n):
            op = ops[i % len(ops)]
            delta = op.apply(description, state)
            # Vary the delta slightly per index to avoid repetition when n > len(ops)
            if i >= len(ops):
                delta = f"{delta} [variant {i}]"
            futures.append(
                _make_future(
                    delta=delta,
                    source_state_id=state.state_id,
                    operator=op,
                    index=i,
                    base_cost=self.config.max_cost * 0.3,
                    explanation=f"Strategy {self.config.strategy.value}, step {i}.",
                )
            )

        return futures

    def generate_from_regime(
        self,
        state: FutureState,
        regime: Any,
        n: int | None = None,
    ) -> list[SemanticFuture]:
        """Generate futures informed by an optional *regime* object.

        Falls back gracefully to standard generation when *regime* is ``None``
        or does not expose a compatible interface.

        Parameters
        ----------
        state:
            Source FutureState.
        regime:
            An optional regime-like object (e.g., IdeationRegime).  Any object
            with a ``description`` or ``name`` attribute is used to bias the
            delta; otherwise generation proceeds without modification.
        n:
            Number of futures to produce.
        """
        if regime is None:
            return self.generate(state, n)

        regime_hint = ""
        for attr in ("description", "name", "label"):
            val = getattr(regime, attr, None)
            if val:
                regime_hint = str(val)
                break

        if not regime_hint:
            return self.generate(state, n)

        if n is None:
            n = self.config.n_futures
        if n == 0:
            return []

        ops = self.operators or DEFAULT_OPERATORS
        futures: list[SemanticFuture] = []
        description = f"{state.description} [regime: {regime_hint}]"

        for i in range(n):
            op = ops[i % len(ops)]
            delta = op.apply(description, state)
            if i >= len(ops):
                delta = f"{delta} [variant {i}]"
            futures.append(
                _make_future(
                    delta=delta,
                    source_state_id=state.state_id,
                    operator=op,
                    index=i,
                    base_cost=self.config.max_cost * 0.3,
                    explanation=f"Regime-informed generation '{regime_hint}', step {i}.",
                )
            )
        return futures

    def generate_analogical(
        self,
        state: FutureState,
        reference: str,
        n: int | None = None,
    ) -> list[SemanticFuture]:
        """Generate futures by analogical transfer from *reference*.

        Parameters
        ----------
        state:
            Source FutureState.
        reference:
            A reference concept or domain string to draw analogies from.
        n:
            Number of futures to produce.
        """
        if n is None:
            n = self.config.n_futures
        if n == 0:
            return []

        analogy_op = next(
            (op for op in self.operators if op.op_id == "analogy"),
            self.operators[0] if self.operators else DEFAULT_OPERATORS[0],
        )
        bridge_op = next(
            (op for op in self.operators if op.op_id == "bridge"),
            analogy_op,
        )
        futures: list[SemanticFuture] = []

        for i in range(n):
            base_delta = f"{state.description} via analogy to '{reference}'"
            op = analogy_op if i % 2 == 0 else bridge_op
            delta = op.apply(base_delta, state)
            if i >= 2:
                delta = f"{delta} [analogy variant {i}]"
            futures.append(
                _make_future(
                    delta=delta,
                    source_state_id=state.state_id,
                    operator=op,
                    index=i,
                    base_cost=self.config.max_cost * 0.35,
                    tags=(FutureTag.EXPLORATORY,),
                    explanation=f"Analogical transfer from '{reference}', step {i}.",
                )
            )
        return futures


# ---------------------------------------------------------------------------
# FutureExpander
# ---------------------------------------------------------------------------


class FutureExpander:
    """Expands a single SemanticFuture into several variants.

    Parameters
    ----------
    config:
        Configuration; defaults to GenerationConfig().
    """

    def __init__(self, config: GenerationConfig | None = None) -> None:
        self.config = config or GenerationConfig()

    def expand(
        self,
        seed: SemanticFuture,
        state: FutureState,
    ) -> list[SemanticFuture]:
        """Produce config.expansion_factor variants from *seed*.

        Each variant applies a different DEFAULT operator to the seed delta.

        Parameters
        ----------
        seed:
            The SemanticFuture to expand.
        state:
            The FutureState providing context.

        Returns
        -------
        list[SemanticFuture]
            Exactly config.expansion_factor variants.
        """
        ops = DEFAULT_OPERATORS
        variants: list[SemanticFuture] = []
        for i in range(self.config.expansion_factor):
            op = ops[i % len(ops)]
            delta = op.apply(seed.delta, state)
            if i >= len(ops):
                delta = f"{delta} [expansion {i}]"
            variants.append(
                _make_future(
                    delta=delta,
                    source_state_id=state.state_id,
                    operator=op,
                    index=i + 1000,  # offset to avoid id collisions with base generation
                    base_cost=seed.cost * op.cost_multiplier,
                    explanation=f"Expanded from future '{seed.future_id}' using {op.name}.",
                )
            )
        return variants

    def expand_batch(
        self,
        seeds: list[SemanticFuture],
        state: FutureState,
    ) -> list[SemanticFuture]:
        """Expand each seed in *seeds* and return the combined list.

        Parameters
        ----------
        seeds:
            SemanticFutures to expand.
        state:
            Source FutureState.

        Returns
        -------
        list[SemanticFuture]
        """
        result: list[SemanticFuture] = []
        for seed in seeds:
            result.extend(self.expand(seed, state))
        return result


# ---------------------------------------------------------------------------
# FuturePruner
# ---------------------------------------------------------------------------


class FuturePruner:
    """Prunes lists of SemanticFutures based on value, dominance, and budget.

    Parameters
    ----------
    min_value:
        Minimum composite_value() below which futures are removed by prune().
    max_cost:
        Maximum cost for futures to survive budget pruning.
    """

    def __init__(
        self,
        min_value: float = 0.0,
        max_cost: float = float("inf"),
    ) -> None:
        self.min_value = min_value
        self.max_cost = max_cost

    def prune(
        self,
        futures: list[SemanticFuture],
        threshold: float = 0.1,
    ) -> list[SemanticFuture]:
        """Remove futures whose composite_value() is below *threshold*.

        Parameters
        ----------
        futures:
            Candidate futures.
        threshold:
            Minimum composite value required to survive.

        Returns
        -------
        list[SemanticFuture]
        """
        return [f for f in futures if f.composite_value() >= threshold]

    def prune_dominated(
        self,
        futures: list[SemanticFuture],
    ) -> list[SemanticFuture]:
        """Remove Pareto-dominated futures.

        A future is dominated if another future dominates it in all three
        objectives: reachability, purpose_alignment, and value.

        Parameters
        ----------
        futures:
            Candidate futures.

        Returns
        -------
        list[SemanticFuture]
            Non-dominated subset.
        """
        non_dominated: list[SemanticFuture] = []
        for candidate in futures:
            dominated = any(other.dominates(candidate) for other in futures if other is not candidate)
            if not dominated:
                non_dominated.append(candidate)
        return non_dominated

    def prune_by_budget(
        self,
        futures: list[SemanticFuture],
        budget: float,
    ) -> list[SemanticFuture]:
        """Remove futures whose cost exceeds *budget*.

        Parameters
        ----------
        futures:
            Candidate futures.
        budget:
            Maximum allowed cost (inclusive).

        Returns
        -------
        list[SemanticFuture]
        """
        return [f for f in futures if f.cost <= budget]
