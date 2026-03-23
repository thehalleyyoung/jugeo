from __future__ import annotations

"""
theory2.tex Ch48 – "Treaty synthesis, negotiation memory, and archival semantics"

# copilot: This module implements core algorithms for treaty_memory orchestration,
# providing planning, execution, normalisation, and archival utilities for
# treaty episodes and candidate laws. All heavy-lifting is pure Python so the
# module is self-contained and importable without optional dependencies.

Design
------
The module is organised into five top-level sections:

  1. Imports & constants
  2. Frozen / mutable dataclasses (value objects)
  3. Planner – builds RetrievalPlan / SynthesisPlan objects
  4. Executor – runs plans against an in-memory index abstraction
  5. Normaliser – canonicalises and deduplicates episode/candidate dicts
  6. Standalone algorithm functions
  7. Internal helpers
  8. Smoke test (__main__)

All jugeo.*  imports are guarded with try/except ImportError so the module
loads cleanly even in an environment where the wider jugeo package is not yet
fully installed.
"""

import collections
import hashlib
import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ─── jugeo optional imports ──────────────────────────────────────────────────

try:
    from jugeo.core.episode import Episode  # type: ignore[import]
except ImportError:
    Episode: Any = None  # type: ignore[misc,assignment]

try:
    from jugeo.core.treaty import Treaty  # type: ignore[import]
except ImportError:
    Treaty: Any = None  # type: ignore[misc,assignment]

try:
    from jugeo.orchestration.treaty_memory.index import TreatyIndex  # type: ignore[import]
except ImportError:
    TreatyIndex: Any = None  # type: ignore[misc,assignment]

try:
    from jugeo.utils.hashing import stable_hash  # type: ignore[import]
except ImportError:
    def stable_hash(obj: Any) -> str:  # type: ignore[misc]
        """Fallback stable hash using repr + sha256."""
        raw = repr(obj).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

# ─── Public API ──────────────────────────────────────────────────────────────

__all__ = [
    # dataclasses
    "RetrievalPlan",
    "SynthesisPlan",
    # classes
    "TreatyMemoryPlanner",
    "TreatyMemoryExecutor",
    "TreatyMemoryNormalizer",
    # standalone functions
    "treaty_jaccard",
    "episode_cluster_kmeans",
    "law_candidate_refinement",
    "memory_compression_lru",
    "semantic_index_build",
    "treaty_diff",
    "convergence_score",
    "friction_signature",
]

# ─── Module-level logger ─────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─── Internal constants ───────────────────────────────────────────────────────

# Maximum number of retrieval steps allowed in a single plan before we warn.
_MAX_RETRIEVAL_STEPS: int = 64

# Default synthesis mode when caller does not specify one explicitly.
_DEFAULT_SYNTHESIS_MODE: str = "union"

# Supported synthesis modes understood by TreatyMemoryExecutor.
_VALID_SYNTHESIS_MODES: frozenset[str] = frozenset(
    {"union", "intersection", "weighted", "sequential", "adversarial"}
)

# Minimum Jaccard similarity required before two episodes are considered
# duplicates during deduplication.
_DEDUP_JACCARD_THRESHOLD: float = 0.90

# Number of LRU cache "generations" kept during memory compression.
_LRU_GENERATION_COUNT: int = 3

# Seed used by the pure-Python k-means initialisation (deterministic runs).
_KMEANS_SEED_PHRASE: str = "treaty-memory-kmeans-v1"

# Score below which a convergence result is flagged as "diverging".
_CONVERGENCE_WARNING_THRESHOLD: float = 0.20

# Weight applied to recency when building priority keys in a RetrievalPlan.
_RECENCY_WEIGHT: float = 0.35

# Weight applied to relevance score when building priority keys.
_RELEVANCE_WEIGHT: float = 0.65

# Byte-length of the friction signature hex digest prefix used as a short ID.
_FRICTION_SIG_LENGTH: int = 16

# Maximum candidate batch size processed in a single executor call.
_EXECUTOR_BATCH_LIMIT: int = 256

# Version tag embedded in generated plan IDs so plans can be invalidated on
# schema changes without comparing content.
_PLAN_VERSION: str = "v1"

# ─── Section 1: Value-object dataclasses ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    """An immutable plan describing how to retrieve treaty episodes from an index.

    Attributes
    ----------
    plan_id:
        Unique identifier for this plan, typically a namespaced UUID.
    steps:
        Ordered tuple of retrieval step descriptors. Each step is an arbitrary
        hashable object (usually a ``dict`` snapshot or small namedtuple)
        describing a single index query.
    estimated_cost:
        Estimated computational cost in arbitrary units (higher == more
        expensive). Used by the optimiser to compare alternative plans.
    priority_keys:
        Ordered tuple of string keys that the executor should use to rank
        retrieved episodes before returning them to the caller. The first key
        has the highest precedence (like an ORDER BY clause with multiple
        columns).

    Notes
    -----
    Because this dataclass is frozen and uses ``slots=True`` it is both
    immutable and memory-efficient — safe to use as a dict key or store in
    a set.
    """

    plan_id: str
    steps: tuple
    estimated_cost: float
    priority_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SynthesisPlan:
    """An immutable plan describing how to synthesise treaty episodes into a
    consolidated treaty representation.

    Attributes
    ----------
    plan_id:
        Unique identifier for this synthesis plan.
    source_episode_ids:
        Tuple of episode IDs that should be included as source material during
        synthesis.
    candidate_ids:
        Tuple of candidate-law IDs that should be evaluated for inclusion in
        the synthesised treaty.
    synthesis_mode:
        One of the values in ``_VALID_SYNTHESIS_MODES`` — controls how clauses
        from multiple episodes are combined.
    estimated_clauses:
        Expected number of clauses in the synthesised treaty. Used for
        pre-allocation and progress tracking.
    """

    plan_id: str
    source_episode_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    synthesis_mode: str
    estimated_clauses: int


# ─── Section 2: Planner ───────────────────────────────────────────────────────


class TreatyMemoryPlanner:
    """Plans retrieval and synthesis operations over the treaty memory index.

    The planner converts a high-level query or episode list into an explicit
    :class:`RetrievalPlan` or :class:`SynthesisPlan` without touching the
    index itself. This separation allows plans to be serialised, cached, or
    handed off to a different process for execution.

    Parameters
    ----------
    config:
        Arbitrary configuration dictionary. Recognised keys:

        ``max_steps`` (int, default 16):
            Upper bound on the number of steps placed in a RetrievalPlan.
        ``synthesis_mode`` (str, default ``"union"``):
            Default synthesis mode to embed in SynthesisPlans.
        ``recency_weight`` (float, default ``_RECENCY_WEIGHT``):
            Weight applied to recency signal when computing priority keys.
        ``relevance_weight`` (float, default ``_RELEVANCE_WEIGHT``):
            Weight applied to relevance signal when computing priority keys.
    """

    def __init__(self, config: dict) -> None:
        self._config: dict = config
        self._max_steps: int = int(config.get("max_steps", 16))
        self._synthesis_mode: str = str(
            config.get("synthesis_mode", _DEFAULT_SYNTHESIS_MODE)
        )
        if self._synthesis_mode not in _VALID_SYNTHESIS_MODES:
            log.warning(
                "Unknown synthesis_mode %r; falling back to %r",
                self._synthesis_mode,
                _DEFAULT_SYNTHESIS_MODE,
            )
            self._synthesis_mode = _DEFAULT_SYNTHESIS_MODE
        self._recency_w: float = float(config.get("recency_weight", _RECENCY_WEIGHT))
        self._relevance_w: float = float(
            config.get("relevance_weight", _RELEVANCE_WEIGHT)
        )
        log.debug("TreatyMemoryPlanner initialised with config=%r", config)

    # ------------------------------------------------------------------
    def plan_retrieval(self, query: dict) -> RetrievalPlan:
        """Build a :class:`RetrievalPlan` from a query descriptor.

        Parameters
        ----------
        query:
            A dict that MAY contain the following keys:

            ``keywords`` (list[str]):
                Terms that must appear in retrieved episodes.
            ``episode_ids`` (list[str]):
                Explicit episode IDs to fetch directly.
            ``time_range`` (tuple[float, float]):
                Unix-timestamp ``(start, end)`` range filter.
            ``min_relevance`` (float):
                Minimum relevance score episodes must satisfy.
            ``limit`` (int):
                Maximum number of episodes to return.

        Returns
        -------
        RetrievalPlan
            A frozen plan ready for execution.
        """
        steps: list[dict] = []

        # Direct-ID fetch step — cheapest, always first
        episode_ids = query.get("episode_ids", [])
        if episode_ids:
            steps.append(
                {
                    "type": "direct_fetch",
                    "ids": tuple(episode_ids),
                    "cost": 1.0,
                }
            )

        # Keyword scan step
        keywords = query.get("keywords", [])
        if keywords:
            steps.append(
                {
                    "type": "keyword_scan",
                    "keywords": tuple(keywords),
                    "cost": float(len(keywords)) * 2.5,
                }
            )

        # Time-range filter step
        time_range = query.get("time_range")
        if time_range is not None:
            start, end = float(time_range[0]), float(time_range[1])
            steps.append(
                {
                    "type": "time_range_filter",
                    "start": start,
                    "end": end,
                    "cost": 3.0,
                }
            )

        # Relevance threshold step
        min_rel = query.get("min_relevance")
        if min_rel is not None:
            steps.append(
                {
                    "type": "relevance_filter",
                    "threshold": float(min_rel),
                    "cost": 4.0,
                }
            )

        # Limit step — always append so executor knows when to stop
        limit = int(query.get("limit", 100))
        steps.append({"type": "limit", "n": limit, "cost": 0.0})

        # Clamp to configured maximum
        if len(steps) > self._max_steps:
            log.warning(
                "Plan has %d steps; truncating to %d", len(steps), self._max_steps
            )
            steps = steps[: self._max_steps]

        estimated_cost = sum(s["cost"] for s in steps)

        priority_keys = self._build_priority_keys(query)

        plan_id = f"ret-{_PLAN_VERSION}-{uuid.uuid4().hex[:12]}"
        plan = RetrievalPlan(
            plan_id=plan_id,
            steps=tuple(steps),
            estimated_cost=estimated_cost,
            priority_keys=priority_keys,
        )
        log.debug("Built RetrievalPlan %s (cost=%.2f)", plan_id, estimated_cost)
        return plan

    # ------------------------------------------------------------------
    def plan_synthesis(
        self, episodes: list, candidates: list
    ) -> SynthesisPlan:
        """Build a :class:`SynthesisPlan` from episode and candidate lists.

        Parameters
        ----------
        episodes:
            List of episode dicts or objects that supply the ``id`` field.
        candidates:
            List of candidate-law dicts or objects that supply the ``id`` field.

        Returns
        -------
        SynthesisPlan
            A frozen plan ready for execution.
        """
        source_ids = tuple(_extract_id(ep) for ep in episodes)
        candidate_ids = tuple(_extract_id(c) for c in candidates)

        # Estimate clause count heuristically: each episode contributes a
        # variable number of clauses; we use a log-scaled approximation.
        estimated_clauses = max(
            1,
            int(math.log1p(len(source_ids)) * 8 + len(candidate_ids) * 1.5),
        )

        plan_id = f"syn-{_PLAN_VERSION}-{uuid.uuid4().hex[:12]}"
        plan = SynthesisPlan(
            plan_id=plan_id,
            source_episode_ids=source_ids,
            candidate_ids=candidate_ids,
            synthesis_mode=self._synthesis_mode,
            estimated_clauses=estimated_clauses,
        )
        log.debug(
            "Built SynthesisPlan %s (mode=%s, episodes=%d, candidates=%d)",
            plan_id,
            self._synthesis_mode,
            len(source_ids),
            len(candidate_ids),
        )
        return plan

    # ------------------------------------------------------------------
    def optimize_plan(self, plan: RetrievalPlan) -> RetrievalPlan:
        """Return an optimised variant of *plan* with redundant steps removed.

        Optimisation rules applied (in order):

        1. Deduplicate ``direct_fetch`` steps by merging their ID sets.
        2. Merge consecutive ``keyword_scan`` steps.
        3. Remove ``time_range_filter`` steps whose range is ``(0, +inf)``.
        4. Re-sort steps so that cheaper steps appear first (push-down).
        5. Recompute ``estimated_cost``.

        Parameters
        ----------
        plan:
            The :class:`RetrievalPlan` to optimise.

        Returns
        -------
        RetrievalPlan
            A new frozen plan (plan_id is preserved with an ``-opt`` suffix).
        """
        steps: list[dict] = list(plan.steps)

        # Rule 1: merge direct_fetch
        fetch_ids: list = []
        other_steps: list[dict] = []
        for step in steps:
            if step["type"] == "direct_fetch":
                fetch_ids.extend(step["ids"])
            else:
                other_steps.append(step)
        if fetch_ids:
            merged_fetch: dict = {
                "type": "direct_fetch",
                "ids": tuple(dict.fromkeys(fetch_ids)),  # dedup, preserve order
                "cost": 1.0,
            }
            steps = [merged_fetch] + other_steps
        else:
            steps = other_steps

        # Rule 2: merge keyword_scan steps
        merged_keywords: list[str] = []
        non_kw: list[dict] = []
        for step in steps:
            if step["type"] == "keyword_scan":
                merged_keywords.extend(step["keywords"])
            else:
                non_kw.append(step)
        if merged_keywords:
            deduped_kw = list(dict.fromkeys(merged_keywords))
            non_kw.insert(
                0,
                {
                    "type": "keyword_scan",
                    "keywords": tuple(deduped_kw),
                    "cost": float(len(deduped_kw)) * 2.5,
                },
            )
        steps = non_kw

        # Rule 3: remove trivial time-range filters
        steps = [
            s
            for s in steps
            if not (
                s["type"] == "time_range_filter"
                and s.get("start", 0) == 0
                and s.get("end", math.inf) == math.inf
            )
        ]

        # Rule 4: sort by cost ascending (limit step always last)
        def _step_sort_key(s: dict) -> float:
            return 1e9 if s["type"] == "limit" else s.get("cost", 0.0)

        steps.sort(key=_step_sort_key)

        estimated_cost = sum(s.get("cost", 0.0) for s in steps)
        optimised_id = plan.plan_id + "-opt"
        optimised = RetrievalPlan(
            plan_id=optimised_id,
            steps=tuple(steps),
            estimated_cost=estimated_cost,
            priority_keys=plan.priority_keys,
        )
        log.debug(
            "Optimised plan %s -> %s (cost %.2f -> %.2f)",
            plan.plan_id,
            optimised_id,
            plan.estimated_cost,
            estimated_cost,
        )
        return optimised

    # ------------------------------------------------------------------
    def _build_priority_keys(self, query: dict) -> tuple[str, ...]:
        """Derive an ordered tuple of sort-key names from *query*.

        Internal helper — not part of the public API.
        """
        keys: list[str] = []
        if self._recency_w >= self._relevance_w:
            keys.extend(["recency", "relevance"])
        else:
            keys.extend(["relevance", "recency"])
        if query.get("keywords"):
            keys.append("keyword_match_count")
        return tuple(keys)


# ─── Section 3: Executor ─────────────────────────────────────────────────────


class TreatyMemoryExecutor:
    """Executes :class:`RetrievalPlan` and :class:`SynthesisPlan` objects
    against an in-memory index.

    The *index* argument passed to each execute method is expected to be a
    dict-like object whose values are episode dicts. The executor is
    intentionally stateless between calls so that multiple executors can run
    concurrently against the same (read-only) index without locks.
    """

    # ------------------------------------------------------------------
    def execute_retrieval(self, plan: RetrievalPlan, index: Any) -> list:
        """Execute *plan* against *index* and return a ranked list of episodes.

        Parameters
        ----------
        plan:
            The :class:`RetrievalPlan` produced by :class:`TreatyMemoryPlanner`.
        index:
            A mapping of ``{episode_id: episode_dict}``.

        Returns
        -------
        list
            List of episode dicts ranked according to ``plan.priority_keys``.
        """
        if index is None:
            log.warning("execute_retrieval: index is None; returning empty list")
            return []

        results: dict[str, dict] = dict(index)  # shallow copy for safety

        for step in plan.steps:
            step_type = step.get("type")
            if step_type == "direct_fetch":
                ids = set(step["ids"])
                results = {k: v for k, v in results.items() if k in ids}

            elif step_type == "keyword_scan":
                keywords = [kw.lower() for kw in step["keywords"]]
                filtered: dict[str, dict] = {}
                for eid, ep in results.items():
                    text = _episode_text(ep).lower()
                    if any(kw in text for kw in keywords):
                        filtered[eid] = ep
                results = filtered

            elif step_type == "time_range_filter":
                start, end = step["start"], step["end"]
                results = {
                    eid: ep
                    for eid, ep in results.items()
                    if start <= float(ep.get("timestamp", 0)) <= end
                }

            elif step_type == "relevance_filter":
                threshold = step["threshold"]
                results = {
                    eid: ep
                    for eid, ep in results.items()
                    if float(ep.get("relevance", 0.0)) >= threshold
                }

            elif step_type == "limit":
                n = step["n"]
                items = list(results.items())[:n]
                results = dict(items)

        episodes = list(results.values())
        episodes = _rank_episodes(episodes, plan.priority_keys)
        log.debug(
            "execute_retrieval plan=%s returned %d episodes",
            plan.plan_id,
            len(episodes),
        )
        return episodes

    # ------------------------------------------------------------------
    def execute_synthesis(
        self, plan: SynthesisPlan, index: Any, candidates: list
    ) -> dict:
        """Execute *plan* against *index* and a list of candidate-law dicts.

        Parameters
        ----------
        plan:
            The :class:`SynthesisPlan` to execute.
        index:
            Mapping of ``{episode_id: episode_dict}``.
        candidates:
            List of candidate-law dicts. Each dict should have at minimum an
            ``id`` key and a ``clauses`` key (list of strings).

        Returns
        -------
        dict
            A synthesised treaty dict with keys:
            ``synthesis_id``, ``mode``, ``clauses``, ``source_episode_ids``,
            ``candidate_ids``, ``created_at``, ``clause_count``.
        """
        if index is None:
            log.warning("execute_synthesis: index is None; using empty episode set")
            episodes: list[dict] = []
        else:
            episodes = [
                index[eid]
                for eid in plan.source_episode_ids
                if eid in index
            ]

        cand_map = {_extract_id(c): c for c in candidates}
        selected_candidates = [
            cand_map[cid] for cid in plan.candidate_ids if cid in cand_map
        ]

        mode = plan.synthesis_mode
        if mode == "union":
            clauses = _synthesis_union(episodes, selected_candidates)
        elif mode == "intersection":
            clauses = _synthesis_intersection(episodes, selected_candidates)
        elif mode == "weighted":
            clauses = _synthesis_weighted(episodes, selected_candidates)
        elif mode == "sequential":
            clauses = _synthesis_sequential(episodes, selected_candidates)
        else:  # adversarial or unknown
            clauses = _synthesis_adversarial(episodes, selected_candidates)

        result = {
            "synthesis_id": f"synth-{uuid.uuid4().hex[:16]}",
            "mode": mode,
            "clauses": clauses,
            "source_episode_ids": list(plan.source_episode_ids),
            "candidate_ids": list(plan.candidate_ids),
            "created_at": time.time(),
            "clause_count": len(clauses),
        }
        log.debug(
            "execute_synthesis plan=%s produced %d clauses",
            plan.plan_id,
            len(clauses),
        )
        return result

    # ------------------------------------------------------------------
    def batch_execute(self, plans: list, index: Any) -> list[dict]:
        """Execute a mixed list of plans in sequence and return results.

        Parameters
        ----------
        plans:
            A list containing any mix of :class:`RetrievalPlan` and
            :class:`SynthesisPlan` objects. Unknown plan types are skipped
            with a warning.
        index:
            The index to execute each plan against.

        Returns
        -------
        list[dict]
            One result dict per plan. RetrievalPlan results are wrapped as
            ``{"plan_id": ..., "type": "retrieval", "episodes": [...]}``.
            SynthesisPlan results are returned as-is with an additional
            ``"type": "synthesis"`` key.
        """
        if len(plans) > _EXECUTOR_BATCH_LIMIT:
            log.warning(
                "batch_execute: %d plans exceed limit %d; truncating",
                len(plans),
                _EXECUTOR_BATCH_LIMIT,
            )
            plans = plans[:_EXECUTOR_BATCH_LIMIT]

        results: list[dict] = []
        for plan in plans:
            if isinstance(plan, RetrievalPlan):
                episodes = self.execute_retrieval(plan, index)
                results.append(
                    {
                        "plan_id": plan.plan_id,
                        "type": "retrieval",
                        "episodes": episodes,
                    }
                )
            elif isinstance(plan, SynthesisPlan):
                synthesis = self.execute_synthesis(plan, index, [])
                synthesis["type"] = "synthesis"
                results.append(synthesis)
            else:
                log.warning("batch_execute: unknown plan type %r; skipping", type(plan))
        return results


# ─── Section 4: Normalizer ───────────────────────────────────────────────────


class TreatyMemoryNormalizer:
    """Canonicalises and deduplicates episode and candidate-law dicts before
    they are inserted into the treaty memory index.

    The normaliser is stateless — all methods are pure transformations.
    """

    # Canonical field names for episodes after normalisation.
    _EPISODE_REQUIRED_FIELDS: tuple[str, ...] = (
        "id",
        "clauses",
        "timestamp",
        "relevance",
        "source",
        "tags",
    )

    # Canonical field names for candidate laws.
    _CANDIDATE_REQUIRED_FIELDS: tuple[str, ...] = (
        "id",
        "clauses",
        "confidence",
        "origin_episode_id",
        "status",
    )

    # ------------------------------------------------------------------
    def normalize_episode(self, episode_dict: dict) -> dict:
        """Return a normalised copy of *episode_dict*.

        Missing required fields are filled with sensible defaults. Extra
        fields are preserved unchanged.

        Parameters
        ----------
        episode_dict:
            Raw episode dict, possibly from an external source.

        Returns
        -------
        dict
            Normalised episode dict with all required fields present.
        """
        out = dict(episode_dict)
        out.setdefault("id", uuid.uuid4().hex)
        out.setdefault("clauses", [])
        out.setdefault("timestamp", time.time())
        out.setdefault("relevance", 0.5)
        out.setdefault("source", "unknown")
        out.setdefault("tags", [])

        # Coerce types
        out["id"] = str(out["id"])
        out["clauses"] = [str(c) for c in out["clauses"]]
        out["timestamp"] = float(out["timestamp"])
        out["relevance"] = max(0.0, min(1.0, float(out["relevance"])))
        out["tags"] = [str(t) for t in out["tags"]]
        return out

    # ------------------------------------------------------------------
    def normalize_candidate(self, candidate_dict: dict) -> dict:
        """Return a normalised copy of *candidate_dict*.

        Parameters
        ----------
        candidate_dict:
            Raw candidate-law dict.

        Returns
        -------
        dict
            Normalised candidate dict with all required fields present.
        """
        out = dict(candidate_dict)
        out.setdefault("id", uuid.uuid4().hex)
        out.setdefault("clauses", [])
        out.setdefault("confidence", 0.5)
        out.setdefault("origin_episode_id", "")
        out.setdefault("status", "pending")

        out["id"] = str(out["id"])
        out["clauses"] = [str(c) for c in out["clauses"]]
        out["confidence"] = max(0.0, min(1.0, float(out["confidence"])))
        out["origin_episode_id"] = str(out["origin_episode_id"])
        out["status"] = str(out["status"])
        return out

    # ------------------------------------------------------------------
    def canonical_pattern(self, pattern: str) -> str:
        """Return a canonical form of *pattern* for index comparison.

        Transformations applied:

        * Strip leading/trailing whitespace.
        * Collapse internal whitespace runs to a single space.
        * Lower-case.
        * Remove punctuation characters ``!?,;:`` at clause boundaries.

        Parameters
        ----------
        pattern:
            Raw pattern string.

        Returns
        -------
        str
            Canonicalised pattern string.
        """
        import re

        p = pattern.strip().lower()
        p = re.sub(r"\s+", " ", p)
        p = re.sub(r"[!?,;:]+$", "", p)
        return p

    # ------------------------------------------------------------------
    def dedup_episodes(self, episodes: list) -> list:
        """Remove near-duplicate episodes from *episodes* using Jaccard similarity.

        Two episodes are considered duplicates if their clause sets have a
        Jaccard similarity >= ``_DEDUP_JACCARD_THRESHOLD``. When a pair is
        detected, the episode with the lower ``relevance`` score is dropped.

        Parameters
        ----------
        episodes:
            List of normalised episode dicts.

        Returns
        -------
        list
            Deduplicated list preserving the relative order of survivors.
        """
        survivors: list[dict] = []
        for ep in episodes:
            is_dup = False
            for survivor in survivors:
                sim = treaty_jaccard(ep.get("clauses", []), survivor.get("clauses", []))
                if sim >= _DEDUP_JACCARD_THRESHOLD:
                    # Keep the one with higher relevance
                    if float(ep.get("relevance", 0)) > float(
                        survivor.get("relevance", 0)
                    ):
                        survivors.remove(survivor)
                        survivors.append(ep)
                    is_dup = True
                    break
            if not is_dup:
                survivors.append(ep)
        log.debug(
            "dedup_episodes: %d -> %d episodes", len(episodes), len(survivors)
        )
        return survivors


# ─── Section 5: Standalone algorithm functions ───────────────────────────────


def treaty_jaccard(a_clauses: list, b_clauses: list) -> float:
    """Compute the Jaccard similarity between two clause lists.

    Jaccard similarity is defined as::

        |A ∩ B| / |A ∪ B|

    where *A* and *B* are treated as sets of canonicalised clause strings.

    Parameters
    ----------
    a_clauses:
        Clauses from the first treaty/episode.
    b_clauses:
        Clauses from the second treaty/episode.

    Returns
    -------
    float
        Similarity in ``[0.0, 1.0]``. Returns ``1.0`` when both lists are
        empty (vacuous equality) and ``0.0`` when the union is empty but at
        least one list is non-empty (should not normally occur).

    Examples
    --------
    >>> treaty_jaccard(["a", "b", "c"], ["b", "c", "d"])
    0.5
    >>> treaty_jaccard([], [])
    1.0
    """
    set_a = {str(c).strip().lower() for c in a_clauses}
    set_b = {str(c).strip().lower() for c in b_clauses}
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 0.0
    intersection = set_a & set_b
    return len(intersection) / len(union)


def episode_cluster_kmeans(
    episodes: list, k: int, iterations: int = 20
) -> list[list]:
    """Cluster *episodes* into *k* groups using a pure-Python k-means variant.

    Each episode is represented as a bag-of-words vector derived from its
    clauses. Distances between vectors are measured by cosine similarity
    (converted to a distance by ``1 - similarity``). Centroids are represented
    as averaged feature dictionaries.

    This implementation is intentionally dependency-free (no NumPy / sklearn).

    Parameters
    ----------
    episodes:
        List of episode dicts. Each dict must have a ``clauses`` key.
    k:
        Number of clusters. Clamped to ``[1, len(episodes)]``.
    iterations:
        Maximum number of k-means iterations.

    Returns
    -------
    list[list]
        A list of *k* lists, each containing the episode dicts assigned to
        that cluster.

    Notes
    -----
    Initialisation uses a deterministic seed derived from
    ``_KMEANS_SEED_PHRASE`` so results are reproducible for the same input.
    """
    if not episodes:
        return []
    k = max(1, min(k, len(episodes)))
    if k == 1:
        return [list(episodes)]

    # Build vocabulary and vectors
    vocab: dict[str, int] = {}
    vectors: list[dict[str, float]] = []
    for ep in episodes:
        vec: dict[str, float] = collections.Counter()
        for clause in ep.get("clauses", []):
            for token in clause.lower().split():
                if token not in vocab:
                    vocab[token] = len(vocab)
                vec[token] += 1.0
        vectors.append(dict(vec))

    # Deterministic initialisation: pick k seeds using stable hash order
    sorted_eps = sorted(
        range(len(episodes)),
        key=lambda i: stable_hash(episodes[i].get("id", str(i))),
    )
    centroid_indices = sorted_eps[:k]
    centroids: list[dict[str, float]] = [
        dict(vectors[i]) for i in centroid_indices
    ]

    assignments: list[int] = [0] * len(episodes)

    for _iter in range(iterations):
        # Assignment step
        changed = False
        for idx, vec in enumerate(vectors):
            best_cluster = 0
            best_sim = -1.0
            for ci, centroid in enumerate(centroids):
                sim = _cosine_sim(vec, centroid)
                if sim > best_sim:
                    best_sim = sim
                    best_cluster = ci
            if assignments[idx] != best_cluster:
                changed = True
                assignments[idx] = best_cluster

        if not changed:
            log.debug("k-means converged after %d iterations", _iter + 1)
            break

        # Update step
        new_centroids: list[dict[str, float]] = [{} for _ in range(k)]
        counts = [0] * k
        for idx, vec in enumerate(vectors):
            ci = assignments[idx]
            counts[ci] += 1
            for token, weight in vec.items():
                new_centroids[ci][token] = new_centroids[ci].get(token, 0.0) + weight

        # Average
        for ci in range(k):
            if counts[ci] > 0:
                for token in new_centroids[ci]:
                    new_centroids[ci][token] /= counts[ci]
            else:
                # Empty cluster: re-seed from the episode furthest from all
                # current centroids (greedy re-seeding)
                worst_idx = max(
                    range(len(vectors)),
                    key=lambda i: min(
                        1.0 - _cosine_sim(vectors[i], c) for c in centroids
                    ),
                )
                new_centroids[ci] = dict(vectors[worst_idx])

        centroids = new_centroids

    # Group episodes by final assignment
    clusters: list[list] = [[] for _ in range(k)]
    for idx, ep in enumerate(episodes):
        clusters[assignments[idx]].append(ep)
    return clusters


def law_candidate_refinement(candidate: dict, new_episodes: list) -> dict:
    """Refine a candidate law by incorporating evidence from *new_episodes*.

    Refinement applies the following rules:

    1. Any clause present in MORE than half the new episodes is added to the
       candidate's clause set if not already present.
    2. Any clause present in the candidate but ABSENT from ALL new episodes
       has its confidence contribution reduced (manifested by lowering the
       overall confidence slightly).
    3. The candidate's ``confidence`` is updated as a weighted average of its
       prior confidence and the mean relevance of the new episodes.
    4. ``status`` is set to ``"refined"`` and ``last_refined_at`` is
       stamped with the current UNIX timestamp.

    Parameters
    ----------
    candidate:
        A normalised candidate-law dict.
    new_episodes:
        List of episode dicts from which to draw refinement evidence.

    Returns
    -------
    dict
        A new candidate dict (shallow copy with modifications applied).
    """
    out = dict(candidate)
    if not new_episodes:
        return out

    existing_clauses = set(out.get("clauses", []))
    clause_counts: collections.Counter = collections.Counter()
    total_relevance = 0.0

    for ep in new_episodes:
        total_relevance += float(ep.get("relevance", 0.5))
        for clause in ep.get("clauses", []):
            clause_counts[clause] += 1

    threshold = len(new_episodes) / 2.0
    mean_relevance = total_relevance / len(new_episodes)

    # Add highly-supported new clauses
    new_clauses = list(existing_clauses)
    for clause, count in clause_counts.items():
        if count > threshold and clause not in existing_clauses:
            new_clauses.append(clause)

    # Penalise unsupported existing clauses
    unsupported = existing_clauses - set(clause_counts.keys())
    penalty = 0.02 * len(unsupported)

    prior_confidence = float(out.get("confidence", 0.5))
    new_confidence = max(
        0.0, min(1.0, 0.6 * prior_confidence + 0.4 * mean_relevance - penalty)
    )

    out["clauses"] = new_clauses
    out["confidence"] = new_confidence
    out["status"] = "refined"
    out["last_refined_at"] = time.time()
    return out


def memory_compression_lru(episodes: list, max_size: int) -> list:
    """Compress *episodes* to at most *max_size* entries using an LRU policy.

    Episodes are sorted by ``last_accessed_at`` (falling back to
    ``timestamp``) in descending order (most-recently-used first) and the
    tail beyond *max_size* is discarded.

    Parameters
    ----------
    episodes:
        List of episode dicts. Each dict is expected to have either a
        ``last_accessed_at`` or ``timestamp`` float field.
    max_size:
        Maximum number of episodes to retain. Must be >= 1.

    Returns
    -------
    list
        Retained episodes sorted by recency (most recent first).
    """
    max_size = max(1, max_size)
    if len(episodes) <= max_size:
        return list(episodes)

    def _access_time(ep: dict) -> float:
        return float(ep.get("last_accessed_at", ep.get("timestamp", 0.0)))

    sorted_eps = sorted(episodes, key=_access_time, reverse=True)
    retained = sorted_eps[:max_size]
    log.debug(
        "memory_compression_lru: %d -> %d episodes (max_size=%d)",
        len(episodes),
        len(retained),
        max_size,
    )
    return retained


def semantic_index_build(entries: list) -> dict:
    """Build a lightweight inverted index from a list of episode or candidate
    dicts suitable for keyword-based retrieval.

    The index maps each unique normalised token to the set of entry IDs that
    contain it in their clauses or tags.

    Parameters
    ----------
    entries:
        List of dicts. Each dict should have an ``id`` key and at least one
        of ``clauses`` (list[str]) or ``tags`` (list[str]).

    Returns
    -------
    dict
        A dict of the form ``{token: set_of_entry_ids}``.

    Notes
    -----
    The returned index is NOT a :class:`TreatyIndex` instance — it is a plain
    Python dict intended for lightweight in-memory lookup.
    """
    index: dict[str, set] = collections.defaultdict(set)
    for entry in entries:
        eid = str(entry.get("id", uuid.uuid4().hex))
        tokens: list[str] = []
        for clause in entry.get("clauses", []):
            tokens.extend(clause.lower().split())
        for tag in entry.get("tags", []):
            tokens.extend(tag.lower().split())
        for token in tokens:
            token = token.strip(".,!?;:")
            if token:
                index[token].add(eid)
    # Convert sets to frozensets so the index values are hashable
    return {token: frozenset(ids) for token, ids in index.items()}


def treaty_diff(treaty_a: dict, treaty_b: dict) -> dict:
    """Compute a symmetric diff between two treaty dicts.

    Compares the ``clauses`` lists and top-level scalar fields of both
    treaties, returning a diff summary.

    Parameters
    ----------
    treaty_a:
        First treaty dict.
    treaty_b:
        Second treaty dict.

    Returns
    -------
    dict
        A diff dict with the following keys:

        ``added_clauses``: clauses in *b* but not *a*.
        ``removed_clauses``: clauses in *a* but not *b*.
        ``common_clauses``: clauses shared by both.
        ``jaccard``: Jaccard similarity of the clause sets.
        ``field_diffs``: dict of scalar fields that differ.
        ``identical``: bool — True iff the treaties are identical.
    """
    clauses_a = set(str(c) for c in treaty_a.get("clauses", []))
    clauses_b = set(str(c) for c in treaty_b.get("clauses", []))

    added = sorted(clauses_b - clauses_a)
    removed = sorted(clauses_a - clauses_b)
    common = sorted(clauses_a & clauses_b)
    jaccard = treaty_jaccard(list(clauses_a), list(clauses_b))

    # Scalar field comparison (exclude clauses key)
    scalar_keys = (
        set(treaty_a.keys()) | set(treaty_b.keys())
    ) - {"clauses"}
    field_diffs: dict[str, dict] = {}
    for key in scalar_keys:
        val_a = treaty_a.get(key)
        val_b = treaty_b.get(key)
        if val_a != val_b:
            field_diffs[key] = {"a": val_a, "b": val_b}

    identical = not added and not removed and not field_diffs

    return {
        "added_clauses": added,
        "removed_clauses": removed,
        "common_clauses": common,
        "jaccard": jaccard,
        "field_diffs": field_diffs,
        "identical": identical,
    }


def convergence_score(episode_history: list) -> float:
    """Compute a convergence score over a temporal sequence of episodes.

    The score measures how consistently the clause content of successive
    episodes agrees with one another. A score near ``1.0`` means the episode
    sequence has converged to a stable set of clauses; a score near ``0.0``
    means the content is still highly volatile.

    Algorithm
    ---------
    1. Compute pairwise Jaccard similarities between consecutive episode pairs.
    2. Return the mean of those similarities.
    3. If fewer than 2 episodes are provided, return ``0.0`` (undefined).

    Parameters
    ----------
    episode_history:
        Ordered list of episode dicts (oldest first).

    Returns
    -------
    float
        Convergence score in ``[0.0, 1.0]``.
    """
    if len(episode_history) < 2:
        return 0.0

    sims: list[float] = []
    for i in range(len(episode_history) - 1):
        ep_a = episode_history[i]
        ep_b = episode_history[i + 1]
        sim = treaty_jaccard(
            ep_a.get("clauses", []), ep_b.get("clauses", [])
        )
        sims.append(sim)

    score = statistics.mean(sims)
    if score < _CONVERGENCE_WARNING_THRESHOLD:
        log.warning(
            "convergence_score=%.3f below warning threshold %.3f — "
            "episode sequence may be diverging",
            score,
            _CONVERGENCE_WARNING_THRESHOLD,
        )
    return score


def friction_signature(episode: dict) -> str:
    """Compute a short, stable friction signature for *episode*.

    The friction signature captures the essential "shape" of an episode's
    conflict structure. It is derived from a SHA-256 hash of the sorted,
    normalised clause set combined with the episode's ``source`` field.

    The signature is truncated to ``_FRICTION_SIG_LENGTH`` hex characters so
    it can be used as a short key in logs and UIs without being unwieldy.

    Parameters
    ----------
    episode:
        An episode dict with at least ``clauses`` and ``source`` fields.

    Returns
    -------
    str
        A hex string of length ``_FRICTION_SIG_LENGTH`` that uniquely
        identifies the episode's clause-level friction profile.
    """
    clauses = sorted(str(c).strip().lower() for c in episode.get("clauses", []))
    source = str(episode.get("source", "")).strip().lower()
    payload = "|".join(clauses) + "||" + source
    full_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return full_hash[:_FRICTION_SIG_LENGTH]


# ─── Section 6: Internal helper functions ────────────────────────────────────


def _extract_id(obj: Any) -> str:
    """Extract a string ID from an object that may be a dict or an ORM-like
    object with an ``id`` attribute.

    Parameters
    ----------
    obj:
        Dict or object.

    Returns
    -------
    str
        The ID as a string.  Falls back to ``repr(obj)`` if no ID can be
        found, ensuring the function never raises.
    """
    if isinstance(obj, dict):
        return str(obj.get("id", repr(obj)))
    try:
        return str(obj.id)
    except AttributeError:
        return str(repr(obj))


def _episode_text(episode: dict) -> str:
    """Concatenate all clauses and tags of *episode* into a single searchable
    string for keyword scanning.

    Parameters
    ----------
    episode:
        An episode dict.

    Returns
    -------
    str
        Space-separated text representation.
    """
    parts: list[str] = []
    parts.extend(str(c) for c in episode.get("clauses", []))
    parts.extend(str(t) for t in episode.get("tags", []))
    parts.append(str(episode.get("source", "")))
    return " ".join(parts)


def _rank_episodes(episodes: list, priority_keys: tuple[str, ...]) -> list:
    """Sort *episodes* according to *priority_keys*.

    Each key is tried in order from highest to lowest priority. Sorting is
    stable so episodes equal on all keys preserve their original order.

    Parameters
    ----------
    episodes:
        List of episode dicts.
    priority_keys:
        Ordered tuple of field names to sort by (descending for all).

    Returns
    -------
    list
        Sorted list of episode dicts.
    """
    for key in reversed(priority_keys):
        episodes = sorted(
            episodes,
            key=lambda ep: float(ep.get(key, 0.0)),
            reverse=True,
        )
    return episodes


def _cosine_sim(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Compute cosine similarity between two sparse bag-of-words dicts.

    Parameters
    ----------
    vec_a:
        First sparse vector as ``{token: weight}``.
    vec_b:
        Second sparse vector as ``{token: weight}``.

    Returns
    -------
    float
        Cosine similarity in ``[-1.0, 1.0]``.  Returns ``0.0`` when either
        vector has zero magnitude.
    """
    if not vec_a or not vec_b:
        return 0.0

    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    dot = sum(vec_a[k] * vec_b[k] for k in common_keys)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _synthesis_union(episodes: list[dict], candidates: list[dict]) -> list[str]:
    """Return the union of all clauses from *episodes* and *candidates*,
    preserving insertion order and removing exact duplicates.
    """
    seen: set[str] = set()
    clauses: list[str] = []
    for source in [*episodes, *candidates]:
        for clause in source.get("clauses", []):
            norm = str(clause).strip()
            if norm and norm not in seen:
                seen.add(norm)
                clauses.append(norm)
    return clauses


def _synthesis_intersection(
    episodes: list[dict], candidates: list[dict]
) -> list[str]:
    """Return only clauses present in ALL sources (episodes + candidates).

    If any source list is empty, returns an empty list.
    """
    all_sources = [*episodes, *candidates]
    if not all_sources:
        return []
    clause_sets = [set(str(c).strip() for c in src.get("clauses", [])) for src in all_sources]
    common = clause_sets[0]
    for s in clause_sets[1:]:
        common &= s
    return sorted(common)


def _synthesis_weighted(episodes: list[dict], candidates: list[dict]) -> list[str]:
    """Return clauses weighted by their frequency across all sources.

    A clause is included only if it appears in at least half of all sources
    combined.
    """
    all_sources = [*episodes, *candidates]
    if not all_sources:
        return []
    counter: collections.Counter = collections.Counter()
    for src in all_sources:
        for clause in set(str(c).strip() for c in src.get("clauses", [])):
            if clause:
                counter[clause] += 1
    threshold = len(all_sources) / 2.0
    return [clause for clause, count in counter.most_common() if count >= threshold]


def _synthesis_sequential(episodes: list[dict], candidates: list[dict]) -> list[str]:
    """Return clauses in the order they appear across episodes then candidates,
    including ALL clauses (no deduplication).
    """
    clauses: list[str] = []
    for src in [*episodes, *candidates]:
        clauses.extend(str(c).strip() for c in src.get("clauses", []) if str(c).strip())
    return clauses


def _synthesis_adversarial(
    episodes: list[dict], candidates: list[dict]
) -> list[str]:
    """Return clauses that are UNIQUE to either episodes or candidates but not
    shared — a symmetric difference of the clause sets. Used to surface
    contested or disputed clauses for adversarial review.
    """
    ep_clauses: set[str] = set()
    cand_clauses: set[str] = set()
    for ep in episodes:
        ep_clauses.update(str(c).strip() for c in ep.get("clauses", []))
    for cand in candidates:
        cand_clauses.update(str(c).strip() for c in cand.get("clauses", []))
    ep_clauses.discard("")
    cand_clauses.discard("")
    return sorted((ep_clauses | cand_clauses) - (ep_clauses & cand_clauses))


def _format_plan_summary(plan: RetrievalPlan | SynthesisPlan) -> str:
    """Return a short human-readable summary of *plan* for logging.

    Parameters
    ----------
    plan:
        Either a :class:`RetrievalPlan` or a :class:`SynthesisPlan`.

    Returns
    -------
    str
        A single-line summary string.
    """
    if isinstance(plan, RetrievalPlan):
        return (
            f"RetrievalPlan(id={plan.plan_id}, "
            f"steps={len(plan.steps)}, "
            f"cost={plan.estimated_cost:.2f})"
        )
    if isinstance(plan, SynthesisPlan):
        return (
            f"SynthesisPlan(id={plan.plan_id}, "
            f"mode={plan.synthesis_mode}, "
            f"episodes={len(plan.source_episode_ids)}, "
            f"candidates={len(plan.candidate_ids)})"
        )
    return f"UnknownPlan({type(plan).__name__})"


def _validate_episode(episode: dict) -> list[str]:
    """Validate an episode dict and return a list of warning strings.

    Parameters
    ----------
    episode:
        The episode dict to validate.

    Returns
    -------
    list[str]
        A (possibly empty) list of human-readable warning messages.
    """
    warnings: list[str] = []
    if "id" not in episode:
        warnings.append("missing 'id' field")
    if "clauses" not in episode:
        warnings.append("missing 'clauses' field")
    elif not isinstance(episode["clauses"], list):
        warnings.append("'clauses' should be a list")
    relevance = episode.get("relevance")
    if relevance is not None:
        try:
            r = float(relevance)
            if not (0.0 <= r <= 1.0):
                warnings.append(f"'relevance' out of range: {r}")
        except (TypeError, ValueError):
            warnings.append(f"'relevance' is not numeric: {relevance!r}")
    return warnings


def _token_frequency_vector(text: str) -> dict[str, float]:
    """Build a normalised term-frequency vector from *text*.

    Parameters
    ----------
    text:
        Raw text string.

    Returns
    -------
    dict[str, float]
        Mapping of token to normalised frequency (TF).
    """
    tokens = [t.lower().strip(".,!?;:") for t in text.split() if t.strip()]
    if not tokens:
        return {}
    counter: collections.Counter = collections.Counter(tokens)
    total = sum(counter.values())
    return {token: count / total for token, count in counter.items()}


def _plan_cost_estimate(steps: list[dict]) -> float:
    """Sum the cost annotations of all steps in *steps*.

    Parameters
    ----------
    steps:
        List of step dicts each containing an optional ``cost`` float field.

    Returns
    -------
    float
        Total estimated cost.
    """
    return sum(float(s.get("cost", 0.0)) for s in steps)


def _episode_age_seconds(episode: dict, reference_time: float | None = None) -> float:
    """Return the age of *episode* in seconds relative to *reference_time*.

    Parameters
    ----------
    episode:
        Episode dict with an optional ``timestamp`` field (UNIX epoch).
    reference_time:
        Reference UNIX timestamp. Defaults to ``time.time()``.

    Returns
    -------
    float
        Age in seconds. Returns 0.0 if the episode has no ``timestamp``.
    """
    ts = float(episode.get("timestamp", 0.0))
    if ts == 0.0:
        return 0.0
    ref = reference_time if reference_time is not None else time.time()
    return max(0.0, ref - ts)


# ─── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    print(f"[smoke] {__file__}")

    # --- RetrievalPlan / SynthesisPlan construction ---
    planner = TreatyMemoryPlanner(
        config={"max_steps": 8, "synthesis_mode": "union"}
    )
    r_plan = planner.plan_retrieval(
        {
            "keywords": ["territory", "sovereignty"],
            "time_range": (0.0, time.time()),
            "min_relevance": 0.3,
            "limit": 50,
        }
    )
    assert isinstance(r_plan, RetrievalPlan), "Expected RetrievalPlan"
    assert r_plan.estimated_cost > 0.0, "Expected non-zero cost"

    opt_plan = planner.optimize_plan(r_plan)
    assert isinstance(opt_plan, RetrievalPlan), "Expected optimised RetrievalPlan"

    s_plan = planner.plan_synthesis(
        episodes=[{"id": "ep1"}, {"id": "ep2"}],
        candidates=[{"id": "c1"}],
    )
    assert isinstance(s_plan, SynthesisPlan), "Expected SynthesisPlan"
    assert s_plan.synthesis_mode == "union"

    # --- Executor ---
    executor = TreatyMemoryExecutor()
    fake_index = {
        "ep1": {
            "id": "ep1",
            "clauses": ["territory clause", "sovereignty clause"],
            "timestamp": time.time() - 10,
            "relevance": 0.8,
            "source": "test",
            "tags": [],
        },
        "ep2": {
            "id": "ep2",
            "clauses": ["border clause"],
            "timestamp": time.time() - 5,
            "relevance": 0.4,
            "source": "test",
            "tags": ["geo"],
        },
    }
    retrieved = executor.execute_retrieval(r_plan, fake_index)
    assert isinstance(retrieved, list), "Expected list"

    synth = executor.execute_synthesis(s_plan, fake_index, [])
    assert "clauses" in synth, "Expected 'clauses' in synthesis result"

    batch = executor.batch_execute([r_plan, s_plan], fake_index)
    assert len(batch) == 2, f"Expected 2 batch results, got {len(batch)}"

    # --- Normaliser ---
    norm = TreatyMemoryNormalizer()
    ep_norm = norm.normalize_episode({"clauses": ["foo", "bar"]})
    assert "id" in ep_norm and "timestamp" in ep_norm
    cand_norm = norm.normalize_candidate({"clauses": ["law1"]})
    assert cand_norm["status"] == "pending"

    pat = norm.canonical_pattern("  Hello World!   ")
    assert pat == "hello world", f"Unexpected pattern: {pat!r}"

    deduped = norm.dedup_episodes(
        [
            {"id": "a", "clauses": ["x", "y"], "relevance": 0.6},
            {"id": "b", "clauses": ["x", "y"], "relevance": 0.4},
            {"id": "c", "clauses": ["z"], "relevance": 0.5},
        ]
    )
    assert len(deduped) == 2, f"Expected 2 after dedup, got {len(deduped)}"

    # --- Standalone functions ---
    j = treaty_jaccard(["a", "b", "c"], ["b", "c", "d"])
    assert abs(j - 0.5) < 1e-9, f"Jaccard mismatch: {j}"
    assert treaty_jaccard([], []) == 1.0

    episodes_for_cluster = [
        {"id": str(i), "clauses": [f"clause {i}", "common"]}
        for i in range(9)
    ]
    clusters = episode_cluster_kmeans(episodes_for_cluster, k=3, iterations=10)
    assert len(clusters) == 3, f"Expected 3 clusters, got {len(clusters)}"
    assert sum(len(c) for c in clusters) == 9, "Cluster sizes don't sum to input"

    refined = law_candidate_refinement(
        {"id": "c1", "clauses": ["shared"], "confidence": 0.6},
        [{"clauses": ["shared", "new"], "relevance": 0.8}],
    )
    assert refined["status"] == "refined"

    compressed = memory_compression_lru(
        [{"id": str(i), "timestamp": float(i)} for i in range(20)],
        max_size=5,
    )
    assert len(compressed) == 5

    idx = semantic_index_build(
        [{"id": "e1", "clauses": ["alpha beta"], "tags": ["gamma"]}]
    )
    assert "alpha" in idx
    assert "e1" in idx["alpha"]

    diff = treaty_diff(
        {"clauses": ["a", "b"], "mode": "union"},
        {"clauses": ["b", "c"], "mode": "intersection"},
    )
    assert "a" in diff["removed_clauses"]
    assert "c" in diff["added_clauses"]
    assert not diff["identical"]

    history = [
        {"clauses": ["a", "b", "c"]},
        {"clauses": ["a", "b", "d"]},
        {"clauses": ["a", "b", "d"]},
    ]
    score = convergence_score(history)
    assert 0.0 <= score <= 1.0, f"Unexpected convergence score: {score}"

    sig = friction_signature({"clauses": ["x", "y"], "source": "test"})
    assert len(sig) == _FRICTION_SIG_LENGTH, f"Unexpected sig length: {len(sig)}"

    print("[smoke] PASS")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Cross-subsystem integration: geometry, solver, judgments, encodings
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.site import Site, SiteDiagnostics
except Exception:
    Site = None  # type: ignore[assignment,misc]
    SiteDiagnostics = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.z3_session import Z3Session as _Z3
except Exception:
    _Z3 = None  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.sections import SectionComparator as _SectionComp
except Exception:
    _SectionComp = None  # type: ignore[assignment,misc]

try:
    from jugeo.encodings import encode_judgment as _enc_judgment
except Exception:
    _enc_judgment = None  # type: ignore[assignment]


def treaty_site_topology(treaty, site):
    """Analyse a treaty's scope against site topology from jugeo.geometry.site.

    Determines which coordinates of the site are affected by the treaty's
    clauses, enabling scope-aware archival.
    """
    clauses = getattr(treaty, "clauses", [])
    coords = getattr(site, "coordinates", [])
    affected = [c for c in coords if any(
        getattr(c, "id", str(c)) in str(cl) for cl in clauses
    )]
    return {
        "affected_coordinates": len(affected),
        "total_coordinates": len(coords),
        "subsystem": "jugeo.geometry.site",
    }


def solver_verify_treaty(treaty):
    """Verify treaty consistency via Z3 (jugeo.solver.z3_session).

    Ensures the treaty's clauses are jointly satisfiable; an
    unsatisfiable treaty indicates contradictory obligations.
    """
    if _Z3 is None:
        return {"consistent": None, "reason": "Z3Session unavailable",
                "subsystem": "jugeo.solver.z3_session"}
    try:
        session = _Z3()
        for clause in getattr(treaty, "clauses", []):
            session.add(clause)
        outcome = session.check()
        return {"consistent": getattr(outcome, "satisfiable", False),
                "subsystem": "jugeo.solver.z3_session"}
    except Exception as exc:
        return {"consistent": None, "reason": str(exc),
                "subsystem": "jugeo.solver.z3_session"}


def treaty_judgment_quality(treaty, sections):
    """Assess treaty quality using judgment sections from jugeo.judgments.sections.

    Higher section quality indicates the treaty was negotiated with
    well-structured evidence and should be prioritised for archival.
    """
    if _SectionComp is None:
        return {"quality": 0.5, "quality_available": False,
                "subsystem": "jugeo.judgments.sections"}
    comparator = _SectionComp()
    scores = []
    for s in (sections or []):
        try:
            scores.append(float(comparator.compare(s, s)))
        except Exception:
            scores.append(0.0)
    avg = sum(scores) / len(scores) if scores else 0.5
    return {"quality": round(avg, 4), "section_count": len(scores),
            "quality_available": True,
            "subsystem": "jugeo.judgments.sections"}


def encode_treaty_for_archive(treaty):
    """Encode a treaty via jugeo.encodings for long-term archival storage."""
    if _enc_judgment is None:
        return {"encoded": False, "reason": "encode_judgment unavailable",
                "subsystem": "jugeo.encodings"}
    try:
        encoded = _enc_judgment(treaty)
        return {"encoded": True,
                "keys": list(encoded.keys()) if isinstance(encoded, dict) else [],
                "subsystem": "jugeo.encodings"}
    except Exception as exc:
        return {"encoded": False, "reason": str(exc),
                "subsystem": "jugeo.encodings"}
