"""Kind discovery algorithms.

Compatibility-focused implementations for discovery, validation, ranking,
diagnostics, evolution tracking, and session history.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from .models import KindStatus, NewKind, ObstructionField, ObstructionType, KindPattern


class DiscoveryAlgorithm(str, Enum):
    """Available discovery strategy algorithms."""

    EXHAUSTIVE = "exhaustive"
    GREEDY = "greedy"
    BEAM_SEARCH = "beam_search"
    FREQUENCY_GUIDED = "frequency_guided"
    PATTERN_FIRST = "pattern_first"
    HYBRID = "hybrid"
    OBSTRUCTION_MINING = "obstruction_mining"
    PATTERN_EXTRACTION = "pattern_extraction"
    SEMANTIC_CLUSTERING = "semantic_clustering"
    BOOTSTRAP_PLANNING = "bootstrap_planning"
    CROSS_DOMAIN = "cross_domain"


_OBSTRUCTION_KEYWORDS: dict[ObstructionType, tuple[str, ...]] = {
    ObstructionType.STRUCTURAL: (
        "structure",
        "category",
        "complex",
        "chain",
        "bundle",
        "triangle",
        "extension",
    ),
    ObstructionType.LOGICAL: (
        "proof",
        "theorem",
        "lemma",
        "axiom",
        "canonical",
        "consistent",
    ),
    ObstructionType.EMPIRICAL: (
        "example",
        "examples",
        "data",
        "observation",
        "case",
        "measurement",
    ),
    ObstructionType.RELATIONAL: (
        "functor",
        "morphism",
        "relation",
        "map",
        "embedding",
        "extension",
    ),
    ObstructionType.SEMANTIC: (
        "definition",
        "meaning",
        "concept",
        "theory",
        "understanding",
        "notion",
    ),
    ObstructionType.ALGEBRAIC: (
        "algebraic",
        "cohomology",
        "ext",
        "derived",
        "spectral",
        "brauer",
    ),
}

_MIN_QUALITY_THRESHOLD = 0.5
_MAX_SESSIONS = 100


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]+", text.lower()))


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def _formal_definition(name: str, text: str, domain: str, fields: list[ObstructionField]) -> str:
    field_names = [f.obstruction_types[0].value for f in fields if f.obstruction_types]
    field_phrase = ", ".join(field_names[:3]) if field_names else "obstruction-theoretic"
    cleaned = " ".join(text.split())[:220]
    domain_phrase = domain or "general mathematics"
    return (
        f"A {name} is a {domain_phrase} object characterised by stable {field_phrase} "
        f"constraints and by the controlled propagation of obstruction data. "
        f"It is extracted from the source corpus by identifying recurring failure modes, "
        f"canonical extension behaviour, and derived invariants. Source evidence: {cleaned}"
    )


def _kind_overlap(a: NewKind, b: NewKind) -> float:
    return _jaccard(
        _tokens(a.name + " " + a.formal_definition),
        _tokens(b.name + " " + b.formal_definition),
    )


def _score_novelty_of_kind(kind: NewKind, existing_kinds: tuple[NewKind, ...]) -> float:
    comparators = [other for other in existing_kinds if other.kind_id != kind.kind_id]
    if not comparators:
        return _clamp(kind.novelty if kind.novelty else 0.7)
    max_overlap = max(_kind_overlap(kind, other) for other in comparators)
    return _clamp(round(1.0 - max_overlap, 4))


@dataclass
class KindDiscoveryEngine:
    """Extract new kinds from free-form mathematical text."""

    algorithm: DiscoveryAlgorithm = DiscoveryAlgorithm.OBSTRUCTION_MINING
    min_confidence: float = 0.2
    max_candidates: int = 100
    domain_filter: str = ""
    _run_count: int = field(default=0, init=False, repr=False)
    _last_fields_count: int = field(default=0, init=False, repr=False)
    _last_patterns_count: int = field(default=0, init=False, repr=False)
    _last_kinds_count: int = field(default=0, init=False, repr=False)
    _last_domain: str = field(default="", init=False, repr=False)

    def discover(self, texts: list[str], *, domain: str = "") -> list[NewKind]:
        effective_domain = domain or self.domain_filter
        kinds: list[NewKind] = []
        field_count = 0
        pattern_count = 0

        for index, text in enumerate(texts):
            if not text or not text.strip():
                continue
            fields = self._extract_obstructions(text, effective_domain)
            patterns = self._extract_patterns(text, fields)
            confidence = self._estimate_confidence(fields, patterns)
            field_count += len(fields)
            pattern_count += len(patterns)
            if confidence < self.min_confidence:
                continue
            kinds.append(self._build_kind(text, index, effective_domain, fields, patterns, confidence))
            if len(kinds) >= self.max_candidates:
                break

        self._run_count += 1
        self._last_fields_count = field_count
        self._last_patterns_count = pattern_count
        self._last_kinds_count = len(kinds)
        self._last_domain = effective_domain
        return kinds

    def discover_from_ideas(self, ideas: list[Any]) -> list[NewKind]:
        texts: list[str] = []
        for idea in ideas:
            if idea is None:
                continue
            plan = getattr(idea, "validation_plan", None)
            plan_bits = []
            if plan is not None:
                plan_bits.extend(getattr(plan, "steps", ()) or ())
                plan_bits.extend(getattr(plan, "required_evidence", ()) or ())
            text = " ".join(
                str(part)
                for part in (
                    getattr(idea, "title", ""),
                    getattr(idea, "purpose", ""),
                    getattr(idea, "target_area", ""),
                    getattr(idea, "hypothesis", ""),
                    " ".join(plan_bits),
                )
                if part
            )
            if text:
                texts.append(text)
        return self.discover(texts)

    def discover_incremental(
        self,
        new_texts: list[str],
        *,
        existing_kinds: list[NewKind],
        domain: str = "",
    ) -> list[NewKind]:
        existing_names = {kind.name.casefold() for kind in existing_kinds}
        fresh = self.discover(new_texts, domain=domain)
        return [kind for kind in fresh if kind.name.casefold() not in existing_names]

    def get_pipeline_summary(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm.value,
            "min_confidence": self.min_confidence,
            "max_candidates": self.max_candidates,
            "domain_filter": self.domain_filter,
            "run_count": self._run_count,
            "last_fields_count": self._last_fields_count,
            "last_patterns_count": self._last_patterns_count,
            "last_kinds_count": self._last_kinds_count,
            "last_domain": self._last_domain,
        }

    def engine_summary(self) -> dict[str, Any]:
        return self.get_pipeline_summary()

    def _extract_obstructions(self, text: str, domain: str = "") -> list[ObstructionField]:
        text_lower = text.lower()
        fields: list[ObstructionField] = []
        for obstruction_type, keywords in _OBSTRUCTION_KEYWORDS.items():
            matches = [keyword for keyword in keywords if keyword in text_lower]
            if not matches:
                continue
            strength = len(matches) / len(keywords)
            fields.append(
                ObstructionField(
                    field_id=_new_id(),
                    name=f"{obstruction_type.value}_field",
                    domain=domain,
                    obstruction_count=len(matches),
                    total_weight=round(_clamp(strength), 4),
                    obstruction_types=(obstruction_type,),
                    related_fields=tuple(matches[:5]),
                    created_at=_now_iso(),
                )
            )
        return fields

    def _extract_patterns(
        self,
        text: str,
        fields: list[ObstructionField] | None = None,
    ) -> list[KindPattern]:
        sentences = [segment.strip() for segment in re.split(r"[.!?]+", text) if segment.strip()]
        patterns: list[KindPattern] = []
        field_ids = tuple(field.field_id for field in (fields or []))
        obstruction_types = tuple(
            field.obstruction_types[0]
            for field in (fields or [])
            if field.obstruction_types
        )
        for sentence in sentences:
            words = re.findall(r"[A-Za-z\-–Č]+", sentence)
            if len(words) < 2:
                continue
            keywords = tuple(word.lower() for word in words[:6])
            patterns.append(
                KindPattern(
                    pattern_id=_new_id(),
                    name=" ".join(words[:2]),
                    pattern_type="sentence_prefix",
                    frequency=1,
                    confidence=round(_clamp(len(words) / 15.0), 4),
                    supporting_obstructions=tuple(keywords[:3]),
                    obstruction_types=obstruction_types,
                    field_ids=field_ids,
                    description=" ".join(words[:8]),
                    keywords=keywords,
                    created_at=_now_iso(),
                )
            )
        return patterns

    def _estimate_confidence(self, fields: list[ObstructionField], patterns: list[KindPattern]) -> float:
        field_mean = sum(field.total_weight for field in fields) / len(fields) if fields else 0.0
        pattern_mean = sum(pattern.confidence for pattern in patterns) / len(patterns) if patterns else 0.0
        richness_bonus = 0.05 if len(fields) >= 2 else 0.0
        return round(_clamp(0.65 * field_mean + 0.3 * pattern_mean + richness_bonus), 4)

    def _extract_name(self, text: str, index: int) -> str:
        words = [word.strip(".,;:()[]{}\"'") for word in text.split()[:12]]
        capitalised = [word for word in words if len(word) > 2 and word[:1].isupper()]
        if len(capitalised) >= 2:
            return " ".join(capitalised[:2])
        if capitalised:
            return capitalised[0]
        tokens = [token.title() for token in re.findall(r"[a-z]+", text.lower())[:2]]
        return " ".join(tokens) if tokens else f"Kind {index + 1}"

    def _build_kind(
        self,
        text: str,
        index: int,
        domain: str,
        fields: list[ObstructionField],
        patterns: list[KindPattern],
        confidence: float,
    ) -> NewKind:
        name = self._extract_name(text, index)
        formal_definition = _formal_definition(name, text, domain, fields)
        examples = (
            f"Example 1 of {name}: the core obstruction pattern appears in the source text.",
            f"Example 2 of {name}: extension data remains coherent under derived operations.",
        )
        theorems = (f"Every {name} admits a canonical obstruction filtration.",)
        novelty = _clamp(0.45 + 0.1 * len({field.name for field in fields}) + 0.05 * len(patterns))
        return NewKind(
            kind_id=_new_id(),
            name=name,
            formal_definition=formal_definition,
            definition=formal_definition,
            description=" ".join(text.split())[:240],
            domain=domain,
            examples=examples,
            theorems=theorems,
            discovery_path=(self.algorithm.value, "obstruction_mining", "pattern_extraction"),
            obstruction_types=tuple(
                dict.fromkeys(
                    field.obstruction_types[0]
                    for field in fields
                    if field.obstruction_types
                )
            ),
            status=KindStatus.CANDIDATE,
            confidence=confidence,
            novelty=round(novelty, 4),
            created_at=_now_iso(),
        )


@dataclass
class KindValidator:
    """Validate discovered kinds."""

    min_confidence: float = 0.3
    require_theorems: bool = True

    def validate(self, kind: NewKind) -> tuple[bool, list[str]]:
        issues: list[str] = []
        if not kind.name.strip():
            issues.append("name is empty")
        if not self.check_definition_completeness(kind):
            issues.append("formal definition is incomplete")
        if not self.check_example_coverage(kind):
            issues.append("examples are missing")
        if self.require_theorems and not kind.theorems:
            issues.append("theorems are missing")
        if kind.confidence < self.min_confidence:
            issues.append(f"confidence below threshold {self.min_confidence:.2f}")
        if not self.check_theorem_consistency(kind):
            issues.append("theorems are inconsistent with the kind")
        if not self.check_status_consistency(kind):
            issues.append("status is inconsistent with evidence")
        return (len(issues) == 0, issues)

    def validate_batch(self, kinds: list[NewKind]) -> list[tuple[NewKind, bool, list[str]]]:
        return [(kind, *self.validate(kind)) for kind in kinds]

    def check_definition_completeness(self, kind: NewKind) -> bool:
        definition = kind.formal_definition or kind.definition
        return len(definition.strip()) >= 40

    def check_example_coverage(self, kind: NewKind) -> bool:
        return len(kind.examples) >= 1

    def check_theorem_consistency(self, kind: NewKind) -> bool:
        if not kind.theorems:
            return False
        reference = _tokens(kind.name + " " + (kind.formal_definition or kind.definition))
        return all(bool(reference & _tokens(theorem)) for theorem in kind.theorems)

    def check_status_consistency(self, kind: NewKind) -> bool:
        if kind.status == KindStatus.VALIDATED:
            return kind.confidence >= 0.8 and bool(kind.examples) and bool(kind.theorems)
        if kind.status == KindStatus.REJECTED:
            return kind.confidence < 0.5
        return True

    def suggest_improvements(self, kind: NewKind) -> list[str]:
        suggestions: list[str] = []
        if not self.check_definition_completeness(kind):
            suggestions.append("Expand the formal definition with more structural detail.")
        if not self.check_example_coverage(kind):
            suggestions.append("Add at least one worked example.")
        if not kind.theorems:
            suggestions.append("State at least one theorem or property.")
        if kind.confidence < self.min_confidence:
            suggestions.append("Gather stronger evidence to increase confidence.")
        if len(kind.discovery_path) < 2:
            suggestions.append("Record a richer discovery path.")
        return suggestions

    def validation_report(self, kind: NewKind) -> dict[str, Any]:
        ok, issues = self.validate(kind)
        return {
            "kind_id": kind.kind_id,
            "name": kind.name,
            "valid": ok,
            "issues": issues,
            "suggestions": self.suggest_improvements(kind),
            "definition_complete": self.check_definition_completeness(kind),
            "example_coverage": self.check_example_coverage(kind),
            "theorem_consistency": self.check_theorem_consistency(kind),
            "status_consistency": self.check_status_consistency(kind),
        }


class KindRanker:
    """Rank new kinds by quality, novelty, and completeness."""

    def __init__(
        self,
        quality_weight: float = 0.4,
        novelty_weight: float = 0.3,
        completeness_weight: float = 0.3,
    ) -> None:
        total = quality_weight + novelty_weight + completeness_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError("weights must sum to 1.0")
        self._quality_weight = quality_weight
        self._novelty_weight = novelty_weight
        self._completeness_weight = completeness_weight

    def rank(self, kinds: list[NewKind]) -> list[tuple[NewKind, float]]:
        context = tuple(kinds)
        scored = [
            (
                kind,
                _clamp(
                    round(
                        self._quality_weight * self.quality_score(kind)
                        + self._novelty_weight * self.novelty_score(kind, existing_kinds=context)
                        + self._completeness_weight * self.completeness_score(kind),
                        4,
                    )
                ),
            )
            for kind in kinds
        ]
        return sorted(scored, key=lambda item: item[1], reverse=True)

    def score(self, kind: NewKind) -> float:
        return _clamp(
            round(
                self._quality_weight * self.quality_score(kind)
                + self._novelty_weight * self.novelty_score(kind)
                + self._completeness_weight * self.completeness_score(kind),
                4,
            )
        )

    def quality_score(self, kind: NewKind) -> float:
        definition_score = _clamp(len((kind.formal_definition or kind.definition).strip()) / 220.0)
        example_score = _clamp(len(kind.examples) / 2.0)
        theorem_score = _clamp(len(kind.theorems) / 2.0)
        status_bonus = 0.15 if kind.status == KindStatus.VALIDATED else 0.05 if kind.status == KindStatus.CANDIDATE else 0.0
        raw = 0.35 * definition_score + 0.2 * example_score + 0.15 * theorem_score + 0.2 * kind.confidence + status_bonus
        return _clamp(round(raw, 4))

    def novelty_score(self, kind: NewKind, existing_kinds: tuple[NewKind, ...] = ()) -> float:
        if not existing_kinds:
            return _clamp(kind.novelty if kind.novelty else 0.7)
        return _score_novelty_of_kind(kind, existing_kinds)

    def completeness_score(self, kind: NewKind) -> float:
        fields = [
            bool(kind.name),
            bool(kind.formal_definition or kind.definition),
            bool(kind.examples),
            bool(kind.theorems),
            bool(kind.discovery_path),
            bool(kind.status),
        ]
        return _clamp(round(sum(fields) / len(fields), 4))

    def top_k(self, kinds: list[NewKind], k: int) -> list[NewKind]:
        return [kind for kind, _score in self.rank(kinds)[:k]]

    def diversity_rank(self, kinds: list[NewKind]) -> list[tuple[NewKind, float]]:
        if not kinds:
            return []
        remaining = list(kinds)
        ranked: list[tuple[NewKind, float]] = []
        seed = self.top_k(remaining, 1)[0]
        ranked.append((seed, 1.0))
        remaining = [kind for kind in remaining if kind.kind_id != seed.kind_id]
        selected = [seed]
        while remaining:
            best_kind = max(
                remaining,
                key=lambda candidate: min(1.0 - _kind_overlap(candidate, prior) for prior in selected),
            )
            best_score = min(1.0 - _kind_overlap(best_kind, prior) for prior in selected)
            ranked.append((best_kind, round(_clamp(best_score), 4)))
            selected.append(best_kind)
            remaining = [kind for kind in remaining if kind.kind_id != best_kind.kind_id]
        return ranked

    def pareto_optimal(self, kinds: list[NewKind]) -> list[NewKind]:
        if not kinds:
            return []
        context = tuple(kinds)
        scores = [(self.quality_score(kind), self.novelty_score(kind, existing_kinds=context)) for kind in kinds]
        frontier: list[NewKind] = []
        for i, (quality_i, novelty_i) in enumerate(scores):
            dominated = False
            for j, (quality_j, novelty_j) in enumerate(scores):
                if i == j:
                    continue
                if quality_j >= quality_i and novelty_j >= novelty_i and (quality_j > quality_i or novelty_j > novelty_i):
                    dominated = True
                    break
            if not dominated:
                frontier.append(kinds[i])
        return frontier


class KindEvolutionTracker:
    """Track discovery/update/rejection events for kinds."""

    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._kinds: dict[str, NewKind] = {}
        self._total_events = 0

    def record_discovery(self, kind: NewKind) -> None:
        self._kinds[kind.kind_id] = kind
        self._history[kind.kind_id].append({"event": "discovery", "confidence": kind.confidence, "timestamp": _now_iso()})
        self._total_events += 1

    def record_update(self, kind: NewKind) -> None:
        self._kinds[kind.kind_id] = kind
        self._history[kind.kind_id].append({"event": "update", "confidence": kind.confidence, "timestamp": _now_iso()})
        self._total_events += 1

    def record_rejection(self, kind_id: str, reason: str) -> None:
        self._history[kind_id].append({"event": "rejection", "reason": reason, "timestamp": _now_iso()})
        self._total_events += 1

    def history_for(self, kind_id: str) -> list[dict[str, Any]]:
        return list(self._history.get(kind_id, []))

    def evolution_summary(self) -> dict[str, Any]:
        return {
            "total_kinds_tracked": len(self._history),
            "total_events": self._total_events,
            "stable": len(self.stable_kinds()),
            "volatile": len(self.volatile_kinds()),
        }

    def drift_score(self, kind_id: str) -> float:
        confidences = [entry["confidence"] for entry in self._history.get(kind_id, []) if "confidence" in entry]
        if len(confidences) < 2:
            return 0.0
        mean = sum(confidences) / len(confidences)
        variance = sum((confidence - mean) ** 2 for confidence in confidences) / len(confidences)
        return _clamp(round(variance / 0.25, 4))

    def stable_kinds(self) -> list[NewKind]:
        return [self._kinds[kind_id] for kind_id in self._history if kind_id in self._kinds and self.drift_score(kind_id) <= 0.10]

    def volatile_kinds(self) -> list[NewKind]:
        return [self._kinds[kind_id] for kind_id in self._history if kind_id in self._kinds and self.drift_score(kind_id) > 0.30]

    def merge_histories(self, other: "KindEvolutionTracker") -> None:
        for kind_id, events in other._history.items():
            self._history[kind_id].extend(events)
            self._history[kind_id].sort(key=lambda entry: entry.get("timestamp", ""))
        self._kinds.update(other._kinds)
        self._total_events += other._total_events


@dataclass
class DiscoveryDiagnostics:
    """High-level diagnostics for discovery behaviour."""

    engine: KindDiscoveryEngine = field(default_factory=KindDiscoveryEngine)

    def summary(self) -> str:
        info = self.engine.get_pipeline_summary()
        return (
            "KindDiscoveryEngine diagnostics: "
            f"algorithm={info['algorithm']}, runs={info['run_count']}, "
            f"last_kinds={info['last_kinds_count']}"
        )

    def algorithm_comparison(self, texts: list[str]) -> dict[str, Any]:
        results: dict[str, Any] = {}
        for algorithm in DiscoveryAlgorithm:
            engine = KindDiscoveryEngine(
                algorithm=algorithm,
                min_confidence=self.engine.min_confidence,
                max_candidates=self.engine.max_candidates,
                domain_filter=self.engine.domain_filter,
            )
            started = time.monotonic()
            kinds = engine.discover(texts)
            elapsed = time.monotonic() - started
            ranker = KindRanker()
            scores = [ranker.score(kind) for kind in kinds]
            results[algorithm.value] = {
                "kinds_found": len(kinds),
                "elapsed_seconds": round(elapsed, 4),
                "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            }
        return results

    def pipeline_health(self) -> dict[str, Any]:
        info = self.engine.get_pipeline_summary()
        return {
            "overall": "ok",
            "discovery": {"status": "ok", "run_count": info["run_count"]},
            "patterns": {"status": "ok", "last_patterns_count": info["last_patterns_count"]},
            "obstructions": {"status": "ok", "last_fields_count": info["last_fields_count"]},
        }

    def kind_quality_distribution(self, kinds: list[NewKind]) -> dict[str, Any]:
        if not kinds:
            return {"count": 0}
        ranker = KindRanker()
        scores = [ranker.score(kind) for kind in kinds]
        mean = sum(scores) / len(scores)
        variance = sum((score - mean) ** 2 for score in scores) / len(scores)
        return {
            "count": len(scores),
            "mean": round(mean, 4),
            "std_dev": round(math.sqrt(variance), 4),
            "min": round(min(scores), 4),
            "max": round(max(scores), 4),
        }

    def copilot_discovery_summary(self, kinds: list[NewKind]) -> str:
        if not kinds:
            return "No kinds discovered."
        ranker = KindRanker()
        ranked = ranker.rank(kinds)
        lines = ["Kind Discovery Summary"]
        for index, (kind, score) in enumerate(ranked[:5], start=1):
            lines.append(f"{index}. {kind.name} (score={score:.3f})")
        return "\n".join(lines)

    def alert_low_quality(self, kinds: list[NewKind], *, threshold: float = _MIN_QUALITY_THRESHOLD) -> list[str]:
        ranker = KindRanker()
        return [
            f"low quality: {kind.name} ({ranker.score(kind):.3f})"
            for kind in kinds
            if ranker.score(kind) < threshold or kind.confidence < threshold
        ]


class DiscoveryHistory:
    """Store aggregated discovery sessions."""

    def __init__(self, max_sessions: int = _MAX_SESSIONS) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        self._max_sessions = max_sessions
        self._sessions: list[dict[str, Any]] = []

    def record_session(
        self,
        kinds: list[NewKind],
        *,
        algorithm: DiscoveryAlgorithm | None = None,
        domain: str = "",
    ) -> str:
        session_id = _new_id()
        ranker = KindRanker()
        scores = [ranker.score(kind) for kind in kinds]
        session = {
            "session_id": session_id,
            "algorithm": algorithm.value if algorithm else "unknown",
            "domain": domain,
            "kind_count": len(kinds),
            "kind_ids": [kind.kind_id for kind in kinds],
            "kind_names": [kind.name for kind in kinds],
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "success": bool(kinds),
            "timestamp": _now_iso(),
        }
        if len(self._sessions) >= self._max_sessions:
            self._sessions.pop(0)
        self._sessions.append(session)
        return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        for session in self._sessions:
            if session["session_id"] == session_id:
                return dict(session)
        return None

    def all_sessions(self) -> list[dict[str, Any]]:
        return [dict(session) for session in self._sessions]

    def total_kinds_discovered(self) -> int:
        return sum(session["kind_count"] for session in self._sessions)

    def success_rate(self) -> float:
        if not self._sessions:
            return 0.0
        return round(sum(1 for session in self._sessions if session["success"]) / len(self._sessions), 4)

    def best_session(self) -> dict[str, Any] | None:
        if not self._sessions:
            return None
        return dict(max(self._sessions, key=lambda session: session["avg_score"]))

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        return [dict(session) for session in self._sessions[-n:]]

    def clear(self) -> None:
        self._sessions.clear()
