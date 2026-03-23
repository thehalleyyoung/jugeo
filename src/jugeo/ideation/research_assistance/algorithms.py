r"""Search and retrieval algorithms for JuGeo research assistance — Chapter 51.

This module provides standalone algorithm classes for proof search, lemma
retrieval, conjecture ranking, and oracle query optimisation.  Each algorithm
is a self-contained object that can be composed with the higher-level engines
in the other sub-modules.

Complexity summary
------------------

.. list-table::
   :header-rows: 1

   * - Algorithm
     - Time complexity
   * - :class:`BreadthFirstProofSearch`
     - :math:`O(b^d)` (Thm 51.9)
   * - :class:`BestFirstProofSearch`
     - :math:`O(N \log N)` for :math:`N` expanded nodes
   * - :class:`LemmaRetrievalAlgorithm`
     - :math:`O(|\text{archive}| \cdot |V|)` for vocabulary :math:`V`
   * - :class:`ConjectureRankingAlgorithm`
     - :math:`O(n \log n)` for :math:`n` conjectures
   * - :class:`OracleQueryOptimizer`
     - :math:`O(q^2)` for :math:`q` queries (pairwise similarity)
"""

from __future__ import annotations

import heapq
import logging
import math
import re
import uuid
from collections import Counter
from typing import Any

from jugeo.ideation.research_assistance.models import (
    ConjectureRecord,
    LemmaCandidate,
    OracleQuery,
    ProofSuggestion,
    ResearchContext,
    VerificationStatus,
)
from jugeo.ideation.research_assistance.lemma_mining import LemmaArchive
from jugeo.ideation.research_assistance.oracle_interface import OraclePolicy

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


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute Jaccard coefficient between two token sets."""
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _tfidf(term: str, document: str, corpus: list[str]) -> float:
    """Compute a TF-IDF weight for term in document given corpus.

    TF is the term frequency within the document.  IDF uses a smoothed
    log formula: :math:`\\ln(N / (1 + \\text{df}))` where :math:`N` is the
    corpus size and :math:`\\text{df}` is the document frequency.
    """
    doc_tokens = document.lower().split()
    if not doc_tokens:
        return 0.0
    tf = doc_tokens.count(term.lower()) / len(doc_tokens)
    n = len(corpus)
    df = sum(1 for doc in corpus if term.lower() in doc.lower())
    idf = math.log(n / (1.0 + df)) if n > 0 else 0.0
    return tf * idf


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class ResearchAssistanceAlgorithm:
    """Abstract base for all research assistance search algorithms.

    Subclasses must override :meth:`run`, :meth:`name`, and
    :meth:`description`.
    """

    def run(self, context: ResearchContext) -> Any:
        """Execute the algorithm against the given context."""
        raise NotImplementedError(f"{type(self).__name__}.run is not implemented")

    def name(self) -> str:
        """Return the algorithm's canonical name."""
        raise NotImplementedError(f"{type(self).__name__}.name is not implemented")

    def description(self) -> str:
        """Return a brief human-readable description of the algorithm."""
        raise NotImplementedError(
            f"{type(self).__name__}.description is not implemented"
        )


# ---------------------------------------------------------------------------
# BreadthFirstProofSearch
# ---------------------------------------------------------------------------


_TACTICS: tuple[str, ...] = (
    "rewrite",
    "induction",
    "apply",
    "cases",
    "contradiction",
    "assumption",
    "specialize",
    "intro",
    "unfold",
    "simp",
)


class BreadthFirstProofSearch(ResearchAssistanceAlgorithm):
    """BFS over proof states up to a maximum depth.

    At each node the search generates ``branching_factor`` child states by
    appending a tactic name to the current partial proof.  The algorithm
    terminates in at most :math:`O(b^d)` steps where :math:`b` is the
    branching factor and :math:`d` is the max depth (Thm 51.9).

    Attributes:
        max_depth: Maximum BFS depth.
        branching_factor: Number of child states generated per node.
    """

    def __init__(self, max_depth: int = 5, branching_factor: int = 3) -> None:
        self.max_depth = max_depth
        self.branching_factor = branching_factor

    def run(self, context: ResearchContext) -> list[ProofSuggestion]:
        """Execute BFS and return all ProofSuggestion nodes encountered."""
        suggestions: list[ProofSuggestion] = []
        queue: list[tuple[str, int]] = [(context.partial_proof, 0)]
        visited: set[str] = set()

        while queue:
            state, depth = queue.pop(0)
            if state in visited or depth > self.max_depth:
                continue
            visited.add(state)

            children = self._expand(state, depth)
            for child in children:
                tactic = child.rsplit(" ", 1)[-1] if " " in child else child
                conf = _clamp(1.0 / (depth + 1))
                suggestion = ProofSuggestion(
                    suggestion_id=str(uuid.uuid4()),
                    tactic_description=tactic,
                    target_goal=context.current_theorem,
                    confidence=conf,
                    justification=f"BFS depth={depth} state={state[:30]!r}",
                    oracle_source="bfs",
                )
                suggestions.append(suggestion)
                if depth + 1 <= self.max_depth:
                    queue.append((child, depth + 1))

        _log.debug("BreadthFirstProofSearch: explored %d nodes", len(visited))
        return suggestions

    def name(self) -> str:
        """Return the algorithm name."""
        return "BreadthFirstProofSearch"

    def description(self) -> str:
        """Return a description with complexity annotation."""
        return (
            f"Breadth-first proof search with max_depth={self.max_depth} "
            f"and branching_factor={self.branching_factor}. "
            f"Time complexity O(b^d) = O({self.branching_factor}^{self.max_depth})."
        )

    def _expand(self, state: str, depth: int) -> list[str]:
        """Generate branching_factor child states by appending tactics."""
        children: list[str] = []
        tactic_offset = depth * self.branching_factor % len(_TACTICS)
        for i in range(self.branching_factor):
            tactic = _TACTICS[(tactic_offset + i) % len(_TACTICS)]
            sep = " " if state else ""
            children.append(state + sep + tactic)
        return children


# ---------------------------------------------------------------------------
# BestFirstProofSearch
# ---------------------------------------------------------------------------


class BestFirstProofSearch(ResearchAssistanceAlgorithm):
    """Best-first proof search using a configurable heuristic.

    Nodes are expanded in order of decreasing heuristic value.  Under an
    admissible heuristic this dominates BFS (Thm 51.10).

    Attributes:
        max_nodes: Maximum number of nodes to expand.
        heuristic: Name of the heuristic function (``"confidence"`` or
            ``"brevity"``).
    """

    def __init__(self, max_nodes: int = 100, heuristic: str = "confidence") -> None:
        self.max_nodes = max_nodes
        self.heuristic = heuristic

    def run(self, context: ResearchContext) -> list[ProofSuggestion]:
        """Execute best-first search and return suggestions sorted by heuristic."""
        suggestions: list[ProofSuggestion] = []
        heap: list[tuple[float, str]] = []
        visited: set[str] = set()

        initial = ProofSuggestion(
            suggestion_id=str(uuid.uuid4()),
            tactic_description="intro",
            target_goal=context.current_theorem,
            confidence=0.8,
            justification="Best-first root node",
            oracle_source="best-first",
        )
        heapq.heappush(heap, (-self._heuristic(initial), initial.suggestion_id))
        state_map: dict[str, ProofSuggestion] = {initial.suggestion_id: initial}

        while heap and len(suggestions) < self.max_nodes:
            neg_score, sid = heapq.heappop(heap)
            if sid in visited:
                continue
            visited.add(sid)
            suggestion = state_map[sid]
            suggestions.append(suggestion)

            for tactic in _TACTICS[:3]:
                child = ProofSuggestion(
                    suggestion_id=str(uuid.uuid4()),
                    tactic_description=tactic,
                    target_goal=context.current_theorem,
                    confidence=_clamp(-neg_score * 0.9),
                    justification=f"Best-first child of {sid[:8]}",
                    oracle_source="best-first",
                )
                if child.suggestion_id not in visited:
                    state_map[child.suggestion_id] = child
                    heapq.heappush(
                        heap, (-self._heuristic(child), child.suggestion_id)
                    )

        _log.debug("BestFirstProofSearch: expanded %d nodes", len(suggestions))
        return suggestions

    def name(self) -> str:
        """Return the algorithm name."""
        return "BestFirstProofSearch"

    def description(self) -> str:
        """Return a description."""
        return (
            f"Best-first proof search with max_nodes={self.max_nodes} "
            f"and heuristic={self.heuristic!r}. "
            f"Dominates BFS under admissible heuristic (Thm 51.10)."
        )

    def _heuristic(self, suggestion: ProofSuggestion) -> float:
        """Return the heuristic value for ordering (higher is better)."""
        if self.heuristic == "confidence":
            return suggestion.confidence
        if self.heuristic == "brevity":
            desc_len = len(suggestion.tactic_description)
            return 1.0 / desc_len if desc_len > 0 else 0.0
        return suggestion.confidence


# ---------------------------------------------------------------------------
# LemmaRetrievalAlgorithm
# ---------------------------------------------------------------------------


class LemmaRetrievalAlgorithm:
    """TF-IDF-based lemma retrieval from an archive.

    Each lemma is scored by summing TF-IDF weights for each query token
    against the lemma statement, treating all statements as the corpus.

    Attributes:
        top_k: Number of top-scoring lemmas to return.
    """

    def __init__(self, top_k: int = 10) -> None:
        self.top_k = top_k

    def run(
        self,
        context: ResearchContext,
        archive: LemmaArchive,
    ) -> list[LemmaCandidate]:
        """Return the top-k lemmas from the archive ranked by TF-IDF score."""
        all_lemmas = archive.all()
        corpus = [lemma.statement for lemma in all_lemmas]
        query_tokens = _tokenize(context.current_theorem + " " + context.purpose)

        scored: list[tuple[float, LemmaCandidate]] = []
        for lemma in all_lemmas:
            score = sum(
                self._tfidf_score(token, lemma.statement, corpus)
                for token in query_tokens
            )
            scored.append((score, lemma))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [lemma for _, lemma in scored[: self.top_k]]

    def _tfidf_score(self, term: str, document: str, corpus: list[str]) -> float:
        """Compute TF-IDF for a single term in a document."""
        return _tfidf(term, document, corpus)


# ---------------------------------------------------------------------------
# ConjectureRankingAlgorithm
# ---------------------------------------------------------------------------


class ConjectureRankingAlgorithm:
    """Weighted multi-criteria ranking of :class:`ConjectureRecord` instances.

    The score is a weighted combination of three criteria:

    - ``confidence``: The conjecture's current confidence score.
    - ``evidence_count``: Normalized count of supporting evidence items.
    - ``novelty``: Inverse of the statement length (longer = less novel
      by proxy — shorter, cleaner statements tend to be more fundamental).

    Attributes:
        _weights: Dictionary mapping criterion name to weight.  Weights are
            normalised to sum to 1.0 at score time.
    """

    _DEFAULT_WEIGHTS: dict[str, float] = {
        "confidence": 0.5,
        "evidence_count": 0.3,
        "novelty": 0.2,
    }

    def __init__(self, weights: dict | None = None) -> None:
        self._weights: dict[str, float] = weights or dict(self._DEFAULT_WEIGHTS)

    def rank(
        self,
        conjectures: list[ConjectureRecord],
    ) -> list[ConjectureRecord]:
        """Return conjectures sorted by composite score descending."""
        return sorted(conjectures, key=self.score, reverse=True)

    def score(self, conjecture: ConjectureRecord) -> float:
        """Compute the composite ranking score for a single conjecture."""
        w = self._weights
        total_weight = sum(w.values()) or 1.0

        confidence_score = conjecture.confidence
        evidence_score = _clamp(len(conjecture.supporting_evidence) / 10.0)
        stmt_len = len(conjecture.statement)
        novelty_score = _clamp(1.0 / stmt_len * 20.0) if stmt_len > 0 else 0.0

        weighted = (
            w.get("confidence", 0.5) * confidence_score
            + w.get("evidence_count", 0.3) * evidence_score
            + w.get("novelty", 0.2) * novelty_score
        )
        return _clamp(weighted / total_weight)


# ---------------------------------------------------------------------------
# OracleQueryOptimizer
# ---------------------------------------------------------------------------


class OracleQueryOptimizer:
    """Reduces oracle query volume by merging similar queries and enforcing budgets.

    Merging is based on token Jaccard similarity: queries with similarity
    above 0.7 are considered equivalent and collapsed to a single
    representative (Thm 51.5).

    Attributes:
        _policy: The :class:`OraclePolicy` governing budget constraints.
    """

    _MERGE_THRESHOLD: float = 0.7

    def __init__(self, policy: OraclePolicy) -> None:
        self._policy = policy

    def optimize(self, queries: list[OracleQuery]) -> list[OracleQuery]:
        """Return an optimized (merged and budget-trimmed) query list."""
        merged = self.merge_similar(queries)
        budget = self._policy.max_queries
        return self.prioritize(merged, budget)

    def merge_similar(self, queries: list[OracleQuery]) -> list[OracleQuery]:
        """Collapse groups of similar queries into a single representative.

        Two queries are similar if their Jaccard content similarity exceeds
        the merge threshold.  The first query encountered in each group is
        kept as the representative (Thm 51.5).
        """
        if not queries:
            return []
        kept: list[OracleQuery] = []
        for query in queries:
            dominated = any(
                self._similarity(query, k) > self._MERGE_THRESHOLD for k in kept
            )
            if not dominated:
                kept.append(query)
        _log.debug(
            "OracleQueryOptimizer: merged %d → %d queries",
            len(queries),
            len(kept),
        )
        return kept

    def prioritize(
        self,
        queries: list[OracleQuery],
        budget: int,
    ) -> list[OracleQuery]:
        """Return the first budget queries sorted by content length descending.

        Longer queries are presumed to be richer and more informative.
        """
        sorted_queries = sorted(queries, key=lambda q: len(q.content), reverse=True)
        return sorted_queries[:budget]

    def _similarity(self, a: OracleQuery, b: OracleQuery) -> float:
        """Return Jaccard token similarity between two query contents."""
        return _jaccard(_tokenize(a.content), _tokenize(b.content))

    def merge_ratio(self, original: list[OracleQuery]) -> float:
        """Return the fraction of queries eliminated by merging."""
        if not original:
            return 0.0
        merged = self.merge_similar(original)
        eliminated = len(original) - len(merged)
        return eliminated / len(original)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "BestFirstProofSearch",
    "BreadthFirstProofSearch",
    "ConjectureRankingAlgorithm",
    "LemmaRetrievalAlgorithm",
    "OracleQueryOptimizer",
    "ResearchAssistanceAlgorithm",
]
