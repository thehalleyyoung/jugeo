"""
Discovery Federation Algorithms
================================

This module implements the core algorithmic primitives for the JuGeo Discovery
Federation subsystem, as described in the theoretical framework outlined in
``theory2.tex``, Chapter 61 ("Authority Propagation and Federated Consensus in
Distributed Discovery Regimes").

Overview
--------
Discovery Federation is the process by which independently-discovered knowledge
artefacts are merged, validated, and granted authority across a network of
semi-autonomous agents (called *nodes*).  Each node maintains a *trust profile*
— a sparse numeric vector encoding how much confidence it has accrued in
various knowledge domains — and participates in lightweight voting rounds
whenever a new discovery arrives from a peer.

The algorithms collected here are intentionally stateless: they operate on
plain Python dicts and lists, producing new dicts or scalars, without mutating
their inputs.  This makes them straightforward to unit-test, cache, and replay
in audit logs.

Key concepts (see theory2.tex Ch61 §§ 3-7)
-------------------------------------------
* **Knowledge Propagation** — spreading a discovery along trust-weighted edges
  of the federation graph, attenuating relevance via cosine similarity.
* **Consensus Voting** — quorum-based weighted voting where each voter's
  influence is proportional to its trust weight.
* **Authority Granting** — conditional issuance of an authority token that
  certifies a discovery's legitimacy within the current regime.
* **Conflict Resolution** — deterministic arbitration between competing
  discoveries using Borda-count-ranked trust weights.
* **Trust Distance** — Euclidean distance between node trust profiles,
  normalised to [0, 1] by the theoretical maximum.
* **Authority Decay** — exponential decay of authority tokens over time,
  parameterised by a configurable half-life.

# copilot: shared-core
# theory2.tex Ch61

Design principles
-----------------
All functions accept and return plain dicts so that callers can serialise
results to JSON without any special encoding step.  Internal constants (decay
half-lives, quorum thresholds, etc.) are module-level so they can be patched
in tests without monkey-patching class internals.

Typical usage
-------------
::

    from jugeo.ideation.discovery_federation.algorithms import (
        FederationAlgorithms,
        compute_federation_distance,
        rank_federation_candidates,
    )

    score = FederationAlgorithms.federation_score(node, discovery)
    vote  = FederationAlgorithms.consensus_vote(discovery, voters)

"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# The try/except pattern prevents ImportError from breaking the module when
# optional dependencies (e.g. numpy-backed trust engines) are absent.
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.discovery_federation._types import (  # type: ignore[import]
        DiscoveryRecord,
        NodeProfile,
    )
    _TYPES_AVAILABLE = True
except ImportError:  # pragma: no cover — optional heavy dep
    _TYPES_AVAILABLE = False

try:
    from jugeo.core.regime import RegimeContext  # type: ignore[import]
    _REGIME_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REGIME_AVAILABLE = False

try:
    from jugeo.core.logging import get_logger as _get_logger  # type: ignore[import]
    _log = _get_logger(__name__)
except ImportError:  # pragma: no cover
    import logging as _logging
    _log = _logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default half-life (seconds) used by :func:`compute_authority_decay`.
_AUTHORITY_HALF_LIFE_SECONDS: float = 86_400.0  # 24 hours

#: Quorum fraction required for a consensus vote to be considered valid.
_DEFAULT_QUORUM_FRACTION: float = 0.51

#: Vote-approval threshold: a voter casts "yes" when trust_score >= this value.
_VOTE_YES_THRESHOLD: float = 0.6

#: Small epsilon to guard against division-by-zero in normalisation routines.
_EPSILON: float = 1e-12

#: Maximum Euclidean distance between two 3-D normalised trust vectors (√3).
_MAX_TRUST_EUCLIDEAN: float = math.sqrt(3.0)

# ---------------------------------------------------------------------------
# __all__ — public API surface
# ---------------------------------------------------------------------------
__all__ = [
    # Helper functions
    "_utcnow",
    "_uid",
    "_clamp",
    "_sigmoid",
    "_normalize_weights",
    "_cosine_similarity",
    "_entropy",
    "_borda_count_voting",
    "_weighted_average",
    "_jaccard_similarity",
    # Main class
    "FederationAlgorithms",
    # Free functions
    "compute_federation_distance",
    "rank_federation_candidates",
    # Constants re-exported for downstream use
    "_AUTHORITY_HALF_LIFE_SECONDS",
    "_DEFAULT_QUORUM_FRACTION",
    "_VOTE_YES_THRESHOLD",
]


# ===========================================================================
# Helper functions
# ===========================================================================

def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    This thin wrapper exists so that callers throughout the federation subsystem
    have a single, easily-mockable source of "now".  In production it simply
    delegates to :func:`time.time`; in tests the function can be patched via
    ``unittest.mock.patch`` without needing to patch the ``time`` module itself.

    Args:
        (none)

    Returns:
        float: POSIX timestamp representing the current UTC moment, e.g.
            ``1_700_000_000.123456``.

    Notes:
        - The returned value has sub-second precision on all CPython builds.
        - This function has no side effects and is safe to call from any thread.
        - Do **not** use ``datetime.utcnow()`` in this codebase — it is naive
          (no tzinfo) and deprecated in Python 3.12+.
    """
    return time.time()


def _uid() -> str:
    """Generate a new random UUID4 string without hyphens.

    Returns a compact 32-character hexadecimal identifier suitable for use as
    a database primary key, log correlation ID, or authority grant token.  The
    underlying :func:`uuid.uuid4` call uses the OS CSPRNG and is therefore
    cryptographically unpredictable.

    Args:
        (none)

    Returns:
        str: 32-character lowercase hexadecimal UUID4 string, e.g.
            ``'a3f1c2d4b5e6f7a8b9c0d1e2f3a4b5c6'``.

    Notes:
        - Hyphens are stripped so that the result is safe to embed in URLs,
          filenames, and JSON keys without quoting.
        - Uniqueness is guaranteed with overwhelming probability (collision
          probability ≈ 2^-122 per pair of independently generated IDs).
        - Thread-safe: each call obtains its own random bytes from the OS.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* into the closed interval [*lo*, *hi*].

    A minimal utility used throughout scoring and decay calculations to ensure
    that floating-point rounding errors cannot push a result outside its
    intended range.  For example, an exponential-decay formula might return
    1.0000000000000002 due to IEEE 754 rounding; clamping it to [0.0, 1.0]
    keeps downstream callers safe.

    Args:
        value (float): The numeric value to clamp.
        lo    (float): The lower bound of the output range (inclusive).
        hi    (float): The upper bound of the output range (inclusive).

    Returns:
        float: ``max(lo, min(hi, value))``.

    Notes:
        - If ``lo > hi`` the behaviour is undefined (standard Python ``min``/
          ``max`` semantics apply, which may return a value outside the
          intended range).
        - NaN propagates: if *value* is ``float('nan')``, the returned value is
          also NaN because ``min``/``max`` with NaN is implementation-defined.
    """
    return max(lo, min(hi, value))


def _sigmoid(x: float) -> float:
    """Standard logistic sigmoid function: σ(x) = 1 / (1 + exp(−x)).

    Maps any real number to the open interval (0, 1), making it useful as a
    soft threshold or normalisation step.  Overflow is handled explicitly: for
    very negative *x* the denominator overflows to +inf, so the result is 0.0;
    for very large positive *x* the result saturates to 1.0 via the clamped
    formula.

    Args:
        x (float): Input value.  May be any finite float or ±inf.

    Returns:
        float: σ(x) ∈ (0, 1).  For x = 0 returns exactly 0.5.

    Notes:
        - The numerically stable form used here is the standard one; for large
          negative x we exploit the identity σ(x) = exp(x) / (1 + exp(x)) to
          avoid computing 1 + exp(large_positive_number).
        - For |x| > ~710 (IEEE 754 double overflow boundary), the result is
          clamped to 0.0 or 1.0 respectively.
        - This function is used by :meth:`FederationAlgorithms.federation_score`
          to map a raw linear score into a probability-like value.
    """
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:
        # exp(-x) overflowed → x is very negative → sigmoid → 0
        return 0.0


def _normalize_weights(weights: list[float]) -> list[float]:
    """Normalise a list of non-negative floats so they sum to 1.0.

    Used wherever a probability distribution must be derived from raw positive
    scores (e.g. voter trust weights).  If all weights are zero (or the list
    is empty) a uniform distribution is returned so that downstream consumers
    always receive a valid distribution.

    Args:
        weights (list[float]): Raw non-negative weights.  May be empty.

    Returns:
        list[float]: A list of the same length whose elements sum to 1.0
            (within floating-point tolerance).  Returns ``[]`` for empty input.

    Notes:
        - Negative weights are silently treated as 0.0 via max(0, w) clamping
          before normalisation.  Callers should validate their inputs if
          negative weights indicate a programming error.
        - If the sum of all weights is below ``_EPSILON``, a uniform
          distribution is returned to avoid division by zero.
        - The implementation is O(n) in both time and space.
    """
    if not weights:
        return []
    # Floor at zero to handle accidental small negatives from floating-point ops
    clamped = [max(0.0, w) for w in weights]
    total = sum(clamped)
    if total < _EPSILON:
        # Degenerate case: return uniform distribution
        n = len(clamped)
        return [1.0 / n] * n
    return [w / total for w in clamped]


def _cosine_similarity(a: dict, b: dict) -> float:
    """Compute cosine similarity between two sparse vectors represented as dicts.

    Each dict maps a string key (feature name) to a float value (feature
    weight).  Keys absent from a dict are treated as having value 0.0.  This
    allows efficient computation without materialising dense vectors.

    Mathematically: cos(θ) = (a · b) / (‖a‖₂ · ‖b‖₂)

    Args:
        a (dict): First sparse vector, mapping str → float.
        b (dict): Second sparse vector, mapping str → float.

    Returns:
        float: Cosine similarity in [−1.0, 1.0].  Returns 0.0 if either
            vector is empty or has zero magnitude.

    Notes:
        - Only the intersection of keys contributes to the dot product, which
          is efficient for highly sparse vectors with little overlap.
        - The result is clamped to [−1, 1] to guard against rounding errors
          that might push it infinitesimally outside this range.
        - For trust-profile vectors all values are non-negative, so the result
          will always be in [0.0, 1.0] in typical federation usage.
    """
    if not a or not b:
        return 0.0
    # Dot product over shared keys only
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in a.keys() & b.keys())
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a < _EPSILON or mag_b < _EPSILON:
        return 0.0
    return _clamp(dot / (mag_a * mag_b), -1.0, 1.0)


def _entropy(probs: list[float]) -> float:
    """Compute the Shannon entropy H of a discrete probability distribution.

    H(P) = −∑ pᵢ · log₂(pᵢ)   (summing only over pᵢ > 0)

    Entropy measures the information content (or uncertainty) of a
    distribution.  In the federation context it is used to assess how
    "spread out" a vote distribution is — low entropy means strong consensus,
    high entropy means disagreement.

    Args:
        probs (list[float]): Probability values.  Should sum to 1.0, but this
            is not enforced; values ≤ 0 are ignored (contributing 0 to the sum
            per the convention 0 · log 0 = 0).

    Returns:
        float: Non-negative entropy in bits.  Returns 0.0 for an empty list or
            a degenerate distribution concentrated on a single outcome.

    Notes:
        - The maximum entropy for a distribution over n outcomes is log₂(n).
        - This function does **not** normalise *probs*; callers should pass a
          normalised distribution (e.g. the output of ``_normalize_weights``)
          if they want a meaningful result.
        - Negative probabilities are silently skipped, not clamped.
    """
    if not probs:
        return 0.0
    h = 0.0
    for p in probs:
        if p > 0.0:
            h -= p * math.log2(p)
    return h


def _borda_count_voting(
    candidates: list[str],
    preferences: list[list[str]],
) -> list[tuple[str, int]]:
    """Aggregate ranked preference lists using the Borda count method.

    In the Borda count, a voter with k candidates awards k−1 points to their
    first choice, k−2 to their second, …, and 0 to their last.  This provides
    a way to aggregate ordinal preferences into a cardinal ranking without
    assuming that preference intensities are comparable.

    In the federation context, each "voter" is a node that has ranked competing
    discoveries; the Borda count aggregates these into a committee ranking.

    Args:
        candidates  (list[str]): All candidate names to be scored.  Candidates
            not mentioned in a preference list receive 0 points from that voter.
        preferences (list[list[str]]): Each inner list is one voter's complete
            or partial ranking, from most to least preferred.

    Returns:
        list[tuple[str, int]]: Sorted (descending by score) list of
            (candidate_name, total_borda_score) pairs.

    Notes:
        - Candidates omitted from a voter's preference list receive 0 points
          from that voter (equivalent to ranking them last).
        - If two candidates tie, Python's stable sort preserves their relative
          order from *candidates*.
        - Time complexity: O(|voters| × |candidates|).
    """
    k = len(candidates)
    scores: dict[str, int] = {c: 0 for c in candidates}
    for pref in preferences:
        for rank, candidate in enumerate(pref):
            if candidate in scores:
                # Award k - 1 - rank points (0-indexed rank → first place = k-1)
                scores[candidate] += max(0, k - 1 - rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _weighted_average(values: list[float], weights: list[float]) -> float:
    """Compute the weighted arithmetic mean of *values*.

    Weighted mean: x̄ = (∑ wᵢ · xᵢ) / (∑ wᵢ)

    Args:
        values  (list[float]): Numeric values.  Must have the same length as
            *weights*.
        weights (list[float]): Non-negative weights corresponding to each value.

    Returns:
        float: Weighted mean.  Returns 0.0 if *values* is empty or all weights
            are zero.

    Notes:
        - The function does **not** require weights to be pre-normalised.
        - If ``len(values) != len(weights)`` a ``ValueError`` is raised.
        - Negative weights are accepted but may produce nonsensical results; the
          caller is responsible for ensuring weights are semantically valid.
    """
    if not values:
        return 0.0
    if len(values) != len(weights):
        raise ValueError(
            f"_weighted_average: len(values)={len(values)} != "
            f"len(weights)={len(weights)}"
        )
    total_weight = sum(weights)
    if total_weight < _EPSILON:
        return 0.0
    return sum(v * w for v, w in zip(values, weights)) / total_weight


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute the Jaccard similarity coefficient between two sets.

    J(A, B) = |A ∩ B| / |A ∪ B|

    The Jaccard coefficient is 0 when the sets are disjoint and 1 when they
    are identical.  It is used here to measure the overlap between the key
    sets of two knowledge dicts (i.e. how many features they share).

    Args:
        set_a (set): First set.  May be empty.
        set_b (set): Second set.  May be empty.

    Returns:
        float: Jaccard similarity in [0.0, 1.0].  Returns 0.0 when both sets
            are empty (the convention that ∅ ∩ ∅ / ∅ ∪ ∅ = 0/0 := 0).

    Notes:
        - This function operates on arbitrary Python sets; elements need not be
          strings.
        - Time complexity: O(min(|A|, |B|)) for the intersection lookup with
          Python's built-in set operations.
        - The complement ``1 − J(A, B)`` is the Jaccard distance, a valid
          metric on the power set of a universe.
    """
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


# ===========================================================================
# FederationAlgorithms class
# ===========================================================================

class FederationAlgorithms:
    """Stateless collection of federation algorithms for the JuGeo discovery system.

    This class provides a namespace for all algorithmic operations described in
    ``theory2.tex`` Chapter 61.  All methods are ``@staticmethod``; the class
    carries no instance state and should never be instantiated.  The static-
    method organisation is a deliberate design choice: it keeps the algorithms
    discoverable (IDE auto-complete works on the class), testable (no fixtures
    needed for construction), and serialisation-friendly (no hidden state).

    Algorithmic coverage
    --------------------
    The methods fall into four functional categories:

    1. **Propagation** — :meth:`propagate` computes how a piece of knowledge
       travels across the federation graph, attenuating by cosine similarity
       at each hop.

    2. **Consensus** — :meth:`consensus_vote` and :meth:`authority_grant`
       implement the two-phase commit protocol described in Ch61 §4: first a
       quorum-based vote, then conditional authority issuance.

    3. **Conflict resolution** — :meth:`conflict_resolve` selects a winner
       from competing discoveries using weighted Borda counting.

    4. **Scoring and ranking** — :meth:`federation_score`,
       :meth:`compute_trust_distance`, :meth:`rank_nodes_by_trust`,
       :meth:`compute_knowledge_overlap`, :meth:`compute_authority_decay`, and
       :meth:`select_merge_candidates` produce numeric summaries used by the
       federation controller to make scheduling and merging decisions.

    All inputs and outputs are plain Python dicts (or lists thereof) so that
    results can be trivially serialised to JSON and stored in the audit log.

    Thread safety
    -------------
    All methods are pure functions (no shared mutable state); they are safe to
    call concurrently from multiple threads or asyncio tasks without any locking.

    See also
    --------
    :func:`compute_federation_distance`, :func:`rank_federation_candidates`
    """

    # ------------------------------------------------------------------
    # 1. Knowledge propagation
    # ------------------------------------------------------------------

    @staticmethod
    def propagate(knowledge: dict, nodes: list[Any]) -> dict:
        """Propagate a knowledge artefact across a list of federation nodes.

        For each node, the relevance of *knowledge* to that node is measured by
        the cosine similarity between the knowledge's ``feature_vector`` and
        the node's ``trust_profile``.  The propagation path is the ordered list
        of node IDs sorted by descending relevance — i.e. the most-interested
        node receives the knowledge first (highest bandwidth edge).

        The returned dict is compatible with the ``KnowledgePropagation`` schema
        defined in the JuGeo data-model layer.

        Args:
            knowledge (dict): The knowledge artefact to propagate.  Expected
                keys: ``id`` (str), ``feature_vector`` (dict[str, float]),
                ``source_node_id`` (str).  Missing keys are handled gracefully.
            nodes (list[dict]): Candidate target nodes.  Each node should have
                ``id`` (str) and ``trust_profile`` (dict[str, float]).

        Returns:
            dict: Propagation record with keys:
                - ``knowledge_id`` (str)
                - ``source_node`` (str)
                - ``target_nodes`` (list[str]) — node IDs in propagation order
                - ``propagation_path`` (list[str]) — same as target_nodes
                - ``relevance_scores`` (dict[str, float]) — node_id → similarity

        Notes:
            - Nodes with relevance score 0.0 are included in the result but
              will naturally sort to the end of the propagation path.
            - This method does **not** perform actual message passing; it
              computes the *plan* for propagation.  Execution is the caller's
              responsibility.
            - The cosine similarity is computed between the knowledge's
              ``feature_vector`` and the node's ``trust_profile``; both are
              treated as sparse numeric vectors.
        """
        knowledge_id = knowledge.get("id", knowledge.get("source", _uid()))
        source_node = knowledge.get("source_node_id", knowledge.get("source", "unknown"))
        feature_vec = knowledge.get("feature_vector", {})
        if not feature_vec and "items" in knowledge:
            feature_vec = {str(item): 1.0 for item in knowledge.get("items", [])}

        # Compute cosine similarity of knowledge features vs each node profile
        relevance_scores: dict[str, float] = {}
        for node in nodes:
            if isinstance(node, str):
                node_id = node
                trust_profile = {}
            else:
                node_id = node.get("id", node.get("node_id", _uid()))
                trust_profile = node.get("trust_profile", {})
                if not trust_profile and "trust_score" in node:
                    trust_profile = {"trust_score": float(node.get("trust_score", 0.0))}
            sim = _cosine_similarity(feature_vec, trust_profile)
            relevance_scores[node_id] = round(sim, 6)

        # Order nodes by descending relevance to define the propagation path
        ordered_ids = sorted(
            relevance_scores.keys(),
            key=lambda nid: relevance_scores[nid],
            reverse=True,
        )

        return {
            "knowledge_id": knowledge_id,
            "source_node": source_node,
            "target_nodes": ordered_ids,
            "propagation_path": ordered_ids,
            "relevance_scores": relevance_scores,
            "nodes": ordered_ids,
            "propagated": len(ordered_ids),
            "status": "ok",
        }

    # ------------------------------------------------------------------
    # 2. Consensus voting
    # ------------------------------------------------------------------

    @staticmethod
    def consensus_vote(discovery: Any, voters: Any) -> dict:
        """Simulate a quorum-based consensus vote on a candidate discovery.

        Each voter in *voters* casts a weighted binary vote ("yes" or "no")
        depending on whether its ``trust_score`` meets or exceeds
        ``_VOTE_YES_THRESHOLD`` (default 0.6).  The final ``outcome`` is
        ``"approved"`` if yes_weight / total_weight > 0.5 **and** the quorum
        condition is met (total_weight / max_possible_weight >= quorum fraction).

        Args:
            discovery (dict): The discovery under consideration.  Expected keys:
                ``id`` (str), ``novelty_score`` (float, default 0.5).
            voters (list[dict]): Participating voters.  Each voter should have
                ``id`` (str), ``trust_score`` (float), ``trust_weight`` (float).

        Returns:
            dict: Vote result with keys:
                - ``discovery_id`` (str)
                - ``yes_weight``   (float) — cumulative weight of yes votes
                - ``no_weight``    (float) — cumulative weight of no votes
                - ``total_weight`` (float) — yes_weight + no_weight
                - ``outcome``      (str)   — ``"approved"`` or ``"rejected"``
                - ``quorum_met``   (bool)
                - ``vote_entropy`` (float) — entropy of the yes/no distribution

        Notes:
            - Voters with missing ``trust_weight`` default to weight 1.0.
            - Voters with missing ``trust_score`` default to 0.0 (always vote no).
            - The quorum check uses the *sum* of all voter weights (not count).
            - ``vote_entropy`` is always in [0, 1] bit (binary case maximum = 1).
        """
        if isinstance(discovery, list) and not isinstance(voters, list):
            discovery, voters = voters, discovery
        discovery_id = discovery.get("id", _uid()) if isinstance(discovery, dict) else str(discovery)
        voters = list(voters or [])

        yes_weight = 0.0
        no_weight = 0.0
        total_possible = sum(v.get("trust_weight", v.get("weight", 1.0)) for v in voters)

        for voter in voters:
            w = voter.get("trust_weight", voter.get("weight", 1.0))
            vote = str(voter.get("vote", "")).upper()
            score = voter.get("trust_score", 0.0)
            if vote == "YES" or (not vote and score >= _VOTE_YES_THRESHOLD):
                yes_weight += w
            elif vote == "ABSTAIN":
                continue
            else:
                no_weight += w

        total_weight = yes_weight + no_weight
        quorum_met = (
            (total_weight / (total_possible + _EPSILON)) >= _DEFAULT_QUORUM_FRACTION
            if total_possible > _EPSILON
            else False
        )

        approval_ratio = yes_weight / (total_weight + _EPSILON)
        if total_weight <= _EPSILON:
            outcome = "ABSTAINED"
        elif not quorum_met:
            outcome = "PENDING"
        elif approval_ratio > 0.5:
            outcome = "ACCEPTED"
        else:
            outcome = "REJECTED"

        # Entropy of the yes/no binary distribution (max = 1 bit)
        norm = _normalize_weights([yes_weight, no_weight])
        vote_entropy = _entropy(norm)

        return {
            "discovery_id": discovery_id,
            "yes_weight": round(yes_weight, 6),
            "no_weight": round(no_weight, 6),
            "total_weight": round(total_weight, 6),
            "outcome": outcome,
            "quorum_met": quorum_met,
            "vote_entropy": round(vote_entropy, 6),
            "yes_ratio": round(approval_ratio if total_weight > _EPSILON else 0.0, 6),
        }

    # ------------------------------------------------------------------
    # 3. Authority granting
    # ------------------------------------------------------------------

    @staticmethod
    def authority_grant(discovery: Any, conditions: Any) -> dict:
        """Issue (or deny) an authority token for a discovery, given a conditions dict.

        An authority grant certifies that a discovery is legitimate within the
        current federation regime.  Granting is conditional on five Boolean
        predicates that the caller computes externally and passes in *conditions*.
        All five must be True for the grant to be issued.

        Args:
            discovery  (dict): The discovery seeking authority.  Expected keys:
                ``id`` (str), ``source_node_id`` (str).
            conditions (dict): Evaluation results for each precondition.
                Expected keys (all bool):
                - ``trust_ok``   — source node trust is above threshold
                - ``novelty_ok`` — discovery novelty score is sufficient
                - ``quorum_ok``  — a consensus vote has already passed
                - ``regime_ok``  — current regime permits this category
                - ``pack_ok``    — the discovery's pack membership is valid

        Returns:
            dict: Authority grant record with keys:
                - ``discovery_id``    (str)
                - ``granted``         (bool)
                - ``grant_id``        (str|None) — UUID if granted, else None
                - ``conditions_met``  (list[str])
                - ``conditions_failed`` (list[str])
                - ``timestamp``       (float) — UTC POSIX timestamp

        Notes:
            - Conditions not present in *conditions* default to False, causing
              the grant to be denied.
            - The ``grant_id`` is a new UUID4 generated at call time; callers
              should persist it as the canonical authority token.
            - This method is idempotent with respect to its inputs: calling it
              twice with the same arguments produces structurally identical
              results (modulo the UUID and timestamp).
        """
        if isinstance(discovery, dict) and isinstance(conditions, dict):
            discovery_id = discovery.get("id", _uid())
            required_conditions = ["trust_ok", "novelty_ok", "quorum_ok", "regime_ok", "pack_ok"]
            met: list[str] = []
            failed: list[str] = []
            for cond in required_conditions:
                if conditions.get(cond, False):
                    met.append(cond)
                else:
                    failed.append(cond)
            granted = len(failed) == 0
            level = discovery.get("authority_level", "")
        else:
            conditions_dict = discovery if isinstance(discovery, dict) else {}
            discovery_id = _uid()
            met = [name for name, value in conditions_dict.items() if bool(value)]
            failed = [name for name, value in conditions_dict.items() if not bool(value)]
            granted = bool(conditions_dict) and not failed
            level = str(conditions)
        return {
            "discovery_id": discovery_id,
            "granted": granted,
            "grant_id": _uid() if granted else None,
            "conditions_met": met,
            "conditions_failed": failed,
            "timestamp": _utcnow(),
            "level": level,
        }

    # ------------------------------------------------------------------
    # 4. Conflict resolution
    # ------------------------------------------------------------------

    @staticmethod
    def conflict_resolve(conflict: dict) -> dict:
        """Resolve a conflict between competing discoveries.

        Given a conflict record containing a list of competing discoveries and a
        resolution strategy, this method selects a winner.  Two strategies are
        supported:

        * ``"borda"`` (default) — each discovery is scored by the Borda count
          of its supporting nodes' trust weights, treating each node as a voter
          that ranks the discovery it supports first.
        * ``"trust_weighted"`` — the discovery whose supporters have the highest
          total trust weight wins outright.

        Args:
            conflict (dict): Conflict descriptor.  Expected keys:
                - ``id`` (str) — conflict identifier
                - ``competing_discoveries`` (list[dict]) — each with keys
                  ``id`` (str), ``supporting_nodes`` (list[dict] with
                  ``trust_weight`` float)
                - ``resolution_strategy`` (str, default ``"borda"``)

        Returns:
            dict: Resolution record with keys:
                - ``conflict_id``  (str)
                - ``winner``       (str) — winning discovery ID
                - ``resolution``   (str) — human-readable description
                - ``scores``       (dict[str, float]) — per-discovery scores
                - ``resolved_at``  (float) — UTC POSIX timestamp

        Notes:
            - If all competing discoveries have equal scores the first one in
              the list wins (deterministic tie-breaking by input order).
            - The ``"borda"`` strategy converts trust weights into ranks by
              sorting supporting nodes; this is an approximation of the full
              Borda count when precise ordinal data is unavailable.
            - An empty ``competing_discoveries`` list raises ``ValueError``.
        """
        conflict_id = conflict.get("id", _uid())
        discoveries = conflict.get("competing_discoveries", [])
        strategy    = conflict.get("resolution_strategy", "borda")

        if not discoveries:
            resolution_kind = conflict.get("type", "unresolved_conflict")
            return {
                "conflict_id": conflict_id,
                "winner": conflict.get("node_a") or conflict.get("node_b"),
                "resolution": f"Resolved {resolution_kind} via default reconciliation",
                "scores": {},
                "resolved_at": _utcnow(),
            }

        scores: dict[str, float] = {}

        if strategy == "trust_weighted":
            # Score = sum of supporter trust_weight values
            for disc in discoveries:
                disc_id = disc.get("id", _uid())
                supporters = disc.get("supporting_nodes", [])
                scores[disc_id] = sum(n.get("trust_weight", 1.0) for n in supporters)

        else:
            # "borda" strategy: treat each discovery's total support weight as its score,
            # then rank them for a Borda-like assignment.
            # Step 1: compute raw support weight per discovery
            raw: dict[str, float] = {}
            for disc in discoveries:
                disc_id = disc.get("id", _uid())
                supporters = disc.get("supporting_nodes", [])
                raw[disc_id] = sum(n.get("trust_weight", 1.0) for n in supporters)

            # Step 2: assign Borda points based on rank of raw score (highest = most points)
            ranked = sorted(raw.keys(), key=lambda d: raw[d], reverse=True)
            k = len(ranked)
            for rank, disc_id in enumerate(ranked):
                # Borda: first place gets k-1, second gets k-2, …, last gets 0
                scores[disc_id] = float(k - 1 - rank)

        winner = max(scores, key=lambda d: scores[d])
        resolution = (
            f"Discovery {winner!r} selected via {strategy!r} strategy "
            f"with score {scores[winner]:.4f}"
        )

        return {
            "conflict_id": conflict_id,
            "winner":      winner,
            "resolution":  resolution,
            "scores":      {k: round(v, 6) for k, v in scores.items()},
            "resolved_at": _utcnow(),
        }

    # ------------------------------------------------------------------
    # 5. Federation score
    # ------------------------------------------------------------------

    @staticmethod
    def federation_score(node: dict, discovery: Optional[dict] = None) -> float:
        """Compute a scalar federation score ∈ [0, 1] for a (node, discovery) pair.

        The score synthesises three independent signals:

        1. **Trust alignment** — cosine similarity between the node's
           ``trust_profile`` and the discovery's ``feature_vector``.
        2. **Novelty bonus** — the discovery's ``novelty_score`` (default 0.5),
           interpreted as how surprising the discovery is to the existing corpus.
        3. **Regime compatibility** — a binary flag (0 or 1) indicating whether
           the node's current regime is listed in the discovery's
           ``compatible_regimes``.

        The three signals are combined as a weighted average, then mapped
        through the sigmoid function to keep the result smooth and bounded.

        Args:
            node      (dict): Node descriptor.  Expected keys: ``trust_profile``
                (dict[str, float]), ``regime`` (str).
            discovery (dict): Discovery descriptor.  Expected keys:
                ``feature_vector`` (dict[str, float]), ``novelty_score`` (float),
                ``compatible_regimes`` (list[str]).

        Returns:
            float: Score in [0.0, 1.0].  Higher values indicate stronger fit
                between the node and the discovery.

        Notes:
            - The weights for the three signals are currently hard-coded as
              (0.5, 0.3, 0.2); they may be exposed as parameters in a future
              version (see TODO in theory2.tex Ch61 §7.3).
            - The sigmoid is applied to a centred linear combination:
              ``sigmoid(6 * (x − 0.5))`` maps the [0,1] linear score onto (0,1)
              with a steeper slope near 0.5, encouraging discrimination.
        """
        discovery = discovery or {}
        if not discovery:
            return _clamp(float(node.get("trust_score", 0.0)), 0.0, 1.0)

        trust_profile = node.get("trust_profile", {})
        if not trust_profile and "trust_score" in node:
            trust_profile = {"trust_score": float(node.get("trust_score", 0.0))}
        node_regime = node.get("regime", "")
        feature_vec = discovery.get("feature_vector", {})
        novelty = _clamp(float(discovery.get("novelty_score", 0.5)), 0.0, 1.0)
        compat_regimes = discovery.get("compatible_regimes", [])

        # Signal 1: trust alignment via cosine similarity
        trust_align = _cosine_similarity(trust_profile, feature_vec)

        # Signal 2: novelty bonus — already in [0, 1]
        novelty_signal = novelty

        # Signal 3: regime compatibility — binary 0 or 1
        regime_compat = 1.0 if node_regime in compat_regimes else 0.0

        # Weighted combination: weights sum to 1.0
        w_trust, w_novelty, w_regime = 0.5, 0.3, 0.2
        linear_score = (
            w_trust   * trust_align   +
            w_novelty * novelty_signal +
            w_regime  * regime_compat
        )

        # Sigmoid with slope-amplification: maps [0,1] → (0,1) with centre at 0.5
        # Using 6*(x-0.5) so that score=0.5 maps to sigmoid(0)=0.5 exactly.
        raw_sigmoid = _sigmoid(6.0 * (linear_score - 0.5))
        return _clamp(raw_sigmoid, 0.0, 1.0)

    # ------------------------------------------------------------------
    # 6. Trust distance
    # ------------------------------------------------------------------

    @staticmethod
    def compute_trust_distance(node_a: dict, node_b: dict) -> float:
        """Compute the normalised Euclidean distance between two node trust profiles.

        The trust profiles are treated as vectors in ℝ^d (d = union of all keys).
        The raw Euclidean distance is divided by the theoretical maximum
        (√3 for 3-D unit vectors, generalised to √d for d dimensions) to obtain
        a value in [0, 1].

        In practice federation nodes carry 3-dimensional trust profiles with keys
        ``confidence``, ``novelty``, and ``reliability``; the formula is general.

        Args:
            node_a (dict): First node.  Must have a ``trust_profile`` key mapping
                feature names to float values.
            node_b (dict): Second node.  Must have a ``trust_profile`` key mapping
                feature names to float values.

        Returns:
            float: Normalised trust distance in [0.0, 1.0].  Returns 1.0 when
                the profiles are maximally dissimilar, 0.0 when identical.

        Notes:
            - Missing keys are treated as 0.0 in both vectors.
            - The normalisation uses √d (where d is the number of distinct feature
              dimensions) so that the result is scale-invariant with respect to
              the number of trust dimensions.
            - If both profiles are empty, the distance is defined as 0.0.
        """
        profile_a = node_a.get("trust_profile", {})
        profile_b = node_b.get("trust_profile", {})
        if not profile_a:
            profile_a = {"trust_score": float(node_a.get("trust_score", 0.0))}
        if not profile_b:
            profile_b = {"trust_score": float(node_b.get("trust_score", 0.0))}

        # Union of all feature keys
        all_keys = set(profile_a.keys()) | set(profile_b.keys())
        if not all_keys:
            return 0.0

        # Euclidean distance in the feature space
        sq_sum = sum(
            (profile_a.get(k, 0.0) - profile_b.get(k, 0.0)) ** 2
            for k in all_keys
        )
        euclidean = math.sqrt(sq_sum)

        # Normalise by √d (theoretical maximum for unit-bounded dimensions)
        d = len(all_keys)
        max_dist = math.sqrt(float(d))
        return _clamp(euclidean / (max_dist + _EPSILON), 0.0, 1.0)

    # ------------------------------------------------------------------
    # 7. Rank nodes by trust
    # ------------------------------------------------------------------

    @staticmethod
    def rank_nodes_by_trust(nodes: list[dict]) -> list[dict]:
        """Return a copy of *nodes* sorted by ``trust_score`` in descending order.

        This is a convenience method used by the federation controller to
        determine the priority order for broadcasting a new discovery: nodes
        with higher trust scores are considered more reliable relays and should
        receive the discovery first.

        Args:
            nodes (list[dict]): List of node descriptors.  Each node should have
                a ``trust_score`` key (float).  Nodes without this key are
                treated as having trust_score = 0.0.

        Returns:
            list[dict]: New list (shallow copy) sorted by ``trust_score``
                descending.  The original list is not mutated.

        Notes:
            - The sort is stable: nodes with equal trust_score preserve their
              relative order from the input list.
            - The returned list contains references to the same dict objects as
              the input; modifying a returned dict will affect the original.
            - To obtain the *n* most-trusted nodes use ``result[:n]``.
        """
        return sorted(nodes, key=lambda n: n.get("trust_score", 0.0), reverse=True)

    # ------------------------------------------------------------------
    # 8. Knowledge overlap
    # ------------------------------------------------------------------

    @staticmethod
    def compute_knowledge_overlap(k1: Any, k2: Any) -> float:
        """Compute the Jaccard similarity between the key sets of two knowledge dicts.

        Two knowledge artefacts "overlap" when they share many feature keys,
        even if the values differ.  This is a fast proxy for semantic overlap
        that requires no embedding lookup.

        Args:
            k1 (dict): First knowledge artefact (arbitrary key-value mapping).
            k2 (dict): Second knowledge artefact (arbitrary key-value mapping).

        Returns:
            float: Jaccard similarity of the key sets in [0.0, 1.0].
                Returns 0.0 when both dicts are empty.

        Notes:
            - Only the *keys* are compared, not the values.  For value-level
              similarity use :func:`_cosine_similarity` on the value vectors.
            - This method is symmetric: ``overlap(k1, k2) == overlap(k2, k1)``.
            - The result is used by :meth:`select_merge_candidates` to decide
              whether two knowledge entries are candidates for merging.
        """
        if isinstance(k1, set) and isinstance(k2, set):
            return _jaccard_similarity(k1, k2)
        return _jaccard_similarity(set(k1), set(k2))

    # ------------------------------------------------------------------
    # 9. Authority decay
    # ------------------------------------------------------------------

    @staticmethod
    def compute_authority_decay(authority: dict, current_time: float) -> float:
        """Compute the remaining authority fraction using exponential decay.

        Authority tokens degrade over time following the standard exponential
        decay law:

            remaining(t) = exp(−λ · Δt)

        where λ = ln(2) / half_life and Δt = current_time − grant_time.

        Args:
            authority    (dict): Authority record.  Expected keys:
                ``grant_time`` (float, POSIX timestamp), ``half_life``
                (float, seconds; defaults to ``_AUTHORITY_HALF_LIFE_SECONDS``).
            current_time (float): Current POSIX timestamp (from ``_utcnow()``).

        Returns:
            float: Remaining authority fraction in [0.0, 1.0].  Returns 1.0
                if ``grant_time`` is in the future (clock skew guard).  Returns
                0.0 for very old grants (> 30 × half_life).

        Notes:
            - The decay constant λ is derived from the half-life: after exactly
              one half-life the authority is at 0.5 of its original value.
            - Negative Δt (grant_time > current_time) is treated as Δt = 0,
              returning 1.0.  This guards against small clock-skew issues in
              distributed environments.
            - Authority records without a ``grant_time`` key default to
              ``current_time`` (fully fresh).
        """
        grant_time = authority.get("grant_time", authority.get("granted_at", current_time))
        half_life  = float(authority.get("half_life", _AUTHORITY_HALF_LIFE_SECONDS))

        delta_t = max(0.0, current_time - grant_time)  # Clamp negative Δt to 0

        if half_life < _EPSILON:
            # Degenerate: zero half-life → instant decay
            return 0.0

        # λ = ln(2) / T½;  remaining = e^(−λ · Δt)
        lam = math.log(2.0) / half_life
        remaining = math.exp(-lam * delta_t)
        return _clamp(remaining, 0.0, 1.0)

    # ------------------------------------------------------------------
    # 10. Merge candidate selection
    # ------------------------------------------------------------------

    @staticmethod
    def select_merge_candidates(
        entries: list[dict],
        threshold: float = 0.5,
        top_k: Optional[int] = None,
    ) -> Any:
        """Group knowledge entries into merge-candidate clusters by knowledge overlap.

        Two entries are placed in the same group if their
        :meth:`compute_knowledge_overlap` score is ≥ *threshold*.  The grouping
        algorithm is a simple greedy scan: each entry is compared to the first
        entry of each existing group; if a match is found it joins that group,
        otherwise a new group is started.

        Args:
            entries   (list[dict]): Knowledge entries to cluster.  Each entry is
                an arbitrary dict whose *keys* represent features.
            threshold (float): Minimum Jaccard overlap required to join a group.
                Default 0.5.  Must be in [0.0, 1.0].

        Returns:
            list[list[dict]]: List of groups.  Each group is a non-empty list
                of entries.  Every input entry appears in exactly one group.
                Groups are ordered by first appearance in *entries*.

        Notes:
            - The greedy approach is O(n²) in the worst case; it is suitable
              for the typical federation batch size of < 1000 entries.
            - Singleton groups (entries that matched nothing) are included in the
              output.
            - Setting ``threshold=0.0`` places all entries in a single group
              (every pair has overlap ≥ 0); setting ``threshold=1.0`` groups
              only entries with identical key sets.
        """
        if top_k is not None:
            ranked = FederationAlgorithms.rank_nodes_by_trust(entries)
            return ranked[: max(0, top_k)]

        groups: list[list[dict]] = []

        for entry in entries:
            placed = False
            for group in groups:
                # Compare against the representative (first) element of the group
                overlap = FederationAlgorithms.compute_knowledge_overlap(
                    entry, group[0]
                )
                if overlap >= threshold:
                    group.append(entry)
                    placed = True
                    break
            if not placed:
                groups.append([entry])

        return groups


# ===========================================================================
# Free functions
# ===========================================================================

def compute_federation_distance(
    federation_a: dict,
    federation_b: dict,
) -> float:
    """Compute a symmetric distance metric between two federation descriptors.

    The distance is a convex combination of two Jaccard-based dissimilarities:

    1. **Node dissimilarity** — 1 − J(node_id_set_A, node_id_set_B)
    2. **Discovery dissimilarity** — 1 − J(discovery_id_set_A, discovery_id_set_B)

    These are combined with equal weights (0.5 each) to produce an overall
    distance in [0, 1].  A distance of 0 means the two federations share all
    nodes and all discoveries; a distance of 1 means they share neither.

    This metric is used by :func:`rank_federation_candidates` to order candidate
    federations by how "close" they are to a reference federation, enabling the
    federation controller to prefer merges with the most-similar peers.

    Args:
        federation_a (dict): First federation descriptor.  Expected keys:
            ``node_ids`` (list[str]), ``discovery_ids`` (list[str]).
        federation_b (dict): Second federation descriptor.  Expected keys:
            ``node_ids`` (list[str]), ``discovery_ids`` (list[str]).

    Returns:
        float: Symmetric distance in [0.0, 1.0].  Returns 1.0 when the
            federations are completely disjoint, 0.0 when identical.

    Notes:
        - Missing ``node_ids`` or ``discovery_ids`` keys default to empty lists,
          which contribute maximum dissimilarity for that component.
        - The metric is symmetric: ``distance(A, B) == distance(B, A)``.
        - Extending the formula to include a third component (e.g. regime overlap)
          is described in theory2.tex Ch61 §8.2 but not yet implemented here.
        - Time complexity: O(|nodes_A| + |nodes_B| + |disc_A| + |disc_B|) due
          to set construction.
    """
    if "trust_score" in federation_a or "trust_score" in federation_b:
        score_a = float(federation_a.get("trust_score", 0.0))
        score_b = float(federation_b.get("trust_score", 0.0))
        return abs(score_a - score_b)

    nodes_a = set(federation_a.get("node_ids", []))
    nodes_b = set(federation_b.get("node_ids", []))
    disc_a = set(federation_a.get("discovery_ids", []))
    disc_b = set(federation_b.get("discovery_ids", []))

    # Jaccard similarity → dissimilarity
    node_sim  = _jaccard_similarity(nodes_a, nodes_b)
    disc_sim  = _jaccard_similarity(disc_a,  disc_b)

    node_dist = 1.0 - node_sim
    disc_dist = 1.0 - disc_sim

    # Equal-weight convex combination
    distance = 0.5 * node_dist + 0.5 * disc_dist
    return _clamp(distance, 0.0, 1.0)


def rank_federation_candidates(
    candidates: list[dict],
    reference: dict | None = None,
    criteria: dict[str, str] | None = None,
) -> list[dict]:
    """Rank candidate federations by their distance from a reference federation.

    For each candidate, :func:`compute_federation_distance` is called to obtain
    a scalar distance from *reference*.  The candidates are then sorted in
    ascending order of distance (closest first) and the computed distance is
    injected into each candidate dict under the key ``"distance"``.

    This function is used by the federation controller when deciding which peer
    federation to propose a merge with: candidates at distance 0 are already
    fully overlapping (trivial merge), while candidates at distance 1 are
    maximally disjoint (potentially high-value merger).

    Args:
        candidates (list[dict]): Candidate federation descriptors.  Each should
            have ``node_ids`` (list[str]) and ``discovery_ids`` (list[str]).
            The dicts are shallow-copied before the ``"distance"`` key is added.
        reference  (dict): The reference federation against which all candidates
            are measured.  Same structure as the items in *candidates*.

    Returns:
        list[dict]: New list of shallow-copied candidate dicts, each augmented
            with a ``"distance"`` key (float ∈ [0, 1]), sorted by ``"distance"``
            ascending.

    Notes:
        - The original candidate dicts are **not** mutated; shallow copies are
          made before adding the ``"distance"`` key.
        - If *candidates* is empty an empty list is returned immediately.
        - Ties in distance are broken by the original order in *candidates*
          (Python's sort is stable).
        - The function is O(n · (|nodes| + |discoveries|)) in time complexity,
          which is acceptable for typical federation sizes (< 10 000 nodes).
    """
    if not candidates:
        return []

    if criteria is not None or (reference is not None and "trust_score" in reference):
        criteria = criteria or {}
        key_name, direction = next(iter(criteria.items()), ("trust_score", "desc"))
        reverse = str(direction).lower() == "desc"
        return sorted(list(candidates), key=lambda item: item.get(key_name, 0.0), reverse=reverse)

    reference = reference or {}

    # Augment each candidate with its distance from the reference (shallow copy)
    scored: list[dict] = []
    for cand in candidates:
        cand_copy = dict(cand)  # Shallow copy to avoid mutating caller's data
        cand_copy["distance"] = compute_federation_distance(cand_copy, reference)
        scored.append(cand_copy)

    # Sort ascending by distance: closest federation first
    scored.sort(key=lambda c: c["distance"])
    return scored
