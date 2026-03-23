"""Obstruction mining stage (S01) of the kind-discovery pipeline.

This module implements the first stage of the kind-discovery pipeline:
extracting, clustering, and analyzing obstructions from free-form text or
``Idea`` objects.  The pipeline proceeds in four steps:

1. **Extract** — :class:`ObstructionExtractor` pulls (phrase, type) pairs from
   text using keyword heuristics.
2. **Cluster** — :class:`ObstructionClusterer` groups similar phrases together.
3. **Build fields** — :class:`ObstructionFieldBuilder` converts clusters into
   :class:`~jugeo.ideation.kind_discovery.models.ObstructionField` instances.
4. **Analyze frequencies** — :class:`FrequencyAnalyzer` tracks how often each
   obstruction appears.

The :class:`ObstructionMiner` orchestrates the full pipeline and exposes
convenience entry points such as :meth:`~ObstructionMiner.mine` and
:meth:`~ObstructionMiner.mine_from_ideas`.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from dataclasses import dataclass
from typing import Any

from jugeo.ideation.kind_discovery.models import (
    KindCandidate,
    KindStatus,
    ObstructionField,
    ObstructionType,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Split *text* into lowercase alphabetic tokens."""
    return [tok.lower() for tok in re.findall(r"[A-Za-z]+", text) if tok]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ---------------------------------------------------------------------------
# ObstructionMiningConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObstructionMiningConfig:
    """Configuration for the obstruction-mining pipeline.

    All fields have sensible defaults so callers only need to override what
    they care about.
    """

    min_frequency: int = 2
    min_confidence: float = 0.3
    max_cluster_size: int = 20
    similarity_threshold: float = 0.4
    include_algebraic: bool = True
    include_structural: bool = True
    include_semantic: bool = True
    include_computational: bool = True
    include_logical: bool = True
    max_obstructions_per_field: int = 50
    field_coherence_threshold: float = 0.3

    # ------------------------------------------------------------------
    # Derived accessors
    # ------------------------------------------------------------------

    def obstruction_types(self) -> list[ObstructionType]:
        """Return all :class:`ObstructionType` values whose ``include_X`` flag is *True*."""
        mapping = {
            ObstructionType.ALGEBRAIC: self.include_algebraic,
            ObstructionType.STRUCTURAL: self.include_structural,
            ObstructionType.SEMANTIC: self.include_semantic,
            ObstructionType.COMPUTATIONAL: self.include_computational,
            ObstructionType.LOGICAL: self.include_logical,
        }
        return [t for t, enabled in mapping.items() if enabled]

    def is_type_included(self, t: ObstructionType) -> bool:
        """Return *True* iff *t* is in :meth:`obstruction_types`."""
        return t in self.obstruction_types()

    def with_min_frequency(self, n: int) -> ObstructionMiningConfig:
        """Return a copy of this config with ``min_frequency`` set to *n*."""
        return dataclasses.replace(self, min_frequency=n)

    def to_dict(self) -> dict[str, Any]:
        """Serialize all fields to a plain dict."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ObstructionMiningConfig:
        """Deserialize from a plain dict produced by :meth:`to_dict`."""
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in dataclasses.fields(cls)}})


# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

_OBSTRUCTION_TRIGGER_WORDS: frozenset[str] = frozenset({
    "cannot", "impossible", "fails", "prevents", "blocks", "obstructs",
    "contradicts", "incompatible", "inconsistent", "undefined", "intractable",
    "failure", "blocked", "obstruction", "prevent", "block", "fail",
    "contradict", "incompatibility", "inconsistency",
})

_TYPE_KEYWORDS: dict[ObstructionType, frozenset[str]] = {
    ObstructionType.STRUCTURAL: frozenset({
        "structure", "form", "shape", "topology", "morphism", "category",
        "structural", "topological",
    }),
    ObstructionType.SEMANTIC: frozenset({
        "meaning", "ambiguous", "undefined", "interpretation", "semantic",
        "semantics", "ambiguity", "vague",
    }),
    ObstructionType.COMPUTATIONAL: frozenset({
        "compute", "algorithm", "complexity", "intractable", "np",
        "resource", "computational", "computable", "decidable", "decidability",
        "undecidable",
    }),
    ObstructionType.LOGICAL: frozenset({
        "contradiction", "inconsistent", "paradox", "logic", "proof", "axiom",
        "logical", "inconsistency", "contradictory", "tautology",
    }),
    ObstructionType.ALGEBRAIC: frozenset({
        "algebra", "ring", "group", "field", "module", "morphism",
        "homomorphism", "algebraic", "isomorphism", "endomorphism",
    }),
}


# ---------------------------------------------------------------------------
# ObstructionExtractor
# ---------------------------------------------------------------------------

class ObstructionExtractor:
    """Extract (phrase, :class:`ObstructionType`) pairs from free-form text."""

    def __init__(self, config: ObstructionMiningConfig | None = None) -> None:
        self._config = config or ObstructionMiningConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, text: str, domain: str = "") -> list[tuple[str, ObstructionType]]:
        """Extract obstruction (phrase, type) pairs from *text*.

        Uses heuristics: sentences / clauses containing trigger words are
        selected as candidate phrases, then each phrase is classified via
        :meth:`_classify_obstruction`.  Empty text returns ``[]``.
        """
        if not text or not text.strip():
            return []

        pairs: list[tuple[str, ObstructionType]] = []
        # Split text into candidate units (sentences / clauses).
        segments = re.split(r"[.;!?\n]+", text)
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            tokens_lower = set(_tokenize(segment))
            if tokens_lower & _OBSTRUCTION_TRIGGER_WORDS:
                t = self._classify_obstruction(segment)
                if self._config.is_type_included(t):
                    pairs.append((segment, t))

        return pairs

    def extract_batch(
        self, texts: list[str], domain: str = ""
    ) -> list[tuple[str, ObstructionType]]:
        """Call :meth:`extract` on each text, concatenate, then deduplicate."""
        combined: list[tuple[str, ObstructionType]] = []
        for text in texts:
            combined.extend(self.extract(text, domain=domain))
        return self._deduplicate(combined)

    def extract_from_idea(self, idea: Any) -> list[tuple[str, ObstructionType]]:
        """Extract from ``idea.hypothesis`` + ``idea.purpose``."""
        parts: list[str] = []
        hypothesis = getattr(idea, "hypothesis", None)
        purpose = getattr(idea, "purpose", None)
        if hypothesis:
            parts.append(str(hypothesis))
        if purpose:
            parts.append(str(purpose))
        return self.extract_batch(parts)

    def classify_all(self, texts: list[str]) -> dict[str, ObstructionType]:
        """Return a dict mapping each unique phrase (from :meth:`extract_batch`) to its type."""
        pairs = self.extract_batch(texts)
        return {phrase: t for phrase, t in pairs}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_obstruction(self, phrase: str) -> ObstructionType:
        """Classify *phrase* into an :class:`ObstructionType` using keyword heuristics.

        Priority order: ALGEBRAIC > LOGICAL > COMPUTATIONAL > SEMANTIC > STRUCTURAL.
        Defaults to STRUCTURAL.
        """
        tokens = set(_tokenize(phrase))
        priority_order = [
            ObstructionType.ALGEBRAIC,
            ObstructionType.LOGICAL,
            ObstructionType.COMPUTATIONAL,
            ObstructionType.SEMANTIC,
            ObstructionType.STRUCTURAL,
        ]
        for t in priority_order:
            if tokens & _TYPE_KEYWORDS[t]:
                return t
        return ObstructionType.STRUCTURAL

    def _score_phrase(self, phrase: str) -> float:
        """Return a float in [0, 1] based on phrase length and keyword density.

        Longer phrases with more obstruction keywords score higher.
        """
        tokens = _tokenize(phrase)
        if not tokens:
            return 0.0
        all_keywords = _OBSTRUCTION_TRIGGER_WORDS.union(
            *(kws for kws in _TYPE_KEYWORDS.values())
        )
        keyword_count = sum(1 for t in tokens if t in all_keywords)
        keyword_density = keyword_count / len(tokens)
        # Scale: keyword_density in [0,1], length bonus capped at 1.
        length_bonus = min(len(tokens) / 20.0, 1.0)
        score = 0.7 * keyword_density + 0.3 * length_bonus
        return min(max(score, 0.0), 1.0)

    def _deduplicate(
        self, pairs: list[tuple[str, ObstructionType]]
    ) -> list[tuple[str, ObstructionType]]:
        """Remove exact duplicate (phrase, type) pairs, preserving first occurrence."""
        seen: set[tuple[str, ObstructionType]] = set()
        result: list[tuple[str, ObstructionType]] = []
        for pair in pairs:
            if pair not in seen:
                seen.add(pair)
                result.append(pair)
        return result


# ---------------------------------------------------------------------------
# ObstructionClusterer
# ---------------------------------------------------------------------------

class ObstructionClusterer:
    """Group similar obstruction (phrase, type) pairs into clusters."""

    def __init__(self, config: ObstructionMiningConfig | None = None) -> None:
        self._config = config or ObstructionMiningConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cluster(
        self, obstructions: list[tuple[str, ObstructionType]]
    ) -> list[list[tuple[str, ObstructionType]]]:
        """Greedy single-link clustering by token Jaccard similarity.

        For each obstruction, it is added to the first existing cluster where
        any member has pairwise similarity >= ``config.similarity_threshold``.
        If no such cluster exists, a new cluster is started.
        """
        threshold = self._config.similarity_threshold
        clusters: list[list[tuple[str, ObstructionType]]] = []

        for obs in obstructions:
            placed = False
            for cluster in clusters:
                for member in cluster:
                    if self._pairwise_similarity(obs, member) >= threshold:
                        cluster.append(obs)
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                clusters.append([obs])

        return clusters

    def _pairwise_similarity(
        self,
        a: tuple[str, ObstructionType],
        b: tuple[str, ObstructionType],
    ) -> float:
        """Jaccard token similarity of the phrase strings of *a* and *b*."""
        tokens_a = frozenset(_tokenize(a[0]))
        tokens_b = frozenset(_tokenize(b[0]))
        return _jaccard(tokens_a, tokens_b)

    def merge_small_clusters(
        self,
        clusters: list[list[tuple[str, ObstructionType]]],
        min_size: int = 2,
    ) -> list[list[tuple[str, ObstructionType]]]:
        """Merge clusters smaller than *min_size* into the largest cluster.

        If there is no other cluster to merge into, small clusters are kept
        as-is.
        """
        if not clusters:
            return clusters

        large = [c for c in clusters if len(c) >= min_size]
        small = [c for c in clusters if len(c) < min_size]

        if not small:
            return large if large else clusters

        if not large:
            # No large cluster; keep everything as-is.
            return clusters

        # Find the largest cluster.
        largest = max(large, key=len)
        for sc in small:
            largest.extend(sc)

        return large  # largest is mutated in-place, already in `large`

    def split_large_clusters(
        self,
        clusters: list[list[tuple[str, ObstructionType]]],
        max_size: int | None = None,
    ) -> list[list[tuple[str, ObstructionType]]]:
        """Split clusters larger than *max_size* by halving them recursively."""
        effective_max = max_size if max_size is not None else self._config.max_cluster_size

        result: list[list[tuple[str, ObstructionType]]] = []
        for cluster in clusters:
            result.extend(self._split_one(cluster, effective_max))
        return result

    def cluster_summary(
        self, clusters: list[list[tuple[str, ObstructionType]]]
    ) -> dict[str, Any]:
        """Return a summary dict for a list of clusters."""
        sizes = [len(c) for c in clusters]
        total = sum(sizes)
        avg = total / len(sizes) if sizes else 0.0
        return {
            "num_clusters": len(clusters),
            "cluster_sizes": sizes,
            "total_obstructions": total,
            "avg_size": avg,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_one(
        self,
        cluster: list[tuple[str, ObstructionType]],
        max_size: int,
    ) -> list[list[tuple[str, ObstructionType]]]:
        """Recursively halve *cluster* until each part is <= *max_size*."""
        if len(cluster) <= max_size:
            return [cluster]
        mid = len(cluster) // 2
        left = cluster[:mid]
        right = cluster[mid:]
        return self._split_one(left, max_size) + self._split_one(right, max_size)


# ---------------------------------------------------------------------------
# ObstructionFieldBuilder
# ---------------------------------------------------------------------------

class ObstructionFieldBuilder:
    """Convert obstruction clusters into :class:`ObstructionField` instances."""

    def __init__(self, config: ObstructionMiningConfig | None = None) -> None:
        self._config = config or ObstructionMiningConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        cluster: list[tuple[str, ObstructionType]],
        domain: str = "",
        field_id: str | None = None,
    ) -> ObstructionField:
        """Build an :class:`ObstructionField` from a cluster."""
        fid = field_id if field_id is not None else str(uuid.uuid4())
        phrases = tuple(phrase for phrase, _ in cluster)
        sem_density = self.compute_semantic_density(cluster)
        coherence = self.compute_coherence(cluster)
        types = self.assign_types(cluster)
        return ObstructionField(
            field_id=fid,
            domain=domain,
            obstructions=phrases,
            semantic_density=sem_density,
            coherence_score=coherence,
            obstruction_types=types,
            created_at=_now_iso(),
            metadata={},
        )

    def build_all(
        self,
        clusters: list[list[tuple[str, ObstructionType]]],
        domain: str = "",
    ) -> list[ObstructionField]:
        """Call :meth:`build` on each cluster and return the list of fields."""
        return [self.build(cluster, domain=domain) for cluster in clusters]

    def compute_semantic_density(
        self, obstructions: list[tuple[str, ObstructionType]]
    ) -> float:
        """Compute semantic density as ``unique_tokens / (total_tokens + 1)``.

        Result is clamped to [0, 1].
        """
        all_tokens: list[str] = []
        for phrase, _ in obstructions:
            all_tokens.extend(_tokenize(phrase))
        total = len(all_tokens)
        unique = len(set(all_tokens))
        return min(max(unique / (total + 1), 0.0), 1.0)

    def compute_coherence(
        self, obstructions: list[tuple[str, ObstructionType]]
    ) -> float:
        """Average pairwise Jaccard similarity of phrase token sets.

        Returns 1.0 if there is at most one obstruction.
        """
        if len(obstructions) <= 1:
            return 1.0
        token_sets = [frozenset(_tokenize(phrase)) for phrase, _ in obstructions]
        scores: list[float] = []
        n = len(token_sets)
        for i in range(n):
            for j in range(i + 1, n):
                scores.append(_jaccard(token_sets[i], token_sets[j]))
        if not scores:
            return 1.0
        return sum(scores) / len(scores)

    def assign_types(
        self, obstructions: list[tuple[str, ObstructionType]]
    ) -> tuple[ObstructionType, ...]:
        """Return a tuple of the type from each (phrase, type) pair."""
        return tuple(t for _, t in obstructions)

    def filter_by_coherence(
        self,
        fields: list[ObstructionField],
        threshold: float | None = None,
    ) -> list[ObstructionField]:
        """Return only fields with ``coherence_score >= threshold``."""
        effective = threshold if threshold is not None else self._config.field_coherence_threshold
        return [f for f in fields if f.coherence_score >= effective]

    def merge_related_fields(
        self, fields: list[ObstructionField]
    ) -> list[ObstructionField]:
        """Merge fields whose phrase-token universes have Jaccard overlap >= 0.5.

        Each merge keeps the first field's ``field_id`` and ``domain``.
        """
        if not fields:
            return fields

        def _token_universe(f: ObstructionField) -> frozenset[str]:
            tokens: set[str] = set()
            for phrase in f.obstructions:
                tokens.update(_tokenize(phrase))
            return frozenset(tokens)

        universes = [_token_universe(f) for f in fields]
        merged_flags = [False] * len(fields)
        result: list[ObstructionField] = []

        for i, fi in enumerate(fields):
            if merged_flags[i]:
                continue
            combined_obstructions = list(fi.obstructions)
            combined_types = list(fi.obstruction_types)
            for j in range(i + 1, len(fields)):
                if merged_flags[j]:
                    continue
                if _jaccard(universes[i], universes[j]) >= 0.5:
                    combined_obstructions.extend(fields[j].obstructions)
                    combined_types.extend(fields[j].obstruction_types)
                    merged_flags[j] = True
            # Rebuild a merged field using first field's id and domain.
            merged = ObstructionField(
                field_id=fi.field_id,
                domain=fi.domain,
                obstructions=tuple(combined_obstructions),
                semantic_density=fi.semantic_density,
                coherence_score=fi.coherence_score,
                obstruction_types=tuple(combined_types),
                created_at=fi.created_at,
                metadata=fi.metadata,
            )
            result.append(merged)

        return result


# ---------------------------------------------------------------------------
# FrequencyAnalyzer
# ---------------------------------------------------------------------------

class FrequencyAnalyzer:
    """Track obstruction frequencies and co-occurrences across domains."""

    def __init__(self, config: ObstructionMiningConfig | None = None) -> None:
        self._config = config or ObstructionMiningConfig()
        # (obstruction, domain) -> weight sum
        self._counts: dict[tuple[str, str], float] = {}
        # co-occurrence: (a, b) -> number of batches both appeared in (a < b lexicographically)
        self._co_occurrence: dict[tuple[str, str], int] = {}
        self._total_batches: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, obstruction: str, domain: str = "", weight: float = 1.0) -> None:
        """Add *weight* to *obstruction*'s frequency for *domain*."""
        key = (obstruction, domain)
        self._counts[key] = self._counts.get(key, 0.0) + weight

    def record_batch(
        self,
        obstructions: list[tuple[str, ObstructionType]],
        domain: str = "",
    ) -> None:
        """Call :meth:`record` for each phrase, and update co-occurrence counts."""
        phrases = [phrase for phrase, _ in obstructions]
        for phrase in phrases:
            self.record(phrase, domain=domain)
        # Update co-occurrence for all pairs in this batch.
        self._total_batches += 1
        unique_phrases = list(dict.fromkeys(phrases))  # deduplicated, ordered
        for i in range(len(unique_phrases)):
            for j in range(i + 1, len(unique_phrases)):
                a, b = sorted([unique_phrases[i], unique_phrases[j]])
                co_key = (a, b)
                self._co_occurrence[co_key] = self._co_occurrence.get(co_key, 0) + 1

    def frequency(self, obstruction: str) -> float:
        """Return total weight for *obstruction* across all domains."""
        return sum(
            w for (obs, _domain), w in self._counts.items() if obs == obstruction
        )

    def top_k(
        self, k: int = 20, domain: str | None = None
    ) -> list[tuple[str, float]]:
        """Return top *k* (obstruction, freq) pairs sorted descending by frequency.

        If *domain* is not ``None``, only counts for that domain are summed.
        """
        agg: dict[str, float] = {}
        for (obs, d), w in self._counts.items():
            if domain is not None and d != domain:
                continue
            agg[obs] = agg.get(obs, 0.0) + w
        sorted_pairs = sorted(agg.items(), key=lambda x: x[1], reverse=True)
        return sorted_pairs[:k]

    def domain_distribution(self) -> dict[str, dict[str, float]]:
        """Return ``{domain: {obstruction: freq, ...}, ...}``."""
        dist: dict[str, dict[str, float]] = {}
        for (obs, domain), w in self._counts.items():
            dist.setdefault(domain, {})[obs] = dist.get(domain, {}).get(obs, 0.0) + w
        return dist

    def rare_obstructions(self, threshold: float = 2.0) -> list[str]:
        """Return obstructions with total frequency < *threshold*."""
        agg = self._aggregate_all()
        return [obs for obs, freq in agg.items() if freq < threshold]

    def common_obstructions(self, threshold: float = 10.0) -> list[str]:
        """Return obstructions with total frequency >= *threshold*."""
        agg = self._aggregate_all()
        return [obs for obs, freq in agg.items() if freq >= threshold]

    def normalize_frequencies(self) -> dict[str, float]:
        """Return ``{obstruction: freq / max_freq}`` for all obstructions.

        Returns ``{}`` if there is no data.  All values are in [0, 1].
        """
        agg = self._aggregate_all()
        if not agg:
            return {}
        max_freq = max(agg.values())
        if max_freq == 0.0:
            return {obs: 0.0 for obs in agg}
        return {obs: freq / max_freq for obs, freq in agg.items()}

    def co_occurrence_score(self, a: str, b: str) -> float:
        """Return fraction of batches where both *a* and *b* appeared.

        Returns 0.0 if they never co-occurred or no batches were recorded.
        """
        if self._total_batches == 0:
            return 0.0
        key = tuple(sorted([a, b]))
        count = self._co_occurrence.get(key, 0)  # type: ignore[arg-type]
        return count / self._total_batches

    def snapshot(self) -> dict[str, Any]:
        """Return a summary snapshot of current state."""
        agg = self._aggregate_all()
        total_weight = sum(agg.values())
        domains = list({d for (_, d) in self._counts})
        top_10 = sorted(agg.items(), key=lambda x: x[1], reverse=True)[:10]
        return {
            "total_obstructions": len(agg),
            "total_weight": total_weight,
            "domains": domains,
            "top_10": top_10,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _aggregate_all(self) -> dict[str, float]:
        """Return {obstruction: total_weight_across_all_domains}."""
        agg: dict[str, float] = {}
        for (obs, _domain), w in self._counts.items():
            agg[obs] = agg.get(obs, 0.0) + w
        return agg


# ---------------------------------------------------------------------------
# ObstructionMiner
# ---------------------------------------------------------------------------

class ObstructionMiner:
    """Orchestrate the full obstruction-mining pipeline."""

    def __init__(self, config: ObstructionMiningConfig | None = None) -> None:
        self._config = config or ObstructionMiningConfig()
        self._extractor = ObstructionExtractor(self._config)
        self._clusterer = ObstructionClusterer(self._config)
        self._builder = ObstructionFieldBuilder(self._config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mine(
        self,
        texts: list[str],
        domain: str = "",
    ) -> tuple[list[ObstructionField], FrequencyAnalyzer]:
        """Full pipeline: extract → cluster → build fields → record frequencies.

        Returns ``(fields, analyzer)``.
        """
        pairs = self._extractor.extract_batch(texts, domain=domain)
        clusters = self._clusterer.cluster(pairs)
        fields = self._builder.build_all(clusters, domain=domain)
        analyzer = FrequencyAnalyzer(self._config)
        analyzer.record_batch(pairs, domain=domain)
        return fields, analyzer

    def mine_from_ideas(
        self, ideas: Any
    ) -> tuple[list[ObstructionField], FrequencyAnalyzer]:
        """Extract from each idea's ``hypothesis`` + ``purpose``, grouped by ``target_area``.

        Returns ``(fields, analyzer)``.
        """
        all_pairs: list[tuple[str, ObstructionType]] = []
        domain_map: dict[str, list[tuple[str, ObstructionType]]] = {}
        analyzer = FrequencyAnalyzer(self._config)

        for idea in ideas:
            domain = str(getattr(idea, "target_area", "") or "")
            pairs = self._extractor.extract_from_idea(idea)
            all_pairs.extend(pairs)
            domain_map.setdefault(domain, []).extend(pairs)
            analyzer.record_batch(pairs, domain=domain)

        clusters = self._clusterer.cluster(all_pairs)
        # Assign domain based on majority vote among cluster members.
        fields: list[ObstructionField] = []
        for cluster in clusters:
            # Use first non-empty domain found in cluster phrases.
            cluster_domain = ""
            for phrase, _ in cluster:
                for d, dpairs in domain_map.items():
                    if any(p == phrase for p, _ in dpairs):
                        cluster_domain = d
                        break
                if cluster_domain:
                    break
            fields.append(self._builder.build(cluster, domain=cluster_domain))

        return fields, analyzer

    def mine_incremental(
        self,
        new_texts: list[str],
        existing_fields: list[ObstructionField],
        domain: str = "",
    ) -> list[ObstructionField]:
        """Mine *new_texts* and merge results with *existing_fields*.

        Uses :meth:`ObstructionFieldBuilder.merge_related_fields` logic.
        """
        new_fields, _ = self.mine(new_texts, domain=domain)
        combined = existing_fields + new_fields
        return self._builder.merge_related_fields(combined)

    def extract_kind_candidates(
        self,
        fields: list[ObstructionField],
        analyzer: FrequencyAnalyzer,
    ) -> list[KindCandidate]:
        """Create a :class:`KindCandidate` for each sufficiently coherent field.

        Fields with ``coherence_score >= config.min_confidence`` are included.
        """
        candidates: list[KindCandidate] = []
        for field in fields:
            if field.coherence_score < self._config.min_confidence:
                continue
            pattern = field.obstructions[0] if field.obstructions else ""
            freq_val = analyzer.frequency(pattern) if pattern else 1.0
            freq_int = max(1, int(round(freq_val)))
            candidate = KindCandidate(
                candidate_id=f"{field.field_id}_cand",
                name=field.domain or field.field_id,
                description=(
                    f"Candidate derived from obstruction field '{field.field_id}' "
                    f"in domain '{field.domain}'."
                ),
                obstruction_pattern=pattern,
                frequency=freq_int,
                confidence=field.coherence_score,
                evidence_sources=field.obstructions,
                status=KindStatus.CANDIDATE,
                created_at=_now_iso(),
                tags=frozenset(field.domain.split()) if field.domain else frozenset(),
            )
            candidates.append(candidate)
        return candidates

    def full_report(
        self,
        fields: list[ObstructionField],
        analyzer: FrequencyAnalyzer,
    ) -> dict[str, Any]:
        """Return a structured summary dict for the mining results."""
        total_obs = sum(len(f.obstructions) for f in fields)
        domains = list({f.domain for f in fields})
        top_obs = analyzer.top_k(k=10)
        coherent = sum(
            1 for f in fields if f.coherence_score >= self._config.field_coherence_threshold
        )
        return {
            "num_fields": len(fields),
            "total_obstructions": total_obs,
            "domains": domains,
            "top_obstructions": top_obs,
            "coherent_fields": coherent,
            "snapshot": analyzer.snapshot(),
        }

    def diagnostics(
        self,
        fields: list[ObstructionField],
        analyzer: FrequencyAnalyzer,
    ) -> str:
        """Return a human-readable string summarising the mining results."""
        report = self.full_report(fields, analyzer)
        lines = [
            "=== Obstruction Mining Diagnostics ===",
            f"Fields discovered   : {report['num_fields']}",
            f"Total obstructions  : {report['total_obstructions']}",
            f"Coherent fields     : {report['coherent_fields']}",
            f"Domains             : {', '.join(report['domains']) or '(none)'}",
            "",
            "Top obstructions by frequency:",
        ]
        for phrase, freq in report["top_obstructions"]:
            short = phrase[:60] + ("..." if len(phrase) > 60 else "")
            lines.append(f"  [{freq:6.1f}]  {short}")
        snap = report["snapshot"]
        lines += [
            "",
            f"Total unique obstructions tracked: {snap['total_obstructions']}",
            f"Total weight recorded            : {snap['total_weight']:.1f}",
        ]
        return "\n".join(lines)
