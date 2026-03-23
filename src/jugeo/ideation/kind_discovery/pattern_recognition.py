"""Pattern recognition stage (S02) of the kind-discovery pipeline.

Implements the following classes:
  PatternSignature    – lightweight signature derived from an ObstructionField
  PatternMatcher      – pairwise and bulk matching between ObstructionFields
  RecurrenceDetector  – discovers recurring signatures across a field corpus
  GeneralityEstimator – scores how broadly a signature generalises
  PatternRanker       – ranks and selects KindPattern objects
  PatternRecognizer   – orchestrates the full recognition pipeline
"""

from __future__ import annotations

import datetime
import functools
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from jugeo.ideation.kind_discovery.models import (
    KindCandidate,
    KindPattern,
    ObstructionField,
    ObstructionType,
)

# ---------------------------------------------------------------------------
# Private helpers (mirrored from models.py to keep this module self-contained)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _clamp(value: float, lo: float, hi: float) -> float:
    """Return *value* clamped to [*lo*, *hi*]."""
    return max(lo, min(hi, value))


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


# ---------------------------------------------------------------------------
# PatternSignature
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatternSignature:
    """A lightweight signature derived from the tokens of an ObstructionField.

    Attributes
    ----------
    sig_id:
        Unique identifier for this signature.
    tokens:
        Frozenset of tokens making up the signature.
    n_gram_hashes:
        Tuple of hash values for consecutive token bigrams (sorted order).
    domain_count:
        Number of domains this signature covers.
    total_occurrences:
        Total number of times this signature has been observed.
    created_at:
        ISO-8601 UTC timestamp of creation.
    """

    sig_id: str
    tokens: frozenset[str]
    n_gram_hashes: tuple[int, ...]
    domain_count: int = 0
    total_occurrences: int = 0
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if not self.sig_id.strip():
            raise ValueError("PatternSignature.sig_id must be non-empty.")
        if self.domain_count < 0:
            raise ValueError(
                f"domain_count must be >= 0; got {self.domain_count}"
            )
        if self.total_occurrences < 0:
            raise ValueError(
                f"total_occurrences must be >= 0; got {self.total_occurrences}"
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def complexity(self) -> float:
        """Measure token richness: len(tokens) / (1 + len(n_gram_hashes)), clamped to [0, 10]."""
        raw = len(self.tokens) / (1 + len(self.n_gram_hashes))
        return _clamp(raw, 0.0, 10.0)

    @property
    def is_degenerate(self) -> bool:
        """True if the signature carries no meaningful content."""
        if len(self.tokens) == 0:
            return True
        if len(self.tokens) == 1 and self.total_occurrences == 0:
            return True
        return False

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def matches(self, other: PatternSignature, threshold: float = 0.6) -> bool:
        """Return True if Jaccard similarity with *other* meets *threshold*."""
        return _jaccard(self.tokens, other.tokens) >= threshold

    def merge(self, other: PatternSignature) -> PatternSignature:
        """Return a new PatternSignature combining self and *other*.

        Keeps self's sig_id.  Tokens are unioned; n_gram_hashes are merged
        (deduplicated); domain_count takes the maximum; total_occurrences are
        summed.
        """
        return PatternSignature(
            sig_id=self.sig_id,
            tokens=self.tokens | other.tokens,
            n_gram_hashes=tuple(set(self.n_gram_hashes) | set(other.n_gram_hashes)),
            domain_count=max(self.domain_count, other.domain_count),
            total_occurrences=self.total_occurrences + other.total_occurrences,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "sig_id": self.sig_id,
            "tokens": sorted(self.tokens),
            "n_gram_hashes": list(self.n_gram_hashes),
            "domain_count": self.domain_count,
            "total_occurrences": self.total_occurrences,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PatternSignature:
        """Deserialise from a dict produced by :meth:`to_dict`."""
        return cls(
            sig_id=d["sig_id"],
            tokens=frozenset(d.get("tokens", [])),
            n_gram_hashes=tuple(int(h) for h in d.get("n_gram_hashes", [])),
            domain_count=int(d.get("domain_count", 0)),
            total_occurrences=int(d.get("total_occurrences", 0)),
            created_at=d.get("created_at", _now_iso()),
        )


# ---------------------------------------------------------------------------
# PatternMatcher
# ---------------------------------------------------------------------------


class PatternMatcher:
    """Matches obstruction fields against each other and against known patterns.

    Parameters
    ----------
    threshold:
        Minimum token-overlap score to include a match pair.
    config:
        Optional configuration object (reserved for future use).
    """

    def __init__(self, threshold: float = 0.4, config: Any = None) -> None:
        self.threshold = threshold
        self.config = config

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def match(
        self,
        field_a: ObstructionField,
        field_b: ObstructionField,
    ) -> list[tuple[str, str, float]]:
        """Return matching (obs_a, obs_b, score) pairs between the two fields.

        All pairs of obstructions across the two fields are compared; only
        those with token-overlap score >= threshold are returned.
        """
        results: list[tuple[str, str, float]] = []
        for obs_a in field_a.obstructions:
            for obs_b in field_b.obstructions:
                score = self._token_overlap(obs_a, obs_b)
                if score >= self.threshold:
                    results.append((obs_a, obs_b, score))
        return results

    def match_all(
        self, fields: list[ObstructionField]
    ) -> list[tuple[str, str, float]]:
        """Return all matches across every distinct pair of fields."""
        results: list[tuple[str, str, float]] = []
        for i in range(len(fields)):
            for j in range(i + 1, len(fields)):
                results.extend(self.match(fields[i], fields[j]))
        return results

    def build_match_graph(
        self, fields: list[ObstructionField]
    ) -> dict[str, list[str]]:
        """Return an adjacency dict of fields connected by at least one match."""
        graph: dict[str, list[str]] = {f.field_id: [] for f in fields}
        for i in range(len(fields)):
            for j in range(i + 1, len(fields)):
                matches = self.match(fields[i], fields[j])
                if matches:
                    graph[fields[i].field_id].append(fields[j].field_id)
                    graph[fields[j].field_id].append(fields[i].field_id)
        return graph

    def find_common_core(self, fields: list[ObstructionField]) -> frozenset[str]:
        """Return the intersection of all token universes across *fields*."""
        if not fields:
            return frozenset()
        result: frozenset[str] = fields[0].token_universe
        for f in fields[1:]:
            result = result & f.token_universe
        return result

    def match_against_known(
        self,
        field: ObstructionField,
        known_patterns: list[KindPattern],
    ) -> list[tuple[KindPattern, float]]:
        """Score *field* against each known pattern; return sorted list descending."""
        scored: list[tuple[KindPattern, float]] = []
        for pattern in known_patterns:
            score = _jaccard(field.token_universe, pattern.signature_tokens)
            scored.append((pattern, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _token_overlap(self, a: str, b: str) -> float:
        """Jaccard similarity of the token sets of strings *a* and *b*."""
        return _jaccard(frozenset(_tokenize(a)), frozenset(_tokenize(b)))

    def _structural_match(
        self, a: ObstructionField, b: ObstructionField
    ) -> float:
        """Fraction of obstruction_types in common (Jaccard on type sets)."""
        types_a = frozenset(a.obstruction_types)
        types_b = frozenset(b.obstruction_types)
        return _jaccard(types_a, types_b)

    def _combined_score(
        self, a: ObstructionField, b: ObstructionField
    ) -> float:
        """Weighted combination of token overlap and structural match."""
        token_score = _jaccard(a.token_universe, b.token_universe)
        struct_score = self._structural_match(a, b)
        return 0.6 * token_score + 0.4 * struct_score


# ---------------------------------------------------------------------------
# RecurrenceDetector
# ---------------------------------------------------------------------------


class RecurrenceDetector:
    """Detects recurring token-level signatures across a corpus of fields.

    Parameters
    ----------
    config:
        Optional configuration object (reserved for future use).
    min_recurrence:
        Minimum number of fields a signature must appear in to be retained.
    """

    def __init__(self, config: Any = None, min_recurrence: int = 2) -> None:
        self.config = config
        self.min_recurrence = min_recurrence

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def detect(self, fields: list[ObstructionField]) -> list[PatternSignature]:
        """Detect recurring signatures in *fields*.

        Pipeline: extract signature per field → group by similarity →
        merge each group → filter by min_recurrence.
        """
        if not fields:
            return []
        sigs = [self._extract_signatures(f) for f in fields]
        groups = self._group_by_similarity(sigs)
        merged: list[PatternSignature] = []
        for group in groups:
            if len(group) >= self.min_recurrence:
                merged.append(self._merge_group(group))
        return merged

    def detect_cross_domain(
        self, fields: list[ObstructionField]
    ) -> list[PatternSignature]:
        """Detect signatures that appear in >= 2 distinct domains."""
        if not fields:
            return []
        # Build a mapping from field_id -> domain for later domain counting.
        domain_map: dict[str, str] = {f.field_id: f.domain for f in fields}

        sigs = [self._extract_signatures(f) for f in fields]
        groups = self._group_by_similarity(sigs)
        results: list[PatternSignature] = []
        for group in groups:
            if len(group) < 2:
                continue
            domains = {domain_map.get(s.sig_id, "") for s in group}
            if len(domains) >= 2:
                results.append(self._merge_group(group))
        return results

    def compute_recurrence_score(
        self, sig: PatternSignature, fields: list[ObstructionField]
    ) -> float:
        """Fraction of *fields* whose token universe overlaps sig by >= 0.3."""
        if not fields:
            return 0.0
        count = sum(
            1
            for f in fields
            if _jaccard(f.token_universe, sig.tokens) >= 0.3
        )
        return _clamp(count / len(fields), 0.0, 1.0)

    def filter_degenerate(
        self, sigs: list[PatternSignature]
    ) -> list[PatternSignature]:
        """Remove degenerate signatures."""
        return [s for s in sigs if not s.is_degenerate]

    def recurrence_matrix(
        self,
        sigs: list[PatternSignature],
        fields: list[ObstructionField],
    ) -> list[list[float]]:
        """Return matrix[i][j] = Jaccard(sigs[i].tokens, fields[j].token_universe)."""
        return [
            [_jaccard(sig.tokens, f.token_universe) for f in fields]
            for sig in sigs
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_signatures(self, field: ObstructionField) -> PatternSignature:
        """Create a PatternSignature from a single ObstructionField."""
        tokens = field.token_universe
        sorted_tokens = sorted(tokens)
        n_gram_hashes = tuple(
            hash(sorted_tokens[i] + sorted_tokens[i + 1])
            for i in range(len(sorted_tokens) - 1)
        )
        return PatternSignature(
            sig_id=field.field_id,
            tokens=tokens,
            n_gram_hashes=n_gram_hashes,
            domain_count=1,
            total_occurrences=field.size,
        )

    def _group_by_similarity(
        self, sigs: list[PatternSignature]
    ) -> list[list[PatternSignature]]:
        """Greedy grouping: place each sig into the first compatible group."""
        groups: list[list[PatternSignature]] = []
        for sig in sigs:
            placed = False
            for group in groups:
                if any(
                    _jaccard(sig.tokens, member.tokens) >= 0.4
                    for member in group
                ):
                    group.append(sig)
                    placed = True
                    break
            if not placed:
                groups.append([sig])
        return groups

    def _merge_group(self, group: list[PatternSignature]) -> PatternSignature:
        """Reduce-merge a group; set domain_count=len(group), total_occurrences=sum."""
        merged = functools.reduce(lambda a, b: a.merge(b), group)
        total_occ = sum(s.total_occurrences for s in group)
        # Re-create with corrected domain_count and total_occurrences.
        return PatternSignature(
            sig_id=merged.sig_id,
            tokens=merged.tokens,
            n_gram_hashes=merged.n_gram_hashes,
            domain_count=len(group),
            total_occurrences=total_occ,
        )


# ---------------------------------------------------------------------------
# GeneralityEstimator
# ---------------------------------------------------------------------------


class GeneralityEstimator:
    """Estimates how broadly a PatternSignature generalises across fields.

    Parameters
    ----------
    config:
        Optional configuration object (reserved for future use).
    """

    def __init__(self, config: Any = None) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def estimate(
        self, sig: PatternSignature, fields: list[ObstructionField]
    ) -> float:
        """Return generalisation score in [0, 1] = domain_spread * structural_generality."""
        spread = self.domain_spread(sig, fields)
        struct = self.structural_generality(sig)
        return _clamp(spread * struct, 0.0, 1.0)

    def estimate_batch(
        self,
        sigs: list[PatternSignature],
        fields: list[ObstructionField],
    ) -> dict[str, float]:
        """Return {sig_id: estimate} for all signatures."""
        return {sig.sig_id: self.estimate(sig, fields) for sig in sigs}

    def domain_spread(
        self, sig: PatternSignature, fields: list[ObstructionField]
    ) -> float:
        """Fraction of distinct domains that contain at least one matching field."""
        all_domains = {f.domain for f in fields}
        if not all_domains:
            return 0.0
        covered = {
            f.domain
            for f in fields
            if _jaccard(f.token_universe, sig.tokens) >= 0.3
        }
        return _clamp(len(covered) / len(all_domains), 0.0, 1.0)

    def structural_generality(self, sig: PatternSignature) -> float:
        """Return min(1.0, len(tokens) / 10.0); more tokens = more general."""
        return min(1.0, len(sig.tokens) / 10.0)

    def semantic_generality(
        self, sig: PatternSignature, fields: list[ObstructionField]
    ) -> float:
        """Average Jaccard similarity of sig.tokens against all fields."""
        if not fields:
            return 0.0
        total = sum(_jaccard(sig.tokens, f.token_universe) for f in fields)
        return total / len(fields)

    def cross_validate_generality(
        self,
        sig: PatternSignature,
        holdout_fields: list[ObstructionField],
    ) -> float:
        """Estimate generalisation on a held-out subset of fields."""
        detector = RecurrenceDetector()
        return detector.compute_recurrence_score(sig, holdout_fields)

    def generality_distribution(
        self,
        sigs: list[PatternSignature],
        fields: list[ObstructionField],
    ) -> dict[str, float]:
        """Return {sig_id: estimate} mapping for all *sigs*."""
        return self.estimate_batch(sigs, fields)

    def rank_by_generality(
        self,
        sigs: list[PatternSignature],
        fields: list[ObstructionField],
    ) -> list[tuple[PatternSignature, float]]:
        """Return list of (sig, score) sorted descending by generality estimate."""
        scored = [(sig, self.estimate(sig, fields)) for sig in sigs]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


# ---------------------------------------------------------------------------
# PatternRanker
# ---------------------------------------------------------------------------


class PatternRanker:
    """Ranks KindPattern objects by a composite quality score.

    Parameters
    ----------
    config:
        Optional configuration object (reserved for future use).
    top_k_default:
        Default number of top patterns to return from :meth:`top_k`.
    """

    def __init__(self, config: Any = None, top_k_default: int = 10) -> None:
        self.config = config
        self.top_k_default = top_k_default

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def rank(
        self, patterns: list[KindPattern]
    ) -> list[tuple[KindPattern, float]]:
        """Return (pattern, score) list sorted descending by composite score."""
        scored = [(p, self.score(p)) for p in patterns]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def score(self, pattern: KindPattern) -> float:
        """Composite score in [0, 1] combining generality, frequency, and domains."""
        freq_term = min(1.0, pattern.frequency / 10.0)
        domain_term = min(1.0, pattern.domain_count / 5.0)
        return 0.4 * pattern.generality_score + 0.3 * freq_term + 0.3 * domain_term

    def novelty_score(
        self,
        pattern: KindPattern,
        existing_patterns: list[KindPattern],
    ) -> float:
        """Return 1 minus max Jaccard overlap with any existing pattern."""
        if not existing_patterns:
            return 1.0
        max_overlap = max(
            _jaccard(pattern.signature_tokens, p.signature_tokens)
            for p in existing_patterns
        )
        return 1.0 - max_overlap

    def significance_score(self, pattern: KindPattern) -> float:
        """Return min(1.0, frequency * generality_score)."""
        return min(1.0, pattern.frequency * pattern.generality_score)

    def diversity_penalty(
        self,
        pattern: KindPattern,
        ranked_so_far: list[tuple[KindPattern, float]],
    ) -> float:
        """Return max Jaccard similarity to any already-ranked pattern."""
        if not ranked_so_far:
            return 0.0
        return max(
            _jaccard(pattern.signature_tokens, ranked[0].signature_tokens)
            for ranked, _ in ranked_so_far
        )

    def top_k(
        self,
        patterns: list[KindPattern],
        k: Optional[int] = None,
    ) -> list[tuple[KindPattern, float]]:
        """Return the top-*k* ranked patterns (uses top_k_default if k is None)."""
        if k is None:
            k = self.top_k_default
        return self.rank(patterns)[:k]

    def pareto_rank(
        self, patterns: list[KindPattern]
    ) -> list[list[KindPattern]]:
        """Return Pareto fronts for (frequency, generality_score, domain_count).

        Front 0 contains non-dominated patterns; subsequent fronts are
        computed by removing each front in turn.
        """
        remaining = list(patterns)
        fronts: list[list[KindPattern]] = []

        while remaining:
            front: list[KindPattern] = []
            for candidate in remaining:
                dominated = False
                for other in remaining:
                    if other is candidate:
                        continue
                    if (
                        other.frequency >= candidate.frequency
                        and other.generality_score >= candidate.generality_score
                        and other.domain_count >= candidate.domain_count
                        and (
                            other.frequency > candidate.frequency
                            or other.generality_score > candidate.generality_score
                            or other.domain_count > candidate.domain_count
                        )
                    ):
                        dominated = True
                        break
                if not dominated:
                    front.append(candidate)
            fronts.append(front)
            front_set = set(id(p) for p in front)
            remaining = [p for p in remaining if id(p) not in front_set]

        return fronts


# ---------------------------------------------------------------------------
# PatternRecognizer
# ---------------------------------------------------------------------------


class PatternRecognizer:
    """Orchestrates the full pattern-recognition pipeline.

    Parameters
    ----------
    config:
        Optional configuration object (reserved for future use).
    """

    def __init__(self, config: Any = None) -> None:
        self.config = config
        self._detector = RecurrenceDetector(config=config)
        self._estimator = GeneralityEstimator(config=config)
        self._ranker = PatternRanker(config=config)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def recognize(self, fields: list[ObstructionField]) -> list[KindPattern]:
        """Run the full pipeline and return sorted KindPatterns.

        Steps: detect recurrent sigs → estimate generality →
        convert to KindPatterns → rank → return.
        """
        sigs = self._detector.detect(fields)
        if not sigs:
            return []
        gen_scores = self._estimator.estimate_batch(sigs, fields)
        patterns = self.signatures_to_patterns(sigs, gen_scores)
        ranked = self._ranker.rank(patterns)
        return [p for p, _ in ranked]

    def recognize_incremental(
        self,
        new_fields: list[ObstructionField],
        existing_patterns: list[KindPattern],
    ) -> list[KindPattern]:
        """Detect on new_fields and merge with existing, skipping near-duplicates."""
        new_sigs = self._detector.detect(new_fields)
        gen_scores = self._estimator.estimate_batch(new_sigs, new_fields)
        new_patterns = self.signatures_to_patterns(new_sigs, gen_scores)

        combined = list(existing_patterns)
        for new_pat in new_patterns:
            # Skip if any existing pattern is very similar (Jaccard >= 0.8).
            if any(
                _jaccard(new_pat.signature_tokens, ep.signature_tokens) >= 0.8
                for ep in combined
            ):
                continue
            combined.append(new_pat)
        return combined

    def signatures_to_patterns(
        self,
        sigs: list[PatternSignature],
        generality_scores: dict[str, float],
    ) -> list[KindPattern]:
        """Convert PatternSignature objects to KindPattern objects."""
        patterns: list[KindPattern] = []
        for sig in sigs:
            signature_str = " ".join(sorted(sig.tokens)) if sig.tokens else "_empty_"
            gen_score = _clamp(generality_scores.get(sig.sig_id, 0.0), 0.0, 1.0)
            patterns.append(
                KindPattern(
                    pattern_id=sig.sig_id,
                    signature=signature_str,
                    frequency=sig.total_occurrences,
                    domains=(),
                    generality_score=gen_score,
                )
            )
        return patterns

    def patterns_from_candidates(
        self, candidates: list[KindCandidate]
    ) -> list[KindPattern]:
        """Group candidates by obstruction_pattern similarity; one KindPattern per group."""
        if not candidates:
            return []

        groups: list[list[KindCandidate]] = []
        for candidate in candidates:
            cand_tokens = frozenset(_tokenize(candidate.obstruction_pattern))
            placed = False
            for group in groups:
                rep_tokens = frozenset(
                    _tokenize(group[0].obstruction_pattern)
                )
                if _jaccard(cand_tokens, rep_tokens) >= 0.4:
                    group.append(candidate)
                    placed = True
                    break
            if not placed:
                groups.append([candidate])

        patterns: list[KindPattern] = []
        for group in groups:
            rep = group[0]
            all_tokens = frozenset(
                tok
                for c in group
                for tok in _tokenize(c.obstruction_pattern)
            )
            signature_str = " ".join(sorted(all_tokens)) if all_tokens else "_empty_"
            frequency = sum(c.frequency for c in group)
            avg_confidence = sum(c.confidence for c in group) / len(group)
            pattern_id = str(uuid.uuid4())
            patterns.append(
                KindPattern(
                    pattern_id=pattern_id,
                    signature=signature_str,
                    frequency=frequency,
                    domains=(),
                    generality_score=_clamp(avg_confidence, 0.0, 1.0),
                )
            )
        return patterns

    def full_report(
        self,
        fields: list[ObstructionField],
        patterns: list[KindPattern],
    ) -> dict[str, Any]:
        """Return a summary dict about the current recognition state."""
        universal = sum(1 for p in patterns if p.is_universal)
        avg_gen = (
            sum(p.generality_score for p in patterns) / len(patterns)
            if patterns
            else 0.0
        )
        ranked = self._ranker.top_k(patterns, k=5)
        top = [p.to_dict() for p, _ in ranked]
        return {
            "num_fields": len(fields),
            "num_patterns": len(patterns),
            "universal_patterns": universal,
            "avg_generality": round(avg_gen, 4),
            "top_patterns": top,
        }

    def diagnostics(self, patterns: list[KindPattern]) -> str:
        """Return a human-readable summary of all patterns with their scores."""
        if not patterns:
            return "No patterns detected."
        lines = [f"Patterns ({len(patterns)} total):"]
        ranked = self._ranker.rank(patterns)
        for i, (p, score) in enumerate(ranked, start=1):
            lines.append(
                f"  {i:3d}. [{p.pattern_id[:8]}] "
                f"sig={p.signature[:40]!r} "
                f"freq={p.frequency} "
                f"domains={p.domain_count} "
                f"generality={p.generality_score:.3f} "
                f"composite={score:.3f}"
            )
        return "\n".join(lines)
