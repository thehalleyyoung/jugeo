from __future__ import annotations

"""
theory2.tex Ch48 §3 – "Treaty synthesis, negotiation memory, and archival semantics"
Chapter 48 §3 — Law discovery as a search problem

# copilot: This module implements the law-discovery subsystem for the treaty-memory
# pipeline.  A "law" in this context is a generalised pattern extracted from a
# corpus of negotiation episodes; the system frames that extraction as a heuristic
# best-first search over a space of candidate patterns, scoring them by evidentiary
# support and refutation counts, then returning a ranked slate of LawCandidate
# objects together with a diagnostic LawAnalysisReport.

Design
------
The core loop is:

  1. Seed the LawSearchSpace with initial pattern strings (supplied by the caller
     or by the coordinator's built-in heuristics).
  2. At each step, pop the highest-scoring SearchNode, generate child patterns via
     LawSearchSpace.expand(), score each child against the episode index, and push
     survivors back into the frontier.
  3. Nodes whose score falls below a pruning threshold are discarded.
  4. When the step budget is exhausted (or the frontier is empty) the coordinator
     materialises a LawCandidate for every node that survived, runs
     LawDiscoverySearchAnalyzer over them, and returns the ranked list.

The module is intentionally free of heavy ML dependencies; scoring uses closed-form
statistics (confidence ratio, log-odds, coverage weighting) so that the smoke-test
runs without GPU resources.

Public surface
--------------
All names listed in ``__all__`` are considered stable.  Everything prefixed with
a single underscore is implementation-private and may change without notice.
"""

# ─── Standard-library imports ──────────────────────────────────────────────────

import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

# ─── Optional jugeo imports (stub fallbacks for environments without the package) ──

try:
    from jugeo.core.episode import Episode  # type: ignore[import]
except ImportError:  # pragma: no cover
    Episode: Any = None  # type: ignore[misc,assignment]

try:
    from jugeo.core.pattern import Pattern  # type: ignore[import]
except ImportError:  # pragma: no cover
    Pattern: Any = None  # type: ignore[misc,assignment]

try:
    from jugeo.orchestration.treaty_memory.base import TreatyMemoryBase  # type: ignore[import]
except ImportError:  # pragma: no cover
    TreatyMemoryBase: Any = object  # type: ignore[misc,assignment]

try:
    from jugeo.telemetry import emit  # type: ignore[import]
except ImportError:  # pragma: no cover
    def emit(event: str, **kwargs: Any) -> None:  # type: ignore[misc]
        """No-op telemetry stub used when jugeo.telemetry is unavailable."""

# ─── Module-level logger ────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─── Public API surface ─────────────────────────────────────────────────────────

__all__ = [
    # Data containers
    "LawCandidate",
    "SearchNode",
    "LawAnalysisReport",
    # Search-space manager
    "LawSearchSpace",
    # Analysis and coordination
    "LawDiscoverySearchAnalyzer",
    "LawDiscoverySearchCoordinator",
    # Helper functions
    "make_candidate",
    "candidate_score",
    "merge_candidates",
    "search_step",
    # Utility helpers (also public)
    "pattern_specificity",
    "episode_coverage_ratio",
    "log_odds_score",
    "normalise_pattern",
    "batch_score_candidates",
    "filter_dominated_candidates",
]

# ─── Internal constants ─────────────────────────────────────────────────────────

# Minimum confidence below which a candidate is immediately discarded.
_MIN_CONFIDENCE: float = 0.05

# Maximum number of nodes kept in the frontier at any one time (memory cap).
_MAX_FRONTIER_SIZE: int = 4096

# Score weight applied to the support-count component.
_SUPPORT_WEIGHT: float = 0.55

# Score weight applied to the confidence component.
_CONFIDENCE_WEIGHT: float = 0.30

# Score weight applied to pattern specificity (longer, more precise patterns
# are preferred over short generic ones).
_SPECIFICITY_WEIGHT: float = 0.15

# Laplace smoothing constant used when computing confidence ratios to avoid
# division-by-zero on unseen patterns.
_LAPLACE_ALPHA: float = 1.0

# Threshold below which nodes are pruned from the search frontier.
_DEFAULT_PRUNE_THRESHOLD: float = 0.10

# Default number of top candidates reported in the analysis report summary.
_TOP_K_REPORT: int = 10

# Maximum edit-distance for two patterns to be considered "conflicting".
_CONFLICT_EDIT_DISTANCE: int = 2

# Separator used when serialising pattern tokens to a canonical form.
_PATTERN_TOKEN_SEP: str = "::"

# Version tag embedded in every exported-laws dict.
_EXPORT_VERSION: str = "s03/1.0"

# Maximum recursion depth for pattern expansion (prevents runaway branching).
_MAX_EXPAND_DEPTH: int = 8

# Default scoring horizon: episodes beyond this many seconds old are down-weighted.
_RECENCY_HALF_LIFE_SECONDS: float = 7 * 24 * 3600.0  # one week


# ─── LawCandidate ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class LawCandidate:
    """An immutable record representing a single candidate law discovered during
    the search.

    A *law* is a generalised pattern that has been observed to hold (support) or
    fail (refute) across a collection of negotiation episodes.  The candidate is
    scored by ``candidate_score()`` and ranked by
    ``LawDiscoverySearchAnalyzer.rank_candidates()``.

    Attributes
    ----------
    candidate_id:
        A UUID-4 string uniquely identifying this candidate.
    pattern:
        The human-readable pattern string, e.g.
        ``"offer::counter::accept"`` or ``"deadline_pressure AND concession"``.
    supporting_episodes:
        Tuple of episode IDs in which the pattern held.
    refuting_episodes:
        Tuple of episode IDs in which the pattern was falsified.
    confidence:
        Laplace-smoothed ratio ``(support + α) / (support + refute + 2α)``.
    support_count:
        Raw number of supporting episodes (convenience mirror of
        ``len(supporting_episodes)``).
    refutation_count:
        Raw number of refuting episodes.
    discovered_at:
        UNIX timestamp of when this candidate was first materialised.
    """

    candidate_id: str
    pattern: str
    supporting_episodes: tuple[str, ...]
    refuting_episodes: tuple[str, ...]
    confidence: float
    support_count: int
    refutation_count: int
    discovered_at: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"LawCandidate.confidence must be in [0, 1]; got {self.confidence}"
            )
        if self.support_count < 0:
            raise ValueError("support_count must be non-negative")
        if self.refutation_count < 0:
            raise ValueError("refutation_count must be non-negative")

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def total_evidence(self) -> int:
        """Total number of episodes (supporting + refuting) that touched this law."""
        return self.support_count + self.refutation_count

    @property
    def age_seconds(self) -> float:
        """How many seconds ago this candidate was discovered (relative to *now*)."""
        return time.time() - self.discovered_at

    @property
    def is_strong(self) -> bool:
        """Return ``True`` when confidence ≥ 0.8 and support_count ≥ 3."""
        return self.confidence >= 0.8 and self.support_count >= 3

    def summary(self) -> str:
        """Return a one-line human-readable summary of this candidate."""
        return (
            f"[{self.candidate_id[:8]}] pattern={self.pattern!r} "
            f"conf={self.confidence:.3f} sup={self.support_count} "
            f"ref={self.refutation_count}"
        )


# ─── SearchNode ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SearchNode:
    """An immutable node in the law-discovery search tree.

    The search tree is traversed best-first: the node with the highest
    ``score`` is expanded next.  ``depth`` is used to enforce
    ``_MAX_EXPAND_DEPTH``.

    Attributes
    ----------
    node_id:
        UUID-4 string uniquely identifying this node within a single search run.
    depth:
        Distance from the root (root nodes have depth 0).
    pattern:
        The candidate pattern string represented by this node.
    score:
        Heuristic score in [0, ∞).  Higher is better.
    parent_id:
        The ``node_id`` of the parent node, or ``None`` for root nodes.
    """

    node_id: str
    depth: int
    pattern: str
    score: float
    parent_id: str | None

    def is_root(self) -> bool:
        """Return ``True`` iff this is a root node (no parent)."""
        return self.parent_id is None

    def child_pattern(self, suffix: str) -> str:
        """Produce a child pattern string by appending *suffix* to this node's
        pattern, separated by the module-level token separator."""
        return f"{self.pattern}{_PATTERN_TOKEN_SEP}{suffix}"


# ─── LawSearchSpace ─────────────────────────────────────────────────────────────

@dataclass(slots=True)
class LawSearchSpace:
    """Mutable priority-queue backed search frontier for law discovery.

    Internally the frontier is kept as a list sorted in descending score order
    so that ``pop_best()`` is an O(1) operation; ``add_node()`` uses an
    insertion-sort step capped at ``_MAX_FRONTIER_SIZE`` to maintain this
    invariant.

    Attributes
    ----------
    _frontier:
        Internal sorted list of ``SearchNode`` objects.
    _visited:
        Set of pattern strings already expanded, used to avoid re-visiting
        identical patterns via different paths.
    _total_added:
        Running count of nodes ever added (for diagnostics).
    """

    _frontier: list[SearchNode] = field(default_factory=list)
    _visited: set[str] = field(default_factory=set)
    _total_added: int = field(default=0)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_node(self, node: SearchNode) -> bool:
        """Insert *node* into the frontier in score-descending order.

        Returns ``True`` if the node was added, ``False`` if it was rejected
        (duplicate pattern or frontier capacity exceeded after pruning).
        """
        if node.pattern in self._visited:
            log.debug("LawSearchSpace: skipping already-visited pattern %r", node.pattern)
            return False
        # Maintain sorted order (descending score).
        inserted = False
        for i, existing in enumerate(self._frontier):
            if node.score >= existing.score:
                self._frontier.insert(i, node)
                inserted = True
                break
        if not inserted:
            self._frontier.append(node)
        # Enforce capacity cap by dropping the lowest-scoring tail.
        if len(self._frontier) > _MAX_FRONTIER_SIZE:
            self._frontier = self._frontier[:_MAX_FRONTIER_SIZE]
        self._total_added += 1
        return True

    def pop_best(self) -> SearchNode | None:
        """Remove and return the highest-scoring node, or ``None`` if empty."""
        if not self._frontier:
            return None
        node = self._frontier.pop(0)
        self._visited.add(node.pattern)
        return node

    def expand(self, node: SearchNode, child_patterns: list[str]) -> list[SearchNode]:
        """Generate child ``SearchNode`` objects for *node* from *child_patterns*.

        Each child inherits the parent's score decayed by 1 / (depth + 2)
        (a simple depth penalty that discourages overly deep patterns).
        Patterns that would exceed ``_MAX_EXPAND_DEPTH`` are silently dropped.

        Parameters
        ----------
        node:
            The parent node being expanded.
        child_patterns:
            Raw pattern strings for the children.  They will be composed with
            ``node.child_pattern()`` to form a full path expression.

        Returns
        -------
        list[SearchNode]
            The list of generated child nodes (may be empty).
        """
        if node.depth >= _MAX_EXPAND_DEPTH:
            log.debug(
                "LawSearchSpace.expand: node %s at max depth %d, skipping",
                node.node_id[:8],
                node.depth,
            )
            return []

        depth_penalty = 1.0 / (node.depth + 2)
        children: list[SearchNode] = []
        for cp in child_patterns:
            full_pattern = node.child_pattern(cp)
            child_score = node.score * depth_penalty
            child = SearchNode(
                node_id=str(uuid.uuid4()),
                depth=node.depth + 1,
                pattern=full_pattern,
                score=child_score,
                parent_id=node.node_id,
            )
            children.append(child)
        return children

    def prune(self, threshold: float = _DEFAULT_PRUNE_THRESHOLD) -> int:
        """Remove all nodes whose score is strictly below *threshold*.

        Returns the number of nodes removed.
        """
        before = len(self._frontier)
        self._frontier = [n for n in self._frontier if n.score >= threshold]
        removed = before - len(self._frontier)
        if removed:
            log.debug("LawSearchSpace.prune: removed %d nodes below %.4f", removed, threshold)
        return removed

    def peek(self) -> SearchNode | None:
        """Return the best node without removing it, or ``None`` if empty."""
        return self._frontier[0] if self._frontier else None

    def iter_nodes(self) -> Iterator[SearchNode]:
        """Iterate over all frontier nodes in score-descending order (non-destructive)."""
        yield from self._frontier

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Current number of nodes in the frontier."""
        return len(self._frontier)

    @property
    def total_added(self) -> int:
        """Total nodes ever added to this space (including those since pruned)."""
        return self._total_added

    def is_empty(self) -> bool:
        """Return ``True`` iff the frontier contains no nodes."""
        return len(self._frontier) == 0


# ─── LawAnalysisReport ─────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class LawAnalysisReport:
    """Immutable summary produced by ``LawDiscoverySearchAnalyzer.analyze()``.

    Attributes
    ----------
    report_id:
        UUID-4 string uniquely identifying this report.
    candidates_found:
        Total number of ``LawCandidate`` objects analysed.
    top_laws:
        Tuple of the top-K (up to ``_TOP_K_REPORT``) ranked candidates.
    conflicts:
        Tuple of ``(candidate_a_id, candidate_b_id, conflict_reason)`` triples
        identifying pairs of mutually contradictory candidates.
    coverage:
        Fraction of the known episode set covered by at least one candidate law.
    generated_at:
        UNIX timestamp of report generation.
    """

    report_id: str
    candidates_found: int
    top_laws: tuple[LawCandidate, ...]
    conflicts: tuple[tuple[str, str, str], ...]
    coverage: float
    generated_at: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.coverage <= 1.0):
            raise ValueError(f"coverage must be in [0, 1]; got {self.coverage}")

    def as_dict(self) -> dict[str, Any]:
        """Serialise this report to a plain dictionary (JSON-compatible)."""
        return {
            "report_id": self.report_id,
            "candidates_found": self.candidates_found,
            "top_laws": [
                {
                    "candidate_id": c.candidate_id,
                    "pattern": c.pattern,
                    "confidence": c.confidence,
                    "support_count": c.support_count,
                    "refutation_count": c.refutation_count,
                }
                for c in self.top_laws
            ],
            "conflicts": [
                {"a": a, "b": b, "reason": r} for a, b, r in self.conflicts
            ],
            "coverage": self.coverage,
            "generated_at": self.generated_at,
        }

    def summary(self) -> str:
        """One-paragraph human-readable summary of this report."""
        lines = [
            f"LawAnalysisReport {self.report_id[:8]}",
            f"  candidates : {self.candidates_found}",
            f"  top laws   : {len(self.top_laws)}",
            f"  conflicts  : {len(self.conflicts)}",
            f"  coverage   : {self.coverage:.2%}",
        ]
        if self.top_laws:
            lines.append("  top-3      :")
            for c in self.top_laws[:3]:
                lines.append(f"    {c.summary()}")
        return "\n".join(lines)


# ─── Helper functions ───────────────────────────────────────────────────────────

def normalise_pattern(pattern: str) -> str:
    """Return a canonical lower-cased, whitespace-collapsed version of *pattern*.

    Canonicalisation ensures that superficially different strings that encode
    the same pattern are treated as identical by the search space.

    Examples
    --------
    >>> normalise_pattern("  Offer :: Counter  ")
    'offer::counter'
    """
    return _PATTERN_TOKEN_SEP.join(
        tok.strip().lower()
        for tok in pattern.replace("::", "\x00").split("\x00")
        if tok.strip()
    )


def pattern_specificity(pattern: str) -> float:
    """Compute a specificity score for *pattern* in [0, 1].

    Specificity is derived from the number of tokens in the pattern: more
    tokens = more specific = higher score (up to a soft ceiling of 10 tokens).

    Formula: ``min(n_tokens, 10) / 10``

    Parameters
    ----------
    pattern:
        A raw or normalised pattern string using ``_PATTERN_TOKEN_SEP`` as the
        token delimiter.

    Returns
    -------
    float
        Specificity score in [0, 1].
    """
    tokens = [t for t in pattern.split(_PATTERN_TOKEN_SEP) if t.strip()]
    return min(len(tokens), 10) / 10.0


def log_odds_score(support: int, refute: int, alpha: float = _LAPLACE_ALPHA) -> float:
    """Compute the Laplace-smoothed log-odds of support over refutation.

    A high positive value indicates strong support with few refutations.
    The result is then sigmoid-transformed into [0, 1].

    Parameters
    ----------
    support:
        Number of supporting observations.
    refute:
        Number of refuting observations.
    alpha:
        Laplace smoothing constant (default ``_LAPLACE_ALPHA``).

    Returns
    -------
    float
        A value in (0, 1) — higher means more strongly supported.
    """
    p = (support + alpha) / (support + refute + 2 * alpha)
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    log_odds = math.log(p / (1.0 - p))
    # Sigmoid re-squash (already mapped, but keep for numerical safety).
    return 1.0 / (1.0 + math.exp(-log_odds))


def episode_coverage_ratio(
    candidates: list[LawCandidate], all_episode_ids: set[str]
) -> float:
    """Return the fraction of *all_episode_ids* covered by at least one candidate.

    An episode is "covered" if it appears in any candidate's
    ``supporting_episodes`` tuple.

    Parameters
    ----------
    candidates:
        The list of ``LawCandidate`` objects to check.
    all_episode_ids:
        The full universe of known episode IDs.

    Returns
    -------
    float
        Coverage ratio in [0, 1].  Returns 0.0 if *all_episode_ids* is empty.
    """
    if not all_episode_ids:
        return 0.0
    covered: set[str] = set()
    for c in candidates:
        covered.update(c.supporting_episodes)
    return len(covered & all_episode_ids) / len(all_episode_ids)


def make_candidate(
    pattern: str,
    supporting: list[str] | None = None,
    refuting: list[str] | None = None,
    alpha: float = _LAPLACE_ALPHA,
) -> LawCandidate:
    """Construct a new ``LawCandidate`` from raw ingredients.

    This is the preferred factory function; it handles UUID generation,
    timestamp stamping, and Laplace-smoothed confidence computation
    automatically.

    Parameters
    ----------
    pattern:
        The pattern string for the candidate (will be normalised).
    supporting:
        List of episode IDs that support the pattern (default: empty).
    refuting:
        List of episode IDs that refute the pattern (default: empty).
    alpha:
        Laplace smoothing constant.

    Returns
    -------
    LawCandidate
        A freshly constructed, immutable candidate.
    """
    sup = list(supporting or [])
    ref = list(refuting or [])
    s = len(sup)
    r = len(ref)
    confidence = (s + alpha) / (s + r + 2 * alpha)
    return LawCandidate(
        candidate_id=str(uuid.uuid4()),
        pattern=normalise_pattern(pattern),
        supporting_episodes=tuple(sup),
        refuting_episodes=tuple(ref),
        confidence=confidence,
        support_count=s,
        refutation_count=r,
        discovered_at=time.time(),
    )


def candidate_score(c: LawCandidate) -> float:
    """Compute the composite heuristic score for a ``LawCandidate``.

    The score is a weighted sum of three components:

    1. **Support component** — log-odds score of support vs. refutation,
       weighted by ``_SUPPORT_WEIGHT``.
    2. **Confidence component** — raw confidence ratio, weighted by
       ``_CONFIDENCE_WEIGHT``.
    3. **Specificity component** — pattern specificity, weighted by
       ``_SPECIFICITY_WEIGHT``.

    Parameters
    ----------
    c:
        The candidate to score.

    Returns
    -------
    float
        A non-negative score.  Higher values indicate a stronger candidate.
    """
    lo = log_odds_score(c.support_count, c.refutation_count)
    spec = pattern_specificity(c.pattern)
    return (
        _SUPPORT_WEIGHT * lo
        + _CONFIDENCE_WEIGHT * c.confidence
        + _SPECIFICITY_WEIGHT * spec
    )


def merge_candidates(a: LawCandidate, b: LawCandidate) -> LawCandidate:
    """Merge two candidates that share the same (normalised) pattern.

    The merged candidate accumulates evidence from both, recomputes
    confidence, and is assigned a fresh ID and timestamp.

    Parameters
    ----------
    a, b:
        The two candidates to merge.  Their ``pattern`` fields should be
        identical after normalisation (a warning is logged if they differ).

    Returns
    -------
    LawCandidate
        A new, merged candidate.
    """
    if a.pattern != b.pattern:
        log.warning(
            "merge_candidates: patterns differ (%r vs %r); merging anyway",
            a.pattern,
            b.pattern,
        )
    combined_supporting = tuple(dict.fromkeys(a.supporting_episodes + b.supporting_episodes))
    combined_refuting = tuple(dict.fromkeys(a.refuting_episodes + b.refuting_episodes))
    s = len(combined_supporting)
    r = len(combined_refuting)
    confidence = (s + _LAPLACE_ALPHA) / (s + r + 2 * _LAPLACE_ALPHA)
    return LawCandidate(
        candidate_id=str(uuid.uuid4()),
        pattern=a.pattern,
        supporting_episodes=combined_supporting,
        refuting_episodes=combined_refuting,
        confidence=confidence,
        support_count=s,
        refutation_count=r,
        discovered_at=time.time(),
    )


def search_step(
    space: LawSearchSpace,
    episode_index: dict[str, list[str]],
    scorer: Callable[[str, dict[str, list[str]]], float],
) -> list[SearchNode]:
    """Execute one step of the best-first search over *space*.

    This function pops the best node from *space*, calls *scorer* to compute
    the initial child scores, generates child patterns from the episode index,
    and pushes qualifying children back into *space*.

    Parameters
    ----------
    space:
        The ``LawSearchSpace`` frontier (mutated in place).
    episode_index:
        A mapping of ``{pattern_token: [episode_id, ...]}`` used to derive
        child patterns from the current node.
    scorer:
        A callable ``(pattern, episode_index) -> float`` that scores a raw
        pattern string.

    Returns
    -------
    list[SearchNode]
        The child nodes that were successfully added to the frontier.
    """
    node = space.pop_best()
    if node is None:
        return []

    # Derive candidate child tokens from co-occurring keys in the episode index.
    current_token = node.pattern.split(_PATTERN_TOKEN_SEP)[-1]
    child_tokens: list[str] = []
    for key in episode_index:
        if key != current_token and key not in node.pattern:
            child_tokens.append(key)
    # Limit fanout to the top 8 tokens by direct scorer lookup to keep search tractable.
    scored_tokens = sorted(
        child_tokens,
        key=lambda t: scorer(t, episode_index),
        reverse=True,
    )[:8]

    children = space.expand(node, scored_tokens)
    added: list[SearchNode] = []
    for child in children:
        child_score_val = scorer(child.pattern, episode_index)
        # Re-create with updated score from the actual scorer.
        scored_child = SearchNode(
            node_id=child.node_id,
            depth=child.depth,
            pattern=child.pattern,
            score=child_score_val,
            parent_id=child.parent_id,
        )
        if scored_child.score >= _DEFAULT_PRUNE_THRESHOLD:
            if space.add_node(scored_child):
                added.append(scored_child)
    return added


def batch_score_candidates(
    candidates: list[LawCandidate],
) -> list[tuple[LawCandidate, float]]:
    """Return a list of ``(candidate, score)`` pairs sorted by score descending.

    Convenience wrapper around ``candidate_score()`` for bulk evaluation.

    Parameters
    ----------
    candidates:
        The candidates to score.

    Returns
    -------
    list[tuple[LawCandidate, float]]
        Pairs sorted highest-score-first.
    """
    pairs = [(c, candidate_score(c)) for c in candidates]
    pairs.sort(key=lambda p: p[1], reverse=True)
    return pairs


def filter_dominated_candidates(
    candidates: list[LawCandidate],
    min_confidence: float = _MIN_CONFIDENCE,
) -> list[LawCandidate]:
    """Remove candidates that are strictly dominated by a stronger one.

    A candidate A is *dominated* by B if:
    - B.confidence ≥ A.confidence
    - B.support_count ≥ A.support_count
    - B.refutation_count ≤ A.refutation_count
    - A ≠ B

    Additionally, candidates below *min_confidence* are always removed.

    Parameters
    ----------
    candidates:
        Input list.
    min_confidence:
        Absolute lower bound on confidence.

    Returns
    -------
    list[LawCandidate]
        Non-dominated candidates above the confidence floor.
    """
    surviving = [c for c in candidates if c.confidence >= min_confidence]
    result: list[LawCandidate] = []
    for i, c in enumerate(surviving):
        dominated = False
        for j, other in enumerate(surviving):
            if i == j:
                continue
            if (
                other.confidence >= c.confidence
                and other.support_count >= c.support_count
                and other.refutation_count <= c.refutation_count
                and other.candidate_id != c.candidate_id
            ):
                dominated = True
                break
        if not dominated:
            result.append(c)
    return result


def _simple_pattern_scorer(
    pattern: str, episode_index: dict[str, list[str]]
) -> float:
    """Default internal scorer: counts episode hits for each token in *pattern*.

    This scorer is intentionally lightweight so that the smoke-test runs fast.
    Production deployments should supply a richer scorer via the coordinator's
    constructor.

    Parameters
    ----------
    pattern:
        A (possibly multi-token) pattern string.
    episode_index:
        Token → episode-IDs mapping.

    Returns
    -------
    float
        A non-negative score.
    """
    tokens = [t for t in pattern.split(_PATTERN_TOKEN_SEP) if t]
    if not tokens:
        return 0.0
    hit_counts = [len(episode_index.get(t, [])) for t in tokens]
    if not hit_counts or max(hit_counts) == 0:
        return 0.0
    # Geometric mean of hit counts, normalised by a soft cap of 100.
    log_sum = sum(math.log1p(h) for h in hit_counts)
    geo_mean = math.exp(log_sum / len(hit_counts)) - 1.0
    return min(geo_mean / 100.0, 1.0)


def _levenshtein_distance(a: str, b: str) -> int:
    """Compute the character-level Levenshtein distance between *a* and *b*.

    Used by the conflict-detection heuristic to identify near-duplicate
    patterns that assert contradictory conclusions.

    Parameters
    ----------
    a, b:
        The strings to compare.

    Returns
    -------
    int
        Edit distance (0 = identical).
    """
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr_row = [i + 1]
        for j, cb in enumerate(b):
            insert = prev_row[j + 1] + 1
            delete = curr_row[j] + 1
            replace = prev_row[j] + (ca != cb)
            curr_row.append(min(insert, delete, replace))
        prev_row = curr_row
    return prev_row[-1]


# ─── LawDiscoverySearchAnalyzer ─────────────────────────────────────────────────

class LawDiscoverySearchAnalyzer:
    """Analyses a collection of ``LawCandidate`` objects produced by a search run.

    The analyser is stateless: all methods are pure functions operating on
    their arguments.  Call ``analyze()`` to obtain a full ``LawAnalysisReport``,
    or use the individual methods for targeted queries.

    Examples
    --------
    >>> analyser = LawDiscoverySearchAnalyzer()
    >>> report = analyser.analyze(candidates)
    >>> print(report.summary())
    """

    def analyze(
        self,
        candidates: list[LawCandidate],
        known_episodes: set[str] | None = None,
    ) -> LawAnalysisReport:
        """Produce a ``LawAnalysisReport`` summarising *candidates*.

        Parameters
        ----------
        candidates:
            The full slate of candidates from a search run.
        known_episodes:
            Optional set of all known episode IDs, used for coverage
            computation.  If ``None``, only episodes referenced by candidates
            are counted (giving coverage = 1.0 as an upper bound).

        Returns
        -------
        LawAnalysisReport
            A frozen report object.
        """
        if known_episodes is None:
            known_episodes = set()
            for c in candidates:
                known_episodes.update(c.supporting_episodes)
                known_episodes.update(c.refuting_episodes)

        ranked = self.rank_candidates(candidates)
        top_laws = tuple(ranked[:_TOP_K_REPORT])
        conflicts_raw = self.identify_conflicts(candidates)
        conflicts = tuple(
            (a_id, b_id, reason) for a_id, b_id, reason in conflicts_raw
        )
        coverage = self.compute_coverage(candidates, known_episodes)

        report = LawAnalysisReport(
            report_id=str(uuid.uuid4()),
            candidates_found=len(candidates),
            top_laws=top_laws,
            conflicts=conflicts,
            coverage=coverage,
            generated_at=time.time(),
        )
        log.info("LawDiscoverySearchAnalyzer.analyze: %s", report.summary())
        emit("law_analysis_complete", report_id=report.report_id, n=len(candidates))
        return report

    def rank_candidates(self, candidates: list[LawCandidate]) -> list[LawCandidate]:
        """Return *candidates* sorted by composite score (highest first).

        Parameters
        ----------
        candidates:
            Unsorted list of candidates.

        Returns
        -------
        list[LawCandidate]
            New list sorted descending by ``candidate_score()``.
        """
        return sorted(candidates, key=candidate_score, reverse=True)

    def identify_conflicts(
        self, candidates: list[LawCandidate]
    ) -> list[tuple[str, str, str]]:
        """Detect pairs of candidates whose patterns are suspiciously similar but
        whose evidence is contradictory.

        Two candidates conflict if:

        1. Their normalised patterns are within ``_CONFLICT_EDIT_DISTANCE``
           Levenshtein distance of each other, AND
        2. The difference in their confidence scores exceeds 0.3 (one strongly
           supports while the other is ambiguous or negative).

        Parameters
        ----------
        candidates:
            The list to inspect.

        Returns
        -------
        list[tuple[str, str, str]]
            Triples of ``(candidate_id_a, candidate_id_b, conflict_reason)``.
        """
        results: list[tuple[str, str, str]] = []
        seen: set[frozenset[str]] = set()
        for i, a in enumerate(candidates):
            for j, b in enumerate(candidates):
                if i >= j:
                    continue
                key = frozenset({a.candidate_id, b.candidate_id})
                if key in seen:
                    continue
                dist = _levenshtein_distance(a.pattern, b.pattern)
                conf_diff = abs(a.confidence - b.confidence)
                if dist <= _CONFLICT_EDIT_DISTANCE and conf_diff > 0.3:
                    reason = (
                        f"edit_dist={dist} conf_diff={conf_diff:.3f} "
                        f"patterns=({a.pattern!r}, {b.pattern!r})"
                    )
                    results.append((a.candidate_id, b.candidate_id, reason))
                    seen.add(key)
        return results

    def compute_coverage(
        self, candidates: list[LawCandidate], episodes: set[str]
    ) -> float:
        """Compute the fraction of *episodes* covered by at least one candidate.

        Parameters
        ----------
        candidates:
            The candidates to evaluate.
        episodes:
            The full universe of episode IDs.

        Returns
        -------
        float
            Coverage in [0, 1].
        """
        return episode_coverage_ratio(candidates, episodes)

    def score_distribution(
        self, candidates: list[LawCandidate]
    ) -> dict[str, float]:
        """Return descriptive statistics of the score distribution over *candidates*.

        Returns
        -------
        dict[str, float]
            Keys: ``mean``, ``median``, ``stdev``, ``min``, ``max``.
            All values are 0.0 when the list is empty.
        """
        if not candidates:
            return {"mean": 0.0, "median": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0}
        scores = [candidate_score(c) for c in candidates]
        return {
            "mean": statistics.mean(scores),
            "median": statistics.median(scores),
            "stdev": statistics.stdev(scores) if len(scores) > 1 else 0.0,
            "min": min(scores),
            "max": max(scores),
        }


# ─── LawDiscoverySearchCoordinator ──────────────────────────────────────────────

class LawDiscoverySearchCoordinator:
    """High-level coordinator that orchestrates the full law-discovery pipeline.

    Usage
    -----
    ::

        coordinator = LawDiscoverySearchCoordinator()
        coordinator.seed_patterns(["offer", "counter", "accept", "deadline"])
        laws = coordinator.run(max_steps=20)
        export = coordinator.export_laws()

    Attributes
    ----------
    _space:
        The ``LawSearchSpace`` frontier managed by this coordinator.
    _episode_index:
        Token → episode-IDs mapping populated during ``seed_patterns()``.
    _candidates:
        Accumulated ``LawCandidate`` objects materialised across all steps.
    _scorer:
        The scoring callable used by ``search_step()``.
    _step_count:
        Number of search steps executed so far.
    _analyser:
        Reusable ``LawDiscoverySearchAnalyzer`` instance.
    """

    def __init__(
        self,
        scorer: Callable[[str, dict[str, list[str]]], float] | None = None,
        prune_threshold: float = _DEFAULT_PRUNE_THRESHOLD,
    ) -> None:
        """Initialise the coordinator.

        Parameters
        ----------
        scorer:
            Optional custom scoring callable.  Defaults to the built-in
            ``_simple_pattern_scorer``.
        prune_threshold:
            Score threshold below which frontier nodes are pruned after each step.
        """
        self._space = LawSearchSpace()
        self._episode_index: dict[str, list[str]] = {}
        self._candidates: list[LawCandidate] = []
        self._scorer: Callable[[str, dict[str, list[str]]], float] = (
            scorer or _simple_pattern_scorer
        )
        self._prune_threshold = prune_threshold
        self._step_count = 0
        self._analyser = LawDiscoverySearchAnalyzer()
        log.debug("LawDiscoverySearchCoordinator initialised (threshold=%.4f)", prune_threshold)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def seed_patterns(self, patterns: list[str]) -> None:
        """Seed the search frontier with *patterns* as root nodes.

        Each pattern is normalised, scored, and inserted as a depth-0
        ``SearchNode``.  The episode index is also bootstrapped with
        a minimal synthetic mapping (one episode per token) so that the
        scorer has something to work with even before real episodes are
        ingested.

        Parameters
        ----------
        patterns:
            List of raw pattern strings (e.g. ``["offer", "counter"]``).
        """
        if not patterns:
            log.warning("seed_patterns called with empty list; nothing to seed")
            return

        for raw in patterns:
            norm = normalise_pattern(raw)
            if not norm:
                continue
            # Bootstrap episode index: assign a synthetic episode to each token.
            for token in norm.split(_PATTERN_TOKEN_SEP):
                if token not in self._episode_index:
                    synthetic_id = f"synthetic::{token}::{uuid.uuid4().hex[:8]}"
                    self._episode_index[token] = [synthetic_id]

            initial_score = self._scorer(norm, self._episode_index)
            node = SearchNode(
                node_id=str(uuid.uuid4()),
                depth=0,
                pattern=norm,
                score=max(initial_score, _DEFAULT_PRUNE_THRESHOLD + 0.01),
                parent_id=None,
            )
            self._space.add_node(node)
            log.debug("Seeded root node pattern=%r score=%.4f", norm, node.score)

        log.info(
            "seed_patterns: added %d root nodes; frontier size=%d",
            len(patterns),
            self._space.size,
        )

    def ingest_episodes(
        self, episode_index: dict[str, list[str]]
    ) -> None:
        """Merge *episode_index* into the coordinator's internal index.

        Parameters
        ----------
        episode_index:
            Mapping of ``{pattern_token: [episode_id, ...]}``.  Duplicate
            episode IDs within a token are deduplicated.
        """
        for token, ep_ids in episode_index.items():
            norm_token = token.strip().lower()
            existing = self._episode_index.get(norm_token, [])
            merged = list(dict.fromkeys(existing + ep_ids))
            self._episode_index[norm_token] = merged
        log.info(
            "ingest_episodes: index now has %d tokens", len(self._episode_index)
        )

    # ------------------------------------------------------------------
    # Search loop
    # ------------------------------------------------------------------

    def step(self) -> list[LawCandidate]:
        """Execute one search step and materialise new ``LawCandidate`` objects.

        The step:

        1. Calls ``search_step()`` to expand the frontier.
        2. For each newly added child node, materialises a ``LawCandidate`` by
           looking up evidence in ``_episode_index``.
        3. Prunes the frontier.
        4. Returns the new candidates discovered in this step.

        Returns
        -------
        list[LawCandidate]
            Candidates materialised during this step (may be empty).
        """
        if self._space.is_empty():
            log.debug("step: frontier is empty; no work to do")
            return []

        new_nodes = search_step(
            self._space, self._episode_index, self._scorer
        )
        new_candidates: list[LawCandidate] = []
        for node in new_nodes:
            candidate = self._materialise_candidate(node)
            if candidate.confidence >= _MIN_CONFIDENCE:
                self._candidates.append(candidate)
                new_candidates.append(candidate)

        self._space.prune(self._prune_threshold)
        self._step_count += 1
        log.debug(
            "step %d: %d new candidates; frontier=%d",
            self._step_count,
            len(new_candidates),
            self._space.size,
        )
        return new_candidates

    def run(self, max_steps: int = 50) -> list[LawCandidate]:
        """Run the search for up to *max_steps* steps.

        Stops early if the frontier becomes empty.

        Parameters
        ----------
        max_steps:
            Maximum number of ``step()`` calls.

        Returns
        -------
        list[LawCandidate]
            All candidates accumulated across all steps, de-duplicated and
            sorted by ``candidate_score()`` descending.
        """
        log.info("LawDiscoverySearchCoordinator.run: max_steps=%d", max_steps)
        t0 = time.time()
        for i in range(max_steps):
            if self._space.is_empty():
                log.info("run: frontier exhausted after %d steps", i)
                break
            self.step()
        elapsed = time.time() - t0
        # De-duplicate by pattern (keep highest-scored representative).
        deduped = self._dedup_candidates(self._candidates)
        ranked = sorted(deduped, key=candidate_score, reverse=True)
        log.info(
            "run complete: %d raw candidates → %d unique in %.3fs",
            len(self._candidates),
            len(ranked),
            elapsed,
        )
        emit(
            "law_discovery_run_complete",
            n_candidates=len(ranked),
            n_steps=self._step_count,
            elapsed=elapsed,
        )
        return ranked

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_laws(self) -> dict[str, Any]:
        """Serialise the current candidate set to a plain dictionary.

        The dictionary is intended to be JSON-serialisable.  It includes a
        version tag and a full ``LawAnalysisReport``.

        Returns
        -------
        dict[str, Any]
            Export payload.
        """
        deduped = self._dedup_candidates(self._candidates)
        ranked = sorted(deduped, key=candidate_score, reverse=True)
        report = self._analyser.analyze(ranked)
        return {
            "version": _EXPORT_VERSION,
            "exported_at": time.time(),
            "step_count": self._step_count,
            "candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "pattern": c.pattern,
                    "confidence": c.confidence,
                    "support_count": c.support_count,
                    "refutation_count": c.refutation_count,
                    "score": candidate_score(c),
                    "discovered_at": c.discovered_at,
                }
                for c in ranked
            ],
            "report": report.as_dict(),
        }

    def get_report(self, known_episodes: set[str] | None = None) -> LawAnalysisReport:
        """Run the analyser over the current candidate set and return the report.

        Parameters
        ----------
        known_episodes:
            Optional universe of episode IDs for coverage computation.

        Returns
        -------
        LawAnalysisReport
        """
        deduped = self._dedup_candidates(self._candidates)
        return self._analyser.analyze(deduped, known_episodes)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _materialise_candidate(self, node: SearchNode) -> LawCandidate:
        """Build a ``LawCandidate`` from a ``SearchNode`` by consulting the episode index.

        Parameters
        ----------
        node:
            The node to materialise.

        Returns
        -------
        LawCandidate
        """
        tokens = [t for t in node.pattern.split(_PATTERN_TOKEN_SEP) if t]
        supporting_ids: list[str] = []
        refuting_ids: list[str] = []
        for token in tokens:
            ep_ids = self._episode_index.get(token, [])
            supporting_ids.extend(ep_ids)
        # Deduplicate while preserving order.
        supporting_ids = list(dict.fromkeys(supporting_ids))
        # Simple heuristic: episodes that appear for *none* of the tokens are
        # considered refuting (only meaningful when a real episode index is in use).
        all_eps: set[str] = set()
        for ids in self._episode_index.values():
            all_eps.update(ids)
        supporting_set = set(supporting_ids)
        refuting_ids = [
            ep for ep in all_eps
            if ep not in supporting_set
        ][:max(0, len(supporting_ids) // 4)]  # cap refutations at 25% of support

        return make_candidate(
            pattern=node.pattern,
            supporting=supporting_ids,
            refuting=refuting_ids,
        )

    @staticmethod
    def _dedup_candidates(
        candidates: list[LawCandidate],
    ) -> list[LawCandidate]:
        """Deduplicate *candidates* by normalised pattern, keeping the best-scored
        representative for each pattern.

        Parameters
        ----------
        candidates:
            Raw (possibly duplicate) list.

        Returns
        -------
        list[LawCandidate]
            One candidate per unique pattern.
        """
        best: dict[str, LawCandidate] = {}
        for c in candidates:
            key = c.pattern
            if key not in best or candidate_score(c) > candidate_score(best[key]):
                best[key] = c
        return list(best.values())


# ─── Smoke test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    print(f"[smoke] {__file__}")

    # --- Helpers ---

    # normalise_pattern
    assert normalise_pattern("  Offer :: Counter  ") == "offer::counter", "normalise_pattern failed"
    print("[smoke] normalise_pattern OK")

    # pattern_specificity
    assert pattern_specificity("a::b::c") == 0.3, f"got {pattern_specificity('a::b::c')}"
    assert pattern_specificity("single") == 0.1
    print("[smoke] pattern_specificity OK")

    # log_odds_score
    lo = log_odds_score(10, 0)
    assert lo > 0.9, f"log_odds_score(10,0) expected >0.9, got {lo}"
    lo2 = log_odds_score(0, 10)
    assert lo2 < 0.2, f"log_odds_score(0,10) expected <0.2, got {lo2}"
    print("[smoke] log_odds_score OK")

    # make_candidate
    c1 = make_candidate("offer::accept", supporting=["ep1", "ep2", "ep3"], refuting=["ep4"])
    assert c1.support_count == 3
    assert c1.refutation_count == 1
    assert 0.0 < c1.confidence < 1.0
    print("[smoke] make_candidate OK")

    # candidate_score
    score = candidate_score(c1)
    assert 0.0 < score <= 1.0, f"candidate_score out of range: {score}"
    print("[smoke] candidate_score OK")

    # merge_candidates
    c2 = make_candidate("offer::accept", supporting=["ep5", "ep6"], refuting=[])
    merged = merge_candidates(c1, c2)
    assert merged.support_count == 5
    print("[smoke] merge_candidates OK")

    # LawCandidate post_init validation
    try:
        bad = LawCandidate(
            candidate_id="x", pattern="p", supporting_episodes=(),
            refuting_episodes=(), confidence=1.5, support_count=0,
            refutation_count=0, discovered_at=0.0,
        )
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("[smoke] LawCandidate validation OK")

    # LawSearchSpace
    space = LawSearchSpace()
    root_node = SearchNode(
        node_id=str(uuid.uuid4()), depth=0, pattern="offer", score=0.8, parent_id=None
    )
    assert space.add_node(root_node)
    assert space.size == 1
    children = space.expand(root_node, ["accept", "counter"])
    assert len(children) == 2
    for ch in children:
        space.add_node(ch)
    assert space.size == 3
    best = space.pop_best()
    assert best is not None
    pruned = space.prune(threshold=0.5)
    print(f"[smoke] LawSearchSpace OK (pruned={pruned})")

    # search_step
    ep_index = {
        "offer": ["ep1", "ep2"],
        "accept": ["ep2", "ep3"],
        "counter": ["ep1", "ep4"],
    }
    space2 = LawSearchSpace()
    space2.add_node(
        SearchNode(
            node_id=str(uuid.uuid4()), depth=0, pattern="offer", score=0.5, parent_id=None
        )
    )
    new_nodes = search_step(space2, ep_index, _simple_pattern_scorer)
    print(f"[smoke] search_step OK (new_nodes={len(new_nodes)})")

    # LawDiscoverySearchCoordinator full run
    coord = LawDiscoverySearchCoordinator()
    coord.seed_patterns(["offer", "counter", "accept", "deadline", "concession"])
    coord.ingest_episodes(ep_index)
    laws = coord.run(max_steps=5)
    assert isinstance(laws, list), "run() must return a list"
    print(f"[smoke] coordinator.run OK ({len(laws)} laws)")

    # export
    export = coord.export_laws()
    assert export["version"] == _EXPORT_VERSION
    assert "candidates" in export
    assert "report" in export
    print("[smoke] coordinator.export_laws OK")

    # LawDiscoverySearchAnalyzer
    analyser = LawDiscoverySearchAnalyzer()
    report = analyser.analyze(laws)
    assert isinstance(report, LawAnalysisReport)
    assert report.candidates_found == len(laws)
    assert 0.0 <= report.coverage <= 1.0
    print(f"[smoke] analyser.analyze OK\n{report.summary()}")

    # filter_dominated_candidates
    c_weak = make_candidate("offer", supporting=["e1"], refuting=["e2", "e3", "e4", "e5"])
    c_strong = make_candidate("offer", supporting=["e1", "e2", "e3", "e4", "e5"], refuting=[])
    survivors = filter_dominated_candidates([c_weak, c_strong])
    assert c_strong in survivors, "strong candidate should survive"
    print("[smoke] filter_dominated_candidates OK")

    # batch_score_candidates
    pairs = batch_score_candidates(laws if laws else [c1, c2])
    assert all(pairs[i][1] >= pairs[i + 1][1] for i in range(len(pairs) - 1)), \
        "batch_score_candidates not sorted descending"
    print("[smoke] batch_score_candidates OK")

    print("[smoke] PASS")
