r"""Proof suggestion engine for JuGeo research assistance — Chapter 51, §1.

This module implements tactic-driven proof step suggestion.  Given a
:class:`~jugeo.ideation.research_assistance.models.ResearchContext` describing
a partial proof state, the :class:`ProofSuggestionEngine` generates a ranked
list of :class:`~jugeo.ideation.research_assistance.models.ProofSuggestion`
candidates by matching available tactics against the proof goal.

Mathematical framing
--------------------

Let :math:`T` be the tactic library and :math:`\text{ctx}` the current
context with goal :math:`g` and available lemma set :math:`L`.  The
composite score of suggestion :math:`s` is:

.. math::

    f(s, \text{ctx}) = s.\text{confidence} \times
        \bigl(1 + 0.1 \cdot |L|\bigr)

where the lemma-bonus factor rewards contexts that have more auxiliary
material available.  Confidence is derived from the Jaccard overlap
between the tactic token set and the goal token set:

.. math::

    \text{conf}(t, g) =
        \frac{|\text{tok}(t) \cap \text{tok}(g)|}
             {|\text{tok}(t) \cup \text{tok}(g)|}

If the union is empty the confidence defaults to a small positive value
:math:`\epsilon = 0.1` so that every tactic has a non-zero chance of
being helpful.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from jugeo.ideation.research_assistance.models import (
    LemmaCandidate,
    ProofSuggestion,
    ResearchContext,
    VerificationStatus,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float to [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _tokenize(text: str) -> set[str]:
    """Return lowercase alphanumeric tokens of length >= 2."""
    return {t.lower() for t in re.split(r"[^a-zA-Z0-9]+", text) if len(t) >= 2}


def _keyword_overlap(a: str, b: str) -> float:
    """Compute Jaccard token overlap between two strings."""
    tok_a = _tokenize(a)
    tok_b = _tokenize(b)
    if not tok_a and not tok_b:
        return 1.0
    union = tok_a | tok_b
    if not union:
        return 0.0
    return len(tok_a & tok_b) / len(union)


# ---------------------------------------------------------------------------
# TacticLibrary
# ---------------------------------------------------------------------------


class TacticLibrary:
    """Catalog of available proof tactics with descriptions.

    The library ships with ten standard tactics drawn from interactive theorem
    provers such as Lean and Coq.  New tactics can be added at runtime via
    :meth:`add_tactic`.
    """

    _DEFAULTS: dict[str, str] = {
        "rewrite": "Rewrite using an equation or lemma",
        "induction": "Apply structural or mathematical induction",
        "apply": "Apply a lemma or hypothesis to the current goal",
        "cases": "Split proof into cases",
        "contradiction": "Derive a contradiction",
        "assumption": "Close goal using a hypothesis in context",
        "specialize": "Specialize a universally quantified lemma",
        "intro": "Introduce quantifiers or implications",
        "unfold": "Unfold a definition",
        "simp": "Simplify using rewrite rules",
    }

    def __init__(self) -> None:
        self._tactics: dict[str, str] = dict(self._DEFAULTS)

    def tactics(self) -> tuple[str, ...]:
        """Return a sorted tuple of all tactic names."""
        return tuple(sorted(self._tactics))

    def add_tactic(self, name: str, description: str) -> None:
        """Register a new tactic; silently overwrites an existing entry."""
        self._tactics[name] = description
        _log.debug("TacticLibrary: added tactic %r", name)

    def has_tactic(self, name: str) -> bool:
        """Return True if the named tactic is registered."""
        return name in self._tactics

    def description(self, name: str) -> str:
        """Return the description of the named tactic, or empty string."""
        return self._tactics.get(name, "")

    def all_descriptions(self) -> dict[str, str]:
        """Return a copy of the full tactic-to-description mapping."""
        return dict(self._tactics)

    def count(self) -> int:
        """Return the number of registered tactics."""
        return len(self._tactics)


# ---------------------------------------------------------------------------
# GoalAnalyzer
# ---------------------------------------------------------------------------


class GoalAnalyzer:
    """Analyzes proof goals to extract structural features for ranking.

    The analyzer computes a complexity estimate, extracts keywords, and
    detects base-case patterns that influence which tactics are likely
    to be applicable.
    """

    _BASE_CASE_TOKENS: frozenset[str] = frozenset(
        {"base", "zero", "nil", "empty", "trivial", "leaf", "unit", "init"}
    )

    def analyze(self, goal: str) -> dict[str, Any]:
        """Return a feature dictionary for the given proof goal string."""
        tokens = _tokenize(goal)
        kw = self.keywords(goal)
        return {
            "tokens": tokens,
            "complexity": self.complexity(goal),
            "keywords": kw,
            "is_base_case": self.is_base_case(goal),
            "length": len(goal),
            "token_count": len(tokens),
        }

    def complexity(self, goal: str) -> float:
        """Estimate goal complexity in [0, 1] from its character length."""
        return min(len(goal) / 200.0, 1.0)

    def keywords(self, goal: str) -> set[str]:
        """Return tokens of length >= 3, which carry semantic weight."""
        return {t for t in _tokenize(goal) if len(t) >= 3}

    def is_base_case(self, goal: str) -> bool:
        """Return True if the goal appears to be a base case."""
        return bool(_tokenize(goal) & self._BASE_CASE_TOKENS)

    def dominant_keyword(self, goal: str) -> str:
        """Return the longest keyword as a rough proxy for the core concept."""
        kws = self.keywords(goal)
        if not kws:
            return ""
        return max(kws, key=len)


# ---------------------------------------------------------------------------
# SuggestionFilter
# ---------------------------------------------------------------------------


class SuggestionFilter:
    """Filters and deduplicates :class:`ProofSuggestion` lists.

    Filtering by minimum confidence removes low-quality suggestions.
    Deduplication keeps the first occurrence of each tactic description,
    ensuring the engine does not return redundant suggestions.
    """

    def filter(
        self,
        suggestions: list[ProofSuggestion],
        min_confidence: float,
    ) -> list[ProofSuggestion]:
        """Return only suggestions with confidence >= min_confidence."""
        return [s for s in suggestions if s.confidence >= min_confidence]

    def deduplicate(
        self,
        suggestions: list[ProofSuggestion],
    ) -> list[ProofSuggestion]:
        """Return suggestions with duplicate tactic_descriptions removed (first wins)."""
        seen: set[str] = set()
        result: list[ProofSuggestion] = []
        for s in suggestions:
            key = s.tactic_description.strip().lower()
            if key not in seen:
                seen.add(key)
                result.append(s)
        return result

    def top_k(
        self,
        suggestions: list[ProofSuggestion],
        k: int,
    ) -> list[ProofSuggestion]:
        """Return the top-k suggestions sorted by confidence descending."""
        return sorted(suggestions, key=lambda s: s.confidence, reverse=True)[:k]


# ---------------------------------------------------------------------------
# ProofStateTracker
# ---------------------------------------------------------------------------


class ProofStateTracker:
    """Tracks proof state changes as a push/pop stack.

    Each ``push`` adds a new state string representing the proof at that
    point.  ``pop`` undoes the most recent push.  This supports backtracking
    in proof search algorithms.
    """

    def __init__(self) -> None:
        self._stack: list[str] = []

    def push(self, state: str) -> None:
        """Push a new proof state onto the stack."""
        self._stack.append(state)
        _log.debug("ProofStateTracker: push depth=%d", len(self._stack))

    def pop(self) -> str | None:
        """Pop the most recent proof state; return None if stack is empty."""
        if not self._stack:
            return None
        state = self._stack.pop()
        _log.debug("ProofStateTracker: pop depth=%d", len(self._stack))
        return state

    def current(self) -> str | None:
        """Return the most recent state without modifying the stack."""
        if not self._stack:
            return None
        return self._stack[-1]

    def history(self) -> tuple[str, ...]:
        """Return all states in push order as an immutable tuple."""
        return tuple(self._stack)

    def depth(self) -> int:
        """Return the current stack depth."""
        return len(self._stack)

    def reset(self) -> None:
        """Clear the entire stack."""
        self._stack.clear()


# ---------------------------------------------------------------------------
# ProofSuggestionEngine
# ---------------------------------------------------------------------------


class ProofSuggestionEngine:
    """Main engine for generating ranked proof suggestions from a context.

    The engine iterates over every registered tactic in its library, scores
    each candidate suggestion using token-overlap confidence and a lemma-count
    bonus, applies optional filtering, and returns the top suggestions.

    Attributes:
        _library: The :class:`TacticLibrary` supplying available tactics.
        _policy: Dictionary of configuration parameters.
        _analyzer: :class:`GoalAnalyzer` for goal feature extraction.
        _filter: :class:`SuggestionFilter` for post-generation filtering.
    """

    def __init__(
        self,
        library: TacticLibrary | None = None,
        policy: dict | None = None,
    ) -> None:
        self._library = library or TacticLibrary()
        self._policy: dict = policy or {
            "max_suggestions": 5,
            "min_confidence": 0.3,
        }
        self._analyzer = GoalAnalyzer()
        self._filter = SuggestionFilter()

    def suggest(self, context: ResearchContext) -> list[ProofSuggestion]:
        """Generate, rank, and return the top proof suggestions for context."""
        raw: list[ProofSuggestion] = []
        for tactic in self._library.tactics():
            candidate = self._generate_from_tactic(tactic, context)
            if candidate is not None:
                raw.append(candidate)

        ranked = self.rank_suggestions(raw, context)
        min_conf = float(self._policy.get("min_confidence", 0.3))
        filtered = self._filter.filter(ranked, min_conf)
        deduped = self._filter.deduplicate(filtered)
        max_n = int(self._policy.get("max_suggestions", 5))
        return deduped[:max_n]

    def rank_suggestions(
        self,
        suggestions: list[ProofSuggestion],
        context: ResearchContext,
    ) -> list[ProofSuggestion]:
        """Sort suggestions by composite score descending."""
        return sorted(
            suggestions,
            key=lambda s: self._score(s, context),
            reverse=True,
        )

    def apply_suggestion(
        self,
        suggestion: ProofSuggestion,
        context: ResearchContext,
    ) -> ResearchContext:
        """Append the suggestion's tactic to the context's partial proof."""
        sep = "\n" if context.partial_proof else ""
        context.update_proof_state(
            context.partial_proof + sep + suggestion.tactic_description
        )
        return context

    def _generate_from_tactic(
        self,
        tactic: str,
        context: ResearchContext,
    ) -> ProofSuggestion | None:
        """Generate a suggestion from a single tactic name against the context."""
        if not self._library.has_tactic(tactic):
            return None

        goal_text = context.current_theorem + " " + context.partial_proof
        confidence = _keyword_overlap(tactic, goal_text)
        if confidence < 1e-9:
            confidence = 0.1

        lemma_text = " ".join(lc.statement for lc in context.available_lemmas)
        if lemma_text:
            lemma_boost = _keyword_overlap(tactic, lemma_text) * 0.2
            confidence = _clamp(confidence + lemma_boost)

        tactic_desc = self._library.description(tactic)
        justification = (
            f"Generated from tactic analysis: {tactic_desc or tactic!r} "
            f"applied to goal with {len(context.available_lemmas)} available lemmas."
        )

        return ProofSuggestion(
            suggestion_id=str(uuid.uuid4()),
            tactic_description=tactic,
            target_goal=context.current_theorem,
            confidence=confidence,
            justification=justification,
            oracle_source="engine",
        )

    def _score(
        self,
        suggestion: ProofSuggestion,
        context: ResearchContext,
    ) -> float:
        """Compute the composite ranking score for a suggestion."""
        return suggestion.confidence * (1.0 + 0.1 * len(context.available_lemmas))

    def with_policy(self, **kwargs: object) -> ProofSuggestionEngine:
        """Return a new engine with an updated policy (immutable-style)."""
        new_policy = dict(self._policy)
        new_policy.update(kwargs)
        return ProofSuggestionEngine(library=self._library, policy=new_policy)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "GoalAnalyzer",
    "ProofStateTracker",
    "ProofSuggestionEngine",
    "SuggestionFilter",
    "TacticLibrary",
]
