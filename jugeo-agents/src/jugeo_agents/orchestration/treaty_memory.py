"""Treaty Memory — cross-pipeline learning from past agent conflicts.

Maintains a persistent, queryable memory of inter-agent friction
patterns and proven resolution strategies.  The core loop is:

1. **Record** — after every treaty negotiation, capture the
   contradiction–resolution pair plus its success/failure outcome.
2. **Pattern Recognition** — group recorded events into
   :class:`FrictionPattern` objects keyed by *(agent_pair, domain,
   obstruction_kind)*.
3. **Strategy Selection** — rank :class:`ResolutionTemplate` objects
   by success rate so future negotiations can start from the best
   known approach.
4. **Preemptive Constraints** — before a pipeline even starts, suggest
   guardrails derived from historical conflict hotspots.

Classes
-------
FrictionPattern
    A recurring conflict pattern between two agents.
ResolutionTemplate
    A proven resolution strategy linked to a friction pattern.
TreatyMemoryStats
    Aggregate statistics over the memory store.
TreatyMemory
    Main in-memory store: record, query, suggest, expire.
TreatyArchive
    Long-term storage tracking how treaties between agent pairs
    evolve across pipelines.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from jugeo_agents.types import (
    Contradiction,
    ObstructionKind,
    TreatyResolution,
    TrustLevel,
)

__all__ = [
    "FrictionPattern",
    "ResolutionTemplate",
    "TreatyMemoryStats",
    "TreatyMemory",
    "TreatyArchive",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECONDS_PER_DAY: float = 86_400.0

_OBSTRUCTION_TO_DOMAIN: dict[ObstructionKind, str] = {
    ObstructionKind.QUANTITATIVE_CONTRADICTION: "numerical_claims",
    ObstructionKind.TEMPORAL_CONTRADICTION: "temporal_claims",
    ObstructionKind.ENTITY_CONTRADICTION: "entity_claims",
    ObstructionKind.DIRECTIONAL_CONTRADICTION: "directional_claims",
    ObstructionKind.LOGICAL_CONTRADICTION: "logical_claims",
    ObstructionKind.DEPENDENCY_CONTRADICTION: "dependency_claims",
    ObstructionKind.TYPE_MISMATCH: "type_claims",
    ObstructionKind.TRUST_BOUNDARY_VIOLATION: "trust_violations",
    ObstructionKind.CASCADING_HALLUCINATION: "hallucination_chains",
    ObstructionKind.PHANTOM_GLOBAL_SECTION: "phantom_sections",
    ObstructionKind.TOOL_HALLUCINATION: "tool_provenance",
}

_DEFAULT_DOMAIN = "general"


def _canonical_pair(a: str, b: str) -> tuple[str, str]:
    """Return the agent pair in sorted order for consistent keying."""
    return (a, b) if a <= b else (b, a)


def _domain_for(kind: ObstructionKind) -> str:
    """Map an obstruction kind to a human-readable domain label."""
    return _OBSTRUCTION_TO_DOMAIN.get(kind, _DEFAULT_DOMAIN)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrictionPattern:
    """A recurring conflict pattern between two agents.

    Patterns are keyed by *(agent_pair, domain, obstruction_kind)* so
    that identical friction sources are grouped across pipelines.
    """

    agent_pair: tuple[str, str]
    domain: str
    obstruction_kind: ObstructionKind
    frequency: int = 0
    examples: list[str] = field(default_factory=list)
    pattern_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass(slots=True)
class ResolutionTemplate:
    """A proven resolution strategy linked to a :class:`FrictionPattern`.

    Templates accumulate win/loss statistics so the memory system can
    recommend the historically most effective strategy for a given
    friction pattern.
    """

    pattern_id: str
    strategy: str
    success_rate: float = 0.0
    total_applications: int = 0
    last_used: float = field(default_factory=time.time)
    description: str = ""

    # Internal counters for incremental success-rate updates.
    _successes: int = field(default=0, repr=False)

    def record_outcome(self, success: bool) -> None:
        """Update running statistics with a new outcome."""
        self.total_applications += 1
        if success:
            self._successes += 1
        self.success_rate = self._successes / self.total_applications
        self.last_used = time.time()


@dataclass(slots=True)
class TreatyMemoryStats:
    """Aggregate statistics for a :class:`TreatyMemory` instance."""

    total_patterns: int = 0
    total_templates: int = 0
    most_conflicted_pair: tuple[str, str] | None = None
    most_common_domain: str = ""
    average_resolution_success_rate: float = 0.0


# ---------------------------------------------------------------------------
# Internal record kept per recorded event
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _RecordedEvent:
    """An individual contradiction-resolution pair stored in memory."""

    contradiction_id: str
    agent_pair: tuple[str, str]
    domain: str
    obstruction_kind: ObstructionKind
    strategy: str
    success: bool
    explanation: str
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# TreatyMemory
# ---------------------------------------------------------------------------


class TreatyMemory:
    """Main in-memory store for cross-pipeline treaty learning.

    Parameters
    ----------
    max_patterns : int
        Maximum number of distinct friction patterns to retain.  When
        the limit is reached the least-recently-seen pattern is evicted.
    max_age_days : float
        Patterns not updated within this window are eligible for
        expiry via :meth:`expire_old`.
    """

    def __init__(
        self,
        max_patterns: int = 1000,
        max_age_days: float = 90,
    ) -> None:
        self._max_patterns = max_patterns
        self._max_age_days = max_age_days

        # Keyed by (agent_pair, domain, obstruction_kind).
        self._patterns: dict[tuple[tuple[str, str], str, ObstructionKind], FrictionPattern] = {}

        # Keyed by (pattern_id, strategy).
        self._templates: dict[tuple[str, str], ResolutionTemplate] = {}

        # Full event log (bounded by pattern eviction).
        self._events: list[_RecordedEvent] = []

        # Fast lookup: agent_pair -> number of recorded events.
        self._pair_event_counts: Counter[tuple[str, str]] = Counter()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        contradiction: Contradiction,
        resolution: TreatyResolution,
        success: bool,
    ) -> None:
        """Record a conflict-resolution pair and update patterns.

        Parameters
        ----------
        contradiction:
            The detected contradiction.
        resolution:
            The treaty resolution that was attempted.
        success:
            Whether the resolution was ultimately accepted.
        """
        pair = _canonical_pair(contradiction.agent_a, contradiction.agent_b)
        domain = _domain_for(contradiction.kind)
        strategy = resolution.strategy_used or "unknown"

        # --- event log ---------------------------------------------------
        event = _RecordedEvent(
            contradiction_id=contradiction.contradiction_id,
            agent_pair=pair,
            domain=domain,
            obstruction_kind=contradiction.kind,
            strategy=strategy,
            success=success,
            explanation=contradiction.explanation,
        )
        self._events.append(event)
        self._pair_event_counts[pair] += 1

        # --- friction pattern --------------------------------------------
        key = (pair, domain, contradiction.kind)
        pattern = self._patterns.get(key)
        if pattern is None:
            pattern = FrictionPattern(
                agent_pair=pair,
                domain=domain,
                obstruction_kind=contradiction.kind,
            )
            self._patterns[key] = pattern
        pattern.frequency += 1
        pattern.last_seen = time.time()
        summary = contradiction.explanation[:120] if contradiction.explanation else str(contradiction.kind)
        if len(pattern.examples) < 20:
            pattern.examples.append(summary)

        # --- resolution template -----------------------------------------
        tpl_key = (pattern.pattern_id, strategy)
        tpl = self._templates.get(tpl_key)
        if tpl is None:
            tpl = ResolutionTemplate(
                pattern_id=pattern.pattern_id,
                strategy=strategy,
                description=f"Strategy '{strategy}' for {domain} conflicts",
            )
            self._templates[tpl_key] = tpl
        tpl.record_outcome(success)

        # --- enforce capacity --------------------------------------------
        self._enforce_capacity()

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def find_patterns(
        self,
        agent_a: str,
        agent_b: str,
        domain: str = "",
    ) -> list[FrictionPattern]:
        """Find friction patterns for the given agent pair.

        Parameters
        ----------
        agent_a, agent_b:
            The two agents.  Order does not matter.
        domain:
            Optional domain filter (e.g. ``"numerical_claims"``).
            When empty, all domains are returned.

        Returns
        -------
        list[FrictionPattern]
            Patterns sorted by descending frequency.
        """
        pair = _canonical_pair(agent_a, agent_b)
        results: list[FrictionPattern] = []
        for (p, d, _k), pat in self._patterns.items():
            if p == pair and (not domain or d == domain):
                results.append(pat)
        results.sort(key=lambda fp: fp.frequency, reverse=True)
        return results

    def best_strategy(self, pattern: FrictionPattern) -> ResolutionTemplate | None:
        """Return the highest-success-rate template for *pattern*.

        Returns ``None`` when no template has been recorded yet.
        """
        candidates = [
            tpl
            for (pid, _s), tpl in self._templates.items()
            if pid == pattern.pattern_id
        ]
        if not candidates:
            return None
        # Primary: success_rate descending.  Secondary: total_applications
        # descending (prefer more battle-tested when rates are equal).
        candidates.sort(
            key=lambda t: (t.success_rate, t.total_applications),
            reverse=True,
        )
        return candidates[0]

    def suggest_preemptive(
        self,
        agent_a: str,
        agent_b: str,
        task_type: str,
    ) -> list[str]:
        """Suggest preemptive constraints based on past friction.

        Analyses historical patterns for the agent pair and produces
        natural-language constraint suggestions that a pipeline
        orchestrator can inject *before* work begins.

        Parameters
        ----------
        agent_a, agent_b:
            The two agents being assigned together.
        task_type:
            A free-form label (e.g. ``"financial_report"``) used to
            weight domain relevance.

        Returns
        -------
        list[str]
            Human-readable constraint suggestions, most important first.
        """
        patterns = self.find_patterns(agent_a, agent_b)
        if not patterns:
            return []

        suggestions: list[str] = []
        for pat in patterns:
            best = self.best_strategy(pat)
            rate = self.conflict_rate(agent_a, agent_b)

            # High-frequency pattern → strong recommendation.
            if pat.frequency >= 5:
                suggestions.append(
                    f"High-friction domain '{pat.domain}' "
                    f"({pat.frequency} past conflicts, "
                    f"pair conflict rate {rate:.0%}). "
                    f"Recommended strategy: "
                    f"{best.strategy if best else 'manual review'}."
                )
            elif pat.frequency >= 2:
                suggestions.append(
                    f"Recurring friction in '{pat.domain}' "
                    f"({pat.frequency}× seen). Consider pre-assigning "
                    f"authoritative source for {pat.domain} claims."
                )

            # If best strategy has poor success, flag it.
            if best and best.success_rate < 0.5 and best.total_applications >= 3:
                suggestions.append(
                    f"Warning: best known strategy '{best.strategy}' "
                    f"for '{pat.domain}' has only "
                    f"{best.success_rate:.0%} success rate "
                    f"({best.total_applications} attempts). "
                    f"Consider human-in-the-loop review."
                )

        # Domain-specific hints derived from task_type.
        task_lower = task_type.lower()
        domain_hints: dict[str, str] = {
            "financial": "numerical_claims",
            "timeline": "temporal_claims",
            "biography": "entity_claims",
            "comparison": "directional_claims",
        }
        for keyword, domain in domain_hints.items():
            if keyword in task_lower:
                domain_patterns = [p for p in patterns if p.domain == domain]
                if domain_patterns:
                    total_freq = sum(p.frequency for p in domain_patterns)
                    suggestions.append(
                        f"Task type '{task_type}' overlaps with known "
                        f"friction domain '{domain}' ({total_freq} "
                        f"historical conflicts). Extra validation "
                        f"recommended."
                    )

        return suggestions

    def conflict_rate(self, agent_a: str, agent_b: str) -> float:
        """Historical conflict rate for the agent pair.

        Returns a value in ``[0, 1]`` representing the fraction of
        events between the two agents that ended in failure.
        Returns ``0.0`` when no events have been recorded.
        """
        pair = _canonical_pair(agent_a, agent_b)
        total = 0
        failures = 0
        for ev in self._events:
            if ev.agent_pair == pair:
                total += 1
                if not ev.success:
                    failures += 1
        if total == 0:
            return 0.0
        return failures / total

    def trending_patterns(
        self,
        window_days: float = 30,
    ) -> list[FrictionPattern]:
        """Return patterns whose frequency is increasing.

        Compares event counts in the recent window against the older
        half of their lifetime.  Patterns with a higher recent rate are
        considered *trending*.

        Parameters
        ----------
        window_days:
            Look-back window for the "recent" period.

        Returns
        -------
        list[FrictionPattern]
            Trending patterns sorted by frequency ratio (descending).
        """
        now = time.time()
        cutoff = now - window_days * _SECONDS_PER_DAY

        # Count events per pattern key in the recent window vs. before.
        recent: Counter[tuple[tuple[str, str], str, ObstructionKind]] = Counter()
        older: Counter[tuple[tuple[str, str], str, ObstructionKind]] = Counter()

        for ev in self._events:
            key = (ev.agent_pair, ev.domain, ev.obstruction_kind)
            if ev.timestamp >= cutoff:
                recent[key] += 1
            else:
                older[key] += 1

        trending: list[tuple[float, FrictionPattern]] = []
        for key, pat in self._patterns.items():
            r = recent.get(key, 0)
            o = older.get(key, 0)
            if r == 0:
                continue
            # Ratio: recent / (older + 1) to avoid division by zero.
            ratio = r / (o + 1)
            if ratio > 1.0 or (o == 0 and r >= 2):
                trending.append((ratio, pat))

        trending.sort(key=lambda t: t[0], reverse=True)
        return [pat for _, pat in trending]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def expire_old(self) -> int:
        """Remove patterns older than *max_age_days*.

        Returns the number of patterns removed.
        """
        cutoff = time.time() - self._max_age_days * _SECONDS_PER_DAY
        expired_keys: list[tuple[tuple[str, str], str, ObstructionKind]] = [
            key
            for key, pat in self._patterns.items()
            if pat.last_seen < cutoff
        ]
        expired_pattern_ids: set[str] = set()
        for key in expired_keys:
            expired_pattern_ids.add(self._patterns[key].pattern_id)
            del self._patterns[key]

        # Remove associated templates.
        tpl_keys_to_remove = [
            tkey
            for tkey in self._templates
            if tkey[0] in expired_pattern_ids
        ]
        for tkey in tpl_keys_to_remove:
            del self._templates[tkey]

        # Prune events that belong to expired patterns.
        self._events = [
            ev for ev in self._events
            if (ev.agent_pair, ev.domain, ev.obstruction_kind) in self._patterns
        ]

        # Rebuild pair counts.
        self._pair_event_counts.clear()
        for ev in self._events:
            self._pair_event_counts[ev.agent_pair] += 1

        return len(expired_keys)

    # ------------------------------------------------------------------
    # Statistics & serialisation
    # ------------------------------------------------------------------

    def stats(self) -> TreatyMemoryStats:
        """Return aggregate statistics over the current memory."""
        templates = list(self._templates.values())
        total_patterns = len(self._patterns)
        total_templates = len(templates)

        # Most conflicted pair.
        most_conflicted: tuple[str, str] | None = None
        if self._pair_event_counts:
            most_conflicted = self._pair_event_counts.most_common(1)[0][0]

        # Most common domain.
        domain_counts: Counter[str] = Counter()
        for (_p, d, _k) in self._patterns:
            domain_counts[d] += 1
        most_common_domain = domain_counts.most_common(1)[0][0] if domain_counts else ""

        # Average resolution success rate.
        avg_rate = 0.0
        if templates:
            avg_rate = sum(t.success_rate for t in templates) / len(templates)

        return TreatyMemoryStats(
            total_patterns=total_patterns,
            total_templates=total_templates,
            most_conflicted_pair=most_conflicted,
            most_common_domain=most_common_domain,
            average_resolution_success_rate=avg_rate,
        )

    def export(self) -> dict:
        """Return a JSON-serialisable snapshot of the full memory.

        The structure is:

        .. code-block:: python

            {
                "patterns": [...],
                "templates": [...],
                "events": [...],
                "exported_at": <float>,
            }
        """
        return {
            "patterns": [
                {
                    "pattern_id": p.pattern_id,
                    "agent_pair": list(p.agent_pair),
                    "domain": p.domain,
                    "obstruction_kind": p.obstruction_kind.name,
                    "frequency": p.frequency,
                    "examples": p.examples[:],
                    "first_seen": p.first_seen,
                    "last_seen": p.last_seen,
                }
                for p in self._patterns.values()
            ],
            "templates": [
                {
                    "pattern_id": t.pattern_id,
                    "strategy": t.strategy,
                    "success_rate": t.success_rate,
                    "total_applications": t.total_applications,
                    "last_used": t.last_used,
                    "description": t.description,
                    "successes": t._successes,
                }
                for t in self._templates.values()
            ],
            "events": [
                {
                    "contradiction_id": e.contradiction_id,
                    "agent_pair": list(e.agent_pair),
                    "domain": e.domain,
                    "obstruction_kind": e.obstruction_kind.name,
                    "strategy": e.strategy,
                    "success": e.success,
                    "explanation": e.explanation,
                    "timestamp": e.timestamp,
                }
                for e in self._events
            ],
            "exported_at": time.time(),
        }

    def import_data(self, data: dict) -> None:
        """Import memory state from a previously exported dict.

        Merges into existing memory rather than replacing it, so that
        imports from multiple sources can be combined.
        """
        kind_map = {k.name: k for k in ObstructionKind}

        for pd in data.get("patterns", []):
            ok = kind_map[pd["obstruction_kind"]]
            pair = tuple(pd["agent_pair"])  # type: ignore[arg-type]
            key = (pair, pd["domain"], ok)
            if key in self._patterns:
                existing = self._patterns[key]
                existing.frequency += pd["frequency"]
                existing.examples.extend(pd.get("examples", []))
                existing.examples = existing.examples[:20]
                existing.last_seen = max(existing.last_seen, pd.get("last_seen", 0.0))
            else:
                self._patterns[key] = FrictionPattern(
                    pattern_id=pd["pattern_id"],
                    agent_pair=pair,  # type: ignore[arg-type]
                    domain=pd["domain"],
                    obstruction_kind=ok,
                    frequency=pd["frequency"],
                    examples=pd.get("examples", [])[:20],
                    first_seen=pd.get("first_seen", time.time()),
                    last_seen=pd.get("last_seen", time.time()),
                )

        for td in data.get("templates", []):
            tkey = (td["pattern_id"], td["strategy"])
            if tkey in self._templates:
                existing_t = self._templates[tkey]
                existing_t.total_applications += td["total_applications"]
                existing_t._successes += td.get("successes", 0)
                if existing_t.total_applications > 0:
                    existing_t.success_rate = (
                        existing_t._successes / existing_t.total_applications
                    )
                existing_t.last_used = max(existing_t.last_used, td.get("last_used", 0.0))
            else:
                self._templates[tkey] = ResolutionTemplate(
                    pattern_id=td["pattern_id"],
                    strategy=td["strategy"],
                    success_rate=td.get("success_rate", 0.0),
                    total_applications=td.get("total_applications", 0),
                    last_used=td.get("last_used", time.time()),
                    description=td.get("description", ""),
                    _successes=td.get("successes", 0),
                )

        for ed in data.get("events", []):
            ok = kind_map[ed["obstruction_kind"]]
            pair = tuple(ed["agent_pair"])  # type: ignore[arg-type]
            self._events.append(
                _RecordedEvent(
                    contradiction_id=ed["contradiction_id"],
                    agent_pair=pair,  # type: ignore[arg-type]
                    domain=ed["domain"],
                    obstruction_kind=ok,
                    strategy=ed["strategy"],
                    success=ed["success"],
                    explanation=ed.get("explanation", ""),
                    timestamp=ed.get("timestamp", time.time()),
                )
            )
            self._pair_event_counts[pair] += 1  # type: ignore[arg-type]

        self._enforce_capacity()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enforce_capacity(self) -> None:
        """Evict least-recently-seen patterns if over capacity."""
        while len(self._patterns) > self._max_patterns:
            # Find the pattern with the oldest last_seen.
            oldest_key = min(
                self._patterns,
                key=lambda k: self._patterns[k].last_seen,
            )
            pid = self._patterns[oldest_key].pattern_id
            del self._patterns[oldest_key]
            # Clean up associated templates.
            tpl_keys = [tk for tk in self._templates if tk[0] == pid]
            for tk in tpl_keys:
                del self._templates[tk]


# ---------------------------------------------------------------------------
# TreatyArchive — long-term treaty evolution tracking
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ArchiveEntry:
    """Single entry in the treaty archive."""

    resolution_id: str
    pipeline_id: str
    agent_pair: tuple[str, str]
    strategy: str
    success: bool
    evidence: str
    merged_text: str
    timestamp: float


class TreatyArchive:
    """Long-term storage tracking how treaties between agent pairs evolve.

    Unlike :class:`TreatyMemory` (which focuses on friction patterns
    and strategies), the archive preserves the full treaty resolution
    timeline so that drift and evolution can be analysed.
    """

    def __init__(self) -> None:
        self._entries: list[_ArchiveEntry] = []
        # Fast index: agent_pair -> list of entry indices.
        self._pair_index: dict[tuple[str, str], list[int]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_treaty(
        self,
        resolution: TreatyResolution,
        pipeline_id: str,
    ) -> None:
        """Archive a treaty resolution with its pipeline context.

        Parameters
        ----------
        resolution:
            The treaty resolution to archive.
        pipeline_id:
            Identifier of the pipeline run that produced this treaty.
        """
        pair = _canonical_pair(
            resolution.winning_agent,
            resolution.winning_agent,  # fallback when only winner is known
        )
        # Try to extract the pair from the audit trail.
        if len(resolution.audit_trail) >= 2:
            agents = []
            for entry in resolution.audit_trail:
                if entry.startswith("agent:"):
                    agents.append(entry.removeprefix("agent:").strip())
            if len(agents) >= 2:
                pair = _canonical_pair(agents[0], agents[1])

        entry = _ArchiveEntry(
            resolution_id=resolution.resolution_id,
            pipeline_id=pipeline_id,
            agent_pair=pair,
            strategy=resolution.strategy_used or "unknown",
            success=resolution.success,
            evidence=resolution.evidence,
            merged_text=resolution.merged_text,
            timestamp=resolution.timestamp,
        )
        idx = len(self._entries)
        self._entries.append(entry)
        self._pair_index[pair].append(idx)

    # ------------------------------------------------------------------
    # Evolution analysis
    # ------------------------------------------------------------------

    def treaty_evolution(
        self,
        agent_pair: tuple[str, str],
    ) -> list[dict]:
        """Return the chronological evolution of treaties for a pair.

        Each dict contains:

        * ``resolution_id``
        * ``pipeline_id``
        * ``strategy``
        * ``success``
        * ``evidence`` (truncated)
        * ``timestamp``
        * ``phase`` — ``"early"`` / ``"middle"`` / ``"recent"`` based
          on position in the timeline.

        Returns
        -------
        list[dict]
            Sorted oldest-first.
        """
        pair = _canonical_pair(*agent_pair)
        indices = self._pair_index.get(pair, [])
        if not indices:
            return []

        entries = [self._entries[i] for i in indices]
        entries.sort(key=lambda e: e.timestamp)

        n = len(entries)
        result: list[dict] = []
        for i, entry in enumerate(entries):
            if n <= 3:
                phase = "recent"
            elif i < n // 3:
                phase = "early"
            elif i < 2 * n // 3:
                phase = "middle"
            else:
                phase = "recent"

            result.append({
                "resolution_id": entry.resolution_id,
                "pipeline_id": entry.pipeline_id,
                "strategy": entry.strategy,
                "success": entry.success,
                "evidence": entry.evidence[:200],
                "timestamp": entry.timestamp,
                "phase": phase,
            })
        return result

    # ------------------------------------------------------------------
    # System health
    # ------------------------------------------------------------------

    def system_health(self, window_days: float = 30) -> dict:
        """Overall system health metrics for the given window.

        Returns
        -------
        dict
            Keys:

            * ``total_treaties`` — total archived treaties.
            * ``window_treaties`` — treaties in the look-back window.
            * ``conflict_rate_trend`` — ``"improving"`` /
              ``"stable"`` / ``"worsening"`` based on success rates
              in the first vs. second half of the window.
            * ``resolution_success_trend`` — same as above for
              resolution success.
            * ``unique_pairs`` — number of distinct agent pairs.
            * ``most_active_pair`` — pair with most treaties in window.
            * ``strategy_diversity`` — number of distinct strategies
              used in the window.
        """
        now = time.time()
        cutoff = now - window_days * _SECONDS_PER_DAY
        midpoint = now - (window_days / 2) * _SECONDS_PER_DAY

        window_entries = [e for e in self._entries if e.timestamp >= cutoff]
        first_half = [e for e in window_entries if e.timestamp < midpoint]
        second_half = [e for e in window_entries if e.timestamp >= midpoint]

        def _success_rate(entries: list[_ArchiveEntry]) -> float:
            if not entries:
                return 0.0
            return sum(1 for e in entries if e.success) / len(entries)

        rate_first = _success_rate(first_half)
        rate_second = _success_rate(second_half)
        delta = rate_second - rate_first

        if delta > 0.05:
            trend = "improving"
        elif delta < -0.05:
            trend = "worsening"
        else:
            trend = "stable"

        # Most active pair in window.
        pair_counts: Counter[tuple[str, str]] = Counter()
        strategies: set[str] = set()
        for e in window_entries:
            pair_counts[e.agent_pair] += 1
            strategies.add(e.strategy)

        most_active = pair_counts.most_common(1)[0][0] if pair_counts else None

        return {
            "total_treaties": len(self._entries),
            "window_treaties": len(window_entries),
            "conflict_rate_trend": trend,
            "resolution_success_trend": trend,
            "unique_pairs": len(pair_counts),
            "most_active_pair": most_active,
            "strategy_diversity": len(strategies),
        }
