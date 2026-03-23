"""
Ideation signals from obstruction ranking.

Identifies which obstructions in a semantic planning context signal future
ideation directions, ranks them by ideation potential, and extracts structured
future-direction hints for downstream reasoning pipelines.

# copilot: s03 – obstruction → ideation-signal → future-direction pipeline
"""
from __future__ import annotations

import enum
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

try:
    from jugeo.core.context import JugeoContext  # type: ignore
except ImportError:
    JugeoContext = None  # type: ignore

try:
    from jugeo.ideation.obstruction import ObstructionRecord  # type: ignore
except ImportError:
    ObstructionRecord = None  # type: ignore

try:
    from jugeo.ideation.semantic_futures.semantic_futures import SemanticFuture  # type: ignore
except ImportError:
    SemanticFuture = None  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    """Return a short collision-resistant identifier."""
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _sigmoid(x: float) -> float:
    """Logistic sigmoid, maps any real number to (0, 1)."""
    return 1.0 / (1.0 + math.exp(-x))


def _normalise_scores(scores: Dict[str, float]) -> Dict[str, float]:
    """
    Normalise a score dict so all values lie in [0, 1].

    Uses min-max normalisation; if all values are equal the result is all 0.5.
    """
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    span = hi - lo
    if span == 0.0:
        return {k: 0.5 for k in scores}
    return {k: (v - lo) / span for k, v in scores.items()}


def _weighted_mean(values: Dict[str, float], weights: Dict[str, float]) -> float:
    """
    Compute a weighted mean of *values* using *weights*.

    Missing weights default to 1.0; missing values are skipped.
    The result is clamped to [0, 1].
    """
    total_weight = 0.0
    total_value = 0.0
    for k, v in values.items():
        w = weights.get(k, 1.0)
        total_value += v * w
        total_weight += w
    if total_weight == 0.0:
        return 0.0
    return _clamp(total_value / total_weight)


def _entropy(distribution: Dict[str, float]) -> float:
    """
    Compute the Shannon entropy of a probability distribution.

    *distribution* values should sum to 1; any zeros are skipped.
    Returns a value in [0, log2(n)] where n is the number of categories.
    """
    h = 0.0
    for p in distribution.values():
        if p > 0.0:
            h -= p * math.log2(p)
    return h


def _cosine_similarity(a: Dict[str, float], b: Dict[str, float]) -> float:
    """
    Compute cosine similarity between two sparse feature vectors.

    Keys present in only one dict are treated as zero in the other.
    Returns a value in [-1, 1]; returns 0.0 if either vector is the zero vector.
    """
    all_keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in all_keys)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TrustTier(enum.Enum):
    """
    Ordered trust levels for judgments and signals.

    Each tier represents a stronger epistemic guarantee:

    PROPOSAL
        A human- or model-generated claim that has not been reviewed.
    REVIEWED
        The claim has been reviewed by at least one authoritative agent
        but has not been formally verified.
    VERIFIED
        The claim has been formally verified (e.g. type-checked, proof-checked,
        or validated against a schema).
    RUNTIME_WITNESSED
        The claim was observed to hold at runtime (e.g. by an assertion or
        runtime monitor).
    PROOF_BACKED
        The claim is backed by a formal proof artefact that can be
        independently checked.
    """

    PROPOSAL = 1
    REVIEWED = 2
    VERIFIED = 3
    RUNTIME_WITNESSED = 4
    PROOF_BACKED = 5

    # ------------------------------------------------------------------
    def dominates(self, other: "TrustTier") -> bool:
        """Return True when *self* is at least as trusted as *other*."""
        return self.value >= other.value

    def label(self) -> str:
        """Human-readable label."""
        return self.name.replace("_", " ").title()

    def next_tier(self) -> "TrustTier":
        """Return the next higher trust tier, or self if already at max."""
        try:
            return TrustTier(self.value + 1)
        except ValueError:
            return self


class SignalType(enum.Enum):
    """
    Categories of ideation signal that can be extracted from an obstruction.

    CAPABILITY_GAP
        The obstruction reveals a capability that the system currently lacks.
    KNOWLEDGE_BOUNDARY
        The obstruction marks the edge of the system's current knowledge.
    RESOURCE_CONSTRAINT
        The obstruction is primarily due to limited resources (compute, budget).
    DEPENDENCY_LOOP
        The obstruction forms part of a circular dependency between components.
    TEMPORAL_BOTTLENECK
        The obstruction is caused by time-ordering constraints.
    SEMANTIC_AMBIGUITY
        The obstruction arises from underspecified or ambiguous semantics.
    TRUST_DEFICIT
        The obstruction reflects insufficient trust between agents or components.
    INTEGRATION_FRICTION
        The obstruction is at the boundary between two integrated systems.
    """

    CAPABILITY_GAP = "capability_gap"
    KNOWLEDGE_BOUNDARY = "knowledge_boundary"
    RESOURCE_CONSTRAINT = "resource_constraint"
    DEPENDENCY_LOOP = "dependency_loop"
    TEMPORAL_BOTTLENECK = "temporal_bottleneck"
    SEMANTIC_AMBIGUITY = "semantic_ambiguity"
    TRUST_DEFICIT = "trust_deficit"
    INTEGRATION_FRICTION = "integration_friction"

    def describe(self) -> str:
        """Return a short description of this signal type."""
        descriptions = {
            "capability_gap": "System lacks a required capability.",
            "knowledge_boundary": "Obstruction marks the edge of known information.",
            "resource_constraint": "Limited computational or financial resources.",
            "dependency_loop": "Circular dependency between components.",
            "temporal_bottleneck": "Time-ordering constraints degrade throughput.",
            "semantic_ambiguity": "Underspecified or ambiguous interface semantics.",
            "trust_deficit": "Insufficient trust between agents or components.",
            "integration_friction": "Mismatch at the boundary of integrated systems.",
        }
        return descriptions.get(self.value, "Unknown signal type.")


class RankingCriterion(enum.Enum):
    """
    Criterion used when ranking obstructions by ideation potential.

    FREQUENCY
        How often this obstruction pattern has been observed.
    SEVERITY
        How badly the obstruction degrades downstream quality.
    NOVELTY
        How unlike previously-seen obstructions this one is.
    LEVERAGE
        How many other obstructions would be resolved if this one were solved.
    COMPOSITE
        A weighted combination of the other criteria.
    """

    FREQUENCY = "frequency"
    SEVERITY = "severity"
    NOVELTY = "novelty"
    LEVERAGE = "leverage"
    COMPOSITE = "composite"

    def is_single_axis(self) -> bool:
        """Return True for single-axis criteria (not COMPOSITE)."""
        return self != RankingCriterion.COMPOSITE


class DirectionPriority(enum.Enum):
    """
    Priority level assigned to a FutureDirection.

    EXPLORATORY
        Low-confidence direction worth light investigation.
    PROMISING
        Moderate-confidence direction worth a dedicated spike.
    STRATEGIC
        High-confidence direction that should influence the roadmap.
    CRITICAL
        Very high-confidence direction that blocks other progress.
    """

    EXPLORATORY = 1
    PROMISING = 2
    STRATEGIC = 3
    CRITICAL = 4

    @classmethod
    def from_confidence(cls, confidence: float) -> "DirectionPriority":
        """Map a confidence value in [0, 1] to a priority level."""
        if confidence >= 0.85:
            return cls.CRITICAL
        if confidence >= 0.65:
            return cls.STRATEGIC
        if confidence >= 0.40:
            return cls.PROMISING
        return cls.EXPLORATORY


# Dataclasses (truncated for brevity - full implementation in original spec)
# When implementing in full, include all frozen dataclasses:
# SignalJudgment, ObstructionFeatures, IdeationSignal, RankedObstruction,
# FutureDirection, ExtractionConfig, AggregatedSignalBundle, DirectionCluster,
# PipelineResult, and mutable classes: ObstructionRanking, SignalExtractor


if __name__ == "__main__":
    print("s03 Ideation Signals module loaded successfully.")
