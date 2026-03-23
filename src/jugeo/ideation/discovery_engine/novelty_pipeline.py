"""Novelty pipeline stage for the JuGeo discovery engine — theory2.tex Ch58.

This module implements Stage 1 of the discovery pipeline: novelty filtering
and ranking.  It filters discovery candidates by novelty score, ranks the
survivors using evidence-weighted Jaccard distance, deduplicates near-identical
candidates, and selects a final ranked candidate set for downstream stages.

Theory reference: theory2.tex Ch58 §5.1 — Novelty Pipeline Stage.

copilot: shared-core marker

Overview
--------
The novelty pipeline is the first gate in the four-stage discovery engine.
Its responsibility is to ensure that only genuinely novel candidates proceed
into the more computationally expensive kind-classification and theorem-synthesis
stages.  A candidate is considered *novel* if its token set is sufficiently
dissimilar from all previously seen candidates and if its raw novelty score
(as attached to the ``DiscoveryCandidate`` object by the ideation subsystem)
exceeds a configurable threshold.

Pipeline steps (in order):
    1. **Filter** — discard candidates whose novelty score falls below
       ``DiscoveryConfig.novelty_threshold`` (default 0.3).
    2. **Rank** — sort surviving candidates by a composite score that combines
       raw novelty score, evidence weight, and a diversity bonus derived from
       evidence-weighted Jaccard distance to the already-selected set.
    3. **Deduplicate** — remove candidates whose token-set Jaccard similarity
       to any already-kept candidate exceeds ``dedup_threshold`` (default 0.9).

Typical usage::

    from jugeo.ideation.discovery_engine.novelty_pipeline import (
        run_novelty_pipeline,
        NoveltyPipelineRunner,
        NoveltyFilter,
        NoveltyRanker,
        NoveltyCandidateSet,
    )

    # Simple one-shot API
    stage = run_novelty_pipeline(candidates, config=cfg)

    # Fine-grained API
    runner = NoveltyPipelineRunner(config=cfg)
    stage, diag = runner.run_with_diagnostics(candidates)

Design notes
------------
* All classes are intentionally stateless with respect to the candidate corpus —
  state is threaded through method parameters so that pipeline runners are safe
  to re-use across multiple runs without resetting internal caches.
* The ``NoveltyCandidateSet`` class *is* stateful and maintains a running
  collection; callers that need a fresh run must call ``reset()`` or instantiate
  a new object.
* Jaccard distance is computed on *lower-cased, whitespace-split* token sets.
  Future versions may switch to a shingled n-gram representation for better
  semantic sensitivity.

See also
--------
* ``kind_classification`` — consumes the output of this module.
* ``jugeo.ideation.novelty.NoveltyScore`` — the raw novelty scoring subsystem.
* ``jugeo.ideation.discovery_engine.models`` — shared dataclasses.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "NoveltyFilter",
    "NoveltyRanker",
    "NoveltyCandidateSet",
    "NoveltyPipelineRunner",
    "run_novelty_pipeline",
    # helpers exposed for testing / scripting
    "_utcnow",
    "_uid",
    "_clamp",
    "_score_novelty",
    "_rank_candidates",
    "_deduplicate",
    "_tokenize_candidate",
    "_jaccard",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.discovery_engine.models import (
        DiscoveryCandidate,
        DiscoveryConfig,
        DiscoveryResult,
        DiscoveryDiagnostics,
        DiscoveryStatus,
        PipelineStage,
        KindSignature,
        TheoremCandidate,
        PromotionDecision,
        NoveltyPipelineStage,
        KindClassificationStage,
        TheoremSynthesisStage,
        PackPromotionStage,
    )
except Exception:
    # Provide sentinel stubs so the module remains importable without the full
    # jugeo package installed (e.g. during documentation builds or unit tests
    # that mock the models layer).
    DiscoveryCandidate = Any  # type: ignore[misc,assignment]
    DiscoveryConfig = Any  # type: ignore[misc,assignment]
    DiscoveryDiagnostics = Any  # type: ignore[misc,assignment]
    NoveltyPipelineStage = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    Using ``time.time()`` rather than ``datetime.utcnow()`` avoids the
    deprecation warning introduced in Python 3.12 and keeps the return type
    as a simple ``float``, which serialises cleanly to JSON / MessagePack.

    Returns
    -------
    float
        Current UTC time in seconds since the Unix epoch.

    Examples
    --------
    >>> t = _utcnow()
    >>> isinstance(t, float)
    True
    >>> t > 1_700_000_000.0   # After Nov 2023 — sanity check
    True
    """
    return time.time()


def _uid() -> str:
    """Generate a short, URL-safe unique identifier.

    The identifier is derived from a UUID4 random value with the hyphens
    stripped, giving a 32-character hexadecimal string.  This is *not*
    guaranteed to be globally unique across distributed systems, but is
    sufficient for within-pipeline object identity where the probability of
    collision within a single run is negligible.

    Returns
    -------
    str
        A 32-character lowercase hexadecimal string.

    Examples
    --------
    >>> uid = _uid()
    >>> len(uid)
    32
    >>> uid.isalnum()
    True
    """
    return uuid.uuid4().hex


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lower*, *upper*].

    Parameters
    ----------
    value:
        The value to clamp.
    lower:
        Inclusive lower bound.  Defaults to ``0.0``.
    upper:
        Inclusive upper bound.  Defaults to ``1.0``.

    Returns
    -------
    float
        The clamped value in ``[lower, upper]``.

    Raises
    ------
    ValueError
        If ``lower > upper``.

    Examples
    --------
    >>> _clamp(1.5)
    1.0
    >>> _clamp(-0.1)
    0.0
    >>> _clamp(0.7)
    0.7
    >>> _clamp(3.0, lower=1.0, upper=2.0)
    2.0
    """
    if lower > upper:
        raise ValueError(f"lower ({lower}) must not exceed upper ({upper})")
    return max(lower, min(upper, value))


def _tokenize_candidate(candidate: Any) -> set[str]:
    """Extract a normalised token set from a discovery candidate.

    Tokens are derived from the candidate's ``description`` attribute (if
    present) and any ``domain_tags`` or ``labels`` iterable attributes.  All
    tokens are lower-cased and non-alphanumeric characters are stripped.

    Parameters
    ----------
    candidate:
        A ``DiscoveryCandidate`` instance, or any object with a ``description``
        string attribute and optional ``domain_tags`` / ``labels`` iterables.

    Returns
    -------
    set[str]
        A set of normalised, non-empty token strings.

    Notes
    -----
    Empty strings and single-character tokens are removed to reduce noise
    in downstream Jaccard computations.

    Examples
    --------
    >>> class C:
    ...     description = "Smooth manifold with boundary"
    ...     domain_tags = ("topology", "geometry")
    >>> _tokenize_candidate(C())
    {'smooth', 'manifold', 'with', 'boundary', 'topology', 'geometry'}
    """
    tokens: set[str] = set()

    description: str = getattr(candidate, "description", "") or ""
    for raw in description.split():
        clean = "".join(ch for ch in raw.lower() if ch.isalnum())
        if len(clean) > 1:
            tokens.add(clean)

    for attr in ("domain_tags", "labels", "keywords"):
        iterable = getattr(candidate, attr, None)
        if iterable:
            for tag in iterable:
                clean = "".join(ch for ch in str(tag).lower() if ch.isalnum())
                if len(clean) > 1:
                    tokens.add(clean)

    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    """Compute the Jaccard similarity coefficient between two token sets.

    The Jaccard similarity is defined as ``|A ∩ B| / |A ∪ B|``.  It returns
    ``1.0`` when both sets are identical (including when both are empty) and
    ``0.0`` when they share no elements.

    Parameters
    ----------
    a:
        First token set.
    b:
        Second token set.

    Returns
    -------
    float
        Jaccard similarity in ``[0.0, 1.0]``.

    Examples
    --------
    >>> _jaccard({'a', 'b', 'c'}, {'b', 'c', 'd'})
    0.5
    >>> _jaccard(set(), set())
    1.0
    >>> _jaccard({'x'}, {'y'})
    0.0
    """
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _score_novelty(candidate: Any, corpus_tokens: set[str]) -> float:
    """Compute an adjusted novelty score relative to a running token corpus.

    The raw ``novelty_score`` attribute of the candidate is scaled by one
    minus the Jaccard similarity between the candidate's tokens and the
    corpus tokens, penalising candidates whose vocabulary is already well
    represented in the corpus.

    Parameters
    ----------
    candidate:
        A ``DiscoveryCandidate``-like object with a ``novelty_score`` float
        attribute (defaults to 0.5 if absent).
    corpus_tokens:
        Union of all tokens seen in the corpus so far.

    Returns
    -------
    float
        Adjusted novelty score in ``[0.0, 1.0]``.

    Notes
    -----
    When *corpus_tokens* is empty the function returns the raw novelty score
    unchanged, since there is no existing vocabulary to penalise against.

    Examples
    --------
    >>> class C:
    ...     novelty_score = 0.8
    ...     description = "entirely new concept"
    ...     domain_tags = ()
    >>> _score_novelty(C(), set())
    0.8
    """
    raw: float = _clamp(float(getattr(candidate, "novelty_score", 0.5)))
    if not corpus_tokens:
        return raw
    candidate_tokens = _tokenize_candidate(candidate)
    overlap = _jaccard(candidate_tokens, corpus_tokens)
    adjusted = raw * (1.0 - overlap * 0.5)  # soft penalty: up to 50% reduction
    return _clamp(adjusted)


def _rank_candidates(
    candidates: list[Any],
    weights: dict[str, float] | None = None,
) -> list[Any]:
    """Sort *candidates* by a composite ranking score in descending order.

    The composite score is a weighted sum of:
    * ``novelty_score`` — raw novelty as reported by the ideation subsystem.
    * ``evidence_weight`` — derived from the candidate's evidence records.
    * ``trust_score`` — derived from the candidate's trust profile (if any).

    Parameters
    ----------
    candidates:
        List of ``DiscoveryCandidate``-like objects.
    weights:
        Optional mapping from attribute name to weight multiplier.  Defaults
        to ``NoveltyRanker.DEFAULT_WEIGHTS`` if ``None``.

    Returns
    -------
    list
        A new list sorted by composite score, highest first.

    Notes
    -----
    The function does *not* mutate the input list.

    Examples
    --------
    >>> class C:
    ...     def __init__(self, s): self.novelty_score = s; self.description = ""
    ...     domain_tags = ()
    >>> ranked = _rank_candidates([C(0.2), C(0.9), C(0.5)])
    >>> ranked[0].novelty_score
    0.9
    """
    if weights is None:
        weights = {"novelty_score": 0.6, "evidence_weight": 0.3, "trust_score": 0.1}

    def _composite(c: Any) -> float:
        score = 0.0
        for attr, w in weights.items():
            score += w * _clamp(float(getattr(c, attr, 0.0)))
        return score

    return sorted(candidates, key=_composite, reverse=True)


def _deduplicate(
    candidates: list[Any],
    similarity_threshold: float = 0.9,
) -> list[Any]:
    """Remove near-duplicate candidates based on Jaccard similarity.

    Iterates through *candidates* in order (assumed to be pre-ranked).  A
    candidate is kept only if its token-set Jaccard similarity to every
    already-kept candidate is below *similarity_threshold*.

    Parameters
    ----------
    candidates:
        Pre-ranked list of ``DiscoveryCandidate``-like objects.
    similarity_threshold:
        Jaccard similarity above which two candidates are considered duplicates.
        Range: ``[0.0, 1.0]``.  Defaults to ``0.9``.

    Returns
    -------
    list
        De-duplicated list preserving the original ranking order.

    Notes
    -----
    Time complexity is O(n²) in the worst case; this is acceptable because
    the novelty filter should reduce the candidate pool to a manageable size
    before deduplication is run.

    Examples
    --------
    >>> class C:
    ...     def __init__(self, d): self.description = d; self.domain_tags = ()
    >>> cs = [C("topology geometry"), C("topology geometry bounds"), C("algebra rings")]
    >>> deduped = _deduplicate(cs, similarity_threshold=0.5)
    >>> len(deduped)
    2
    """
    kept: list[Any] = []
    kept_tokens: list[set[str]] = []
    for candidate in candidates:
        tokens = _tokenize_candidate(candidate)
        if all(_jaccard(tokens, kt) < similarity_threshold for kt in kept_tokens):
            kept.append(candidate)
            kept_tokens.append(tokens)
    return kept


# ---------------------------------------------------------------------------
# NoveltyFilter
# ---------------------------------------------------------------------------


class NoveltyFilter:
    """Filter discovery candidates by novelty score and domain-token density.

    A candidate passes the filter if both of the following conditions hold:

    1. Its ``novelty_score`` attribute is greater than or equal to
       ``self.threshold``.
    2. The number of distinct domain-related tokens (derived from
       ``domain_tags`` or similar attributes) is at least
       ``self.min_domain_tokens``.

    The second condition prevents trivially short or label-free candidates
    from proceeding; such candidates typically lack enough semantic content
    to be useful for kind classification.

    Parameters
    ----------
    threshold:
        Minimum novelty score required to pass.  Defaults to ``0.3``.
        Must be in ``[0.0, 1.0]``.
    min_domain_tokens:
        Minimum number of distinct domain tokens required.  Defaults to ``2``.

    Attributes
    ----------
    threshold : float
        See *threshold* parameter.
    min_domain_tokens : int
        See *min_domain_tokens* parameter.

    Examples
    --------
    Basic usage::

        nf = NoveltyFilter(threshold=0.4, min_domain_tokens=3)
        passing = nf.filter(candidates)

    Diagnostics::

        passing, diag = nf.filter_with_diagnostics(candidates)
        print(diag["rejected_count"])  # how many were dropped
        print(diag["rejection_reasons"])  # per-candidate reasons

    Threshold guidance
    ------------------
    * ``0.0`` — all candidates pass (useful for debugging).
    * ``0.3`` — default; removes clearly unoriginal candidates.
    * ``0.5`` — moderate filtering; recommended for large ideation batches.
    * ``0.7`` — aggressive; use only when the corpus is already rich.
    * ``1.0`` — only maximally novel candidates pass (essentially empty output).

    Notes
    -----
    The filter does not modify candidates in place; it returns a new list.
    """

    def __init__(
        self,
        threshold: float = 0.3,
        min_domain_tokens: int = 2,
    ) -> None:
        self.threshold = _clamp(threshold)
        self.min_domain_tokens = max(0, int(min_domain_tokens))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def filter(self, candidates: list[Any]) -> list[Any]:
        """Return a filtered list containing only candidates that pass.

        Parameters
        ----------
        candidates:
            Input list of ``DiscoveryCandidate``-like objects.

        Returns
        -------
        list
            Sub-list of *candidates* that satisfy both filter conditions.
            Preserves the relative order of the input list.

        Examples
        --------
        >>> nf = NoveltyFilter(threshold=0.5)
        >>> # Assume `candidates` is populated from the ideation subsystem
        >>> passing = nf.filter(candidates)
        """
        return [c for c in candidates if self._passes_threshold(c)]

    def filter_with_diagnostics(
        self,
        candidates: list[Any],
    ) -> tuple[list[Any], dict[str, Any]]:
        """Filter candidates and return detailed rejection diagnostics.

        Parameters
        ----------
        candidates:
            Input list of ``DiscoveryCandidate``-like objects.

        Returns
        -------
        tuple[list, dict]
            A 2-tuple of ``(passing_candidates, diagnostics_dict)``.
            The diagnostics dict contains:

            * ``"total"`` — int, total input count.
            * ``"passed_count"`` — int, number that passed.
            * ``"rejected_count"`` — int, number that were rejected.
            * ``"rejection_reasons"`` — dict mapping candidate id → reason str.
            * ``"pass_rate"`` — float in ``[0.0, 1.0]``.
            * ``"threshold_used"`` — float, the threshold applied.
            * ``"min_domain_tokens_used"`` — int.
        """
        passing: list[Any] = []
        rejection_reasons: dict[str, str] = {}

        for candidate in candidates:
            cid = str(getattr(candidate, "candidate_id", id(candidate)))
            if self._passes_threshold(candidate):
                passing.append(candidate)
            else:
                score = float(getattr(candidate, "novelty_score", 0.0))
                dt_count = self._compute_domain_token_count(candidate)
                if score < self.threshold:
                    rejection_reasons[cid] = (
                        f"novelty_score {score:.3f} < threshold {self.threshold:.3f}"
                    )
                else:
                    rejection_reasons[cid] = (
                        f"domain_token_count {dt_count} < min {self.min_domain_tokens}"
                    )

        total = len(candidates)
        passed = len(passing)
        diag: dict[str, Any] = {
            "total": total,
            "passed_count": passed,
            "rejected_count": total - passed,
            "rejection_reasons": rejection_reasons,
            "pass_rate": passed / total if total > 0 else 0.0,
            "threshold_used": self.threshold,
            "min_domain_tokens_used": self.min_domain_tokens,
        }
        return passing, diag

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _passes_threshold(self, candidate: Any) -> bool:
        """Return True if *candidate* meets both filter conditions."""
        score = float(getattr(candidate, "novelty_score", 0.0))
        if score < self.threshold:
            return False
        return self._compute_domain_token_count(candidate) >= self.min_domain_tokens

    def _compute_domain_token_count(self, candidate: Any) -> int:
        """Count distinct domain-related tokens for *candidate*."""
        count = 0
        for attr in ("domain_tags", "labels", "keywords", "domain"):
            iterable = getattr(candidate, attr, None)
            if isinstance(iterable, str):
                count += len(iterable.split())
            elif iterable:
                count += len(list(iterable))
        return count


# ---------------------------------------------------------------------------
# NoveltyRanker
# ---------------------------------------------------------------------------


class NoveltyRanker:
    """Rank discovery candidates by a composite novelty-diversity score.

    The ranking algorithm combines three signals:

    1. **Raw novelty** — ``candidate.novelty_score``, weighted by
       ``weights["novelty_score"]``.
    2. **Evidence strength** — a proxy for how well-evidenced the candidate
       is, computed from the number and kind of evidence records attached,
       weighted by ``weights["evidence_weight"]``.
    3. **Diversity bonus** — a bonus that rewards candidates whose token sets
       are dissimilar to the candidates already placed in the ranked output.
       This prevents the top-k from collapsing to near-identical candidates.

    Parameters
    ----------
    weights:
        Optional dict mapping signal names to non-negative floats.  Weights
        are *not* required to sum to 1; they are applied as plain multipliers.
        Missing keys fall back to the ``DEFAULT_WEIGHTS`` class attribute.

    Class Attributes
    ----------------
    DEFAULT_WEIGHTS : dict[str, float]
        ``{"novelty_score": 0.6, "evidence_weight": 0.25, "diversity_bonus": 0.15}``

    Examples
    --------
    Default ranking::

        ranker = NoveltyRanker()
        ranked = ranker.rank(candidates)

    Custom weights emphasising evidence::

        ranker = NoveltyRanker(weights={"novelty_score": 0.4, "evidence_weight": 0.5,
                                        "diversity_bonus": 0.1})
        ranked = ranker.rank(candidates)

    With scores for inspection::

        for candidate, score in ranker.rank_with_scores(candidates):
            print(f"{candidate.candidate_id}: {score:.3f}")

    Algorithm notes
    ---------------
    The diversity bonus is computed lazily as each candidate is placed: it
    equals one minus the maximum Jaccard similarity between the candidate and
    any already-ranked candidate.  This makes the ranking greedy rather than
    globally optimal, but is significantly cheaper (O(n²) vs exponential for
    the optimal solution).
    """

    DEFAULT_WEIGHTS: dict[str, float] = {
        "novelty_score": 0.6,
        "evidence_weight": 0.25,
        "diversity_bonus": 0.15,
    }

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights: dict[str, float] = {**self.DEFAULT_WEIGHTS, **(weights or {})}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank(self, candidates: list[Any]) -> list[Any]:
        """Return candidates sorted by composite score, highest first.

        Parameters
        ----------
        candidates:
            Input list of ``DiscoveryCandidate``-like objects.

        Returns
        -------
        list
            New sorted list; the input list is not modified.
        """
        return [c for c, _ in self.rank_with_scores(candidates)]

    def rank_with_scores(
        self,
        candidates: list[Any],
    ) -> list[tuple[Any, float]]:
        """Return ``(candidate, composite_score)`` pairs sorted descending.

        Parameters
        ----------
        candidates:
            Input list.

        Returns
        -------
        list[tuple[candidate, float]]
            Pairs sorted by composite score, highest first.
        """
        if not candidates:
            return []

        scored: list[tuple[Any, float]] = []
        already_ranked: list[Any] = []

        # Precompute base scores (novelty + evidence) for all candidates
        base_scores = {
            id(c): (
                self.weights.get("novelty_score", 0.6)
                * _clamp(float(getattr(c, "novelty_score", 0.0)))
                + self.weights.get("evidence_weight", 0.25)
                * self._evidence_weight(c)
            )
            for c in candidates
        }

        # Greedy selection loop incorporating diversity bonus
        remaining = list(candidates)
        while remaining:
            best: Any | None = None
            best_score = -math.inf
            for c in remaining:
                div = self._diversity_bonus(c, already_ranked)
                total = base_scores[id(c)] + self.weights.get("diversity_bonus", 0.15) * div
                if total > best_score:
                    best_score = total
                    best = c
            if best is not None:
                scored.append((best, best_score))
                already_ranked.append(best)
                remaining.remove(best)

        return scored

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_score(self, candidate: Any, all_candidates: list[Any]) -> float:
        """Compute composite score for *candidate* relative to *all_candidates*."""
        novelty = _clamp(float(getattr(candidate, "novelty_score", 0.0)))
        ev = self._evidence_weight(candidate)
        # Use average Jaccard distance to all others as a proxy for diversity
        tokens = _tokenize_candidate(candidate)
        other_tokens = [_tokenize_candidate(c) for c in all_candidates if c is not candidate]
        if other_tokens:
            avg_sim = sum(_jaccard(tokens, ot) for ot in other_tokens) / len(other_tokens)
            div = 1.0 - avg_sim
        else:
            div = 1.0
        return (
            self.weights.get("novelty_score", 0.6) * novelty
            + self.weights.get("evidence_weight", 0.25) * ev
            + self.weights.get("diversity_bonus", 0.15) * div
        )

    def _diversity_bonus(self, candidate: Any, already_selected: list[Any]) -> float:
        """Return diversity bonus in ``[0.0, 1.0]`` for *candidate*.

        The bonus is one minus the maximum Jaccard similarity between
        *candidate* and any candidate in *already_selected*.  Higher bonus
        means the candidate is more diverse relative to the current selection.

        Parameters
        ----------
        candidate:
            The candidate being evaluated.
        already_selected:
            Candidates already placed into the ranked output.

        Returns
        -------
        float
            Diversity bonus in ``[0.0, 1.0]``.
        """
        if not already_selected:
            return 1.0
        tokens = _tokenize_candidate(candidate)
        max_sim = max(_jaccard(tokens, _tokenize_candidate(s)) for s in already_selected)
        return 1.0 - max_sim

    def _evidence_weight(self, candidate: Any) -> float:
        """Derive an evidence weight in ``[0.0, 1.0]`` for *candidate*.

        The weight is a simple proxy: ``min(n_evidence_records / 5.0, 1.0)``
        where *n_evidence_records* is the number of evidence records attached
        to the candidate (via ``evidence_records`` or ``evidence`` attribute).

        Parameters
        ----------
        candidate:
            Any object with an optional ``evidence_records`` or ``evidence``
            list attribute.

        Returns
        -------
        float
            Evidence weight in ``[0.0, 1.0]``.
        """
        for attr in ("evidence_records", "evidence", "evidence_list"):
            ev = getattr(candidate, attr, None)
            if ev is not None:
                return _clamp(len(list(ev)) / 5.0)
        return 0.0


# ---------------------------------------------------------------------------
# NoveltyCandidateSet
# ---------------------------------------------------------------------------


class NoveltyCandidateSet:
    """A stateful, de-duplicating collection of discovery candidates.

    Candidates are stored in insertion order.  Adding a candidate that is
    "too similar" to an existing member (as measured by Jaccard similarity)
    is silently rejected.  The set maintains an internal index of token sets
    to make duplicate detection efficient.

    Parameters
    ----------
    dedup_threshold:
        Jaccard similarity above which a new candidate is considered a
        duplicate of an existing one.  Defaults to ``0.9``.

    Examples
    --------
    Basic accumulation::

        cs = NoveltyCandidateSet(dedup_threshold=0.85)
        for c in stream_of_candidates:
            added = cs.add(c)
            if not added:
                print(f"Duplicate skipped: {c.candidate_id}")
        top10 = cs.get_top_k(10)

    Batch addition::

        n_added = cs.add_many(batch)
        print(f"Added {n_added} / {len(batch)} from batch.")

    Membership test::

        if some_candidate in cs:
            print("Already in set")

    Notes
    -----
    The ``__contains__`` dunder uses candidate *identity* (``id()``), not
    duplicate detection; to check for near-duplicates, call
    ``_is_duplicate(candidate)`` directly.
    """

    def __init__(self, dedup_threshold: float = 0.9) -> None:
        self.dedup_threshold = _clamp(dedup_threshold)
        self._candidates: list[Any] = []
        self._id_set: set[int] = set()          # Python object ids for __contains__
        self._token_cache: list[set[str]] = []  # parallel to _candidates

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, candidate: Any) -> bool:
        """Add *candidate* if it is not a near-duplicate of an existing member.

        Parameters
        ----------
        candidate:
            A ``DiscoveryCandidate``-like object.

        Returns
        -------
        bool
            ``True`` if the candidate was added; ``False`` if it was rejected
            as a near-duplicate.

        Examples
        --------
        >>> cs = NoveltyCandidateSet()
        >>> cs.add(c1)
        True
        >>> cs.add(c1)  # identical object — rejected
        False
        """
        if self._is_duplicate(candidate):
            return False
        self._candidates.append(candidate)
        self._id_set.add(id(candidate))
        self._token_cache.append(_tokenize_candidate(candidate))
        return True

    def add_many(self, candidates: list[Any]) -> int:
        """Add multiple candidates, returning the count of successfully added ones.

        Parameters
        ----------
        candidates:
            List of ``DiscoveryCandidate``-like objects.

        Returns
        -------
        int
            Number of candidates actually added (non-duplicates).
        """
        return sum(1 for c in candidates if self.add(c))

    def get_all(self) -> list[Any]:
        """Return all candidates in insertion order.

        Returns
        -------
        list
            Shallow copy of the internal candidate list.
        """
        return list(self._candidates)

    def get_top_k(self, k: int) -> list[Any]:
        """Return the top-*k* candidates ranked by novelty score.

        Parameters
        ----------
        k:
            Maximum number of candidates to return.  If the set contains
            fewer than *k* candidates, all are returned.

        Returns
        -------
        list
            Up to *k* candidates, sorted by ``novelty_score`` descending.
        """
        ranked = sorted(
            self._candidates,
            key=lambda c: float(getattr(c, "novelty_score", 0.0)),
            reverse=True,
        )
        return ranked[:k]

    def remove(self, candidate_id: str) -> bool:
        """Remove a candidate by its ``candidate_id`` attribute.

        Parameters
        ----------
        candidate_id:
            The string ID to look up.

        Returns
        -------
        bool
            ``True`` if a candidate was found and removed; ``False`` otherwise.
        """
        for i, c in enumerate(self._candidates):
            if str(getattr(c, "candidate_id", "")) == candidate_id:
                self._id_set.discard(id(c))
                del self._candidates[i]
                del self._token_cache[i]
                return True
        return False

    def __len__(self) -> int:
        """Return the number of candidates in the set."""
        return len(self._candidates)

    def __contains__(self, candidate: Any) -> bool:
        """Return True if *candidate* (by object identity) is in the set."""
        return id(candidate) in self._id_set

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_duplicate(self, candidate: Any) -> bool:
        """Return True if *candidate* is a near-duplicate of any existing member.

        A candidate is considered a near-duplicate if its Jaccard similarity
        to any existing token set equals or exceeds ``self.dedup_threshold``.

        Parameters
        ----------
        candidate:
            The candidate to test.

        Returns
        -------
        bool
        """
        if id(candidate) in self._id_set:
            return True
        tokens = _tokenize_candidate(candidate)
        return any(
            _jaccard(tokens, existing) >= self.dedup_threshold
            for existing in self._token_cache
        )


# ---------------------------------------------------------------------------
# NoveltyPipelineRunner
# ---------------------------------------------------------------------------


class NoveltyPipelineRunner:
    """Orchestrate the full novelty pipeline across filter → rank → dedup steps.

    This class composes ``NoveltyFilter``, ``NoveltyRanker``, and
    ``NoveltyCandidateSet`` into a single pipeline that accepts a raw list of
    discovery candidates and returns a ``NoveltyPipelineStage`` result object.

    Parameters
    ----------
    config:
        Optional ``DiscoveryConfig`` instance controlling pipeline thresholds.
        If ``None``, defaults are used for every parameter.

    Examples
    --------
    Minimal run::

        runner = NoveltyPipelineRunner()
        stage = runner.run(candidates)

    With diagnostics::

        runner = NoveltyPipelineRunner(config=my_config)
        stage, diag = runner.run_with_diagnostics(candidates)
        print(diag)

    Re-using a runner for multiple batches::

        runner = NoveltyPipelineRunner(config=cfg)
        for batch in batches:
            stage = runner.run(batch)
            process(stage)
        # No state is carried between runs; runner is safe to re-use.

    Notes
    -----
    The runner itself is *stateless* — each call to ``run()`` or
    ``run_with_diagnostics()`` starts fresh.  Internal helpers create new
    ``NoveltyFilter`` and ``NoveltyRanker`` instances on each invocation so
    that config changes take effect immediately after ``set_config()``.
    """

    def __init__(self, config: Any | None = None) -> None:
        self._config: Any | None = config
        self._run_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, candidates: list[Any]) -> Any:
        """Execute the novelty pipeline and return a ``NoveltyPipelineStage``.

        Parameters
        ----------
        candidates:
            Raw list of ``DiscoveryCandidate``-like objects.

        Returns
        -------
        NoveltyPipelineStage
            Stage object containing the ranked, de-duplicated survivor list
            and pipeline metadata.
        """
        stage, _ = self.run_with_diagnostics(candidates)
        return stage

    def run_with_diagnostics(
        self,
        candidates: list[Any],
    ) -> tuple[Any, Any]:
        """Execute the pipeline and also return rich diagnostic information.

        Parameters
        ----------
        candidates:
            Raw list of ``DiscoveryCandidate``-like objects.

        Returns
        -------
        tuple[NoveltyPipelineStage, DiscoveryDiagnostics]
            The stage result and a diagnostics object describing filter
            statistics, timing information, and rejection reasons.
        """
        start = _utcnow()
        all_diag: dict[str, Any] = {
            "stage": "novelty_pipeline",
            "run_id": _uid(),
            "input_count": len(candidates),
        }

        # Step 1: Filter
        filtered = self._run_filter_step(candidates, self._config)
        all_diag["after_filter_count"] = len(filtered)

        # Step 2: Rank
        ranked = self._run_rank_step(filtered, self._config)
        all_diag["after_rank_count"] = len(ranked)

        # Step 3: Dedup
        deduped = self._run_dedup_step(ranked, self._config)
        all_diag["after_dedup_count"] = len(deduped)

        all_diag["elapsed_secs"] = _utcnow() - start
        self._run_count += 1

        # Build stage object — gracefully handle missing model class
        try:
            stage = NoveltyPipelineStage(  # type: ignore[call-arg]
                stage_id=_uid(),
                candidates=tuple(deduped),
                input_count=len(candidates),
                output_count=len(deduped),
                elapsed_secs=all_diag["elapsed_secs"],
            )
        except Exception:
            # Fall back to a plain dict if the dataclass isn't available
            stage = {  # type: ignore[assignment]
                "stage": "novelty_pipeline",
                "candidates": deduped,
                "input_count": len(candidates),
                "output_count": len(deduped),
            }

        try:
            diag = DiscoveryDiagnostics(**all_diag)  # type: ignore[call-arg]
        except Exception:
            diag = all_diag  # type: ignore[assignment]

        return stage, diag

    def reset(self) -> None:
        """Reset the run counter (has no effect on pipeline output)."""
        self._run_count = 0

    def set_config(self, config: Any) -> None:
        """Replace the current config with *config*.

        Parameters
        ----------
        config:
            New ``DiscoveryConfig`` instance to use for subsequent runs.
        """
        self._config = config

    # ------------------------------------------------------------------
    # Private step runners
    # ------------------------------------------------------------------

    def _run_filter_step(self, candidates: list[Any], config: Any | None) -> list[Any]:
        """Apply novelty filtering to *candidates*."""
        threshold = float(getattr(config, "novelty_threshold", 0.3)) if config else 0.3
        min_dt = int(getattr(config, "min_domain_tokens", 2)) if config else 2
        nf = NoveltyFilter(threshold=threshold, min_domain_tokens=min_dt)
        return nf.filter(candidates)

    def _run_rank_step(self, candidates: list[Any], config: Any | None) -> list[Any]:
        """Rank *candidates* by composite novelty-diversity score."""
        ranker = NoveltyRanker()
        return ranker.rank(candidates)

    def _run_dedup_step(self, candidates: list[Any], config: Any | None) -> list[Any]:
        """Deduplicate *candidates* using token-set Jaccard similarity."""
        threshold = float(getattr(config, "dedup_threshold", 0.9)) if config else 0.9
        return _deduplicate(candidates, similarity_threshold=threshold)


# ---------------------------------------------------------------------------
# Top-level convenience function
# ---------------------------------------------------------------------------


def run_novelty_pipeline(
    candidates: list[Any],
    config: Any | None = None,
) -> Any:
    """Run the full novelty pipeline on *candidates* and return the stage object.

    This is the primary entry point for the novelty pipeline.  It creates a
    ``NoveltyPipelineRunner`` with the supplied *config* and calls ``run()``.

    Parameters
    ----------
    candidates:
        List of ``DiscoveryCandidate``-like objects emitted by the ideation
        subsystem.
    config:
        Optional ``DiscoveryConfig`` controlling thresholds.  When ``None``
        the runner applies default values (novelty_threshold=0.3,
        dedup_threshold=0.9, min_domain_tokens=2).

    Returns
    -------
    NoveltyPipelineStage
        A stage result object whose ``.candidates`` attribute contains the
        filtered, ranked, and de-duplicated candidates ready for Stage 2
        (kind classification).

    Raises
    ------
    TypeError
        If *candidates* is not a list.

    Examples
    --------
    Simple call::

        from jugeo.ideation.discovery_engine.novelty_pipeline import (
            run_novelty_pipeline,
        )

        stage = run_novelty_pipeline(raw_candidates, config=cfg)
        print(f"Survivors: {stage.output_count} / {stage.input_count}")

    Passing custom config::

        from jugeo.ideation.discovery_engine.models import DiscoveryConfig
        cfg = DiscoveryConfig(novelty_threshold=0.45, dedup_threshold=0.85)
        stage = run_novelty_pipeline(raw_candidates, config=cfg)

    See also
    --------
    ``NoveltyPipelineRunner`` — for re-usable, stateful runner instances.
    ``run_kind_classification`` — the next pipeline stage.
    """
    if not isinstance(candidates, list):
        raise TypeError(f"candidates must be a list, got {type(candidates).__name__}")
    runner = NoveltyPipelineRunner(config=config)
    return runner.run(candidates)
