"""Package manifest for mixed-evidence routing (theory2.tex Ch45)."""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

try:
    from jugeo.orchestration.mixed_evidence_routing.models import (
        EvidenceChannel,
        JurisdictionMap,
        RoutingStrategy,
    )
except Exception:
    class EvidenceChannel(str, enum.Enum):  # type: ignore[no-redef]
        Z3 = "z3"
        COPILOT_LLM = "copilot_llm"
        RUNTIME_WITNESS = "runtime_witness"
        HUMAN = "human"
        COMPOSITE = "composite"

    class RoutingStrategy(str, enum.Enum):  # type: ignore[no-redef]
        STRICT_JURISDICTION = "strict_jurisdiction"
        COST_OPTIMAL = "cost_optimal"
        LATENCY_OPTIMAL = "latency_optimal"
        TRUST_OPTIMAL = "trust_optimal"
        LOAD_BALANCED = "load_balanced"

    @dataclass(frozen=True, slots=True)  # type: ignore[no-redef]
    class JurisdictionMap:
        map_id: str
        channel: Any
        supported_claim_kinds: tuple
        max_complexity: float
        min_trust_level: str
        exclusions: tuple

        @classmethod
        def new(cls, channel, supported_claim_kinds, max_complexity=10.0,
                min_trust_level="UNVERIFIED", exclusions=()):
            return cls(map_id=str(uuid4()), channel=channel,
                       supported_claim_kinds=tuple(supported_claim_kinds),
                       max_complexity=max_complexity,
                       min_trust_level=min_trust_level,
                       exclusions=tuple(exclusions))

        def can_handle(self, claim):
            kind = claim.get("claim_kind", "")
            complexity = float(claim.get("complexity", 1.0))
            return kind in self.supported_claim_kinds and complexity <= self.max_complexity

        def to_dict(self):
            return {"map_id": self.map_id, "channel": self.channel,
                    "supported_claim_kinds": list(self.supported_claim_kinds),
                    "max_complexity": self.max_complexity,
                    "min_trust_level": self.min_trust_level,
                    "exclusions": list(self.exclusions)}

        def coverage_fraction(self, all_claim_kinds):
            if not all_claim_kinds:
                return 0.0
            covered = sum(1 for k in all_claim_kinds if k in self.supported_claim_kinds)
            return covered / len(all_claim_kinds)


# ---------------------------------------------------------------------------
# MixedEvidenceRoutingManifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MixedEvidenceRoutingManifest:
    """Immutable metadata record for the mixed-evidence routing package.

    Encodes the version, theoretical chapter reference, authorship, and a
    brief human-readable description of the package.  Provides lightweight
    validation to detect misconfiguration.

    Attributes:
        version: Semantic version string of the package.
        chapter_ref: Chapter reference from *theory2.tex*.
        package_name: Python package name.
        author: Authoring entity.
        description: Human-readable description of the package's purpose.
        created_at: Unix timestamp when this manifest was instantiated.
    """

    version: str = "1.0.0"
    chapter_ref: str = "Ch45"
    package_name: str = "mixed_evidence_routing"
    author: str = "jugeo"
    description: str = (
        "Mixed-evidence routing: dispatches theorem-proving tasks to the most "
        "appropriate evidence channel (Z3, Copilot LLM, runtime witness, human) "
        "based on claim kind, complexity, trust ceiling, cost, and latency "
        "constraints, as formalised in theory2.tex Chapter 45."
    )
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this manifest to a plain dictionary.

        Returns:
            A JSON-serialisable dictionary representation.
        """
        return {
            "version": self.version,
            "chapter_ref": self.chapter_ref,
            "package_name": self.package_name,
            "author": self.author,
            "description": self.description,
            "created_at": self.created_at,
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check this manifest for configuration errors.

        Returns:
            A (possibly empty) list of human-readable error strings.
        """
        errors: list[str] = []
        if not self.version:
            errors.append("version must not be empty")
        parts = self.version.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            errors.append(f"version {self.version!r} is not a valid semver string")
        if not self.chapter_ref.startswith("Ch"):
            errors.append(f"chapter_ref {self.chapter_ref!r} should start with 'Ch'")
        if not self.package_name:
            errors.append("package_name must not be empty")
        if not self.author:
            errors.append("author must not be empty")
        if not self.description:
            errors.append("description must not be empty")
        if self.created_at <= 0:
            errors.append("created_at must be a positive Unix timestamp")
        return errors

    def summary(self) -> str:
        """Return a one-line human-readable summary of this manifest.

        Returns:
            A compact summary string.
        """
        return (
            f"{self.package_name} v{self.version} ({self.chapter_ref}) "
            f"by {self.author}"
        )

    def is_valid(self) -> bool:
        """Return True if :meth:`validate` reports no errors.

        Returns:
            True when the manifest is well-formed.
        """
        return len(self.validate()) == 0


# ---------------------------------------------------------------------------
# ChannelRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChannelRegistry:
    """Mutable registry mapping evidence channel names to configuration dicts.

    Each entry holds a dictionary of channel-specific settings such as
    capacity, trust ceiling, cost per query, and average latency.

    Attributes:
        registry_id: Unique UUID identifier for this registry instance.
        channels: Mapping of channel-name strings to configuration dicts.
    """

    registry_id: str
    channels: dict[str, dict[str, Any]]

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> ChannelRegistry:
        """Create a registry pre-populated with sensible defaults.

        The default registry contains entries for all five evidence channels:
        Z3, COPILOT_LLM, RUNTIME_WITNESS, HUMAN, and COMPOSITE.

        Returns:
            A new :class:`ChannelRegistry` with default configurations.
        """
        channels: dict[str, dict[str, Any]] = {
            EvidenceChannel.Z3.value: {
                "capacity": 100,
                "trust_ceiling": "SOLVER_DISCHARGED",
                "cost_per_query": 0.01,
                "avg_latency_ms": 500.0,
                "enabled": True,
                "description": "Z3 SMT/SAT solver — mechanically verified outputs",
            },
            EvidenceChannel.COPILOT_LLM.value: {
                "capacity": 50,
                "trust_ceiling": "COPILOT_SUGGESTED",
                "cost_per_query": 0.05,
                "avg_latency_ms": 2000.0,
                "enabled": True,
                "description": "GitHub Copilot LLM — heuristic suggestions",
            },
            EvidenceChannel.RUNTIME_WITNESS.value: {
                "capacity": 200,
                "trust_ceiling": "RUNTIME_WITNESSED",
                "cost_per_query": 0.02,
                "avg_latency_ms": 1000.0,
                "enabled": True,
                "description": "Dynamic execution witnesses and property tests",
            },
            EvidenceChannel.HUMAN.value: {
                "capacity": 10,
                "trust_ceiling": "HUMAN_ATTESTED",
                "cost_per_query": 5.00,
                "avg_latency_ms": 3_600_000.0,  # 1 hour in ms
                "enabled": True,
                "description": "Human expert review for escalated claims",
            },
            EvidenceChannel.COMPOSITE.value: {
                "capacity": 20,
                "trust_ceiling": "SOLVER_DISCHARGED",
                "cost_per_query": 0.10,
                "avg_latency_ms": 3000.0,
                "enabled": True,
                "description": "Composite channel combining multiple sub-channels",
            },
        }
        return cls(registry_id=str(uuid4()), channels=channels)

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def register(self, channel_name: str, config: dict[str, Any]) -> None:
        """Register a new channel or overwrite an existing one.

        Args:
            channel_name: The name of the channel to register.
            config: Configuration dictionary for the channel.
        """
        self.channels[channel_name] = dict(config)

    def deregister(self, channel_name: str) -> bool:
        """Remove a channel from the registry.

        Args:
            channel_name: Name of the channel to remove.

        Returns:
            True if the channel was found and removed, False otherwise.
        """
        if channel_name in self.channels:
            del self.channels[channel_name]
            return True
        return False

    def get_config(self, channel_name: str) -> dict[str, Any] | None:
        """Return the configuration for *channel_name*, or None if absent.

        Args:
            channel_name: The channel name to look up.

        Returns:
            A copy of the configuration dict, or None.
        """
        cfg = self.channels.get(channel_name)
        return dict(cfg) if cfg is not None else None

    def list_channels(self) -> list[str]:
        """Return a sorted list of all registered channel names.

        Returns:
            Sorted list of channel name strings.
        """
        return sorted(self.channels.keys())

    def update_config(self, channel_name: str, key: str, value: Any) -> bool:
        """Update a single key in a channel's configuration.

        Args:
            channel_name: The channel to update.
            key: Configuration key to set.
            value: New value for the key.

        Returns:
            True if the channel exists and was updated, False otherwise.
        """
        if channel_name not in self.channels:
            return False
        self.channels[channel_name][key] = value
        return True

    def channel_count(self) -> int:
        """Return the number of registered channels.

        Returns:
            Non-negative integer count.
        """
        return len(self.channels)

    def is_registered(self, channel_name: str) -> bool:
        """Return True if *channel_name* is in the registry.

        Args:
            channel_name: The channel name to check.

        Returns:
            True when the channel is registered.
        """
        return channel_name in self.channels

    def to_dict(self) -> dict[str, Any]:
        """Serialise this registry to a plain dictionary.

        Returns:
            A JSON-serialisable dictionary representation.
        """
        return {
            "registry_id": self.registry_id,
            "channels": {name: dict(cfg) for name, cfg in self.channels.items()},
        }


# ---------------------------------------------------------------------------
# JurisdictionCatalog
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class JurisdictionCatalog:
    """Mutable catalog of :class:`JurisdictionMap` objects.

    Provides look-up helpers to find which channels can handle a given
    claim kind and to validate the catalog for coverage gaps.

    Attributes:
        catalog_id: Unique UUID identifier for this catalog instance.
        jurisdiction_maps: Ordered list of jurisdiction maps.
    """

    catalog_id: str
    jurisdiction_maps: list[JurisdictionMap]

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> JurisdictionCatalog:
        """Build a catalog with sensible default jurisdiction maps.

        Covers all five evidence channels with pre-defined claim kinds.

        Returns:
            A new :class:`JurisdictionCatalog` ready for use.
        """
        maps = get_default_jurisdiction_maps()
        return cls(catalog_id=str(uuid4()), jurisdiction_maps=maps)

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def add_map(self, jmap: JurisdictionMap) -> None:
        """Append *jmap* to the catalog.

        Args:
            jmap: The :class:`JurisdictionMap` to add.
        """
        self.jurisdiction_maps.append(jmap)

    def remove_map(self, map_id: str) -> bool:
        """Remove the map with *map_id*, if present.

        Args:
            map_id: UUID of the map to remove.

        Returns:
            True if a map was found and removed, False otherwise.
        """
        for i, jmap in enumerate(self.jurisdiction_maps):
            if jmap.map_id == map_id:
                del self.jurisdiction_maps[i]
                return True
        return False

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_maps_for_channel(self, channel: EvidenceChannel) -> list[JurisdictionMap]:
        """Return all jurisdiction maps for *channel*.

        Args:
            channel: The channel to filter by.

        Returns:
            A list of matching :class:`JurisdictionMap` instances.
        """
        return [jm for jm in self.jurisdiction_maps if jm.channel == channel]

    def find_capable_channels(self, claim_kind: str) -> list[EvidenceChannel]:
        """Return all channels whose jurisdiction maps support *claim_kind*.

        Args:
            claim_kind: The claim kind to look up.

        Returns:
            A list of :class:`EvidenceChannel` members (may contain duplicates
            if multiple maps cover the same channel).
        """
        channels: list[EvidenceChannel] = []
        seen: set[EvidenceChannel] = set()
        for jm in self.jurisdiction_maps:
            if claim_kind in jm.supported_claim_kinds and jm.channel not in seen:
                channels.append(jm.channel)
                seen.add(jm.channel)
        return channels

    def all_claim_kinds(self) -> list[str]:
        """Return a sorted, deduplicated list of all supported claim kinds.

        Returns:
            Sorted list of claim-kind strings.
        """
        kinds: set[str] = set()
        for jm in self.jurisdiction_maps:
            kinds.update(jm.supported_claim_kinds)
        return sorted(kinds)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this catalog to a plain dictionary.

        Returns:
            A JSON-serialisable dictionary representation.
        """
        return {
            "catalog_id": self.catalog_id,
            "jurisdiction_map_count": len(self.jurisdiction_maps),
            "jurisdiction_maps": [jm.to_dict() for jm in self.jurisdiction_maps],
        }

    def validate(self) -> list[str]:
        """Check the catalog for coverage gaps and duplicate map IDs.

        Returns:
            A (possibly empty) list of human-readable error strings.
        """
        errors: list[str] = []
        seen_ids: set[str] = set()
        for jm in self.jurisdiction_maps:
            if jm.map_id in seen_ids:
                errors.append(f"Duplicate map_id: {jm.map_id}")
            seen_ids.add(jm.map_id)
        if not self.jurisdiction_maps:
            errors.append("Catalog contains no jurisdiction maps")
        return errors


# ---------------------------------------------------------------------------
# RoutingConfiguration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RoutingConfiguration:
    """Mutable runtime configuration for the mixed-evidence router.

    Controls the default routing strategy, budget limits, trust constraints,
    and fallback behaviour.

    Attributes:
        config_id: Unique UUID identifier.
        default_strategy: The routing strategy to apply when no override is present.
        max_routing_latency_ms: Maximum acceptable routing-decision latency in ms.
        cost_budget_per_task: Maximum allowable cost per task routing.
        trust_ceiling_copilot: Trust ceiling string for Copilot-routed outputs.
        enable_composite_routing: Whether composite multi-channel routing is enabled.
        fallback_channel: Channel to use when all other channels fail.
        retry_limit: Maximum number of routing retries before escalating to human.
    """

    config_id: str
    default_strategy: RoutingStrategy
    max_routing_latency_ms: float
    cost_budget_per_task: float
    trust_ceiling_copilot: str
    enable_composite_routing: bool
    fallback_channel: EvidenceChannel
    retry_limit: int

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> RoutingConfiguration:
        """Create a :class:`RoutingConfiguration` with sensible defaults.

        Returns:
            A new :class:`RoutingConfiguration` ready for use.
        """
        return cls(
            config_id=str(uuid4()),
            default_strategy=RoutingStrategy.TRUST_OPTIMAL,
            max_routing_latency_ms=5000.0,
            cost_budget_per_task=1.0,
            trust_ceiling_copilot="COPILOT_SUGGESTED",
            enable_composite_routing=True,
            fallback_channel=EvidenceChannel.HUMAN,
            retry_limit=3,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this configuration to a plain dictionary.

        Returns:
            A JSON-serialisable dictionary representation.
        """
        return {
            "config_id": self.config_id,
            "default_strategy": self.default_strategy.value,
            "max_routing_latency_ms": self.max_routing_latency_ms,
            "cost_budget_per_task": self.cost_budget_per_task,
            "trust_ceiling_copilot": self.trust_ceiling_copilot,
            "enable_composite_routing": self.enable_composite_routing,
            "fallback_channel": self.fallback_channel.value,
            "retry_limit": self.retry_limit,
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check this configuration for invalid parameter values.

        Returns:
            A (possibly empty) list of human-readable error strings.
        """
        errors: list[str] = []
        if self.max_routing_latency_ms <= 0:
            errors.append("max_routing_latency_ms must be positive")
        if self.cost_budget_per_task < 0:
            errors.append("cost_budget_per_task must be non-negative")
        if self.retry_limit < 0:
            errors.append("retry_limit must be non-negative")
        if not self.trust_ceiling_copilot:
            errors.append("trust_ceiling_copilot must not be empty")
        return errors

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def with_strategy(self, strategy: RoutingStrategy) -> RoutingConfiguration:
        """Return a copy of this configuration with a different default strategy.

        Args:
            strategy: The new default routing strategy.

        Returns:
            A new :class:`RoutingConfiguration` with the updated strategy.
        """
        return RoutingConfiguration(
            config_id=self.config_id,
            default_strategy=strategy,
            max_routing_latency_ms=self.max_routing_latency_ms,
            cost_budget_per_task=self.cost_budget_per_task,
            trust_ceiling_copilot=self.trust_ceiling_copilot,
            enable_composite_routing=self.enable_composite_routing,
            fallback_channel=self.fallback_channel,
            retry_limit=self.retry_limit,
        )


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def get_default_jurisdiction_maps() -> list[JurisdictionMap]:
    """Return the default set of jurisdiction maps for all five channels.

    Covers:

    - **Z3**: equality, arithmetic, bitvector, horn_clause, quantifier_free_lra
    - **COPILOT_LLM**: natural_language, code_suggestion, explanation,
      heuristic, sketch
    - **RUNTIME_WITNESS**: execution_trace, test_case, property_test,
      fuzzing, benchmark
    - **HUMAN**: ethical_judgment, ambiguous_spec, policy_decision,
      novel_claim, escalation
    - **COMPOSITE**: all of the above

    Returns:
        A list of five :class:`JurisdictionMap` instances.
    """
    z3_kinds = (
        "equality",
        "arithmetic",
        "bitvector",
        "horn_clause",
        "quantifier_free_lra",
    )
    copilot_kinds = (
        "natural_language",
        "code_suggestion",
        "explanation",
        "heuristic",
        "sketch",
    )
    runtime_kinds = (
        "execution_trace",
        "test_case",
        "property_test",
        "fuzzing",
        "benchmark",
    )
    human_kinds = (
        "ethical_judgment",
        "ambiguous_spec",
        "policy_decision",
        "novel_claim",
        "escalation",
    )
    composite_kinds = z3_kinds + copilot_kinds + runtime_kinds + human_kinds

    return [
        JurisdictionMap.new(EvidenceChannel.Z3, z3_kinds, max_complexity=8.0,
                            min_trust_level="SOLVER_DISCHARGED"),
        JurisdictionMap.new(EvidenceChannel.COPILOT_LLM, copilot_kinds, max_complexity=5.0,
                            min_trust_level="COPILOT_SUGGESTED"),
        JurisdictionMap.new(EvidenceChannel.RUNTIME_WITNESS, runtime_kinds, max_complexity=6.0,
                            min_trust_level="RUNTIME_WITNESSED"),
        JurisdictionMap.new(EvidenceChannel.HUMAN, human_kinds, max_complexity=15.0,
                            min_trust_level="HUMAN_ATTESTED"),
        JurisdictionMap.new(EvidenceChannel.COMPOSITE, composite_kinds, max_complexity=15.0,
                            min_trust_level="SOLVER_DISCHARGED"),
    ]


def get_channel_trust_ceilings() -> dict[str, str]:
    """Return the trust-ceiling string for each evidence channel.

    Returns:
        A mapping of channel-value strings to trust-ceiling name strings.
    """
    return {
        EvidenceChannel.Z3.value: "SOLVER_DISCHARGED",
        EvidenceChannel.COPILOT_LLM.value: "COPILOT_SUGGESTED",
        EvidenceChannel.RUNTIME_WITNESS.value: "RUNTIME_WITNESSED",
        EvidenceChannel.HUMAN.value: "HUMAN_ATTESTED",
        EvidenceChannel.COMPOSITE.value: "SOLVER_DISCHARGED",
    }


def build_manifest() -> dict[str, Any]:
    """Build a complete manifest dictionary for the mixed-evidence routing package.

    Combines the :class:`MixedEvidenceRoutingManifest`, a default
    :class:`ChannelRegistry`, a default :class:`JurisdictionCatalog`, and a
    default :class:`RoutingConfiguration` into a single dictionary.

    Returns:
        A JSON-serialisable dictionary containing all sub-manifests.
    """
    manifest = MixedEvidenceRoutingManifest()
    registry = ChannelRegistry.default()
    catalog = JurisdictionCatalog.default()
    config = RoutingConfiguration.default()
    return {
        "manifest": manifest.to_dict(),
        "registry": registry.to_dict(),
        "catalog": catalog.to_dict(),
        "config": config.to_dict(),
    }


def validate_manifest(manifest_dict: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a manifest dictionary produced by :func:`build_manifest`.

    Checks that all expected top-level keys are present and non-empty, and
    that the nested manifest passes :class:`MixedEvidenceRoutingManifest` validation.

    Args:
        manifest_dict: A dictionary as returned by :func:`build_manifest`.

    Returns:
        A 2-tuple ``(is_valid, errors)`` where *is_valid* is True iff
        *errors* is empty.
    """
    errors: list[str] = []
    required_keys = {"manifest", "registry", "catalog", "config"}
    missing = required_keys - set(manifest_dict.keys())
    for key in sorted(missing):
        errors.append(f"Missing top-level key: {key!r}")

    if "manifest" in manifest_dict:
        inner = manifest_dict["manifest"]
        # Reconstruct a MixedEvidenceRoutingManifest for deeper validation
        try:
            m = MixedEvidenceRoutingManifest(
                version=inner.get("version", ""),
                chapter_ref=inner.get("chapter_ref", ""),
                package_name=inner.get("package_name", ""),
                author=inner.get("author", ""),
                description=inner.get("description", ""),
                created_at=inner.get("created_at", 0.0),
            )
            errors.extend(m.validate())
        except Exception as exc:
            errors.append(f"Could not reconstruct manifest: {exc}")

    if "registry" in manifest_dict:
        reg = manifest_dict["registry"]
        if "channels" not in reg:
            errors.append("registry dict is missing 'channels' key")
        elif not reg["channels"]:
            errors.append("registry.channels must not be empty")

    if "catalog" in manifest_dict:
        cat = manifest_dict["catalog"]
        if "jurisdiction_maps" not in cat:
            errors.append("catalog dict is missing 'jurisdiction_maps' key")

    if "config" in manifest_dict:
        cfg = manifest_dict["config"]
        if "default_strategy" not in cfg:
            errors.append("config dict is missing 'default_strategy' key")

    return len(errors) == 0, errors
