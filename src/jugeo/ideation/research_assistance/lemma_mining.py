r"""Lemma mining engine for JuGeo research assistance — Chapter 51, §2.

The lemma miner discovers auxiliary lemmas from an indexed archive that are
relevant to a given :class:`~jugeo.ideation.research_assistance.models.ResearchContext`.
Relevance is measured via a TF-IDF-inspired token overlap score; the miner
filters, deduplicates, and ranks candidates before returning the top-K.

Mathematical framing
--------------------

Let :math:`A` be a lemma archive of size :math:`N` and let
:math:`\theta \in [0, 1]` be the minimum relevance threshold.  The
relevance score of lemma :math:`\ell \in A` with respect to context
:math:`\text{ctx}` is the Jaccard coefficient between the token sets:

.. math::

    \text{rel}(\ell, \text{ctx}) =
        \frac{|\text{tok}(\ell) \cap \text{tok}(\text{ctx})|}
             {|\text{tok}(\ell) \cup \text{tok}(\text{ctx})|}

where :math:`\text{tok}(\text{ctx})` is the union of tokens from the
current theorem, partial proof, and purpose string.

The miner returns all :math:`\ell` with :math:`\text{rel}(\ell, \text{ctx})
\geq \theta`, sorted descending by score and truncated to
:math:`\text{max\_candidates}` (Thm 51.2).

Time complexity is dominated by the final sort step:
:math:`O(N \log N)`.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from jugeo.ideation.research_assistance.models import (
    LemmaCandidate,
    LemmaSource,
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


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute the Jaccard coefficient between two token sets."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _normalize_statement(stmt: str) -> str:
    """Normalize a lemma statement for deduplication comparison."""
    return " ".join(sorted(_tokenize(stmt)))


# ---------------------------------------------------------------------------
# MiningConfig
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MiningConfig:
    """Configuration parameters for the :class:`LemmaMiner`.

    Attributes:
        max_candidates: Maximum number of candidates to return.
        min_relevance: Minimum relevance score threshold in [0, 1].
        deduplicate: Whether to deduplicate candidates by normalized statement.
        include_dependencies: Whether to include lemma dependency chains.
        max_proof_sketch_length: Truncate proof sketches beyond this length.
    """

    max_candidates: int = 50
    min_relevance: float = 0.2
    deduplicate: bool = True
    include_dependencies: bool = True
    max_proof_sketch_length: int = 500


# ---------------------------------------------------------------------------
# PatternExtractor
# ---------------------------------------------------------------------------


class PatternExtractor:
    """Extracts structural patterns from collections of theorem statements.

    Patterns are simplified templates where variable-like tokens are replaced
    with a placeholder.  This enables discovery of recurring proof idioms.
    """

    def extract(self, statements: list[str]) -> list[str]:
        """Return the list of unique structural templates from statements."""
        templates: list[str] = []
        seen: set[str] = set()
        for stmt in statements:
            tmpl = self.structural_template(stmt)
            if tmpl not in seen:
                seen.add(tmpl)
                templates.append(tmpl)
        return templates

    def common_patterns(self, statements: list[str]) -> dict[str, int]:
        """Return a mapping of structural template → occurrence count."""
        counter: Counter[str] = Counter()
        for stmt in statements:
            counter[self.structural_template(stmt)] += 1
        return dict(counter.most_common())

    def structural_template(self, stmt: str) -> str:
        """Replace variable-like tokens with VAR to produce a template.

        A token is considered variable-like if it contains a digit or is a
        single character.
        """
        tokens = re.split(r"(\s+)", stmt)
        result_parts: list[str] = []
        for token in tokens:
            if token.strip() == "":
                result_parts.append(token)
            elif re.search(r"\d", token) or len(token.strip()) == 1:
                result_parts.append("VAR")
            else:
                result_parts.append(token)
        normalized = " ".join(p.strip() for p in result_parts if p.strip())
        return normalized or stmt

    def variable_tokens(self, stmt: str) -> list[str]:
        """Return the list of tokens that would be replaced by VAR."""
        return [
            t
            for t in re.split(r"\s+", stmt)
            if t and (re.search(r"\d", t) or len(t) == 1)
        ]


# ---------------------------------------------------------------------------
# RelevanceScorer
# ---------------------------------------------------------------------------


class RelevanceScorer:
    """Scores lemma relevance using token overlap against a research context.

    The scorer computes the Jaccard coefficient between the lemma statement's
    token set and the aggregated context token set (theorem + proof + purpose).
    """

    def score(self, lemma_statement: str, context: ResearchContext) -> float:
        """Compute relevance score in [0, 1] for the given lemma and context."""
        lemma_tokens = _tokenize(lemma_statement)
        context_text = (
            context.current_theorem
            + " "
            + context.partial_proof
            + " "
            + context.purpose
        )
        context_tokens = _tokenize(context_text)
        return _jaccard(lemma_tokens, context_tokens)

    def keyword_overlap(self, a: str, b: str) -> float:
        """Return Jaccard overlap between token sets of two strings."""
        return _jaccard(_tokenize(a), _tokenize(b))

    def structural_similarity(self, a: str, b: str) -> float:
        """Compare structural templates of two statements for similarity."""
        extractor = PatternExtractor()
        tmpl_a = extractor.structural_template(a)
        tmpl_b = extractor.structural_template(b)
        return _jaccard(_tokenize(tmpl_a), _tokenize(tmpl_b))

    def batch_score(
        self,
        lemma_statements: list[str],
        context: ResearchContext,
    ) -> list[float]:
        """Return relevance scores for a list of lemma statements."""
        return [self.score(stmt, context) for stmt in lemma_statements]


# ---------------------------------------------------------------------------
# LemmaArchive
# ---------------------------------------------------------------------------


class LemmaArchive:
    """Indexed in-memory storage for :class:`LemmaCandidate` instances.

    Candidates are indexed by ``candidate_id`` and by tag for efficient
    retrieval.  The archive supports full-text search via token overlap.
    """

    def __init__(self) -> None:
        self._store: dict[str, LemmaCandidate] = {}
        self._by_tag: dict[str, list[LemmaCandidate]] = defaultdict(list)

    def add(self, lemma: LemmaCandidate) -> None:
        """Add a lemma to the archive, updating tag indices."""
        self._store[lemma.candidate_id] = lemma
        for tag in lemma.tags:
            if lemma not in self._by_tag[tag]:
                self._by_tag[tag].append(lemma)
        _log.debug("LemmaArchive: added lemma %s", lemma.candidate_id)

    def by_id(self, candidate_id: str) -> LemmaCandidate | None:
        """Return the lemma with the given id, or None."""
        return self._store.get(candidate_id)

    def by_tag(self, tag: str) -> list[LemmaCandidate]:
        """Return a copy of the lemma list associated with a tag."""
        return list(self._by_tag.get(tag, []))

    def all(self) -> list[LemmaCandidate]:
        """Return all stored lemmas in insertion order."""
        return list(self._store.values())

    def size(self) -> int:
        """Return the number of stored lemmas."""
        return len(self._store)

    def search(self, query: str) -> list[LemmaCandidate]:
        """Return lemmas with token overlap with query, sorted by overlap desc."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return self.all()

        scored: list[tuple[float, LemmaCandidate]] = []
        for lemma in self._store.values():
            overlap = _jaccard(query_tokens, _tokenize(lemma.statement))
            if overlap > 0.0:
                scored.append((overlap, lemma))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [lemma for _, lemma in scored]

    def remove(self, candidate_id: str) -> bool:
        """Remove a lemma; return True if it existed."""
        if candidate_id not in self._store:
            return False
        lemma = self._store.pop(candidate_id)
        for tag in lemma.tags:
            tag_list = self._by_tag.get(tag, [])
            if lemma in tag_list:
                tag_list.remove(lemma)
        return True

    def tag_index(self) -> dict[str, list[str]]:
        """Return a mapping of tag → list of candidate_ids."""
        return {
            tag: [c.candidate_id for c in candidates]
            for tag, candidates in self._by_tag.items()
        }


# ---------------------------------------------------------------------------
# LemmaMiner
# ---------------------------------------------------------------------------


class LemmaMiner:
    """Main lemma mining engine.

    Given a :class:`LemmaArchive` and a :class:`ResearchContext`, the miner
    computes relevance scores for all archived lemmas, filters by threshold,
    optionally deduplicates, and returns the top candidates.
    """

    def __init__(self, config: MiningConfig | None = None) -> None:
        self._config = config or MiningConfig()
        self._scorer = RelevanceScorer()
        self._extractor = PatternExtractor()

    def mine(
        self,
        archive: LemmaArchive,
        context: ResearchContext,
    ) -> list[LemmaCandidate]:
        """Mine the archive for relevant lemma candidates.

        Returns a list of up to ``config.max_candidates`` lemmas with
        relevance_score >= ``config.min_relevance``, sorted by score desc.
        """
        candidates = self._build_candidates(archive, context)
        filtered = [
            c for c in candidates if c.relevance_score >= self._config.min_relevance
        ]
        if self._config.deduplicate:
            filtered = self.deduplicate(filtered)

        filtered.sort(key=lambda c: c.relevance_score, reverse=True)
        result = filtered[: self._config.max_candidates]
        _log.debug(
            "LemmaMiner: mine returned %d/%d candidates (threshold=%.2f)",
            len(result),
            archive.size(),
            self._config.min_relevance,
        )
        return result

    def score_relevance(
        self,
        lemma: LemmaCandidate,
        context: ResearchContext,
    ) -> float:
        """Delegate relevance scoring to the internal :class:`RelevanceScorer`."""
        return self._scorer.score(lemma.statement, context)

    def deduplicate(
        self,
        candidates: list[LemmaCandidate],
    ) -> list[LemmaCandidate]:
        """Remove duplicate candidates, keeping the highest-scored per statement.

        Two candidates are considered duplicates if their normalized statement
        (sorted token bag) is identical.  The one with the higher
        ``relevance_score`` is retained (Thm 51.11).
        """
        best: dict[str, LemmaCandidate] = {}
        for candidate in candidates:
            key = _normalize_statement(candidate.statement)
            if key not in best or candidate.relevance_score > best[key].relevance_score:
                best[key] = candidate
        return list(best.values())

    def _build_candidates(
        self,
        archive: LemmaArchive,
        context: ResearchContext,
    ) -> list[LemmaCandidate]:
        """Build a scored list of all lemmas from the archive.

        Each returned candidate has its ``relevance_score`` updated to reflect
        the score against the current context.
        """
        result: list[LemmaCandidate] = []
        for lemma in archive.all():
            new_score = self.score_relevance(lemma, context)
            sketch = lemma.proof_sketch
            if len(sketch) > self._config.max_proof_sketch_length:
                sketch = sketch[: self._config.max_proof_sketch_length] + "…"
            updated = replace(lemma, relevance_score=new_score, proof_sketch=sketch)
            result.append(updated)
        return result

    def with_config(self, **kwargs: object) -> LemmaMiner:
        """Return a new LemmaMiner with updated config fields."""
        import dataclasses

        new_config = dataclasses.replace(self._config, **kwargs)
        return LemmaMiner(config=new_config)


# ---------------------------------------------------------------------------
# Archive factory
# ---------------------------------------------------------------------------


def make_archive_from_statements(
    statements: list[str],
    *,
    source: LemmaSource = LemmaSource.MINED,
) -> LemmaArchive:
    """Build a :class:`LemmaArchive` from a list of raw statement strings."""
    archive = LemmaArchive()
    for stmt in statements:
        lemma = LemmaCandidate(
            candidate_id=str(uuid.uuid4()),
            statement=stmt,
            proof_sketch="",
            relevance_score=0.0,
            source=source,
        )
        archive.add(lemma)
    return archive


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "LemmaArchive",
    "LemmaMiner",
    "MiningConfig",
    "PatternExtractor",
    "RelevanceScorer",
    "make_archive_from_statements",
]
