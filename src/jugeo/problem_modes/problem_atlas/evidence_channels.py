"""Section 14.3 — Evidence Channels for the Unified Problem Atlas.

copilot: evidence channel registry and trust aggregation engine.

This module implements §14.3 of Theory2.tex, providing the machinery for
cataloging evidence channels, routing evidence to problem class requirements,
and computing aggregate trust scores.

An *evidence channel* is a named source of verification evidence, such as:
  - STATIC_ANALYSIS  : Results from static analysis tools (type checkers, linters)
  - TYPE_CHECKING    : Results from type system verification
  - TESTING          : Results from automated test suites
  - FORMAL_PROOF     : Machine-verified formal proofs
  - COPILOT_SYNTHESIS: AI-generated evidence and suggestions
  - RUNTIME_MONITORING: Runtime assertions and monitoring
  - HUMAN_REVIEW     : Manual code/design review
  - ORACLE_QUERY     : Query to an external oracle or reference

Channels contribute evidence items with associated trust scores.  The
ChannelRouter maps an evidence collection to the relevant problem class
requirements, and TrustLevelComputer aggregates trust across channels.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Iterator, Mapping, Sequence, TypeAlias

try:
    from jugeo.problem_modes.problem_atlas.models import (
        EvidenceRequirement,
        ProblemClass,
        ConjunctionMode,
    )
except ImportError:
    EvidenceRequirement = object  # type: ignore[assignment,misc]
    ProblemClass = object  # type: ignore[assignment,misc]
    ConjunctionMode = None  # type: ignore[assignment]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = object  # type: ignore[assignment,misc]
    CertificateStatus = None  # type: ignore[assignment]

try:
    from jugeo.evidence.channels import EvidenceChannel
except ImportError:
    EvidenceChannel = object  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustProfile
except ImportError:
    TrustProfile = object  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ChannelId: TypeAlias = str
TrustScore: TypeAlias = float
MetadataPairs: TypeAlias = tuple[tuple[str, str], ...]


# ---------------------------------------------------------------------------
# ChannelKind
# ---------------------------------------------------------------------------


class ChannelKind(str, Enum):
    """Enumeration of evidence channel kinds.

    Each kind corresponds to a distinct class of verification source.
    Automated kinds do not require human intervention; human-driven kinds do.

    Attributes:
        STATIC_ANALYSIS: Results from static analysis tools such as linters
            and abstract interpreters.
        TYPE_CHECKING: Results from a type system (e.g. mypy, pyright).
        TESTING: Results from automated test suites (unit, integration, e2e).
        FORMAL_PROOF: Machine-verified proofs produced by proof assistants
            such as Lean, Coq, or Isabelle.
        COPILOT_SYNTHESIS: AI-generated evidence, suggestions, and artefacts
            produced by a Copilot or similar LLM-based system.
        RUNTIME_MONITORING: Evidence gathered from runtime assertions,
            invariant checks, and observability tooling.
        HUMAN_REVIEW: Manual review by a human engineer or domain expert.
        ORACLE_QUERY: A query dispatched to an external trusted oracle or
            reference database.
        COMPOSITE: A channel that aggregates multiple sub-channels.
        UNKNOWN: Placeholder for channels whose kind cannot be determined.
    """

    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    TYPE_CHECKING = "TYPE_CHECKING"
    TESTING = "TESTING"
    FORMAL_PROOF = "FORMAL_PROOF"
    COPILOT_SYNTHESIS = "COPILOT_SYNTHESIS"
    RUNTIME_MONITORING = "RUNTIME_MONITORING"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    ORACLE_QUERY = "ORACLE_QUERY"
    COMPOSITE = "COMPOSITE"
    UNKNOWN = "UNKNOWN"

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_automated(self) -> bool:
        """Return True when the channel requires no human intervention.

        Returns:
            bool: True for STATIC_ANALYSIS, TYPE_CHECKING, TESTING,
                FORMAL_PROOF, and RUNTIME_MONITORING; False otherwise.
        """
        return self in (
            ChannelKind.STATIC_ANALYSIS,
            ChannelKind.TYPE_CHECKING,
            ChannelKind.TESTING,
            ChannelKind.FORMAL_PROOF,
            ChannelKind.RUNTIME_MONITORING,
        )

    def requires_human(self) -> bool:
        """Return True when the channel requires a human actor.

        Returns:
            bool: True for HUMAN_REVIEW and ORACLE_QUERY; False otherwise.
        """
        return self in (ChannelKind.HUMAN_REVIEW, ChannelKind.ORACLE_QUERY)

    def base_trust_weight(self) -> float:
        """Return the canonical base trust weight for this channel kind.

        The trust weight reflects the reliability tier assigned by §14.3 of
        Theory2.tex.  Values range from 0.5 (lowest) to 1.0 (highest).

        Returns:
            float: A value in [0.5, 1.0].
        """
        weights: dict[ChannelKind, float] = {
            ChannelKind.FORMAL_PROOF: 1.0,
            ChannelKind.HUMAN_REVIEW: 0.9,
            ChannelKind.TYPE_CHECKING: 0.85,
            ChannelKind.TESTING: 0.75,
            ChannelKind.STATIC_ANALYSIS: 0.7,
            ChannelKind.RUNTIME_MONITORING: 0.65,
            ChannelKind.COPILOT_SYNTHESIS: 0.6,
            ChannelKind.ORACLE_QUERY: 0.8,
        }
        return weights.get(self, 0.5)


# ---------------------------------------------------------------------------
# ChannelPriority
# ---------------------------------------------------------------------------


class ChannelPriority(IntEnum):
    """Ordered priority levels for evidence channels.

    Higher numeric values indicate greater urgency.  Channels with priority
    CRITICAL or HIGH are considered *blocking*: if their evidence is absent
    or below threshold the overall verification is stalled.

    Attributes:
        CRITICAL: Must be satisfied; absence halts progress.
        HIGH: Strongly desired; absence is a significant gap.
        MEDIUM: Standard importance.
        LOW: Nice-to-have supplementary evidence.
        OPTIONAL: Informational only; never blocking.
    """

    CRITICAL = 5
    HIGH = 4
    MEDIUM = 3
    LOW = 2
    OPTIONAL = 1

    def affects_blocking(self) -> bool:
        """Return True when this priority level can cause a blocking gap.

        Returns:
            bool: True for CRITICAL and HIGH.
        """
        return self in (ChannelPriority.CRITICAL, ChannelPriority.HIGH)


# ---------------------------------------------------------------------------
# ChannelDescriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelDescriptor:
    """Immutable descriptor for a single evidence channel.

    Captures all static configuration for a channel: its identity, kind,
    trust weight, the problem categories it covers, and arbitrary metadata
    stored as an ordered sequence of key-value pairs.

    Attributes:
        channel_id: Unique machine-readable identifier (e.g. ``"static_analysis_01"``).
        name: Human-readable name for the channel.
        kind: The :class:`ChannelKind` classification.
        description: Free-text description of what the channel provides.
        priority: Operational :class:`ChannelPriority` of this channel.
        base_trust_weight: Default trust weight in ``[0.0, 1.0]``.
        supported_problem_categories: Tuple of problem-category strings this
            channel is applicable to.  An empty tuple means *all* categories.
        metadata: Immutable sequence of ``(key, value)`` string pairs carrying
            channel-specific configuration.
    """

    channel_id: str
    name: str
    kind: ChannelKind
    description: str
    priority: ChannelPriority
    base_trust_weight: float
    supported_problem_categories: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

    def get_metadata(self, key: str) -> str | None:
        """Look up a metadata value by key.

        Args:
            key: The metadata key to look up.

        Returns:
            The associated string value, or ``None`` if the key is absent.
        """
        for k, v in self.metadata:
            if k == key:
                return v
        return None

    def supports_category(self, category: str) -> bool:
        """Return True when this channel applies to *category*.

        An empty ``supported_problem_categories`` tuple signals universal
        applicability.

        Args:
            category: Problem-category string to test.

        Returns:
            bool: True if applicable; False otherwise.
        """
        if not self.supported_problem_categories:
            return True
        return category in self.supported_problem_categories

    def effective_trust_weight(self, context_boost: float = 0.0) -> float:
        """Return the effective trust weight after applying a contextual boost.

        The result is clamped to ``[0.0, 1.0]`` so that boosts cannot push
        the weight beyond the maximum.

        Args:
            context_boost: Non-negative additive boost to apply on top of the
                ``base_trust_weight``.  Negative values are silently treated
                as zero.

        Returns:
            float: Clamped effective trust weight.
        """
        boost = max(0.0, context_boost)
        return min(1.0, self.base_trust_weight + boost)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the descriptor to a plain dictionary.

        Returns:
            dict[str, Any]: JSON-serialisable representation of this descriptor.
        """
        return {
            "channel_id": self.channel_id,
            "name": self.name,
            "kind": self.kind.value,
            "description": self.description,
            "priority": self.priority.name,
            "priority_value": int(self.priority),
            "base_trust_weight": self.base_trust_weight,
            "supported_problem_categories": list(self.supported_problem_categories),
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# ChannelContribution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelContribution:
    """An immutable record of the evidence contributed by one channel run.

    Attributes:
        channel_id: Identifier matching a :class:`ChannelDescriptor`.
        channel_name: Human-readable channel name (denormalised for display).
        trust_score: Raw trust score in ``[0.0, 1.0]`` reported by the channel.
        evidence_count: Number of distinct evidence items contributed.
        passed: Whether the channel's own pass/fail verdict is positive.
        notes: Free-text notes from the channel run (warnings, caveats, etc.).
        timestamp: ISO-8601 timestamp string of when the contribution was made.
    """

    channel_id: str
    channel_name: str
    trust_score: float
    evidence_count: int
    passed: bool
    notes: str
    timestamp: str

    def weighted_score(self, weight: float) -> float:
        """Return the trust score scaled by an external weight.

        Args:
            weight: Multiplicative weight to apply; should be in ``[0.0, 1.0]``.

        Returns:
            float: Product of ``trust_score`` and ``weight``.
        """
        return self.trust_score * weight

    def to_dict(self) -> dict[str, Any]:
        """Serialize this contribution to a plain dictionary.

        Returns:
            dict[str, Any]: JSON-serialisable representation.
        """
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "trust_score": self.trust_score,
            "evidence_count": self.evidence_count,
            "passed": self.passed,
            "notes": self.notes,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# ChannelRegistry
# ---------------------------------------------------------------------------


class ChannelRegistry:
    """A mutable, in-process registry of :class:`ChannelDescriptor` objects.

    The registry is the authoritative source of channel metadata.  All other
    components that need channel configuration should obtain it from here
    rather than hard-coding values.

    The registry is keyed by ``channel_id``; duplicate registration raises
    :class:`ValueError`.
    """

    def __init__(self) -> None:
        """Initialise an empty registry."""
        self._channels: dict[str, ChannelDescriptor] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, descriptor: ChannelDescriptor) -> None:
        """Add a channel descriptor to the registry.

        Args:
            descriptor: The :class:`ChannelDescriptor` to register.

        Raises:
            ValueError: If a channel with the same ``channel_id`` is already
                registered.
        """
        if descriptor.channel_id in self._channels:
            raise ValueError(
                f"Channel '{descriptor.channel_id}' is already registered. "
                "Unregister it first if you need to replace it."
            )
        self._channels[descriptor.channel_id] = descriptor

    def unregister(self, channel_id: str) -> None:
        """Remove a channel from the registry.

        Args:
            channel_id: The identifier of the channel to remove.

        Raises:
            KeyError: If no channel with that identifier exists.
        """
        if channel_id not in self._channels:
            raise KeyError(f"No channel with id '{channel_id}' is registered.")
        del self._channels[channel_id]

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, channel_id: str) -> ChannelDescriptor | None:
        """Return the descriptor for *channel_id*, or ``None``.

        Args:
            channel_id: Identifier to look up.

        Returns:
            :class:`ChannelDescriptor` or ``None``.
        """
        return self._channels.get(channel_id)

    def get_by_name(self, name: str) -> ChannelDescriptor | None:
        """Return the first descriptor whose ``name`` matches exactly.

        Args:
            name: Exact channel name to search for.

        Returns:
            :class:`ChannelDescriptor` or ``None`` if not found.
        """
        for descriptor in self._channels.values():
            if descriptor.name == name:
                return descriptor
        return None

    def get_by_kind(self, kind: ChannelKind) -> list[ChannelDescriptor]:
        """Return all descriptors whose ``kind`` matches.

        Args:
            kind: The :class:`ChannelKind` to filter by.

        Returns:
            list[ChannelDescriptor]: Possibly empty list of matching descriptors.
        """
        return [d for d in self._channels.values() if d.kind == kind]

    def list_all(self) -> list[ChannelDescriptor]:
        """Return every registered descriptor.

        Returns:
            list[ChannelDescriptor]: All registered channels in insertion order.
        """
        return list(self._channels.values())

    def list_automated(self) -> list[ChannelDescriptor]:
        """Return all descriptors whose kind :meth:`ChannelKind.is_automated`.

        Returns:
            list[ChannelDescriptor]: Automated channels only.
        """
        return [d for d in self._channels.values() if d.kind.is_automated()]

    def list_human_driven(self) -> list[ChannelDescriptor]:
        """Return all descriptors whose kind :meth:`ChannelKind.requires_human`.

        Returns:
            list[ChannelDescriptor]: Human-driven channels only.
        """
        return [d for d in self._channels.values() if d.kind.requires_human()]

    def count(self) -> int:
        """Return the number of registered channels.

        Returns:
            int: Total channel count.
        """
        return len(self._channels)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "ChannelRegistry":
        """Construct a registry pre-populated with all 8 standard channels.

        Returns:
            ChannelRegistry: A new registry containing descriptors for
                STATIC_ANALYSIS, TYPE_CHECKING, TESTING, FORMAL_PROOF,
                COPILOT_SYNTHESIS, RUNTIME_MONITORING, HUMAN_REVIEW, and
                ORACLE_QUERY.
        """
        registry = cls()
        for descriptor in STANDARD_CHANNELS.values():
            registry.register(descriptor)
        return registry


# ---------------------------------------------------------------------------
# ChannelRouter
# ---------------------------------------------------------------------------


class ChannelRouter:
    """Routes a collection of channel contributions to problem requirements.

    Given a set of :class:`ChannelContribution` objects and an
    :class:`EvidenceRequirement`, the router determines which contributions
    are relevant, computes a routing score, and identifies gaps.

    Args:
        registry: The :class:`ChannelRegistry` used for channel lookups.
    """

    def __init__(self, registry: ChannelRegistry) -> None:
        """Initialise the router with a channel registry.

        Args:
            registry: Source of channel metadata.
        """
        self._registry = registry

    def route_contributions(
        self,
        contributions: list[ChannelContribution],
        requirement: EvidenceRequirement,
    ) -> dict[str, ChannelContribution]:
        """Map channel contributions to the channels required by *requirement*.

        Only contributions whose ``channel_id`` appears in the requirement's
        required channel list are included in the result.  When
        ``EvidenceRequirement`` is the fallback ``object`` type (import failed),
        all contributions are returned keyed by their own id.

        Args:
            contributions: All available channel contributions.
            requirement: The evidence requirement specifying which channels
                are needed.

        Returns:
            dict[str, ChannelContribution]: Mapping from channel_id to the
                first matching contribution for that channel.
        """
        by_id = {c.channel_id: c for c in contributions}

        # Graceful degradation when models are unavailable.
        required: list[str]
        if hasattr(requirement, "required_channels"):
            required = list(requirement.required_channels)
        else:
            return by_id

        return {cid: by_id[cid] for cid in required if cid in by_id}

    def find_covering_channels(
        self,
        requirement: EvidenceRequirement,
        available_channels: list[str],
    ) -> list[str]:
        """Return the subset of *available_channels* that covers *requirement*.

        A channel "covers" the requirement if it appears in the requirement's
        required channel list.

        Args:
            requirement: Evidence requirement defining needed channels.
            available_channels: Channel IDs currently available.

        Returns:
            list[str]: Channels from *available_channels* that are required.
        """
        if not hasattr(requirement, "required_channels"):
            return list(available_channels)
        required: set[str] = set(requirement.required_channels)
        return [c for c in available_channels if c in required]

    def compute_routing_score(
        self,
        contributions: dict[str, ChannelContribution],
        requirement: EvidenceRequirement,
    ) -> float:
        """Compute a scalar score for how well *contributions* cover *requirement*.

        The score is the fraction of required channels for which a contribution
        exists, weighted by the contribution's trust score.

        Args:
            contributions: Routed contributions keyed by channel_id.
            requirement: The requirement being scored against.

        Returns:
            float: Score in ``[0.0, 1.0]``; 1.0 means full coverage at full trust.
        """
        if not hasattr(requirement, "required_channels"):
            if not contributions:
                return 0.0
            return sum(c.trust_score for c in contributions.values()) / len(contributions)

        required: list[str] = list(requirement.required_channels)
        if not required:
            return 1.0

        total = 0.0
        for cid in required:
            if cid in contributions:
                total += contributions[cid].trust_score

        return total / len(required)

    def get_missing_channels(
        self,
        contributions: dict[str, ChannelContribution],
        requirement: EvidenceRequirement,
    ) -> list[str]:
        """Return the required channel IDs not present in *contributions*.

        Args:
            contributions: Currently available contributions, keyed by channel_id.
            requirement: The requirement whose required channels we check.

        Returns:
            list[str]: Channel IDs that are required but have no contribution.
        """
        if not hasattr(requirement, "required_channels"):
            return []
        return [
            cid
            for cid in requirement.required_channels
            if cid not in contributions
        ]

    def prioritize_channels(self, channels: list[str]) -> list[str]:
        """Sort *channels* by their registered priority (highest first).

        Channels not found in the registry are placed at the end with a
        notional priority of 0.

        Args:
            channels: List of channel IDs to sort.

        Returns:
            list[str]: Channels sorted descending by :class:`ChannelPriority`.
        """
        def priority_key(cid: str) -> int:
            descriptor = self._registry.get(cid)
            return int(descriptor.priority) if descriptor else 0

        return sorted(channels, key=priority_key, reverse=True)


# ---------------------------------------------------------------------------
# TrustLevelComputer
# ---------------------------------------------------------------------------


class TrustLevelComputer:
    """Computes aggregate trust scores from a collection of channel contributions.

    Supports multiple aggregation modes as defined by :class:`ConjunctionMode`:
    - ALL  → bottleneck (minimum trust);
    - ANY  → best-effort (maximum trust);
    - WEIGHTED → weighted average using registry weights.

    Args:
        registry: :class:`ChannelRegistry` used for weight lookups.
    """

    def __init__(self, registry: ChannelRegistry) -> None:
        """Initialise with the channel registry.

        Args:
            registry: Source of channel metadata and weights.
        """
        self._registry = registry

    def compute_aggregate_trust(
        self,
        contributions: list[ChannelContribution],
        mode: Any,  # ConjunctionMode when available
    ) -> float:
        """Aggregate trust across *contributions* according to *mode*.

        Args:
            contributions: List of channel contributions to aggregate.
            mode: A :class:`ConjunctionMode` value.  When the models package
                is unavailable the mode is matched by string value.

        Returns:
            float: Aggregated trust score in ``[0.0, 1.0]``.
        """
        if not contributions:
            return 0.0

        mode_str = str(mode).upper() if mode is not None else "ALL"
        if "ALL" in mode_str:
            return self.compute_minimum_trust(contributions)
        if "ANY" in mode_str:
            return self.compute_maximum_trust(contributions)
        if "WEIGHTED" in mode_str:
            return self.compute_weighted_average(contributions)
        # Fallback: weighted average.
        return self.compute_weighted_average(contributions)

    def compute_weighted_average(
        self, contributions: list[ChannelContribution]
    ) -> float:
        """Return the weighted average trust score across all contributions.

        Each contribution is weighted by its channel's ``base_trust_weight``
        as retrieved from the registry.  Channels not found in the registry
        receive a neutral weight of 0.5.

        Args:
            contributions: Contributions to average.

        Returns:
            float: Weighted average trust score in ``[0.0, 1.0]``.
        """
        if not contributions:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0
        for contrib in contributions:
            descriptor = self._registry.get(contrib.channel_id)
            w = descriptor.base_trust_weight if descriptor else 0.5
            weighted_sum += contrib.trust_score * w
            total_weight += w

        if total_weight == 0.0:
            return 0.0
        return self.normalize_trust(weighted_sum / total_weight)

    def compute_minimum_trust(
        self, contributions: list[ChannelContribution]
    ) -> float:
        """Return the minimum (bottleneck) trust across all contributions.

        Used for ALL/conjunction mode: the chain is only as strong as its
        weakest link.

        Args:
            contributions: Contributions to aggregate.

        Returns:
            float: Minimum trust score; 0.0 if the list is empty.
        """
        if not contributions:
            return 0.0
        return self.normalize_trust(min(c.trust_score for c in contributions))

    def compute_maximum_trust(
        self, contributions: list[ChannelContribution]
    ) -> float:
        """Return the maximum (best-effort) trust across all contributions.

        Used for ANY/disjunction mode: the aggregate is as good as the best
        single channel.

        Args:
            contributions: Contributions to aggregate.

        Returns:
            float: Maximum trust score; 0.0 if the list is empty.
        """
        if not contributions:
            return 0.0
        return self.normalize_trust(max(c.trust_score for c in contributions))

    def compute_threshold_trust(
        self,
        contributions: list[ChannelContribution],
        threshold: float,
    ) -> bool:
        """Return True when the weighted average trust meets *threshold*.

        Args:
            contributions: Contributions to check.
            threshold: Minimum required trust level in ``[0.0, 1.0]``.

        Returns:
            bool: True if aggregate trust ≥ threshold.
        """
        return self.compute_weighted_average(contributions) >= threshold

    def compute_majority_trust(
        self, contributions: list[ChannelContribution]
    ) -> float:
        """Return the average trust of the top 50% of contributions.

        Contributions are ranked by trust score descending.  The top half
        (rounded up) is averaged.  This models a "majority quorum" trust
        policy.

        Args:
            contributions: Contributions to aggregate.

        Returns:
            float: Majority trust score in ``[0.0, 1.0]``.
        """
        if not contributions:
            return 0.0
        ranked = sorted(contributions, key=lambda c: c.trust_score, reverse=True)
        top_n = max(1, math.ceil(len(ranked) / 2))
        top = ranked[:top_n]
        return self.normalize_trust(sum(c.trust_score for c in top) / len(top))

    def normalize_trust(self, raw_score: float) -> float:
        """Clamp *raw_score* to the valid trust range ``[0.0, 1.0]``.

        Args:
            raw_score: Unclamped trust score.

        Returns:
            float: Score clamped to ``[0.0, 1.0]``.
        """
        return max(0.0, min(1.0, raw_score))


# ---------------------------------------------------------------------------
# ChannelCompatibilityChecker
# ---------------------------------------------------------------------------


class ChannelCompatibilityChecker:
    """Checks compatibility between channels and problem categories.

    Compatibility is determined by inspecting each channel's
    ``supported_problem_categories`` field.  An empty tuple in that field
    means the channel is universally compatible.

    Args:
        registry: :class:`ChannelRegistry` used to look up channel descriptors.
    """

    def __init__(self, registry: ChannelRegistry) -> None:
        """Initialise with the channel registry.

        Args:
            registry: Registry from which channel descriptors are fetched.
        """
        self._registry = registry

    def check_requirement_satisfied(
        self,
        requirement: EvidenceRequirement,
        contributions: list[ChannelContribution],
    ) -> bool:
        """Return True when *contributions* satisfy *requirement*.

        The check compares the set of contributing channel IDs against the
        requirement's required channel list.  All required channels must have
        at least one contribution with ``passed=True``.

        Args:
            requirement: The requirement to satisfy.
            contributions: Available evidence contributions.

        Returns:
            bool: True if all required channels have a passing contribution.
        """
        if not hasattr(requirement, "required_channels"):
            return bool(contributions)

        passing_ids = {c.channel_id for c in contributions if c.passed}
        return all(cid in passing_ids for cid in requirement.required_channels)

    def check_channel_compatible(
        self, channel_id: str, problem_category: str
    ) -> bool:
        """Return True when *channel_id* is compatible with *problem_category*.

        Args:
            channel_id: The channel to check.
            problem_category: The problem category string.

        Returns:
            bool: True if the channel is compatible; also True when the channel
                is not registered (fail-open).
        """
        descriptor = self._registry.get(channel_id)
        if descriptor is None:
            return True  # Unknown channels are not blocked.
        return descriptor.supports_category(problem_category)

    def find_incompatible_channels(
        self, channels: list[str], problem_category: str
    ) -> list[str]:
        """Return the subset of *channels* that are incompatible with *problem_category*.

        Args:
            channels: Channel IDs to check.
            problem_category: The problem category against which compatibility
                is evaluated.

        Returns:
            list[str]: Channel IDs that are explicitly incompatible.
        """
        return [
            cid
            for cid in channels
            if not self.check_channel_compatible(cid, problem_category)
        ]

    def suggest_additional_channels(
        self,
        current: list[str],
        requirement: EvidenceRequirement,
    ) -> list[str]:
        """Suggest additional channels that would help satisfy *requirement*.

        Returns channels that are in the requirement's required list but are
        absent from *current*, i.e. channels that would fill coverage gaps.

        Args:
            current: Channel IDs already in use.
            requirement: The requirement we are trying to satisfy.

        Returns:
            list[str]: Suggested additional channel IDs.
        """
        if not hasattr(requirement, "required_channels"):
            return []
        current_set = set(current)
        return [
            cid
            for cid in requirement.required_channels
            if cid not in current_set
        ]


# ---------------------------------------------------------------------------
# EvidenceAggregator
# ---------------------------------------------------------------------------


class EvidenceAggregator:
    """Aggregates channel contributions into a unified trust assessment.

    The aggregator combines multiple :class:`ChannelContribution` objects
    into a single result dictionary containing trust score, coverage metrics,
    and per-category breakdowns.

    Args:
        registry: :class:`ChannelRegistry` for weight and kind lookups.
    """

    def __init__(self, registry: ChannelRegistry) -> None:
        """Initialise the aggregator.

        Args:
            registry: The channel registry.
        """
        self._registry = registry
        self._computer = TrustLevelComputer(registry)

    def aggregate(
        self,
        contributions: list[ChannelContribution],
        mode: Any,
    ) -> dict[str, Any]:
        """Aggregate *contributions* and return a result dictionary.

        Args:
            contributions: Channel contributions to aggregate.
            mode: :class:`ConjunctionMode` controlling the aggregation strategy.

        Returns:
            dict[str, Any]: Result with keys ``trust_score`` (float),
                ``passed`` (bool), ``channel_count`` (int), and
                ``coverage`` (float, always 1.0 when no requirement is given).
        """
        trust = self._computer.compute_aggregate_trust(contributions, mode)
        passed_count = sum(1 for c in contributions if c.passed)
        return {
            "trust_score": trust,
            "passed": trust >= 0.5 and passed_count >= max(1, len(contributions) // 2),
            "channel_count": len(contributions),
            "coverage": 1.0 if contributions else 0.0,
            "contributions": [c.to_dict() for c in contributions],
        }

    def build_contribution(
        self,
        channel_id: str,
        trust_score: float,
        evidence_count: int,
        passed: bool,
        notes: str = "",
    ) -> ChannelContribution:
        """Construct a :class:`ChannelContribution` for the given channel.

        The channel name is resolved from the registry; if the channel is
        not registered the name falls back to the channel_id.

        Args:
            channel_id: The contributing channel's identifier.
            trust_score: Trust score reported for this run.
            evidence_count: Number of evidence items produced.
            passed: Whether the channel's pass verdict is positive.
            notes: Optional free-text notes from the run.

        Returns:
            ChannelContribution: The constructed contribution record.
        """
        import datetime

        descriptor = self._registry.get(channel_id)
        name = descriptor.name if descriptor else channel_id
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        clamped = max(0.0, min(1.0, trust_score))
        return ChannelContribution(
            channel_id=channel_id,
            channel_name=name,
            trust_score=clamped,
            evidence_count=evidence_count,
            passed=passed,
            notes=notes,
            timestamp=ts,
        )

    def aggregate_by_category(
        self, contributions: list[ChannelContribution]
    ) -> dict[str, float]:
        """Group contributions by channel kind and return average trust per kind.

        Args:
            contributions: Contributions to categorise.

        Returns:
            dict[str, float]: Mapping from :class:`ChannelKind` value string to
                the average trust score of contributions in that category.
        """
        buckets: dict[str, list[float]] = defaultdict(list)
        for contrib in contributions:
            descriptor = self._registry.get(contrib.channel_id)
            kind_str = descriptor.kind.value if descriptor else ChannelKind.UNKNOWN.value
            buckets[kind_str].append(contrib.trust_score)

        return {
            kind: sum(scores) / len(scores)
            for kind, scores in buckets.items()
        }

    def compute_coverage(
        self,
        contributions: list[ChannelContribution],
        required_channels: list[str],
    ) -> float:
        """Return the fraction of *required_channels* covered by *contributions*.

        A channel is "covered" when at least one contribution with that
        ``channel_id`` exists in the list.

        Args:
            contributions: Available contributions.
            required_channels: Channel IDs that must be covered.

        Returns:
            float: Coverage ratio in ``[0.0, 1.0]``; 1.0 if *required_channels*
                is empty.
        """
        if not required_channels:
            return 1.0
        present = {c.channel_id for c in contributions}
        covered = sum(1 for cid in required_channels if cid in present)
        return covered / len(required_channels)

    def to_summary_dict(
        self,
        contributions: list[ChannelContribution],
        mode: Any,
    ) -> dict[str, Any]:
        """Produce a comprehensive summary dictionary for *contributions*.

        Combines the aggregate result, per-category breakdown, evidence
        totals, and per-channel details into one structure.

        Args:
            contributions: Contributions to summarise.
            mode: Aggregation mode.

        Returns:
            dict[str, Any]: Summary with keys ``aggregate``, ``by_category``,
                ``total_evidence``, ``channels``.
        """
        aggregate = self.aggregate(contributions, mode)
        by_category = self.aggregate_by_category(contributions)
        total_evidence = sum(c.evidence_count for c in contributions)
        channels = [c.to_dict() for c in contributions]

        return {
            "aggregate": aggregate,
            "by_category": by_category,
            "total_evidence": total_evidence,
            "channels": channels,
            "mode": str(mode),
        }


# ---------------------------------------------------------------------------
# Standard channel descriptors
# ---------------------------------------------------------------------------

STANDARD_CHANNELS: dict[str, ChannelDescriptor] = {
    "STATIC_ANALYSIS": ChannelDescriptor(
        channel_id="STATIC_ANALYSIS",
        name="Static Analysis",
        kind=ChannelKind.STATIC_ANALYSIS,
        description=(
            "Evidence from static analysis tools such as linters, abstract "
            "interpreters, and data-flow analysers.  Covers unreachable code, "
            "undefined variables, and common anti-patterns."
        ),
        priority=ChannelPriority.HIGH,
        base_trust_weight=ChannelKind.STATIC_ANALYSIS.base_trust_weight(),
        supported_problem_categories=(),
        metadata=(
            ("tool_hint", "ruff,pylint,flake8"),
            ("scope", "whole_project"),
        ),
    ),
    "TYPE_CHECKING": ChannelDescriptor(
        channel_id="TYPE_CHECKING",
        name="Type Checking",
        kind=ChannelKind.TYPE_CHECKING,
        description=(
            "Evidence from a gradual or strict type checker (e.g. mypy, "
            "pyright).  Validates type annotations and detects type errors "
            "at compile-time."
        ),
        priority=ChannelPriority.HIGH,
        base_trust_weight=ChannelKind.TYPE_CHECKING.base_trust_weight(),
        supported_problem_categories=(),
        metadata=(
            ("tool_hint", "mypy,pyright"),
            ("strict_mode", "recommended"),
        ),
    ),
    "TESTING": ChannelDescriptor(
        channel_id="TESTING",
        name="Automated Testing",
        kind=ChannelKind.TESTING,
        description=(
            "Evidence from automated test suites including unit tests, "
            "integration tests, property-based tests, and end-to-end tests.  "
            "Coverage and pass-rate are the primary metrics."
        ),
        priority=ChannelPriority.CRITICAL,
        base_trust_weight=ChannelKind.TESTING.base_trust_weight(),
        supported_problem_categories=(),
        metadata=(
            ("tool_hint", "pytest,hypothesis"),
            ("min_coverage", "0.8"),
        ),
    ),
    "FORMAL_PROOF": ChannelDescriptor(
        channel_id="FORMAL_PROOF",
        name="Formal Proof",
        kind=ChannelKind.FORMAL_PROOF,
        description=(
            "Machine-verified formal proofs produced by interactive proof "
            "assistants such as Lean 4, Coq, or Isabelle/HOL.  Provides the "
            "highest possible trust level."
        ),
        priority=ChannelPriority.CRITICAL,
        base_trust_weight=ChannelKind.FORMAL_PROOF.base_trust_weight(),
        supported_problem_categories=(
            "VERIFICATION",
            "OPTIMIZATION",
            "INFERENCE",
        ),
        metadata=(
            ("tool_hint", "lean4,coq"),
            ("proof_style", "constructive"),
        ),
    ),
    "COPILOT_SYNTHESIS": ChannelDescriptor(
        channel_id="COPILOT_SYNTHESIS",
        name="Copilot Synthesis",
        kind=ChannelKind.COPILOT_SYNTHESIS,
        description=(
            "AI-generated evidence and artefacts from GitHub Copilot or a "
            "compatible LLM orchestration layer.  Trust is lower than human "
            "review but higher than nothing; should always be paired with at "
            "least one automated channel."
        ),
        priority=ChannelPriority.MEDIUM,
        base_trust_weight=ChannelKind.COPILOT_SYNTHESIS.base_trust_weight(),
        supported_problem_categories=(),
        metadata=(
            ("model_family", "gpt-4,claude"),
            ("review_required", "true"),
        ),
    ),
    "RUNTIME_MONITORING": ChannelDescriptor(
        channel_id="RUNTIME_MONITORING",
        name="Runtime Monitoring",
        kind=ChannelKind.RUNTIME_MONITORING,
        description=(
            "Evidence gathered at runtime from assertions, invariant checkers, "
            "contract libraries, and observability tooling.  Complements static "
            "analysis by catching issues that only manifest at execution time."
        ),
        priority=ChannelPriority.MEDIUM,
        base_trust_weight=ChannelKind.RUNTIME_MONITORING.base_trust_weight(),
        supported_problem_categories=(),
        metadata=(
            ("tool_hint", "deal,icontract,sentry"),
            ("sampling_rate", "1.0"),
        ),
    ),
    "HUMAN_REVIEW": ChannelDescriptor(
        channel_id="HUMAN_REVIEW",
        name="Human Review",
        kind=ChannelKind.HUMAN_REVIEW,
        description=(
            "Manual review conducted by a human engineer, architect, or domain "
            "expert.  High trust but resource-intensive; typically reserved for "
            "security-sensitive or architecturally significant changes."
        ),
        priority=ChannelPriority.HIGH,
        base_trust_weight=ChannelKind.HUMAN_REVIEW.base_trust_weight(),
        supported_problem_categories=(),
        metadata=(
            ("review_type", "pull_request,design_doc"),
            ("min_reviewers", "1"),
        ),
    ),
    "ORACLE_QUERY": ChannelDescriptor(
        channel_id="ORACLE_QUERY",
        name="Oracle Query",
        kind=ChannelKind.ORACLE_QUERY,
        description=(
            "A query dispatched to a trusted external oracle or reference "
            "database (e.g. SMT solver, constraint oracle, verified knowledge "
            "base).  Trust level depends on the oracle's certification."
        ),
        priority=ChannelPriority.MEDIUM,
        base_trust_weight=ChannelKind.ORACLE_QUERY.base_trust_weight(),
        supported_problem_categories=(
            "DECISION",
            "VERIFICATION",
            "CLASSIFICATION",
        ),
        metadata=(
            ("oracle_type", "smt,constraint,knowledge"),
            ("timeout_seconds", "30"),
        ),
    ),
}


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def get_standard_registry() -> ChannelRegistry:
    """Return a :class:`ChannelRegistry` pre-populated with standard channels.

    Returns:
        ChannelRegistry: Registry containing all 8 standard channels.
    """
    return ChannelRegistry.default()


def compute_trust(
    contributions: list[ChannelContribution],
    mode: str = "ALL",
) -> float:
    """Compute aggregate trust for a list of contributions using a named mode.

    This is a module-level convenience wrapper around
    :class:`TrustLevelComputer`.

    Args:
        contributions: Channel contributions to aggregate.
        mode: Aggregation mode string — one of ``"ALL"``, ``"ANY"``,
            or ``"WEIGHTED"``.  Defaults to ``"ALL"``.

    Returns:
        float: Aggregate trust score in ``[0.0, 1.0]``.
    """
    registry = get_standard_registry()
    computer = TrustLevelComputer(registry)
    mode_upper = mode.strip().upper()
    return computer.compute_aggregate_trust(contributions, mode_upper)


def route_evidence(
    contributions: list[ChannelContribution],
    requirement: EvidenceRequirement,
) -> dict[str, Any]:
    """Route *contributions* to *requirement* and return a routing summary.

    Args:
        contributions: All available channel contributions.
        requirement: The evidence requirement to route against.

    Returns:
        dict[str, Any]: Routing summary with keys ``routed``,
            ``routing_score``, ``missing_channels``, and ``prioritized``.
    """
    registry = get_standard_registry()
    router = ChannelRouter(registry)
    routed = router.route_contributions(contributions, requirement)
    score = router.compute_routing_score(routed, requirement)
    missing = router.get_missing_channels(routed, requirement)
    prioritized = router.prioritize_channels(list(routed.keys()))

    return {
        "routed": {cid: c.to_dict() for cid, c in routed.items()},
        "routing_score": score,
        "missing_channels": missing,
        "prioritized": prioritized,
    }




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.orchestration)
# ---------------------------------------------------------------------------


def atlas_site(atlas: Any) -> dict[str, Any]:
    """Interpret the problem atlas as a geometric site.

    The atlas IS a site — problem classes are objects, morphisms are
    subsumption relations, and covering families are evidence channels.

    Parameters
    ----------
    atlas : Any
        A ProblemAtlas, ProblemClassRegistry, or dict with atlas data.

    Returns
    -------
    dict[str, Any]
        Site representation with ``site_id``, ``objects``, ``morphisms``,
        ``covering_families``, and ``site_obj`` keys.
    """
    try:
        from jugeo.geometry.site import Site, build_site
    except ImportError:
        Site = None
        build_site = None

    atlas_id = getattr(atlas, "atlas_id", None) or getattr(atlas, "registry_id", None) or (
        atlas.get("atlas_id") if isinstance(atlas, dict) else "default_atlas"
    )
    classes = getattr(atlas, "classes", None) or getattr(atlas, "entries", None) or (
        atlas.get("classes") if isinstance(atlas, dict) else []
    )

    site: dict[str, Any] = {
        "site_id": f"atlas_site_{atlas_id}",
        "objects": [getattr(c, "name", str(c)) for c in (classes or [])],
        "morphisms": [],
        "covering_families": [],
        "site_obj": None,
    }

    if build_site is not None:
        try:
            s = build_site(objects=site["objects"], source="problem_atlas")
            site["site_obj"] = s
            site["morphisms"] = getattr(s, "morphisms", [])
            site["covering_families"] = getattr(s, "covering_families", [])
        except Exception:
            pass

    return site


def atlas_evidence_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to appropriate evidence channels.

    Evidence routing maps a problem instance to the set of evidence
    channels that can provide relevant verification evidence.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Routing record with ``problem_id``, ``channels``, ``trust_budget``,
        ``routing_strategy``, and ``channel_objs`` keys.
    """
    try:
        from jugeo.evidence.channels import route_to_channels, EvidenceChannel
    except ImportError:
        route_to_channels = None
        EvidenceChannel = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    routing: dict[str, Any] = {
        "problem_id": problem_id,
        "channels": ["STATIC_ANALYSIS", "TYPE_CHECKING", "TESTING"],
        "trust_budget": 1.0,
        "routing_strategy": f"default_for_{kind_str}",
        "channel_objs": [],
    }

    if route_to_channels is not None:
        try:
            channels = route_to_channels(problem)
            routing["channels"] = [getattr(c, "name", str(c)) for c in channels]
            routing["channel_objs"] = list(channels)
        except Exception:
            pass

    return routing


def atlas_orchestration_routing(problem: Any) -> dict[str, Any]:
    """Route a problem to the appropriate orchestration subsystem.

    Orchestration routing determines which solver, checker, or synthesis
    pipeline should handle a given problem class.

    Parameters
    ----------
    problem : Any
        A problem instance, ProblemClass, or dict.

    Returns
    -------
    dict[str, Any]
        Orchestration record with ``problem_id``, ``subsystem``,
        ``pipeline_steps``, ``priority``, and ``orchestrator_obj`` keys.
    """
    try:
        from jugeo.orchestration import route_problem, OrchestratorConfig
    except ImportError:
        route_problem = None
        OrchestratorConfig = None

    problem_id = getattr(problem, "problem_id", None) or getattr(problem, "class_id", None) or (
        problem.get("problem_id") if isinstance(problem, dict) else "unknown"
    )
    kind = getattr(problem, "kind", None) or (problem.get("kind") if isinstance(problem, dict) else None)
    kind_str = kind.value if hasattr(kind, "value") else str(kind) if kind else "general"

    orchestration: dict[str, Any] = {
        "problem_id": problem_id,
        "subsystem": f"{kind_str}_solver",
        "pipeline_steps": ["classify", "encode", "solve", "certify"],
        "priority": getattr(problem, "priority", 1) if not isinstance(problem, dict) else problem.get("priority", 1),
        "orchestrator_obj": None,
    }

    if route_problem is not None:
        try:
            result = route_problem(problem)
            orchestration["subsystem"] = getattr(result, "subsystem", orchestration["subsystem"])
            orchestration["pipeline_steps"] = getattr(result, "steps", orchestration["pipeline_steps"])
            orchestration["orchestrator_obj"] = result
        except Exception:
            pass

    return orchestration


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "ChannelKind",
    "ChannelPriority",
    # Dataclasses
    "ChannelDescriptor",
    "ChannelContribution",
    # Core classes
    "ChannelRegistry",
    "ChannelRouter",
    "TrustLevelComputer",
    "ChannelCompatibilityChecker",
    "EvidenceAggregator",
    # Module-level data
    "STANDARD_CHANNELS",
    # Module-level functions
    "get_standard_registry",
    "compute_trust",
    "route_evidence",
    # Type aliases
    "ChannelId",
    "TrustScore",
    "MetadataPairs",
    # Unified architecture cross-references
    "atlas_site",
    "atlas_evidence_routing",
    "atlas_orchestration_routing",
]

# copilot: shared-core marker for future LLM orchestration.
