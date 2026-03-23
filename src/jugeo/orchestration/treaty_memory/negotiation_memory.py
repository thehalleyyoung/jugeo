from __future__ import annotations

"""
theory2.tex Ch48 – "Treaty synthesis, negotiation memory, and archival semantics"

# copilot: This module implements the negotiation-memory layer described in Chapter 48 §2
# of the Jugeo formal theory.  It provides persistent, queryable records of every
# negotiation episode conducted between treaty parties, together with analytical
# utilities for extracting aggregate patterns (success rates, friction hotspots,
# round-count distributions) and a top-level coordinator that wires everything
# together for runtime use.

Design
------
The architecture follows three tiers:

  1. **Record layer** – ``NegotiationEpisode`` is an immutable value object that
     captures all salient facts about a single negotiation episode: the parties
     involved, the clauses they agreed on, the friction keys that caused difficulty,
     timing, and a free-form metadata bucket.

  2. **Index layer** – ``EpisodeIndex`` is a mutable in-memory index that wraps a
     list of episodes and exposes efficient O(n) queries by treaty id, party,
     outcome, and recency, together with aggregate statistics.

  3. **Coordinator layer** – ``NegotiationMemoryCoordinator`` owns an ``EpisodeIndex``
     and provides the high-level API used by the rest of the orchestration subsystem:
     recording new episodes, running queries with composite filters, merging foreign
     indices, and serialising/deserialising the entire memory store.

Analytical utilities are factored into ``NegotiationMemoryAnalyzer``, which is
stateless and operates on any ``EpisodeIndex`` passed to it.

All public names are listed in ``__all__``.

References
----------
* theory2.tex §48.2 – "Negotiation memory formalisation"
* theory2.tex §48.3 – "Friction key semantics"
* theory2.tex §48.5 – "Archival compression policies"
"""

import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ─── Optional jugeo imports (stub fallbacks) ────────────────────────────────

try:
    from jugeo.orchestration.treaty_memory.treaty_base import TreatyBase  # type: ignore
except ImportError:
    TreatyBase: Any = None  # type: ignore[misc,assignment]

try:
    from jugeo.core.identifiers import make_id  # type: ignore
except ImportError:
    def make_id(prefix: str = "") -> str:  # type: ignore[misc]
        """Stub: generate a UUID-based identifier."""
        return f"{prefix}{uuid.uuid4().hex}"

try:
    from jugeo.core.clock import monotonic_now  # type: ignore
except ImportError:
    def monotonic_now() -> float:  # type: ignore[misc]
        """Stub: return current monotonic time."""
        return time.monotonic()

try:
    from jugeo.core.serialisation import jugeo_json_dumps, jugeo_json_loads  # type: ignore
except ImportError:
    import json as _json

    def jugeo_json_dumps(obj: Any) -> str:  # type: ignore[misc]
        return _json.dumps(obj)

    def jugeo_json_loads(s: str) -> Any:  # type: ignore[misc]
        return _json.loads(s)

# ─── Public API ─────────────────────────────────────────────────────────────

__all__ = [
    # dataclasses
    "NegotiationEpisode",
    "EpisodeIndex",
    "MemoryAnalysisReport",
    # classes
    "NegotiationMemoryAnalyzer",
    "NegotiationMemoryCoordinator",
    # helpers
    "make_episode",
    "episode_similarity",
    "compress_episodes",
    "merge_episode_lists",
    "filter_episodes",
    "episode_duration",
    "outcome_is_successful",
    # constants
    "OUTCOME_AGREED",
    "OUTCOME_FAILED",
    "OUTCOME_PARTIAL",
    "OUTCOME_WITHDRAWN",
    "OUTCOME_TIMEOUT",
    "COMPRESS_POLICY_NONE",
    "COMPRESS_POLICY_DEDUP",
    "COMPRESS_POLICY_THIN",
    "DEFAULT_RECENCY_WINDOW",
    "SIMILARITY_CLAUSE_WEIGHT",
    "SIMILARITY_FRICTION_WEIGHT",
    "SIMILARITY_PARTY_WEIGHT",
    "MIN_ROUNDS_MEANINGFUL",
    "MAX_EPISODES_BEFORE_WARN",
]

# ─── Module-level logger ─────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

# Canonical outcome strings as defined in theory2.tex §48.2 Table 3.
OUTCOME_AGREED = "agreed"       # All parties reached full consensus.
OUTCOME_FAILED = "failed"       # Negotiation collapsed without resolution.
OUTCOME_PARTIAL = "partial"     # Some clauses agreed; remainder unresolved.
OUTCOME_WITHDRAWN = "withdrawn" # One or more parties withdrew before conclusion.
OUTCOME_TIMEOUT = "timeout"     # Wall-clock or round limit exceeded.

# Recognised compression policies for the archival tier (§48.5).
COMPRESS_POLICY_NONE = "none"   # No compression; keep all episodes verbatim.
COMPRESS_POLICY_DEDUP = "dedup" # Remove exact-duplicate episodes by content hash.
COMPRESS_POLICY_THIN = "thin"   # Keep only one representative per (treaty, outcome) pair.

# How many recent episodes ``EpisodeIndex.recent()`` returns by default.
DEFAULT_RECENCY_WINDOW: int = 20

# Weights used when computing the Jaccard-based episode similarity score.
SIMILARITY_CLAUSE_WEIGHT: float = 0.50   # Fraction of score from shared clauses.
SIMILARITY_FRICTION_WEIGHT: float = 0.30 # Fraction of score from shared friction keys.
SIMILARITY_PARTY_WEIGHT: float = 0.20    # Fraction of score from shared parties.

# Episodes with fewer rounds than this threshold are flagged as "trivial".
MIN_ROUNDS_MEANINGFUL: int = 2

# When the index grows beyond this size the coordinator emits a warning.
MAX_EPISODES_BEFORE_WARN: int = 10_000

# Outcomes that count as "successful" for rate calculations.
_SUCCESSFUL_OUTCOMES: frozenset[str] = frozenset({OUTCOME_AGREED, OUTCOME_PARTIAL})

# Epsilon for floating-point comparisons (theory2.tex §48.1 Notation).
_FLOAT_EPS: float = 1e-9

# ─── Record layer ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NegotiationEpisode:
    """Immutable record of a single negotiation episode.

    Corresponds to the *Episode* tuple ``E = (id, τ, P, R, κ, C, F, t₀, t₁, μ)``
    defined in theory2.tex §48.2 Definition 1.

    Attributes
    ----------
    episode_id:
        Unique identifier for this episode.  Generated by :func:`make_episode`
        if not supplied explicitly.
    treaty_id:
        Identifier of the treaty that this episode belongs to.
    parties:
        Immutable ordered tuple of party identifiers participating in the
        negotiation.  Order is preserved from the initiating call.
    rounds:
        Total number of negotiation rounds conducted.  A round is one
        complete offer–counter-offer cycle across all parties.
    outcome:
        Terminal outcome string; should be one of the ``OUTCOME_*`` constants
        but is not validated at construction time to allow forward-compatibility
        with new outcome types.
    clauses_agreed:
        Tuple of clause identifiers that all parties accepted by the end of
        the episode.
    friction_keys:
        Tuple of symbolic keys identifying the specific issues that caused
        friction or delay during the episode (§48.3).
    started_at:
        Monotonic timestamp (seconds) at episode start.
    ended_at:
        Monotonic timestamp (seconds) at episode end.  Must be ≥ ``started_at``.
    metadata:
        Arbitrary auxiliary data; e.g. mediator ids, protocol version, flags.
        Stored as a plain ``dict`` — callers are responsible for ensuring it
        is serialisable if they intend to use :meth:`NegotiationMemoryCoordinator.export_memory`.
    """

    episode_id: str
    treaty_id: str
    parties: Tuple[str, ...]
    rounds: int
    outcome: str
    clauses_agreed: Tuple[str, ...]
    friction_keys: Tuple[str, ...]
    started_at: float
    ended_at: float
    metadata: Dict[str, Any]

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def duration(self) -> float:
        """Wall-clock duration of the episode in seconds."""
        return max(0.0, self.ended_at - self.started_at)

    @property
    def is_successful(self) -> bool:
        """``True`` iff the outcome is considered successful (§48.2 §3)."""
        return outcome_is_successful(self.outcome)

    @property
    def is_trivial(self) -> bool:
        """``True`` when the episode completed in fewer rounds than the meaningful threshold."""
        return self.rounds < MIN_ROUNDS_MEANINGFUL

    @property
    def clause_count(self) -> int:
        """Number of clauses agreed in this episode."""
        return len(self.clauses_agreed)

    @property
    def friction_count(self) -> int:
        """Number of distinct friction keys raised in this episode."""
        return len(self.friction_keys)

    def content_hash(self) -> int:
        """Deterministic hash based on content (not identity).

        Useful for deduplication in :func:`compress_episodes`.  The hash is
        computed over ``(treaty_id, parties, outcome, clauses_agreed,
        friction_keys)`` — timing and metadata are intentionally excluded so
        that semantically identical episodes hash the same way.
        """
        return hash((
            self.treaty_id,
            self.parties,
            self.outcome,
            self.clauses_agreed,
            self.friction_keys,
        ))

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this episode to a plain ``dict``."""
        return {
            "episode_id": self.episode_id,
            "treaty_id": self.treaty_id,
            "parties": list(self.parties),
            "rounds": self.rounds,
            "outcome": self.outcome,
            "clauses_agreed": list(self.clauses_agreed),
            "friction_keys": list(self.friction_keys),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NegotiationEpisode":
        """Deserialise an episode from the ``dict`` produced by :meth:`to_dict`."""
        return cls(
            episode_id=d["episode_id"],
            treaty_id=d["treaty_id"],
            parties=tuple(d["parties"]),
            rounds=int(d["rounds"]),
            outcome=d["outcome"],
            clauses_agreed=tuple(d["clauses_agreed"]),
            friction_keys=tuple(d["friction_keys"]),
            started_at=float(d["started_at"]),
            ended_at=float(d["ended_at"]),
            metadata=dict(d.get("metadata", {})),
        )


# ─── Index layer ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class EpisodeIndex:
    """Mutable in-memory index over a collection of :class:`NegotiationEpisode` objects.

    The index stores episodes in insertion order and maintains a set of
    secondary look-up dictionaries that are rebuilt lazily when the underlying
    list changes.  All query methods are O(n) in the worst case; callers that
    need sub-linear performance should maintain their own structures on top of
    this class.

    Attributes
    ----------
    _episodes:
        Ordered list of episodes.  Do not mutate directly; use :meth:`add`.
    _dirty:
        ``True`` when the secondary caches need to be rebuilt.
    _by_treaty:
        Cache mapping treaty_id → list of episodes.
    _by_party:
        Cache mapping party id → list of episodes.
    _by_outcome:
        Cache mapping outcome string → list of episodes.
    """

    _episodes: List[NegotiationEpisode] = field(default_factory=list)
    _dirty: bool = field(default=True)
    _by_treaty: Dict[str, List[NegotiationEpisode]] = field(default_factory=dict)
    _by_party: Dict[str, List[NegotiationEpisode]] = field(default_factory=dict)
    _by_outcome: Dict[str, List[NegotiationEpisode]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, episode: NegotiationEpisode) -> None:
        """Add *episode* to the index.

        Parameters
        ----------
        episode:
            The episode to record.  Duplicate ``episode_id`` values are
            allowed (the index does not enforce uniqueness) so that imported
            snapshots can be merged without pre-filtering.
        """
        if not isinstance(episode, NegotiationEpisode):
            raise TypeError(f"Expected NegotiationEpisode, got {type(episode)!r}")
        self._episodes.append(episode)
        self._dirty = True
        log.debug("EpisodeIndex.add: episode_id=%s treaty=%s outcome=%s",
                  episode.episode_id, episode.treaty_id, episode.outcome)
        if len(self._episodes) > MAX_EPISODES_BEFORE_WARN and len(self._episodes) % 1000 == 0:
            log.warning(
                "EpisodeIndex contains %d episodes which may impact query performance",
                len(self._episodes),
            )

    def _rebuild_caches(self) -> None:
        """Rebuild the secondary look-up caches.

        Called automatically before any query that depends on the caches.
        This is O(n × p) where p is the average number of parties per episode.
        """
        by_treaty: Dict[str, List[NegotiationEpisode]] = {}
        by_party: Dict[str, List[NegotiationEpisode]] = {}
        by_outcome: Dict[str, List[NegotiationEpisode]] = {}
        for ep in self._episodes:
            by_treaty.setdefault(ep.treaty_id, []).append(ep)
            for party in ep.parties:
                by_party.setdefault(party, []).append(ep)
            by_outcome.setdefault(ep.outcome, []).append(ep)
        self._by_treaty = by_treaty
        self._by_party = by_party
        self._by_outcome = by_outcome
        self._dirty = False

    def _ensure_caches(self) -> None:
        if self._dirty:
            self._rebuild_caches()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def by_treaty(self, treaty_id: str) -> List[NegotiationEpisode]:
        """Return all episodes belonging to *treaty_id*."""
        self._ensure_caches()
        return list(self._by_treaty.get(treaty_id, []))

    def by_party(self, party: str) -> List[NegotiationEpisode]:
        """Return all episodes in which *party* participated."""
        self._ensure_caches()
        return list(self._by_party.get(party, []))

    def by_outcome(self, outcome: str) -> List[NegotiationEpisode]:
        """Return all episodes with the given *outcome*."""
        self._ensure_caches()
        return list(self._by_outcome.get(outcome, []))

    def recent(self, n: int = DEFAULT_RECENCY_WINDOW) -> List[NegotiationEpisode]:
        """Return the *n* most recently ended episodes.

        Parameters
        ----------
        n:
            Maximum number of episodes to return.  Defaults to
            :data:`DEFAULT_RECENCY_WINDOW`.
        """
        if n <= 0:
            return []
        sorted_eps = sorted(self._episodes, key=lambda e: e.ended_at, reverse=True)
        return sorted_eps[:n]

    def all_episodes(self) -> List[NegotiationEpisode]:
        """Return a snapshot of all episodes in insertion order."""
        return list(self._episodes)

    def episode_ids(self) -> List[str]:
        """Return a list of all episode identifiers in insertion order."""
        return [e.episode_id for e in self._episodes]

    def __len__(self) -> int:
        return len(self._episodes)

    def __contains__(self, episode_id: str) -> bool:
        return any(e.episode_id == episode_id for e in self._episodes)

    # ------------------------------------------------------------------
    # Aggregate statistics
    # ------------------------------------------------------------------

    def statistics(self) -> Dict[str, Any]:
        """Compute summary statistics over the entire index.

        Returns
        -------
        dict
            Keys: ``total``, ``outcomes`` (dict of outcome → count),
            ``success_rate``, ``avg_rounds``, ``median_rounds``,
            ``avg_duration``, ``avg_clauses``, ``avg_friction``,
            ``trivial_fraction``, ``parties`` (set of all party ids).
        """
        eps = self._episodes
        total = len(eps)
        if total == 0:
            return {
                "total": 0,
                "outcomes": {},
                "success_rate": 0.0,
                "avg_rounds": 0.0,
                "median_rounds": 0.0,
                "avg_duration": 0.0,
                "avg_clauses": 0.0,
                "avg_friction": 0.0,
                "trivial_fraction": 0.0,
                "parties": set(),
            }

        outcome_counts: Dict[str, int] = {}
        rounds_list: List[float] = []
        duration_list: List[float] = []
        clauses_list: List[float] = []
        friction_list: List[float] = []
        successful = 0
        trivial = 0
        all_parties: set[str] = set()

        for ep in eps:
            outcome_counts[ep.outcome] = outcome_counts.get(ep.outcome, 0) + 1
            rounds_list.append(float(ep.rounds))
            duration_list.append(ep.duration)
            clauses_list.append(float(ep.clause_count))
            friction_list.append(float(ep.friction_count))
            if ep.is_successful:
                successful += 1
            if ep.is_trivial:
                trivial += 1
            all_parties.update(ep.parties)

        return {
            "total": total,
            "outcomes": outcome_counts,
            "success_rate": successful / total,
            "avg_rounds": statistics.mean(rounds_list),
            "median_rounds": statistics.median(rounds_list),
            "avg_duration": statistics.mean(duration_list),
            "avg_clauses": statistics.mean(clauses_list),
            "avg_friction": statistics.mean(friction_list),
            "trivial_fraction": trivial / total,
            "parties": all_parties,
        }


# ─── Analysis report ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MemoryAnalysisReport:
    """Immutable snapshot of analytical results derived from an :class:`EpisodeIndex`.

    Produced by :meth:`NegotiationMemoryAnalyzer.analyze`.

    Attributes
    ----------
    report_id:
        Unique identifier for this report.
    total_episodes:
        Total number of episodes analysed.
    success_rate:
        Fraction of episodes whose outcome is considered successful.
    avg_rounds:
        Mean number of rounds across all episodes.
    hotspots:
        Tuple of friction keys ranked by frequency (most frequent first).
    party_rates:
        Mapping from party id to that party's individual success rate.
    generated_at:
        Monotonic timestamp at which the report was produced.
    """

    report_id: str
    total_episodes: int
    success_rate: float
    avg_rounds: float
    hotspots: Tuple[str, ...]
    party_rates: Dict[str, float]
    generated_at: float

    def summary_line(self) -> str:
        """Return a single-line human-readable summary of this report."""
        top = self.hotspots[:3] if self.hotspots else []
        return (
            f"MemoryAnalysisReport({self.report_id}) "
            f"episodes={self.total_episodes} "
            f"success={self.success_rate:.1%} "
            f"avg_rounds={self.avg_rounds:.1f} "
            f"top_friction={top}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise this report to a plain ``dict``."""
        return {
            "report_id": self.report_id,
            "total_episodes": self.total_episodes,
            "success_rate": self.success_rate,
            "avg_rounds": self.avg_rounds,
            "hotspots": list(self.hotspots),
            "party_rates": dict(self.party_rates),
            "generated_at": self.generated_at,
        }


# ─── Analyser ────────────────────────────────────────────────────────────────


class NegotiationMemoryAnalyzer:
    """Stateless analyser that derives aggregate patterns from an :class:`EpisodeIndex`.

    All methods are pure functions: they do not modify the index and can be
    called concurrently without locks (assuming the index is not being mutated
    at the same time).

    Design note (theory2.tex §48.4): The analyser implements the *Memory
    Diagnosis* operation ``Diag(M)`` which projects a raw episode memory ``M``
    onto a set of derived signals useful for treaty synthesis decisions.
    """

    # ------------------------------------------------------------------
    # Primary analysis
    # ------------------------------------------------------------------

    def analyze(self, index: EpisodeIndex) -> MemoryAnalysisReport:
        """Produce a :class:`MemoryAnalysisReport` from *index*.

        Parameters
        ----------
        index:
            The episode index to analyse.

        Returns
        -------
        MemoryAnalysisReport
            A frozen snapshot of the analysis results.
        """
        stats = index.statistics()
        hotspots = self.friction_hotspots(index)
        party_rates = self.success_rate_by_party(index)
        avg_rounds = stats.get("avg_rounds", 0.0)
        return MemoryAnalysisReport(
            report_id=make_id("report-"),
            total_episodes=stats["total"],
            success_rate=stats.get("success_rate", 0.0),
            avg_rounds=avg_rounds,
            hotspots=tuple(hotspots),
            party_rates=party_rates,
            generated_at=monotonic_now(),
        )

    # ------------------------------------------------------------------
    # Sub-analyses
    # ------------------------------------------------------------------

    def friction_hotspots(self, index: EpisodeIndex) -> List[str]:
        """Return friction keys ranked by frequency (most common first).

        A *hotspot* is a friction key that appears more frequently than
        average across all episodes.  If no episodes exist, returns ``[]``.

        Parameters
        ----------
        index:
            The episode index to query.

        Returns
        -------
        list[str]
            Friction keys sorted by descending occurrence count, then
            lexicographically for ties.
        """
        freq: Dict[str, int] = {}
        for ep in index.all_episodes():
            for key in ep.friction_keys:
                freq[key] = freq.get(key, 0) + 1
        if not freq:
            return []
        total_eps = len(index)
        avg_count = sum(freq.values()) / max(1, len(freq))
        # Return keys that appear at least once, sorted by frequency desc.
        ranked = sorted(freq.keys(), key=lambda k: (-freq[k], k))
        return ranked

    def success_rate_by_party(self, index: EpisodeIndex) -> Dict[str, float]:
        """Compute per-party success rates.

        For each party p, the success rate is defined as:
        ``|{e ∈ M : p ∈ e.parties ∧ e.is_successful}| / |{e ∈ M : p ∈ e.parties}|``

        Parameters
        ----------
        index:
            The episode index to query.

        Returns
        -------
        dict[str, float]
            Mapping from party id to success rate in [0, 1].
        """
        total_by_party: Dict[str, int] = {}
        success_by_party: Dict[str, int] = {}
        for ep in index.all_episodes():
            for party in ep.parties:
                total_by_party[party] = total_by_party.get(party, 0) + 1
                if ep.is_successful:
                    success_by_party[party] = success_by_party.get(party, 0) + 1
        return {
            p: success_by_party.get(p, 0) / count
            for p, count in total_by_party.items()
        }

    def average_rounds_by_outcome(self, index: EpisodeIndex) -> Dict[str, float]:
        """Compute mean round count grouped by outcome.

        Parameters
        ----------
        index:
            The episode index to query.

        Returns
        -------
        dict[str, float]
            Mapping from outcome string to mean round count.  Outcomes with
            zero episodes are omitted.
        """
        buckets: Dict[str, List[int]] = {}
        for ep in index.all_episodes():
            buckets.setdefault(ep.outcome, []).append(ep.rounds)
        return {
            outcome: statistics.mean(rounds)
            for outcome, rounds in buckets.items()
            if rounds
        }

    def duration_distribution(self, index: EpisodeIndex) -> Dict[str, float]:
        """Return descriptive statistics for episode durations.

        Returns
        -------
        dict
            Keys: ``min``, ``max``, ``mean``, ``median``, ``stdev``
            (stdev is 0.0 when there is only one episode).
        """
        durations = [ep.duration for ep in index.all_episodes()]
        if not durations:
            return {"min": 0.0, "max": 0.0, "mean": 0.0, "median": 0.0, "stdev": 0.0}
        return {
            "min": min(durations),
            "max": max(durations),
            "mean": statistics.mean(durations),
            "median": statistics.median(durations),
            "stdev": statistics.stdev(durations) if len(durations) > 1 else 0.0,
        }

    def clause_coverage(self, index: EpisodeIndex) -> Dict[str, int]:
        """Return a frequency map of agreed clauses across all episodes.

        Useful for identifying which clauses are consistently negotiated and
        which appear only rarely.
        """
        freq: Dict[str, int] = {}
        for ep in index.all_episodes():
            for clause in ep.clauses_agreed:
                freq[clause] = freq.get(clause, 0) + 1
        return freq


# ─── Coordinator ─────────────────────────────────────────────────────────────


class NegotiationMemoryCoordinator:
    """Top-level coordinator for the negotiation memory subsystem.

    The coordinator owns a single :class:`EpisodeIndex` and exposes a
    high-level API for recording, querying, merging, exporting, and importing
    episodes.  It is the primary integration point used by the orchestration
    layer.

    Design note (theory2.tex §48.6): The coordinator acts as the *Memory
    Archivist* role ``MA`` that receives episode notifications from the treaty
    engine and maintains the persistent memory store.

    Parameters
    ----------
    label:
        Optional human-readable label for this memory store (e.g. the name
        of the orchestration node that owns it).
    """

    def __init__(self, label: str = "default") -> None:
        self.label = label
        self._index: EpisodeIndex = EpisodeIndex()
        self._analyzer: NegotiationMemoryAnalyzer = NegotiationMemoryAnalyzer()
        self._created_at: float = monotonic_now()
        log.info("NegotiationMemoryCoordinator initialised label=%r", label)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_episode(
        self,
        *,
        treaty_id: str,
        parties: Tuple[str, ...],
        rounds: int,
        outcome: str,
        clauses_agreed: Tuple[str, ...] = (),
        friction_keys: Tuple[str, ...] = (),
        started_at: Optional[float] = None,
        ended_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        episode_id: Optional[str] = None,
    ) -> NegotiationEpisode:
        """Create and record a new :class:`NegotiationEpisode`.

        Parameters
        ----------
        treaty_id:
            Identifier of the treaty being negotiated.
        parties:
            Tuple of participating party identifiers.
        rounds:
            Number of rounds conducted.
        outcome:
            Terminal outcome (use the ``OUTCOME_*`` constants).
        clauses_agreed:
            Clause ids that were agreed upon.
        friction_keys:
            Symbolic keys for issues that caused friction.
        started_at:
            Monotonic start time.  Defaults to ``monotonic_now()`` minus a
            small heuristic offset based on *rounds* if not provided.
        ended_at:
            Monotonic end time.  Defaults to ``monotonic_now()``.
        metadata:
            Arbitrary auxiliary data.
        episode_id:
            Explicit episode identifier.  Auto-generated when omitted.

        Returns
        -------
        NegotiationEpisode
            The newly created and indexed episode.
        """
        now = monotonic_now()
        if ended_at is None:
            ended_at = now
        if started_at is None:
            # Heuristic: assume each round took ~0.1 s (arbitrary for tests).
            started_at = ended_at - max(0.0, rounds * 0.1)
        episode = make_episode(
            treaty_id=treaty_id,
            parties=parties,
            rounds=rounds,
            outcome=outcome,
            clauses_agreed=clauses_agreed,
            friction_keys=friction_keys,
            started_at=started_at,
            ended_at=ended_at,
            metadata=metadata or {},
            episode_id=episode_id or make_id("ep-"),
        )
        self._index.add(episode)
        return episode

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query(self, filters: Dict[str, Any]) -> List[NegotiationEpisode]:
        """Return episodes matching all conditions in *filters*.

        Supported filter keys
        ~~~~~~~~~~~~~~~~~~~~~
        ``treaty_id`` : str
            Exact match on ``episode.treaty_id``.
        ``party`` : str
            Episode must include this party.
        ``outcome`` : str
            Exact match on ``episode.outcome``.
        ``min_rounds`` : int
            ``episode.rounds >= min_rounds``.
        ``max_rounds`` : int
            ``episode.rounds <= max_rounds``.
        ``successful_only`` : bool
            When ``True``, only include successful episodes.
        ``friction_key`` : str
            Episode must contain this friction key.
        ``clause`` : str
            Episode must have agreed this clause.
        ``after`` : float
            ``episode.ended_at > after``.
        ``before`` : float
            ``episode.ended_at < before``.

        Parameters
        ----------
        filters:
            Dict of filter key → value pairs.  All supplied filters are
            combined with logical AND.

        Returns
        -------
        list[NegotiationEpisode]
        """
        return filter_episodes(self._index.all_episodes(), filters)

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    def merge_memories(self, other: EpisodeIndex) -> int:
        """Merge all episodes from *other* into this coordinator's index.

        Episodes that already exist (by ``episode_id``) in the local index are
        skipped to avoid duplicates.

        Parameters
        ----------
        other:
            The foreign index to merge from.

        Returns
        -------
        int
            Number of episodes actually added (after deduplication).
        """
        existing_ids: set[str] = set(self._index.episode_ids())
        added = 0
        for ep in other.all_episodes():
            if ep.episode_id not in existing_ids:
                self._index.add(ep)
                existing_ids.add(ep.episode_id)
                added += 1
        log.info("merge_memories: added %d new episodes from foreign index", added)
        return added

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def export_memory(self) -> Dict[str, Any]:
        """Serialise the entire memory store to a plain ``dict``.

        The returned structure can be round-tripped through
        :meth:`import_memory`.

        Returns
        -------
        dict
            Keys: ``label``, ``created_at``, ``exported_at``, ``episodes``
            (list of episode dicts).
        """
        return {
            "label": self.label,
            "created_at": self._created_at,
            "exported_at": monotonic_now(),
            "episodes": [ep.to_dict() for ep in self._index.all_episodes()],
        }

    def import_memory(self, data: Dict[str, Any]) -> int:
        """Load episodes from a previously exported memory ``dict``.

        Existing episodes with the same ``episode_id`` are silently skipped.

        Parameters
        ----------
        data:
            Dict as produced by :meth:`export_memory`.

        Returns
        -------
        int
            Number of episodes imported.
        """
        raw_episodes: List[Dict[str, Any]] = data.get("episodes", [])
        existing_ids: set[str] = set(self._index.episode_ids())
        imported = 0
        for raw in raw_episodes:
            ep = NegotiationEpisode.from_dict(raw)
            if ep.episode_id not in existing_ids:
                self._index.add(ep)
                existing_ids.add(ep.episode_id)
                imported += 1
        log.info("import_memory: imported %d episodes from snapshot label=%r",
                 imported, data.get("label"))
        return imported

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------

    def analyze(self) -> MemoryAnalysisReport:
        """Run a full analysis over the current memory store."""
        return self._analyzer.analyze(self._index)

    @property
    def index(self) -> EpisodeIndex:
        """Read-only access to the underlying :class:`EpisodeIndex`."""
        return self._index

    def __repr__(self) -> str:
        return (
            f"NegotiationMemoryCoordinator(label={self.label!r}, "
            f"episodes={len(self._index)})"
        )


# ─── Helper functions ────────────────────────────────────────────────────────


def make_episode(
    *,
    treaty_id: str,
    parties: Tuple[str, ...],
    rounds: int,
    outcome: str,
    clauses_agreed: Tuple[str, ...] = (),
    friction_keys: Tuple[str, ...] = (),
    started_at: Optional[float] = None,
    ended_at: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    episode_id: Optional[str] = None,
) -> NegotiationEpisode:
    """Factory function for :class:`NegotiationEpisode`.

    Provides ergonomic construction with sensible defaults for timing fields
    and optional components.

    Parameters
    ----------
    treaty_id:
        Identifier of the treaty.
    parties:
        Tuple of party identifiers.
    rounds:
        Number of negotiation rounds.
    outcome:
        Terminal outcome string.
    clauses_agreed:
        Tuple of agreed clause identifiers.  Defaults to empty tuple.
    friction_keys:
        Tuple of friction key strings.  Defaults to empty tuple.
    started_at:
        Start time.  Defaults to ``monotonic_now() - rounds * 0.1``.
    ended_at:
        End time.  Defaults to ``monotonic_now()``.
    metadata:
        Auxiliary data dict.  Defaults to ``{}``.
    episode_id:
        Explicit id.  Defaults to a new UUID-based id.

    Returns
    -------
    NegotiationEpisode
    """
    now = monotonic_now()
    if ended_at is None:
        ended_at = now
    if started_at is None:
        started_at = ended_at - max(0.0, rounds * 0.1)
    return NegotiationEpisode(
        episode_id=episode_id or make_id("ep-"),
        treaty_id=treaty_id,
        parties=tuple(parties),
        rounds=int(rounds),
        outcome=outcome,
        clauses_agreed=tuple(clauses_agreed),
        friction_keys=tuple(friction_keys),
        started_at=float(started_at),
        ended_at=float(ended_at),
        metadata=dict(metadata or {}),
    )


def episode_similarity(a: NegotiationEpisode, b: NegotiationEpisode) -> float:
    """Compute a similarity score between two episodes in [0, 1].

    The score is a weighted sum of three Jaccard-similarity terms:

    * **Clause similarity** — Jaccard index of ``clauses_agreed`` sets,
      weighted by :data:`SIMILARITY_CLAUSE_WEIGHT`.
    * **Friction similarity** — Jaccard index of ``friction_keys`` sets,
      weighted by :data:`SIMILARITY_FRICTION_WEIGHT`.
    * **Party similarity** — Jaccard index of ``parties`` sets,
      weighted by :data:`SIMILARITY_PARTY_WEIGHT`.

    If both episodes have empty sets for a component, that component
    contributes its full weight (they are trivially identical on that axis).

    Parameters
    ----------
    a, b:
        The two episodes to compare.

    Returns
    -------
    float
        Similarity in [0, 1].  1.0 means identical on all compared axes.
    """
    def _jaccard(xs: frozenset[str], ys: frozenset[str]) -> float:
        if not xs and not ys:
            return 1.0
        union = xs | ys
        if not union:
            return 1.0
        return len(xs & ys) / len(union)

    clause_sim = _jaccard(frozenset(a.clauses_agreed), frozenset(b.clauses_agreed))
    friction_sim = _jaccard(frozenset(a.friction_keys), frozenset(b.friction_keys))
    party_sim = _jaccard(frozenset(a.parties), frozenset(b.parties))

    score = (
        SIMILARITY_CLAUSE_WEIGHT * clause_sim
        + SIMILARITY_FRICTION_WEIGHT * friction_sim
        + SIMILARITY_PARTY_WEIGHT * party_sim
    )
    # Clamp to [0, 1] to guard against floating-point drift.
    return max(0.0, min(1.0, score))


def compress_episodes(
    episodes: List[NegotiationEpisode],
    policy: str = COMPRESS_POLICY_DEDUP,
) -> List[NegotiationEpisode]:
    """Reduce *episodes* according to *policy*.

    This implements the archival compression operation described in
    theory2.tex §48.5.

    Policies
    --------
    :data:`COMPRESS_POLICY_NONE`:
        Return *episodes* unchanged.
    :data:`COMPRESS_POLICY_DEDUP`:
        Remove exact content duplicates, keeping the earliest occurrence
        (smallest ``started_at``).
    :data:`COMPRESS_POLICY_THIN`:
        Keep only the most recent episode per ``(treaty_id, outcome)`` pair.

    Parameters
    ----------
    episodes:
        Input list.  Not mutated.
    policy:
        One of the ``COMPRESS_POLICY_*`` constants.

    Returns
    -------
    list[NegotiationEpisode]
        Compressed list, preserving relative order of retained elements.

    Raises
    ------
    ValueError
        If *policy* is not a recognised compression policy string.
    """
    if policy == COMPRESS_POLICY_NONE:
        return list(episodes)

    if policy == COMPRESS_POLICY_DEDUP:
        seen: set[int] = set()
        result: List[NegotiationEpisode] = []
        for ep in sorted(episodes, key=lambda e: e.started_at):
            h = ep.content_hash()
            if h not in seen:
                seen.add(h)
                result.append(ep)
        # Restore original relative order by started_at (stable).
        result.sort(key=lambda e: e.started_at)
        return result

    if policy == COMPRESS_POLICY_THIN:
        best: Dict[Tuple[str, str], NegotiationEpisode] = {}
        for ep in episodes:
            key = (ep.treaty_id, ep.outcome)
            existing = best.get(key)
            if existing is None or ep.ended_at > existing.ended_at:
                best[key] = ep
        return sorted(best.values(), key=lambda e: e.started_at)

    raise ValueError(
        f"Unknown compression policy {policy!r}. "
        f"Expected one of: {COMPRESS_POLICY_NONE!r}, "
        f"{COMPRESS_POLICY_DEDUP!r}, {COMPRESS_POLICY_THIN!r}."
    )


def merge_episode_lists(
    *lists: List[NegotiationEpisode],
) -> List[NegotiationEpisode]:
    """Merge multiple episode lists, deduplicating by ``episode_id``.

    Parameters
    ----------
    *lists:
        Any number of episode lists.

    Returns
    -------
    list[NegotiationEpisode]
        Combined list in order of first occurrence, with later duplicates
        by ``episode_id`` discarded.
    """
    seen_ids: set[str] = set()
    merged: List[NegotiationEpisode] = []
    for lst in lists:
        for ep in lst:
            if ep.episode_id not in seen_ids:
                seen_ids.add(ep.episode_id)
                merged.append(ep)
    return merged


def filter_episodes(
    episodes: List[NegotiationEpisode],
    filters: Dict[str, Any],
) -> List[NegotiationEpisode]:
    """Apply a filter dict to a list of episodes.

    See :meth:`NegotiationMemoryCoordinator.query` for the supported filter
    keys and semantics.

    Parameters
    ----------
    episodes:
        Source list to filter.
    filters:
        Dict of filter key → value.

    Returns
    -------
    list[NegotiationEpisode]
        Episodes matching all supplied filters.
    """
    result = episodes
    if "treaty_id" in filters:
        result = [e for e in result if e.treaty_id == filters["treaty_id"]]
    if "party" in filters:
        p = filters["party"]
        result = [e for e in result if p in e.parties]
    if "outcome" in filters:
        result = [e for e in result if e.outcome == filters["outcome"]]
    if "min_rounds" in filters:
        result = [e for e in result if e.rounds >= filters["min_rounds"]]
    if "max_rounds" in filters:
        result = [e for e in result if e.rounds <= filters["max_rounds"]]
    if filters.get("successful_only"):
        result = [e for e in result if e.is_successful]
    if "friction_key" in filters:
        fk = filters["friction_key"]
        result = [e for e in result if fk in e.friction_keys]
    if "clause" in filters:
        cl = filters["clause"]
        result = [e for e in result if cl in e.clauses_agreed]
    if "after" in filters:
        t = float(filters["after"])
        result = [e for e in result if e.ended_at > t]
    if "before" in filters:
        t = float(filters["before"])
        result = [e for e in result if e.ended_at < t]
    return result


def episode_duration(episode: NegotiationEpisode) -> float:
    """Return the wall-clock duration of *episode* in seconds.

    Convenience wrapper around :attr:`NegotiationEpisode.duration`.
    """
    return episode.duration


def outcome_is_successful(outcome: str) -> bool:
    """Return ``True`` iff *outcome* is a successful outcome.

    Successful outcomes are defined as those in :data:`_SUCCESSFUL_OUTCOMES`,
    i.e. :data:`OUTCOME_AGREED` and :data:`OUTCOME_PARTIAL`.

    Parameters
    ----------
    outcome:
        An outcome string (typically one of the ``OUTCOME_*`` constants).
    """
    return outcome in _SUCCESSFUL_OUTCOMES


def episodes_to_index(episodes: List[NegotiationEpisode]) -> EpisodeIndex:
    """Construct an :class:`EpisodeIndex` pre-populated with *episodes*.

    Parameters
    ----------
    episodes:
        Episodes to add to the new index.

    Returns
    -------
    EpisodeIndex
    """
    idx = EpisodeIndex()
    for ep in episodes:
        idx.add(ep)
    return idx


def build_friction_frequency_table(
    index: EpisodeIndex,
) -> List[Tuple[str, int]]:
    """Build a frequency table of friction keys, sorted by descending count.

    Parameters
    ----------
    index:
        Episode index to analyse.

    Returns
    -------
    list[tuple[str, int]]
        List of ``(friction_key, count)`` pairs sorted by count descending,
        then lexicographically by key for determinism.
    """
    freq: Dict[str, int] = {}
    for ep in index.all_episodes():
        for key in ep.friction_keys:
            freq[key] = freq.get(key, 0) + 1
    return sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))


def rank_parties_by_success(
    index: EpisodeIndex,
) -> List[Tuple[str, float, int]]:
    """Rank all parties by their success rate, descending.

    Parameters
    ----------
    index:
        Episode index to analyse.

    Returns
    -------
    list[tuple[str, float, int]]
        Each element is ``(party_id, success_rate, total_episodes)``,
        sorted by success_rate descending, then by total_episodes descending
        for ties.
    """
    total_map: Dict[str, int] = {}
    success_map: Dict[str, int] = {}
    for ep in index.all_episodes():
        for party in ep.parties:
            total_map[party] = total_map.get(party, 0) + 1
            if ep.is_successful:
                success_map[party] = success_map.get(party, 0) + 1
    rows = [
        (p, success_map.get(p, 0) / count, count)
        for p, count in total_map.items()
    ]
    rows.sort(key=lambda r: (-r[1], -r[2], r[0]))
    return rows


def summarise_index(index: EpisodeIndex) -> str:
    """Return a multi-line human-readable summary of *index*.

    Useful for logging and debugging without needing a full analysis report.
    """
    stats = index.statistics()
    lines = [
        f"EpisodeIndex summary",
        f"  Total episodes : {stats['total']}",
        f"  Success rate   : {stats.get('success_rate', 0.0):.1%}",
        f"  Avg rounds     : {stats.get('avg_rounds', 0.0):.2f}",
        f"  Avg duration   : {stats.get('avg_duration', 0.0):.3f}s",
        f"  Trivial frac.  : {stats.get('trivial_fraction', 0.0):.1%}",
        f"  Outcomes       : {stats.get('outcomes', {})}",
        f"  Party count    : {len(stats.get('parties', set()))}",
    ]
    return "\n".join(lines)


def validate_episode(episode: NegotiationEpisode) -> List[str]:
    """Validate an episode and return a list of warning strings.

    Does not raise; instead collects all issues and returns them so the
    caller can decide how to handle them.

    Checks performed
    ~~~~~~~~~~~~~~~~
    * ``parties`` must be non-empty.
    * ``rounds`` must be ≥ 0.
    * ``ended_at`` must be ≥ ``started_at``.
    * ``outcome`` should be one of the recognised ``OUTCOME_*`` values.
    * ``episode_id`` must be non-empty.
    * ``treaty_id`` must be non-empty.
    """
    warnings: List[str] = []
    if not episode.parties:
        warnings.append("parties tuple is empty")
    if episode.rounds < 0:
        warnings.append(f"rounds={episode.rounds} is negative")
    if episode.ended_at < episode.started_at - _FLOAT_EPS:
        warnings.append(
            f"ended_at={episode.ended_at} is before started_at={episode.started_at}"
        )
    known_outcomes = {
        OUTCOME_AGREED, OUTCOME_FAILED, OUTCOME_PARTIAL,
        OUTCOME_WITHDRAWN, OUTCOME_TIMEOUT,
    }
    if episode.outcome not in known_outcomes:
        warnings.append(
            f"outcome={episode.outcome!r} is not a recognised OUTCOME_* constant"
        )
    if not episode.episode_id:
        warnings.append("episode_id is empty")
    if not episode.treaty_id:
        warnings.append("treaty_id is empty")
    return warnings


# ─── Smoke test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    print(f"[smoke] {__file__}")

    # ── 1. make_episode / NegotiationEpisode basics ──────────────────────────
    ep1 = make_episode(
        treaty_id="t-001",
        parties=("alpha", "beta"),
        rounds=5,
        outcome=OUTCOME_AGREED,
        clauses_agreed=("c1", "c2", "c3"),
        friction_keys=("price", "timeline"),
        metadata={"protocol": "v2"},
    )
    assert ep1.is_successful, "agreed episode must be successful"
    assert not ep1.is_trivial, "5-round episode is not trivial"
    assert ep1.clause_count == 3
    assert ep1.friction_count == 2
    assert ep1.duration >= 0.0
    print(f"[smoke] ep1: {ep1.episode_id}, outcome={ep1.outcome}, duration={ep1.duration:.3f}s")

    # ── 2. round-trip serialisation ──────────────────────────────────────────
    ep1_dict = ep1.to_dict()
    ep1_restored = NegotiationEpisode.from_dict(ep1_dict)
    assert ep1_restored.episode_id == ep1.episode_id
    assert ep1_restored.clauses_agreed == ep1.clauses_agreed
    print("[smoke] serialisation round-trip: OK")

    # ── 3. EpisodeIndex ───────────────────────────────────────────────────────
    idx = EpisodeIndex()
    for outcome in (OUTCOME_AGREED, OUTCOME_FAILED, OUTCOME_PARTIAL,
                    OUTCOME_WITHDRAWN, OUTCOME_TIMEOUT, OUTCOME_AGREED):
        idx.add(make_episode(
            treaty_id="t-001" if outcome in (OUTCOME_AGREED, OUTCOME_PARTIAL) else "t-002",
            parties=("alpha", "gamma"),
            rounds=3,
            outcome=outcome,
            clauses_agreed=("c1",),
            friction_keys=("price",) if outcome != OUTCOME_AGREED else (),
        ))
    assert len(idx) == 6
    assert len(idx.by_treaty("t-001")) == 3   # agreed x2 + partial x1
    assert len(idx.by_party("alpha")) == 6
    assert len(idx.by_outcome(OUTCOME_AGREED)) == 2
    recent = idx.recent(3)
    assert len(recent) == 3
    stats = idx.statistics()
    assert stats["total"] == 6
    assert 0.0 <= stats["success_rate"] <= 1.0
    print(f"[smoke] EpisodeIndex stats: {stats['total']} episodes, "
          f"success_rate={stats['success_rate']:.1%}")

    # ── 4. NegotiationMemoryAnalyzer ─────────────────────────────────────────
    analyzer = NegotiationMemoryAnalyzer()
    report = analyzer.analyze(idx)
    assert report.total_episodes == 6
    assert 0.0 <= report.success_rate <= 1.0
    assert isinstance(report.hotspots, tuple)
    assert isinstance(report.party_rates, dict)
    print(f"[smoke] {report.summary_line()}")

    avg_by_outcome = analyzer.average_rounds_by_outcome(idx)
    assert OUTCOME_AGREED in avg_by_outcome
    dur_dist = analyzer.duration_distribution(idx)
    assert "mean" in dur_dist

    # ── 5. NegotiationMemoryCoordinator ──────────────────────────────────────
    coord = NegotiationMemoryCoordinator(label="smoke-test")
    for i in range(10):
        coord.record_episode(
            treaty_id=f"t-{i % 3:03d}",
            parties=("p1", "p2", f"p{i}"),
            rounds=i + 1,
            outcome=OUTCOME_AGREED if i % 2 == 0 else OUTCOME_FAILED,
            clauses_agreed=(f"c{i}",),
            friction_keys=(f"fk{i % 4}",),
        )
    assert len(coord.index) == 10

    results = coord.query({"outcome": OUTCOME_AGREED})
    assert all(e.outcome == OUTCOME_AGREED for e in results)
    results2 = coord.query({"min_rounds": 5, "successful_only": True})
    assert all(e.rounds >= 5 and e.is_successful for e in results2)
    print(f"[smoke] coordinator query agreed={len(results)} high-round-success={len(results2)}")

    # ── 6. merge_memories ────────────────────────────────────────────────────
    foreign_idx = EpisodeIndex()
    for j in range(5):
        foreign_idx.add(make_episode(
            treaty_id="t-foreign",
            parties=("x", "y"),
            rounds=2,
            outcome=OUTCOME_PARTIAL,
        ))
    added = coord.merge_memories(foreign_idx)
    assert added == 5
    assert len(coord.index) == 15
    print(f"[smoke] merge_memories: added={added}, total={len(coord.index)}")

    # ── 7. export / import ───────────────────────────────────────────────────
    snapshot = coord.export_memory()
    assert len(snapshot["episodes"]) == 15
    coord2 = NegotiationMemoryCoordinator(label="reimport")
    n_imported = coord2.import_memory(snapshot)
    assert n_imported == 15
    assert len(coord2.index) == 15
    # Re-importing should skip all duplicates.
    n_reimported = coord2.import_memory(snapshot)
    assert n_reimported == 0
    print(f"[smoke] export/import: imported={n_imported}, re-imported={n_reimported}")

    # ── 8. compress_episodes ─────────────────────────────────────────────────
    ep_a = make_episode(treaty_id="tx", parties=("a",), rounds=2, outcome=OUTCOME_AGREED,
                        clauses_agreed=("c1",), friction_keys=())
    # Create a content-identical episode (same content hash).
    ep_b = make_episode(treaty_id="tx", parties=("a",), rounds=2, outcome=OUTCOME_AGREED,
                        clauses_agreed=("c1",), friction_keys=())
    ep_c = make_episode(treaty_id="tx", parties=("a",), rounds=4, outcome=OUTCOME_FAILED)
    duped = [ep_a, ep_b, ep_c]
    deduped = compress_episodes(duped, COMPRESS_POLICY_DEDUP)
    assert len(deduped) == 2, f"expected 2 after dedup, got {len(deduped)}"
    thinned = compress_episodes(duped, COMPRESS_POLICY_THIN)
    assert len(thinned) == 2  # one per (treaty, outcome)
    none_compressed = compress_episodes(duped, COMPRESS_POLICY_NONE)
    assert len(none_compressed) == 3
    print(f"[smoke] compress_episodes: dedup={len(deduped)} thin={len(thinned)}")

    # ── 9. episode_similarity ────────────────────────────────────────────────
    ep_x = make_episode(treaty_id="t", parties=("a", "b"), rounds=3, outcome=OUTCOME_AGREED,
                        clauses_agreed=("c1", "c2"), friction_keys=("f1",))
    ep_y = make_episode(treaty_id="t", parties=("a", "b"), rounds=3, outcome=OUTCOME_AGREED,
                        clauses_agreed=("c1", "c2"), friction_keys=("f1",))
    ep_z = make_episode(treaty_id="t", parties=("c", "d"), rounds=1, outcome=OUTCOME_FAILED,
                        clauses_agreed=("c9",), friction_keys=("f9",))
    sim_xy = episode_similarity(ep_x, ep_y)
    sim_xz = episode_similarity(ep_x, ep_z)
    assert sim_xy == 1.0, f"identical episodes should have sim=1.0, got {sim_xy}"
    assert sim_xz < sim_xy, "dissimilar episodes should score lower"
    print(f"[smoke] episode_similarity: identical={sim_xy:.3f} dissimilar={sim_xz:.3f}")

    # ── 10. validate_episode ─────────────────────────────────────────────────
    bad_ep = make_episode(treaty_id="", parties=(), rounds=-1, outcome="unknown",
                          started_at=100.0, ended_at=50.0)
    warnings = validate_episode(bad_ep)
    assert len(warnings) >= 4, f"expected >=4 warnings, got {warnings}"
    print(f"[smoke] validate_episode: {len(warnings)} warnings: {warnings}")

    # ── 11. misc helpers ─────────────────────────────────────────────────────
    freq_table = build_friction_frequency_table(idx)
    assert isinstance(freq_table, list)
    ranked = rank_parties_by_success(idx)
    assert all(0.0 <= r[1] <= 1.0 for r in ranked)
    summary = summarise_index(idx)
    assert "Total episodes" in summary
    print(f"[smoke] misc helpers: friction_freq_keys={[k for k,_ in freq_table]}")
    print(f"[smoke] summarise_index:\n{summary}")

    print("[smoke] PASS")
