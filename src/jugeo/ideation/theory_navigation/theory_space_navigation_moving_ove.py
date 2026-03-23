"""Theory-space navigation: moving over candidate regimes.

This module implements the machinery for traversing the space of candidate
mathematical frameworks (regimes) during proof-construction and obstruction
resolution.  Each "regime" represents a coherent body of mathematics —
type theory, category theory, algebraic geometry, homotopy type theory, etc. —
that may supply the semantic vocabulary needed to phrase or dissolve a given
obstruction.

The central question answered here is: *given the current proof state and a
purpose-conditioned relevance signal, which candidate regime should we move to
next?*  The answer is computed through a three-stage pipeline:

1. **Scoring** — each candidate regime is assigned three independent sub-scores:
   *relevance* (overlap with the current purpose keywords), *novelty* (how
   different the candidate is from recently visited regimes), and *coverage*
   (how many of the outstanding obstruction classes the regime can handle).
   These sub-scores are combined into a single :attr:`CandidateRegime.composite_score`.

2. **Selection** — the :class:`TheorySpaceNavigationAnalyzer` applies a
   diversity-penalised ranking to pick the best next candidate, taking into
   account the current :class:`NavigationState` (depth, cumulative cost, and
   purpose alignment).

3. **Witnessing** — every regime transition is logged in the
   :class:`TheorySpaceNavigationWitness`, which provides a full audit trail of
   the navigation path, transition costs, and regime selection reasons.

The :class:`TheorySpaceNavigationCoordinator` orchestrates the full pipeline
for a configurable number of navigation steps and produces a structured report.

Module layout::

    ──────────────────────────────────────────────────────────────────────────
    Symbol                              Kind         Purpose
    ──────────────────────────────────────────────────────────────────────────
    CandidateRegimeConfig               dataclass    hyper-parameters for scoring
    CandidateRegime                     dataclass    scored candidate framework
    RegimeTransition                    dataclass    edge in the navigation path
    NavigationState (local)             dataclass    current navigator position
    TheorySpaceNavigationAnalyzer       class        scoring + selection logic
    TheorySpaceNavigationWitness        class        audit trail of transitions
    TheorySpaceNavigationCoordinator    class        orchestrator / entry-point
    ──────────────────────────────────────────────────────────────────────────

Private helpers::

    _clamp(v, lo, hi)                   clamp float to [lo, hi]
    _now_iso()                          current UTC time as ISO-8601 string
    _regime_id()                        generate a short random regime ID
    _composite_score(r,n,c,wr,wn,wc)   weighted composite of sub-scores
    _tokenize(text)                     split text into a set of lowercase tokens
    _jaccard(a, b)                      Jaccard similarity between two token sets

# copilot: theory-space navigation — candidate regime selection and traversal

Reference: theory2.tex §§ theory-space navigation, purpose-conditioned search.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Optional cross-package imports
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.theory_navigation.models import (
        TheoryNode,
        TheorySpace,
        NavigationStrategy,
        PurposeCondition,
        NodeMaturity,
    )
except ImportError:
    TheoryNode = None  # type: ignore[assignment,misc]
    TheorySpace = None  # type: ignore[assignment,misc]
    NavigationStrategy = None  # type: ignore[assignment,misc]
    PurposeCondition = None  # type: ignore[assignment,misc]
    NodeMaturity = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    v:
        The value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to 0.0.
    hi:
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        The clamped value.

    Examples
    --------
    >>> _clamp(-0.3)
    0.0
    >>> _clamp(1.7)
    1.0
    >>> _clamp(0.5)
    0.5
    >>> _clamp(2.5, 0.0, 2.0)
    2.0
    """
    return max(lo, min(hi, v))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        Timestamp string, e.g. ``'2024-01-15T12:00:00+00:00'``.

    Examples
    --------
    >>> ts = _now_iso()
    >>> isinstance(ts, str) and "T" in ts
    True
    """
    return datetime.now(timezone.utc).isoformat()


def _regime_id() -> str:
    """Generate a short random regime identifier.

    Returns a ``'reg-'`` prefixed 8-character hex string drawn from a new
    UUID4, giving 2^32 possible IDs while remaining human-readable in logs.

    Returns
    -------
    str
        A string of the form ``'reg-xxxxxxxx'``.

    Examples
    --------
    >>> rid = _regime_id()
    >>> rid.startswith("reg-")
    True
    >>> len(rid) == 12
    True
    """
    return f"reg-{uuid.uuid4().hex[:8]}"


def _composite_score(
    relevance: float,
    novelty: float,
    coverage: float,
    w_relevance: float,
    w_novelty: float,
    w_coverage: float,
) -> float:
    """Compute a normalised weighted composite of three sub-scores.

    The raw weighted sum is clamped to [0, 1] to guard against floating-point
    drift when weights do not sum exactly to 1.0.

    Parameters
    ----------
    relevance:
        Relevance sub-score in [0, 1].
    novelty:
        Novelty sub-score in [0, 1].
    coverage:
        Coverage sub-score in [0, 1].
    w_relevance:
        Weight for the relevance component.
    w_novelty:
        Weight for the novelty component.
    w_coverage:
        Weight for the coverage component.

    Returns
    -------
    float
        Weighted composite in [0, 1].

    Examples
    --------
    >>> _composite_score(0.8, 0.5, 0.6, 0.5, 0.2, 0.3)
    0.65
    """
    raw = w_relevance * relevance + w_novelty * novelty + w_coverage * coverage
    return _clamp(raw)


def _tokenize(text: str) -> set[str]:
    """Tokenise *text* into a set of lowercase alphabetic tokens.

    Strips punctuation, splits on non-alphabetic characters, lower-cases all
    tokens, and discards single-character tokens.

    Parameters
    ----------
    text:
        Raw text to tokenise.

    Returns
    -------
    set[str]
        Normalised word tokens with length > 1.

    Examples
    --------
    >>> sorted(_tokenize("Type theory is great!"))
    ['great', 'is', 'theory', 'type']
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
        ``|a ∩ b| / |a ∪ b|``, or 0.0 when both sets are empty.

    Examples
    --------
    >>> _jaccard({"a", "b", "c"}, {"b", "c", "d"})
    0.5
    >>> _jaccard(set(), set())
    0.0
    """
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateRegimeConfig:
    """Hyper-parameters that govern candidate regime scoring and selection.

    All weights must be non-negative; they need not sum to 1.0 because the
    composite scorer normalises them implicitly via :func:`_composite_score`.

    Attributes
    ----------
    max_candidates:
        Maximum number of candidate regimes to maintain in the ranked list at
        any one navigation step.  Defaults to 20.
    diversity_weight:
        Weight applied to the novelty sub-score when computing the composite.
        Higher values encourage exploration of less-visited areas.
        Defaults to 0.3.
    relevance_weight:
        Weight applied to the relevance sub-score.  Higher values keep the
        navigator close to the current purpose keywords.  Defaults to 0.5.
    novelty_weight:
        Weight applied to the novelty sub-score (alias of *diversity_weight*
        kept for interface symmetry).  Defaults to 0.2.
    min_coverage:
        Minimum coverage score a candidate must achieve to be included in the
        ranked list.  Candidates below this threshold are silently discarded.
        Defaults to 0.1.

    Examples
    --------
    >>> cfg = CandidateRegimeConfig()
    >>> cfg.max_candidates
    20
    >>> cfg.relevance_weight + cfg.diversity_weight + cfg.novelty_weight
    1.0
    """

    max_candidates: int = 20
    diversity_weight: float = 0.3
    relevance_weight: float = 0.5
    novelty_weight: float = 0.2
    min_coverage: float = 0.1


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateRegime:
    """A scored candidate mathematical framework for the next navigation step.

    Instances are produced by :meth:`TheorySpaceNavigationAnalyzer.score_candidate`
    and are immutable — create a new instance if a score must be updated.

    Attributes
    ----------
    regime_id:
        Unique identifier, typically generated by :func:`_regime_id`.
    name:
        Human-readable name, e.g. ``'Homotopy Type Theory'``.
    area:
        Broad mathematical area label, e.g. ``'type_theory'``.
    relevance_score:
        Overlap between the regime's vocabulary and the current purpose
        keywords.  In [0, 1].
    novelty_score:
        Dissimilarity from recently visited regimes.  In [0, 1].
    coverage_score:
        Fraction of outstanding obstruction classes the regime can handle.
        In [0, 1].
    composite_score:
        Weighted combination of the three sub-scores.  In [0, 1].
    description:
        Short free-text summary of what makes this regime a good candidate.
    """

    regime_id: str
    name: str
    area: str
    relevance_score: float
    novelty_score: float
    coverage_score: float
    composite_score: float
    description: str


@dataclass(frozen=True, slots=True)
class RegimeTransition:
    """A directed edge in the navigation path representing a regime change.

    Attributes
    ----------
    from_regime:
        Identifier of the regime being left.  May be ``'__start__'`` for the
        initial transition.
    to_regime:
        Identifier of the regime being entered.
    transition_cost:
        Non-negative cost of making this transition.  Computed by
        :meth:`TheorySpaceNavigationAnalyzer.compute_transition_cost`.
    reason:
        Human-readable explanation of why this transition was chosen.
    timestamp:
        ISO-8601 UTC timestamp when the transition was recorded.
    """

    from_regime: str
    to_regime: str
    transition_cost: float
    reason: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class NavigationState:
    """Snapshot of the navigator's position in theory space at a single step.

    This is a *local* dataclass used only within this module.  It intentionally
    mirrors the structure of the shared ``NavigationState`` from ``models.py``
    but is kept separate to avoid tight coupling.

    Attributes
    ----------
    current_regime:
        Identifier of the regime the navigator currently occupies.
    visited:
        Tuple of regime identifiers visited so far, in chronological order.
    depth:
        Number of transitions made so far (0 = start).
    cumulative_cost:
        Sum of all transition costs accumulated along the path.
    purpose_alignment:
        Running average of purpose-alignment scores for visited regimes.
        In [0, 1].
    """

    current_regime: str
    visited: tuple[str, ...]
    depth: int
    cumulative_cost: float
    purpose_alignment: float


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class TheorySpaceNavigationAnalyzer:
    """Scores, ranks, and selects candidate regimes for theory-space navigation.

    The analyzer is stateless between calls — every method is a pure function
    of its inputs and the optional :class:`CandidateRegimeConfig`.  This makes
    the analyzer safe to share across threads.

    Parameters
    ----------
    config:
        Scoring hyper-parameters.  If *None*, defaults are used.

    Examples
    --------
    >>> analyzer = TheorySpaceNavigationAnalyzer()
    >>> regime = analyzer.score_candidate(
    ...     {"name": "Category Theory", "area": "category_theory",
    ...      "description": "Morphisms, functors, natural transformations"},
    ...     ["functor", "morphism"],
    ... )
    >>> regime.relevance_score > 0
    True
    """

    def __init__(self, config: CandidateRegimeConfig | None = None) -> None:
        """Initialise with optional configuration.

        Parameters
        ----------
        config:
            Hyper-parameter bundle.  Defaults to :class:`CandidateRegimeConfig`
            with all factory defaults.
        """
        self.config: CandidateRegimeConfig = config or CandidateRegimeConfig()
        self._distance_cache: dict[tuple[str, str], float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_candidate(
        self,
        regime: dict[str, Any],
        purpose_keywords: list[str],
    ) -> CandidateRegime:
        """Convert a raw regime dict into a fully scored :class:`CandidateRegime`.

        Scoring procedure:

        * **Relevance** — Jaccard similarity between the regime's combined
          text (name + area + description) and *purpose_keywords*.
        * **Novelty** — 1.0 minus the length-normalised number of shared
          tokens between the regime's text and the purpose keywords (a proxy
          for how "fresh" the regime is).
        * **Coverage** — ratio of ``obstruction_handles`` that appear in the
          regime dict to the number of purpose keywords, clamped to [0, 1].
        * **Composite** — weighted combination using the config weights.

        Parameters
        ----------
        regime:
            Raw dict with at minimum keys ``name``, ``area``, ``description``.
            Optional keys: ``obstruction_handles`` (list[str]).
        purpose_keywords:
            List of keywords derived from the current purpose condition.

        Returns
        -------
        CandidateRegime
            A fully populated and scored candidate.

        Raises
        ------
        ValueError
            If *regime* is missing the required ``name`` key.
        """
        if "name" not in regime:
            raise ValueError("regime dict must contain a 'name' key")

        name: str = str(regime.get("name", ""))
        area: str = str(regime.get("area", "unknown"))
        description: str = str(regime.get("description", ""))
        obstruction_handles: list[str] = list(regime.get("obstruction_handles", []))

        regime_tokens = _tokenize(f"{name} {area} {description}")
        purpose_tokens = _tokenize(" ".join(purpose_keywords))

        relevance = _jaccard(regime_tokens, purpose_tokens)

        # Novelty: inverse of keyword overlap density
        overlap = len(regime_tokens & purpose_tokens)
        novelty = _clamp(1.0 - (overlap / max(len(purpose_tokens), 1)))

        # Coverage: fraction of obstruction handles matching purpose keywords
        if obstruction_handles and purpose_keywords:
            matched = sum(1 for h in obstruction_handles if h in purpose_keywords)
            coverage = _clamp(matched / len(purpose_keywords))
        else:
            coverage = self.config.min_coverage

        composite = _composite_score(
            relevance,
            novelty,
            coverage,
            self.config.relevance_weight,
            self.config.novelty_weight,
            self.config.diversity_weight,
        )

        return CandidateRegime(
            regime_id=_regime_id(),
            name=name,
            area=area,
            relevance_score=round(relevance, 6),
            novelty_score=round(novelty, 6),
            coverage_score=round(coverage, 6),
            composite_score=round(composite, 6),
            description=description,
        )

    def rank_candidates(
        self,
        candidates: list[dict[str, Any]],
        purpose: str,
    ) -> list[CandidateRegime]:
        """Score and rank a list of raw regime dicts by composite score.

        Candidates whose coverage score falls below
        :attr:`CandidateRegimeConfig.min_coverage` are dropped.  The returned
        list is sorted in descending order of ``composite_score``.

        Parameters
        ----------
        candidates:
            List of raw regime dicts, each with at least a ``name`` key.
        purpose:
            Free-form purpose string; tokenised internally to derive keywords.

        Returns
        -------
        list[CandidateRegime]
            Ranked list (best first), truncated to
            :attr:`CandidateRegimeConfig.max_candidates`.

        Notes
        -----
        Ties in ``composite_score`` are broken by ``name`` alphabetically to
        ensure deterministic ordering.
        """
        purpose_kws = list(_tokenize(purpose))
        scored: list[CandidateRegime] = []

        for raw in candidates:
            try:
                candidate = self.score_candidate(raw, purpose_kws)
            except ValueError as exc:
                logger.warning("Skipping malformed candidate: %s", exc)
                continue
            if candidate.coverage_score >= self.config.min_coverage:
                scored.append(candidate)

        scored.sort(key=lambda c: (-c.composite_score, c.name))
        return scored[: self.config.max_candidates]

    def select_next(
        self,
        candidates: list[CandidateRegime],
        state: NavigationState,
        config: CandidateRegimeConfig,
    ) -> CandidateRegime:
        """Select the best next candidate given the current navigation state.

        The selection criterion combines the candidate's ``composite_score``
        with a diversity bonus that rewards regimes not already visited and a
        depth penalty that discourages revisiting expensive paths.

        Parameters
        ----------
        candidates:
            Pre-ranked list of :class:`CandidateRegime` instances.
        state:
            Current navigator state (visited set, depth, costs).
        config:
            Scoring configuration (used for weight extraction).

        Returns
        -------
        CandidateRegime
            The selected best candidate.

        Raises
        ------
        ValueError
            If *candidates* is empty.
        """
        if not candidates:
            raise ValueError("candidate list is empty — cannot select next regime")

        visited_set = set(state.visited)
        best: CandidateRegime | None = None
        best_adj: float = -1.0

        for candidate in candidates:
            diversity_bonus = config.diversity_weight if candidate.name not in visited_set else 0.0
            depth_penalty = 0.05 * state.depth
            adjusted = _clamp(candidate.composite_score + diversity_bonus - depth_penalty)
            if adjusted > best_adj:
                best_adj = adjusted
                best = candidate

        assert best is not None
        return best

    def compute_transition_cost(
        self,
        from_regime: str,
        to_regime: str,
    ) -> float:
        """Estimate the semantic distance cost of moving between two regimes.

        The cost is computed as one minus the Jaccard similarity of the token
        sets of the two regime identifiers/names.  Results are cached to avoid
        redundant computation.

        Parameters
        ----------
        from_regime:
            Identifier or name of the source regime.
        to_regime:
            Identifier or name of the target regime.

        Returns
        -------
        float
            Transition cost in [0, 1]; 0 = same regime, 1 = maximally distant.

        Examples
        --------
        >>> analyzer = TheorySpaceNavigationAnalyzer()
        >>> c = analyzer.compute_transition_cost("type_theory", "type_theory")
        >>> c
        0.0
        """
        key = (from_regime, to_regime)
        if key in self._distance_cache:
            return self._distance_cache[key]

        a = _tokenize(from_regime)
        b = _tokenize(to_regime)
        similarity = _jaccard(a, b)
        cost = round(1.0 - similarity, 6)
        self._distance_cache[key] = cost
        return cost

    def explain_selection(
        self,
        selected: CandidateRegime,
        alternatives: list[CandidateRegime],
    ) -> str:
        """Produce a human-readable explanation of a regime selection.

        The explanation names the selected regime, states its composite score,
        and contrasts it with the top alternative (if any).

        Parameters
        ----------
        selected:
            The chosen :class:`CandidateRegime`.
        alternatives:
            All other candidates that were considered (excluding *selected*).

        Returns
        -------
        str
            Multi-sentence explanation string suitable for logging or reports.

        Examples
        --------
        >>> a = TheorySpaceNavigationAnalyzer()
        >>> sel = CandidateRegime("r1","HoTT","hott",0.8,0.6,0.7,0.74,"desc")
        >>> exp = a.explain_selection(sel, [])
        >>> "HoTT" in exp
        True
        """
        lines: list[str] = [
            f"Selected regime: '{selected.name}' (id={selected.regime_id}).",
            f"  Composite score: {selected.composite_score:.4f}  "
            f"[relevance={selected.relevance_score:.3f}, "
            f"novelty={selected.novelty_score:.3f}, "
            f"coverage={selected.coverage_score:.3f}].",
        ]
        if alternatives:
            runner_up = max(alternatives, key=lambda c: c.composite_score)
            margin = selected.composite_score - runner_up.composite_score
            lines.append(
                f"  Runner-up: '{runner_up.name}' "
                f"(score={runner_up.composite_score:.4f}, margin={margin:.4f})."
            )
            lines.append(
                f"  {len(alternatives)} alternative(s) were considered and passed the "
                f"coverage threshold."
            )
        else:
            lines.append("  No alternatives passed the coverage threshold.")

        if selected.description:
            lines.append(f"  Description: {selected.description}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class TheorySpaceNavigationWitness:
    """Audit trail that records every :class:`RegimeTransition` made during navigation.

    The witness is a mutable accumulator.  It provides summary statistics and
    export functionality without exposing the raw transition list directly.

    Attributes
    ----------
    _transitions : list[RegimeTransition]
        Internal ordered log of all recorded transitions.

    Examples
    --------
    >>> witness = TheorySpaceNavigationWitness()
    >>> t = RegimeTransition("__start__", "hott", 0.5, "initial", _now_iso())
    >>> witness.record(t)
    >>> witness.path()
    ['hott']
    >>> witness.total_cost()
    0.5
    """

    def __init__(self) -> None:
        """Initialise an empty witness."""
        self._transitions: list[RegimeTransition] = []

    def record(self, transition: RegimeTransition) -> None:
        """Append *transition* to the audit trail.

        Parameters
        ----------
        transition:
            A :class:`RegimeTransition` to record.

        Notes
        -----
        Transitions are stored in insertion order.  Duplicate transitions (same
        ``from_regime`` → ``to_regime`` at different timestamps) are all
        recorded faithfully.
        """
        self._transitions.append(transition)
        logger.debug(
            "Witness recorded transition %s → %s (cost=%.4f)",
            transition.from_regime,
            transition.to_regime,
            transition.transition_cost,
        )

    def path(self) -> list[str]:
        """Return the sequence of regime identifiers visited, excluding the start.

        Returns
        -------
        list[str]
            Ordered list of ``to_regime`` identifiers for all recorded
            transitions.
        """
        return [t.to_regime for t in self._transitions]

    def total_cost(self) -> float:
        """Return the sum of all transition costs recorded so far.

        Returns
        -------
        float
            Cumulative transition cost.
        """
        return sum(t.transition_cost for t in self._transitions)

    def summary(self) -> dict[str, Any]:
        """Return a structured summary of the navigation trail.

        The summary includes total steps, unique regimes visited, total cost,
        average cost per step, and the full path.

        Returns
        -------
        dict[str, Any]
            Summary mapping with keys: ``steps``, ``unique_regimes``,
            ``total_cost``, ``avg_cost``, ``path``.
        """
        path = self.path()
        steps = len(path)
        unique = len(set(path))
        total = self.total_cost()
        avg = total / steps if steps else 0.0
        return {
            "steps": steps,
            "unique_regimes": unique,
            "total_cost": round(total, 6),
            "avg_cost": round(avg, 6),
            "path": path,
        }

    def export(self) -> list[dict[str, Any]]:
        """Serialise all recorded transitions to a list of plain dicts.

        Returns
        -------
        list[dict[str, Any]]
            Each entry has keys: ``from_regime``, ``to_regime``,
            ``transition_cost``, ``reason``, ``timestamp``.
        """
        return [
            {
                "from_regime": t.from_regime,
                "to_regime": t.to_regime,
                "transition_cost": t.transition_cost,
                "reason": t.reason,
                "timestamp": t.timestamp,
            }
            for t in self._transitions
        ]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class TheorySpaceNavigationCoordinator:
    """Orchestrate multi-step theory-space navigation.

    The coordinator drives the :class:`TheorySpaceNavigationAnalyzer` for a
    configurable number of steps, records each transition in the
    :class:`TheorySpaceNavigationWitness`, and produces a final structured
    report.

    Parameters
    ----------
    config:
        Scoring hyper-parameters.  If *None*, defaults are used.

    Examples
    --------
    >>> coord = TheorySpaceNavigationCoordinator()
    >>> candidates = [
    ...     {"name": "HoTT", "area": "homotopy_type_theory",
    ...      "description": "homotopy types fibrations path spaces"},
    ...     {"name": "Category Theory", "area": "category_theory",
    ...      "description": "functors morphisms adjunctions limits"},
    ... ]
    >>> transitions = coord.navigate(candidates, "resolve fibration obstruction", steps=2)
    >>> len(transitions) <= 2
    True
    """

    def __init__(self, config: CandidateRegimeConfig | None = None) -> None:
        """Initialise the coordinator.

        Parameters
        ----------
        config:
            Hyper-parameter bundle.  Defaults to :class:`CandidateRegimeConfig`
            with all factory defaults.
        """
        self.config: CandidateRegimeConfig = config or CandidateRegimeConfig()
        self._analyzer: TheorySpaceNavigationAnalyzer = TheorySpaceNavigationAnalyzer(self.config)
        self._witness: TheorySpaceNavigationWitness = TheorySpaceNavigationWitness()
        self._purpose_used: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def navigate(
        self,
        candidates: list[dict[str, Any]],
        purpose: str,
        steps: int = 5,
    ) -> list[RegimeTransition]:
        """Run multi-step navigation over *candidates* conditioned on *purpose*.

        At each step the analyzer ranks all candidates, selects the best next
        regime, computes the transition cost, records it in the witness, and
        advances the navigation state.

        Parameters
        ----------
        candidates:
            Raw regime dicts to score and select from.  Must be non-empty.
        purpose:
            Free-form purpose / obstruction description string.
        steps:
            Number of navigation steps to perform.  If fewer candidates are
            available than *steps*, navigation stops early.

        Returns
        -------
        list[RegimeTransition]
            Ordered list of transitions performed during this navigation run.

        Raises
        ------
        ValueError
            If *candidates* is empty or *steps* < 1.

        Notes
        -----
        Each call to :meth:`navigate` appends to the shared witness.  To start
        fresh, create a new :class:`TheorySpaceNavigationCoordinator`.
        """
        if not candidates:
            raise ValueError("candidate list must be non-empty")
        if steps < 1:
            raise ValueError(f"steps must be ≥ 1, got {steps}")

        self._purpose_used = purpose
        state = NavigationState(
            current_regime="__start__",
            visited=(),
            depth=0,
            cumulative_cost=0.0,
            purpose_alignment=0.0,
        )

        transitions_this_run: list[RegimeTransition] = []

        for step_idx in range(steps):
            ranked = self._analyzer.rank_candidates(candidates, purpose)
            if not ranked:
                logger.warning("No candidates survived coverage filter at step %d", step_idx)
                break

            # Exclude the current regime from selection (avoid self-loops)
            eligible = [c for c in ranked if c.name != state.current_regime]
            if not eligible:
                eligible = ranked  # fallback: allow self-loop rather than stopping

            try:
                selected = self._analyzer.select_next(eligible, state, self.config)
            except ValueError:
                logger.warning("select_next raised ValueError at step %d", step_idx)
                break

            cost = self._analyzer.compute_transition_cost(state.current_regime, selected.name)
            alternatives = [c for c in eligible if c.regime_id != selected.regime_id]
            reason = self._analyzer.explain_selection(selected, alternatives)

            transition = RegimeTransition(
                from_regime=state.current_regime,
                to_regime=selected.name,
                transition_cost=cost,
                reason=reason,
                timestamp=_now_iso(),
            )
            self._witness.record(transition)
            transitions_this_run.append(transition)

            new_alignment = _clamp(
                (state.purpose_alignment * state.depth + selected.relevance_score)
                / (state.depth + 1)
            )
            state = NavigationState(
                current_regime=selected.name,
                visited=state.visited + (selected.name,),
                depth=state.depth + 1,
                cumulative_cost=state.cumulative_cost + cost,
                purpose_alignment=new_alignment,
            )

        return transitions_this_run

    def report(self) -> dict[str, Any]:
        """Produce a structured navigation report.

        The report aggregates information from the witness, the last-used
        purpose string, and the configuration.

        Returns
        -------
        dict[str, Any]
            Report with keys: ``summary`` (from witness), ``purpose``,
            ``config`` (dataclass fields), ``transitions`` (exported list).
        """
        return {
            "summary": self._witness.summary(),
            "purpose": self._purpose_used,
            "config": {
                "max_candidates": self.config.max_candidates,
                "relevance_weight": self.config.relevance_weight,
                "diversity_weight": self.config.diversity_weight,
                "novelty_weight": self.config.novelty_weight,
                "min_coverage": self.config.min_coverage,
            },
            "transitions": self._witness.export(),
        }


# ---------------------------------------------------------------------------
# Additional constants and lookup tables
# ---------------------------------------------------------------------------

#: Canonical list of well-known mathematical regime names used as seed
#: candidates when no external candidate list is provided.
SEED_REGIMES: list[dict[str, Any]] = [
    {
        "name": "Type Theory",
        "area": "type_theory",
        "description": "Dependent types, Martin-Löf type theory, propositions as types",
        "obstruction_handles": ["type_mismatch", "proof_term", "dependent_product"],
    },
    {
        "name": "Category Theory",
        "area": "category_theory",
        "description": "Functors, natural transformations, adjunctions, limits, colimits",
        "obstruction_handles": ["naturality", "adjunction", "universal_property"],
    },
    {
        "name": "Algebraic Geometry",
        "area": "algebraic_geometry",
        "description": "Schemes, sheaves, cohomology, morphisms of varieties",
        "obstruction_handles": ["cohomological_obstruction", "scheme_obstruction"],
    },
    {
        "name": "Homotopy Type Theory",
        "area": "homotopy_type_theory",
        "description": "Univalence axiom, higher inductive types, path spaces, fibrations",
        "obstruction_handles": ["path_obstruction", "fibration", "univalence"],
    },
    {
        "name": "Homological Algebra",
        "area": "homological_algebra",
        "description": "Chain complexes, derived functors, Ext, Tor, spectral sequences",
        "obstruction_handles": ["extension_class", "derived_functor", "exact_sequence"],
    },
    {
        "name": "Sheaf Theory",
        "area": "sheaf_theory",
        "description": "Sheaves on sites, Grothendieck topologies, étale cohomology",
        "obstruction_handles": ["sheaf_obstruction", "gluing_condition"],
    },
    {
        "name": "Topos Theory",
        "area": "topos_theory",
        "description": "Elementary toposes, geometric morphisms, internal logic",
        "obstruction_handles": ["internal_logic", "subobject_classifier"],
    },
    {
        "name": "Differential Geometry",
        "area": "differential_geometry",
        "description": "Smooth manifolds, vector bundles, connections, curvature",
        "obstruction_handles": ["curvature_obstruction", "holonomy"],
    },
    {
        "name": "Representation Theory",
        "area": "representation_theory",
        "description": "Group representations, modules, characters, Schur's lemma",
        "obstruction_handles": ["representation_obstruction", "character_sum"],
    },
    {
        "name": "K-Theory",
        "area": "k_theory",
        "description": "Algebraic K-theory, topological K-theory, Grothendieck group",
        "obstruction_handles": ["k_theoretic_obstruction", "grothendieck_group"],
    },
]

#: Transition cost table for well-known regime pairs based on structural
#: distance between mathematical frameworks.  Used to seed the distance cache
#: with expert-curated values.
EXPERT_TRANSITION_COSTS: dict[tuple[str, str], float] = {
    ("Type Theory", "Homotopy Type Theory"): 0.15,
    ("Homotopy Type Theory", "Type Theory"): 0.15,
    ("Category Theory", "Topos Theory"): 0.20,
    ("Topos Theory", "Category Theory"): 0.20,
    ("Algebraic Geometry", "Sheaf Theory"): 0.18,
    ("Sheaf Theory", "Algebraic Geometry"): 0.18,
    ("Homological Algebra", "Algebraic Geometry"): 0.25,
    ("Category Theory", "Homological Algebra"): 0.22,
    ("Differential Geometry", "Algebraic Geometry"): 0.35,
    ("K-Theory", "Algebraic Geometry"): 0.30,
    ("K-Theory", "Homological Algebra"): 0.28,
    ("Representation Theory", "Homological Algebra"): 0.32,
}


def _load_expert_costs(analyzer: TheorySpaceNavigationAnalyzer) -> None:
    """Seed *analyzer*'s distance cache with expert-curated transition costs.

    Parameters
    ----------
    analyzer:
        The :class:`TheorySpaceNavigationAnalyzer` whose cache to populate.

    Notes
    -----
    Expert costs override the Jaccard-based heuristic for regime pairs listed
    in :data:`EXPERT_TRANSITION_COSTS`.  This allows domain knowledge to
    improve navigation quality without retraining.
    """
    for (fr, to), cost in EXPERT_TRANSITION_COSTS.items():
        analyzer._distance_cache[(fr, to)] = cost


# ---------------------------------------------------------------------------
# Smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=== Theory-Space Navigation: Moving Over Candidate Regimes ===\n")

    cfg = CandidateRegimeConfig(max_candidates=10, relevance_weight=0.5, diversity_weight=0.3, novelty_weight=0.2)
    coord = TheorySpaceNavigationCoordinator(config=cfg)
    _load_expert_costs(coord._analyzer)

    purpose = "resolve H1 fibration obstruction using path-space arguments"
    print(f"Purpose: {purpose!r}\n")

    transitions = coord.navigate(SEED_REGIMES, purpose, steps=4)

    print(f"Navigation completed in {len(transitions)} step(s):\n")
    for i, t in enumerate(transitions, start=1):
        print(f"  Step {i}: {t.from_regime!r} → {t.to_regime!r}  (cost={t.transition_cost:.4f})")

    print("\nFull report (JSON):")
    report = coord.report()
    summary = report["summary"]
    print(f"  steps={summary['steps']}, unique_regimes={summary['unique_regimes']}, "
          f"total_cost={summary['total_cost']:.4f}, avg_cost={summary['avg_cost']:.4f}")
    print(f"  path={summary['path']}")

    print("\nSmoke-test PASSED.")
