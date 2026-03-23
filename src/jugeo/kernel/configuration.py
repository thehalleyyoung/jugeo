"""Configuration subsystem for the JuGeo kernel.

This module implements the full configuration lifecycle for the JuGeo judgment
geometry framework.  Configuration is normalized into explicit typed records
before any kernel service starts, ensuring that trust policy boundaries,
evidence channel jurisdictions, descent depth limits, and copilot integration
settings are all visible and auditable.

The design follows theory2.tex §Configuration-Governance: every configurable
parameter that touches the trust ordered algebra T = (E_adm, ≼, ⊕, ⊖, ↑_π,
↓_χ) must be validated against the algebra's axioms *before* the kernel boots.
Silent trust promotion is forbidden at the configuration level — any attempt
to configure it raises a structured failure.

Key invariants enforced here:

1. Trust ceilings respect the ordered algebra: no channel may be configured
   with a ceiling above the algebra's top element for its jurisdiction.
2. Evidence channel jurisdictions must not overlap silently — overlaps require
   an explicit arbitration policy.
3. Copilot/LLM-backed channels are always capped at PROPOSAL authority.
4. Descent depth limits are finite and solver timeouts are positive.
5. Obstruction retention policies must specify archival before garbage
   collection.

The resulting RuntimeConfiguration is safe to project into diagnostics,
copilot-aware orchestration logs, and reproducibility snapshots.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from jugeo.errors import FailureScope, JuGeoError, StructuredFailure
from jugeo.package_manifest import PackageManifest, build_package_manifest
from jugeo.runtime_defaults import RuntimeDefaults, default_runtime_options


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ConfigSource(str, Enum):
    """Origin of a configuration value.

    Precedence order (lowest to highest): DEFAULT < FILE < ENVIRONMENT < CLI < OVERRIDE.
    This mirrors the layered merge strategy described in theory2.tex §Layered-Config.
    """

    DEFAULT = 'default'
    FILE = 'file'
    ENVIRONMENT = 'environment'
    CLI = 'cli'
    OVERRIDE = 'override'

    @property
    def precedence(self) -> int:
        """Numeric precedence for merge ordering."""
        _order = {
            ConfigSource.DEFAULT: 0,
            ConfigSource.FILE: 1,
            ConfigSource.ENVIRONMENT: 2,
            ConfigSource.CLI: 3,
            ConfigSource.OVERRIDE: 4,
        }
        return _order[self]


class EvidenceChannelKind(str, Enum):
    """Kinds of evidence channels as defined in theory2.tex §Evidence-Bundles.

    Each channel has different trust characteristics: solver-backed evidence is
    verified, runtime-witnessed evidence is observed, oracle-proposed evidence
    (including copilot suggestions) requires explicit review, and proof-backed
    evidence is formally certified.
    """

    SOLVER = 'solver'
    RUNTIME = 'runtime'
    ORACLE = 'oracle'
    PROOF = 'proof'
    COPILOT = 'copilot'
    HUMAN = 'human'


class TrustComparisonOperator(str, Enum):
    """Comparison operators for the trust ordered algebra.

    In theory2.tex §Trust-Algebra, the partial order ≼ on E_adm is the
    fundamental comparison.  These operators extend it to configuration
    predicates.
    """

    LESS_THAN = 'lt'
    LESS_EQUAL = 'le'
    EQUAL = 'eq'
    GREATER_EQUAL = 'ge'
    GREATER_THAN = 'gt'


class TrustCompositionRule(str, Enum):
    """Rules for composing trust values via ⊕ in the ordered algebra.

    MEET takes the greatest lower bound, JOIN takes the least upper bound,
    and EXPLICIT requires a manual specification for each pair.
    """

    MEET = 'meet'
    JOIN = 'join'
    EXPLICIT = 'explicit'


class ObstructionRetentionStrategy(str, Enum):
    """How resolved obstructions are retained (theory2.tex §Obstruction-Persistence).

    Obstructions are persistent cohomology classes, not mere error logs.
    Even after resolution they carry geometric information.
    """

    KEEP_ALL = 'keep_all'
    ARCHIVE_AFTER_RESOLUTION = 'archive_after_resolution'
    GARBAGE_COLLECT = 'garbage_collect'
    TIERED = 'tiered'


class OverlapCheckStrategy(str, Enum):
    """Strategy for checking overlaps during descent (theory2.tex §Descent).

    EAGER checks overlaps as soon as sections are proposed.  LAZY defers
    until gluing.  BATCHED groups overlaps for solver efficiency.
    """

    EAGER = 'eager'
    LAZY = 'lazy'
    BATCHED = 'batched'


class CoordinateNamingConvention(str, Enum):
    """Naming conventions for semantic coordinates in the site S_proj.

    DOT_SEPARATED uses 'module.class.method' style.  SLASH_SEPARATED uses
    filesystem-like paths.  URI uses full URIs.
    """

    DOT_SEPARATED = 'dot'
    SLASH_SEPARATED = 'slash'
    URI = 'uri'


class CopilotModelTier(str, Enum):
    """Model tier for copilot/LLM-backed evidence channels.

    The tier affects latency, cost, and the quality of oracle proposals.
    All tiers remain capped at PROPOSAL authority per theory2.tex
    §Copilot-Governance: oracle evidence never silently reaches VERIFIED.
    """

    FAST = 'fast'
    BALANCED = 'balanced'
    CAPABLE = 'capable'


# ---------------------------------------------------------------------------
# Trust policy configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustPolicyConfiguration:
    """Configuration for the trust ordered algebra T = (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ).

    Per theory2.tex §Trust-Algebra, trust is *not* a scalar — it is an element
    of an ordered algebra with explicit composition (⊕), decomposition (⊖),
    promotion (↑_π), and demotion (↓_χ) operations.  This configuration
    governs the algebra's runtime behavior.

    The ``silent_promotion_allowed`` flag is *always* False.  Any attempt to
    set it True is rejected at validation time, enforcing the core invariant
    that no trust promotion may occur without an explicit evidence trail.
    """

    silent_promotion_allowed: bool = False
    composition_rule: TrustCompositionRule = TrustCompositionRule.MEET
    promotion_requires_evidence: bool = True
    demotion_requires_reason: bool = True
    ceiling_tier: int = 3
    floor_tier: int = 1
    admissibility_predicate: str = 'default'
    comparison_operator: TrustComparisonOperator = TrustComparisonOperator.LESS_EQUAL
    promotion_cooldown_seconds: float = 0.0
    demotion_propagates_to_dependents: bool = True
    copilot_trust_ceiling: int = 1
    explicit_pairs: tuple[tuple[str, str, str], ...] = ()
    provenance: tuple[str, ...] = ()

    def effective_ceiling(self, channel: EvidenceChannelKind) -> int:
        """Return the effective trust ceiling for *channel*.

        Copilot/oracle channels are always capped at ``copilot_trust_ceiling``
        regardless of the global ceiling.  This prevents oracle-proposed
        evidence from silently reaching VERIFIED tier.
        """
        if channel in (EvidenceChannelKind.ORACLE,):
            return min(self.copilot_trust_ceiling, self.ceiling_tier)
        return self.ceiling_tier

    def allows_promotion(self, from_tier: int, to_tier: int) -> bool:
        """Check whether promotion from *from_tier* to *to_tier* is admissible.

        Silent promotion is never allowed.  Even explicit promotion must stay
        within the ceiling and requires evidence when configured.
        """
        if self.silent_promotion_allowed:
            return False  # paradoxical: if someone tries to enable it, block
        if to_tier > self.ceiling_tier:
            return False
        if to_tier <= from_tier:
            return False
        return True

    def allows_demotion(self, from_tier: int, to_tier: int) -> bool:
        """Check whether demotion from *from_tier* to *to_tier* is admissible."""
        if to_tier < self.floor_tier:
            return False
        if to_tier >= from_tier:
            return False
        return True

    def compose(self, tier_a: int, tier_b: int) -> int:
        """Compose two trust tiers using the configured composition rule (⊕)."""
        if self.composition_rule == TrustCompositionRule.MEET:
            return min(tier_a, tier_b)
        elif self.composition_rule == TrustCompositionRule.JOIN:
            return max(tier_a, tier_b)
        else:
            key = f'{tier_a},{tier_b}'
            for left, right, result in self.explicit_pairs:
                if left == str(tier_a) and right == str(tier_b):
                    return int(result)
            return min(tier_a, tier_b)

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty if valid)."""
        errors: list[str] = []
        if self.silent_promotion_allowed:
            errors.append(
                'silent_promotion_allowed must be False — '
                'theory2.tex §Trust-Algebra forbids silent trust promotion'
            )
        if self.ceiling_tier < self.floor_tier:
            errors.append(
                f'ceiling_tier ({self.ceiling_tier}) must be >= '
                f'floor_tier ({self.floor_tier})'
            )
        if self.copilot_trust_ceiling > self.ceiling_tier:
            errors.append(
                f'copilot_trust_ceiling ({self.copilot_trust_ceiling}) must not '
                f'exceed ceiling_tier ({self.ceiling_tier})'
            )
        if self.copilot_trust_ceiling < self.floor_tier:
            errors.append(
                f'copilot_trust_ceiling ({self.copilot_trust_ceiling}) must be >= '
                f'floor_tier ({self.floor_tier})'
            )
        if self.promotion_cooldown_seconds < 0:
            errors.append('promotion_cooldown_seconds must be non-negative')
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'silent_promotion_allowed': self.silent_promotion_allowed,
            'composition_rule': self.composition_rule.value,
            'promotion_requires_evidence': self.promotion_requires_evidence,
            'demotion_requires_reason': self.demotion_requires_reason,
            'ceiling_tier': self.ceiling_tier,
            'floor_tier': self.floor_tier,
            'admissibility_predicate': self.admissibility_predicate,
            'comparison_operator': self.comparison_operator.value,
            'promotion_cooldown_seconds': self.promotion_cooldown_seconds,
            'demotion_propagates_to_dependents': self.demotion_propagates_to_dependents,
            'copilot_trust_ceiling': self.copilot_trust_ceiling,
            'explicit_pairs': [list(p) for p in self.explicit_pairs],
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TrustPolicyConfiguration:
        """Deserialize from a dictionary."""
        return cls(
            silent_promotion_allowed=data.get('silent_promotion_allowed', False),
            composition_rule=TrustCompositionRule(
                data.get('composition_rule', 'meet')
            ),
            promotion_requires_evidence=data.get('promotion_requires_evidence', True),
            demotion_requires_reason=data.get('demotion_requires_reason', True),
            ceiling_tier=data.get('ceiling_tier', 3),
            floor_tier=data.get('floor_tier', 1),
            admissibility_predicate=data.get('admissibility_predicate', 'default'),
            comparison_operator=TrustComparisonOperator(
                data.get('comparison_operator', 'le')
            ),
            promotion_cooldown_seconds=data.get('promotion_cooldown_seconds', 0.0),
            demotion_propagates_to_dependents=data.get(
                'demotion_propagates_to_dependents', True
            ),
            copilot_trust_ceiling=data.get('copilot_trust_ceiling', 1),
            explicit_pairs=tuple(
                tuple(p) for p in data.get('explicit_pairs', [])
            ),
            provenance=tuple(data.get('provenance', ())),
        )


# ---------------------------------------------------------------------------
# Evidence channel configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceChannelConfiguration:
    """Per-channel configuration for evidence gathering.

    Theory2.tex §Evidence-Bundles defines four evidence kinds — solver-backed,
    runtime-witnessed, oracle-proposed (including copilot suggestions), and
    proof-backed.  Each channel has independent timeout, retry, weight, and
    jurisdiction boundaries.

    Jurisdiction boundaries prevent channels from producing evidence outside
    their competence.  For example, a solver channel should not issue runtime
    observations, and a copilot oracle channel must not produce proof
    certificates.
    """

    kind: EvidenceChannelKind = EvidenceChannelKind.SOLVER
    enabled: bool = True
    timeout_seconds: float = 30.0
    retry_count: int = 2
    retry_delay_seconds: float = 1.0
    weight: float = 1.0
    trust_ceiling: int = 3
    jurisdiction: frozenset[str] = field(default_factory=frozenset)
    excluded_coordinates: frozenset[str] = field(default_factory=frozenset)
    priority: int = 0
    max_concurrent_requests: int = 4
    backpressure_threshold: int = 16
    provenance: tuple[str, ...] = ()

    @property
    def is_oracle_channel(self) -> bool:
        """True if this channel carries oracle/copilot-proposed evidence."""
        return self.kind == EvidenceChannelKind.ORACLE

    def effective_trust_ceiling(self, policy: TrustPolicyConfiguration) -> int:
        """Compute the effective trust ceiling respecting global policy.

        Oracle channels are always capped at the policy's copilot trust
        ceiling, regardless of their local configuration.
        """
        if self.is_oracle_channel:
            return min(self.trust_ceiling, policy.copilot_trust_ceiling)
        return min(self.trust_ceiling, policy.ceiling_tier)

    def accepts_coordinate(self, coordinate: str) -> bool:
        """Check whether *coordinate* falls within this channel's jurisdiction.

        If jurisdiction is empty, the channel accepts all coordinates not
        explicitly excluded.
        """
        if coordinate in self.excluded_coordinates:
            return False
        if not self.jurisdiction:
            return True
        return any(coordinate.startswith(j) for j in self.jurisdiction)

    def validate(self) -> list[str]:
        """Return validation errors for this channel configuration."""
        errors: list[str] = []
        if self.timeout_seconds <= 0:
            errors.append(
                f'{self.kind.value} channel: timeout_seconds must be positive'
            )
        if self.retry_count < 0:
            errors.append(
                f'{self.kind.value} channel: retry_count must be non-negative'
            )
        if self.weight < 0:
            errors.append(
                f'{self.kind.value} channel: weight must be non-negative'
            )
        if self.trust_ceiling < 1:
            errors.append(
                f'{self.kind.value} channel: trust_ceiling must be >= 1'
            )
        if self.max_concurrent_requests < 1:
            errors.append(
                f'{self.kind.value} channel: max_concurrent_requests must be >= 1'
            )
        if self.retry_delay_seconds < 0:
            errors.append(
                f'{self.kind.value} channel: retry_delay_seconds must be non-negative'
            )
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'kind': self.kind.value,
            'enabled': self.enabled,
            'timeout_seconds': self.timeout_seconds,
            'retry_count': self.retry_count,
            'retry_delay_seconds': self.retry_delay_seconds,
            'weight': self.weight,
            'trust_ceiling': self.trust_ceiling,
            'jurisdiction': sorted(self.jurisdiction),
            'excluded_coordinates': sorted(self.excluded_coordinates),
            'priority': self.priority,
            'max_concurrent_requests': self.max_concurrent_requests,
            'backpressure_threshold': self.backpressure_threshold,
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceChannelConfiguration:
        """Deserialize from a dictionary."""
        if isinstance(data, str):
            kind = 'oracle' if data == 'copilot' else data
            return cls(kind=EvidenceChannelKind(kind))
        return cls(
            kind=EvidenceChannelKind('oracle' if data.get('kind', 'solver') == 'copilot' else data.get('kind', 'solver')),
            enabled=data.get('enabled', True),
            timeout_seconds=data.get('timeout_seconds', 30.0),
            retry_count=data.get('retry_count', 2),
            retry_delay_seconds=data.get('retry_delay_seconds', 1.0),
            weight=data.get('weight', 1.0),
            trust_ceiling=_coerce_trust_tier_value(data.get('trust_ceiling', 3), 3),
            jurisdiction=frozenset(data.get('jurisdiction', [])),
            excluded_coordinates=frozenset(data.get('excluded_coordinates', [])),
            priority=data.get('priority', 0),
            max_concurrent_requests=data.get('max_concurrent_requests', 4),
            backpressure_threshold=data.get('backpressure_threshold', 16),
            provenance=tuple(data.get('provenance', ())),
        )


# ---------------------------------------------------------------------------
# Descent configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DescentConfiguration:
    """Configuration for descent/gluing procedures.

    Theory2.tex §Descent defines descent as the process of gluing local
    sections into a global section over a cover.  This configuration governs
    the depth limits, hypercover expansion bounds, overlap checking strategy,
    and gluing timeout.

    If descent reaches the depth limit without a successful gluing, the
    kernel returns an obstruction rather than silently truncating.
    """

    max_depth: int = 8
    max_hypercover_expansion: int = 32
    overlap_check_strategy: OverlapCheckStrategy = OverlapCheckStrategy.EAGER
    gluing_timeout_seconds: float = 60.0
    parallel_gluing: bool = True
    max_parallel_gluings: int = 4
    retry_on_timeout: bool = False
    cocycle_cache_size: int = 256
    refinement_budget: int = 16
    copilot_assisted_refinement: bool = False
    provenance: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        """Return validation errors."""
        errors: list[str] = []
        if self.max_depth < 1:
            errors.append('max_depth must be >= 1')
        if self.max_hypercover_expansion < 1:
            errors.append('max_hypercover_expansion must be >= 1')
        if self.gluing_timeout_seconds <= 0:
            errors.append('gluing_timeout_seconds must be positive')
        if self.max_parallel_gluings < 1:
            errors.append('max_parallel_gluings must be >= 1')
        if self.cocycle_cache_size < 0:
            errors.append('cocycle_cache_size must be non-negative')
        if self.refinement_budget < 0:
            errors.append('refinement_budget must be non-negative')
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'max_depth': self.max_depth,
            'max_hypercover_expansion': self.max_hypercover_expansion,
            'overlap_check_strategy': self.overlap_check_strategy.value,
            'gluing_timeout_seconds': self.gluing_timeout_seconds,
            'parallel_gluing': self.parallel_gluing,
            'max_parallel_gluings': self.max_parallel_gluings,
            'retry_on_timeout': self.retry_on_timeout,
            'cocycle_cache_size': self.cocycle_cache_size,
            'refinement_budget': self.refinement_budget,
            'copilot_assisted_refinement': self.copilot_assisted_refinement,
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DescentConfiguration:
        """Deserialize from a dictionary."""
        return cls(
            max_depth=data.get('max_depth', 8),
            max_hypercover_expansion=data.get('max_hypercover_expansion', 32),
            overlap_check_strategy=OverlapCheckStrategy(
                data.get('overlap_check_strategy', 'eager')
            ),
            gluing_timeout_seconds=data.get('gluing_timeout_seconds', 60.0),
            parallel_gluing=data.get('parallel_gluing', True),
            max_parallel_gluings=data.get('max_parallel_gluings', 4),
            retry_on_timeout=data.get('retry_on_timeout', False),
            cocycle_cache_size=data.get('cocycle_cache_size', 256),
            refinement_budget=data.get('refinement_budget', 16),
            copilot_assisted_refinement=data.get(
                'copilot_assisted_refinement', False
            ),
            provenance=tuple(data.get('provenance', ())),
        )


# ---------------------------------------------------------------------------
# Obstruction retention policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionRetentionPolicy:
    """Policy governing how obstructions are retained and archived.

    Per theory2.tex §Obstruction-Persistence, obstructions are persistent
    cohomology classes — they carry geometric information even after the
    underlying issue is resolved.  This policy determines how long they
    persist, when they are archived, and when they may be garbage-collected.

    The tiered strategy archives obstructions after a retention window, then
    garbage-collects archived obstructions after a longer archival window.
    This preserves the obstruction's information for diagnostics and
    copilot-mediated repair while bounding memory usage.
    """

    strategy: ObstructionRetentionStrategy = (
        ObstructionRetentionStrategy.ARCHIVE_AFTER_RESOLUTION
    )
    retention_seconds: float = 3600.0
    archival_seconds: float = 86400.0
    max_active_obstructions: int = 1024
    max_archived_obstructions: int = 8192
    gc_interval_seconds: float = 300.0
    preserve_cohomology_class: bool = True
    compress_archived: bool = True
    provenance: tuple[str, ...] = ()

    def should_archive(self, age_seconds: float, is_resolved: bool) -> bool:
        """Determine whether an obstruction should be archived.

        Resolved obstructions are archived after ``retention_seconds``.
        Unresolved obstructions are never archived — they remain active.
        """
        if not is_resolved:
            return False
        if self.strategy == ObstructionRetentionStrategy.KEEP_ALL:
            return False
        return age_seconds >= self.retention_seconds

    def should_gc(self, age_seconds: float, is_resolved: bool, is_archived: bool) -> bool:
        """Determine whether an obstruction should be garbage-collected.

        Only archived obstructions past the archival window may be collected.
        The KEEP_ALL strategy never garbage-collects.
        """
        if self.strategy == ObstructionRetentionStrategy.KEEP_ALL:
            return False
        if self.strategy == ObstructionRetentionStrategy.ARCHIVE_AFTER_RESOLUTION:
            return False  # archive forever, never GC
        if not is_resolved or not is_archived:
            return False
        return age_seconds >= (self.retention_seconds + self.archival_seconds)

    def validate(self) -> list[str]:
        """Return validation errors."""
        errors: list[str] = []
        if self.retention_seconds < 0:
            errors.append('retention_seconds must be non-negative')
        if self.archival_seconds < 0:
            errors.append('archival_seconds must be non-negative')
        if self.max_active_obstructions < 1:
            errors.append('max_active_obstructions must be >= 1')
        if self.max_archived_obstructions < 0:
            errors.append('max_archived_obstructions must be non-negative')
        if self.gc_interval_seconds <= 0:
            errors.append('gc_interval_seconds must be positive')
        if (
            self.strategy == ObstructionRetentionStrategy.GARBAGE_COLLECT
            and not self.preserve_cohomology_class
        ):
            errors.append(
                'preserve_cohomology_class should be True when using '
                'garbage_collect strategy to retain geometric information'
            )
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'strategy': self.strategy.value,
            'retention_seconds': self.retention_seconds,
            'archival_seconds': self.archival_seconds,
            'max_active_obstructions': self.max_active_obstructions,
            'max_archived_obstructions': self.max_archived_obstructions,
            'gc_interval_seconds': self.gc_interval_seconds,
            'preserve_cohomology_class': self.preserve_cohomology_class,
            'compress_archived': self.compress_archived,
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ObstructionRetentionPolicy:
        """Deserialize from a dictionary."""
        return cls(
            strategy=ObstructionRetentionStrategy(
                data.get('strategy', 'archive_after_resolution')
            ),
            retention_seconds=data.get('retention_seconds', 3600.0),
            archival_seconds=data.get('archival_seconds', 86400.0),
            max_active_obstructions=data.get('max_active_obstructions', 1024),
            max_archived_obstructions=data.get('max_archived_obstructions', 8192),
            gc_interval_seconds=data.get('gc_interval_seconds', 300.0),
            preserve_cohomology_class=data.get('preserve_cohomology_class', True),
            compress_archived=data.get('compress_archived', True),
            provenance=tuple(data.get('provenance', ())),
        )


# ---------------------------------------------------------------------------
# Copilot integration configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CopilotIntegrationConfig:
    """Configuration for copilot/LLM-backed evidence channels.

    Theory2.tex §Copilot-Governance specifies that copilot proposals are
    oracle evidence — they enter the system at PROPOSAL authority and require
    explicit review before promotion.  This configuration controls model
    selection, prompt templates, rate limits, and the trust ceiling for
    copilot-produced evidence.

    The ``trust_ceiling`` here must never exceed the global
    ``copilot_trust_ceiling`` in :class:`TrustPolicyConfiguration`.  The
    :class:`ConfigurationValidator` enforces this cross-reference.
    """

    enabled: bool = True
    model_tier: CopilotModelTier = CopilotModelTier.BALANCED
    model_identifier: str = ''
    trust_ceiling: int = 1
    max_tokens_per_request: int = 4096
    max_requests_per_minute: int = 30
    max_requests_per_hour: int = 500
    prompt_template_path: str = ''
    system_prompt_override: str = ''
    temperature: float = 0.2
    include_context_window: bool = True
    context_window_max_tokens: int = 8192
    retry_count: int = 2
    retry_delay_seconds: float = 2.0
    timeout_seconds: float = 30.0
    jurisdiction: frozenset[str] = field(default_factory=frozenset)
    enable_streaming: bool = False
    log_prompts: bool = False
    log_completions: bool = False
    provenance: tuple[str, ...] = ()

    @property
    def is_active(self) -> bool:
        """True if copilot integration is enabled and configured."""
        return self.enabled

    def effective_rate_limit(self, window_seconds: float) -> int:
        """Compute effective rate limit for a time window.

        Interpolates between per-minute and per-hour limits.
        """
        if window_seconds <= 60:
            return self.max_requests_per_minute
        elif window_seconds >= 3600:
            return self.max_requests_per_hour
        ratio = window_seconds / 3600.0
        return max(1, int(self.max_requests_per_hour * ratio))

    def validate(self) -> list[str]:
        """Return validation errors for copilot integration settings."""
        errors: list[str] = []
        if self.trust_ceiling < 1:
            errors.append('copilot trust_ceiling must be >= 1')
        if self.trust_ceiling > 1:
            errors.append(
                'copilot trust_ceiling should be 1 (PROPOSAL) — copilot '
                'evidence must not silently reach higher authority tiers'
            )
        if self.max_tokens_per_request < 1:
            errors.append('max_tokens_per_request must be >= 1')
        if self.max_requests_per_minute < 0:
            errors.append('max_requests_per_minute must be non-negative')
        if self.max_requests_per_hour < self.max_requests_per_minute:
            errors.append(
                'max_requests_per_hour must be >= max_requests_per_minute'
            )
        if not (0.0 <= self.temperature <= 2.0):
            errors.append('temperature must be between 0.0 and 2.0')
        if self.timeout_seconds <= 0:
            errors.append('timeout_seconds must be positive')
        if self.context_window_max_tokens < 1:
            errors.append('context_window_max_tokens must be >= 1')
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'enabled': self.enabled,
            'model_tier': self.model_tier.value,
            'model_identifier': self.model_identifier,
            'trust_ceiling': self.trust_ceiling,
            'max_tokens_per_request': self.max_tokens_per_request,
            'max_requests_per_minute': self.max_requests_per_minute,
            'max_requests_per_hour': self.max_requests_per_hour,
            'prompt_template_path': self.prompt_template_path,
            'system_prompt_override': self.system_prompt_override,
            'temperature': self.temperature,
            'include_context_window': self.include_context_window,
            'context_window_max_tokens': self.context_window_max_tokens,
            'retry_count': self.retry_count,
            'retry_delay_seconds': self.retry_delay_seconds,
            'timeout_seconds': self.timeout_seconds,
            'jurisdiction': sorted(self.jurisdiction),
            'enable_streaming': self.enable_streaming,
            'log_prompts': self.log_prompts,
            'log_completions': self.log_completions,
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CopilotIntegrationConfig:
        """Deserialize from a dictionary."""
        return cls(
            enabled=data.get('enabled', True),
            model_tier=CopilotModelTier(data.get('model_tier', 'balanced')),
            model_identifier=data.get('model_identifier', ''),
            trust_ceiling=_coerce_trust_tier_value(data.get('trust_ceiling', 1), 1),
            max_tokens_per_request=data.get('max_tokens_per_request', 4096),
            max_requests_per_minute=data.get('max_requests_per_minute', 30),
            max_requests_per_hour=data.get('max_requests_per_hour', 500),
            prompt_template_path=data.get('prompt_template_path', ''),
            system_prompt_override=data.get('system_prompt_override', ''),
            temperature=data.get('temperature', 0.2),
            include_context_window=data.get('include_context_window', True),
            context_window_max_tokens=data.get('context_window_max_tokens', 8192),
            retry_count=data.get('retry_count', 2),
            retry_delay_seconds=data.get('retry_delay_seconds', 2.0),
            timeout_seconds=data.get('timeout_seconds', 30.0),
            jurisdiction=frozenset(data.get('jurisdiction', [])),
            enable_streaming=data.get('enable_streaming', False),
            log_prompts=data.get('log_prompts', False),
            log_completions=data.get('log_completions', False),
            provenance=tuple(data.get('provenance', ())),
        )


# ---------------------------------------------------------------------------
# Solver federation configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SolverFederationConfig:
    """Configuration for Z3 session pooling and solver federation.

    Theory2.tex §Solver-Semantics describes how the kernel federates logical
    queries across solver sessions.  Each session maintains its own assertion
    stack and the kernel routes fragments to the most appropriate session
    based on the logical fragment classification.

    Countermodel extraction is used to produce obstruction witnesses when a
    query is unsatisfiable — these feed into the obstruction presheaf.
    """

    pool_size: int = 4
    max_pool_size: int = 16
    session_timeout_seconds: float = 60.0
    query_timeout_seconds: float = 30.0
    enable_countermodel_extraction: bool = True
    enable_incremental_solving: bool = True
    fragment_routing_enabled: bool = True
    supported_fragments: frozenset[str] = field(
        default_factory=lambda: frozenset({'QF_LIA', 'QF_LRA', 'QF_BV', 'QF_UF', 'HORN'})
    )
    default_fragment: str = 'QF_UF'
    assertion_stack_depth_limit: int = 64
    model_completion: bool = True
    proof_generation: bool = False
    random_seed: int = 0
    memory_limit_mb: int = 2048
    enable_copilot_query_assist: bool = False
    provenance: tuple[str, ...] = ()

    def fragment_for_coordinate(self, coordinate: str) -> str:
        """Select the solver fragment for a coordinate.

        This is a placeholder for the full fragment routing logic in
        jugeo.solver.fragments.  When fragment routing is disabled, all
        queries use the default fragment.
        """
        if not self.fragment_routing_enabled:
            return self.default_fragment
        return self.default_fragment

    def effective_timeout(self, fragment: str) -> float:
        """Return the effective query timeout for *fragment*.

        Some fragments may deserve longer timeouts; for now all fragments
        share the same timeout.
        """
        return self.query_timeout_seconds

    def validate(self) -> list[str]:
        """Return validation errors."""
        errors: list[str] = []
        if self.pool_size < 1:
            errors.append('pool_size must be >= 1')
        if self.max_pool_size < self.pool_size:
            errors.append('max_pool_size must be >= pool_size')
        if self.session_timeout_seconds <= 0:
            errors.append('session_timeout_seconds must be positive')
        if self.query_timeout_seconds <= 0:
            errors.append('query_timeout_seconds must be positive')
        if self.assertion_stack_depth_limit < 1:
            errors.append('assertion_stack_depth_limit must be >= 1')
        if self.memory_limit_mb < 1:
            errors.append('memory_limit_mb must be >= 1')
        if self.default_fragment not in self.supported_fragments:
            errors.append(
                f'default_fragment {self.default_fragment!r} not in '
                f'supported_fragments'
            )
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'pool_size': self.pool_size,
            'max_pool_size': self.max_pool_size,
            'session_timeout_seconds': self.session_timeout_seconds,
            'query_timeout_seconds': self.query_timeout_seconds,
            'enable_countermodel_extraction': self.enable_countermodel_extraction,
            'enable_incremental_solving': self.enable_incremental_solving,
            'fragment_routing_enabled': self.fragment_routing_enabled,
            'supported_fragments': sorted(self.supported_fragments),
            'default_fragment': self.default_fragment,
            'assertion_stack_depth_limit': self.assertion_stack_depth_limit,
            'model_completion': self.model_completion,
            'proof_generation': self.proof_generation,
            'random_seed': self.random_seed,
            'memory_limit_mb': self.memory_limit_mb,
            'enable_copilot_query_assist': self.enable_copilot_query_assist,
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SolverFederationConfig:
        """Deserialize from a dictionary."""
        return cls(
            pool_size=data.get('pool_size', 4),
            max_pool_size=data.get('max_pool_size', 16),
            session_timeout_seconds=data.get('session_timeout_seconds', 60.0),
            query_timeout_seconds=data.get('query_timeout_seconds', 30.0),
            enable_countermodel_extraction=data.get(
                'enable_countermodel_extraction', True
            ),
            enable_incremental_solving=data.get('enable_incremental_solving', True),
            fragment_routing_enabled=data.get('fragment_routing_enabled', True),
            supported_fragments=frozenset(
                data.get('supported_fragments', ['QF_LIA', 'QF_LRA', 'QF_BV', 'QF_UF', 'HORN'])
            ),
            default_fragment=data.get('default_fragment', 'QF_UF'),
            assertion_stack_depth_limit=data.get('assertion_stack_depth_limit', 64),
            model_completion=data.get('model_completion', True),
            proof_generation=data.get('proof_generation', False),
            random_seed=data.get('random_seed', 0),
            memory_limit_mb=data.get('memory_limit_mb', 2048),
            enable_copilot_query_assist=data.get(
                'enable_copilot_query_assist', False
            ),
            provenance=tuple(data.get('provenance', ())),
        )


# ---------------------------------------------------------------------------
# Top-level configuration schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigurationSchema:
    """Complete configuration schema for the JuGeo kernel.

    This dataclass aggregates all sub-configurations into a single typed
    record.  It is the top-level unit of configuration: every kernel boot
    begins by constructing or loading a ConfigurationSchema, validating it,
    and then freezing it into a :class:`ConfigurationSnapshot` for
    reproducibility.

    The schema covers:
    - Trust policy (the ordered algebra T)
    - Evidence channels (solver, runtime, oracle/copilot, proof)
    - Descent/gluing parameters
    - Obstruction retention policy
    - Copilot/LLM integration settings
    - Solver federation / Z3 pooling
    - Coordinate naming conventions
    - Diagnostics and logging
    """

    trust_policy: TrustPolicyConfiguration = field(
        default_factory=TrustPolicyConfiguration
    )
    evidence_channels: tuple[EvidenceChannelConfiguration, ...] = field(
        default_factory=lambda: (
            EvidenceChannelConfiguration(kind=EvidenceChannelKind.SOLVER),
            EvidenceChannelConfiguration(
                kind=EvidenceChannelKind.RUNTIME, timeout_seconds=10.0
            ),
            EvidenceChannelConfiguration(
                kind=EvidenceChannelKind.ORACLE, trust_ceiling=1
            ),
            EvidenceChannelConfiguration(
                kind=EvidenceChannelKind.PROOF, timeout_seconds=120.0
            ),
        )
    )
    descent: DescentConfiguration = field(default_factory=DescentConfiguration)
    obstruction_retention: ObstructionRetentionPolicy = field(
        default_factory=ObstructionRetentionPolicy
    )
    copilot: CopilotIntegrationConfig = field(
        default_factory=CopilotIntegrationConfig
    )
    solver: SolverFederationConfig = field(
        default_factory=SolverFederationConfig
    )
    coordinate_naming: CoordinateNamingConvention = (
        CoordinateNamingConvention.DOT_SEPARATED
    )
    diagnostics_enabled: bool = True
    diagnostics_verbosity: int = 1
    replay_enabled: bool = True
    replay_depth: int = 16
    cache_slots: int = 128
    kernel_version: str = '0.1.0'
    provenance: tuple[str, ...] = ()

    @property
    def channel_map(self) -> dict[EvidenceChannelKind, EvidenceChannelConfiguration]:
        """Map from channel kind to its configuration."""
        return {ch.kind: ch for ch in self.evidence_channels}

    def channel(self, kind: EvidenceChannelKind) -> EvidenceChannelConfiguration | None:
        """Retrieve the configuration for a specific evidence channel."""
        return self.channel_map.get(kind)

    def enabled_channels(self) -> tuple[EvidenceChannelConfiguration, ...]:
        """Return only enabled evidence channels."""
        return tuple(ch for ch in self.evidence_channels if ch.enabled)

    def validate(self) -> list[str]:
        """Validate the entire configuration schema for internal consistency.

        Checks cross-references between sub-configurations, such as ensuring
        copilot trust ceilings respect the global trust policy.
        """
        errors: list[str] = []
        errors.extend(self.trust_policy.validate())
        for ch in self.evidence_channels:
            errors.extend(ch.validate())
        errors.extend(self.descent.validate())
        errors.extend(self.obstruction_retention.validate())
        errors.extend(self.copilot.validate())
        errors.extend(self.solver.validate())
        # Cross-configuration checks
        if self.copilot.trust_ceiling > self.trust_policy.copilot_trust_ceiling:
            errors.append(
                f'copilot.trust_ceiling ({self.copilot.trust_ceiling}) exceeds '
                f'trust_policy.copilot_trust_ceiling '
                f'({self.trust_policy.copilot_trust_ceiling})'
            )
        oracle_ch = self.channel(EvidenceChannelKind.ORACLE)
        if oracle_ch and oracle_ch.trust_ceiling > self.trust_policy.copilot_trust_ceiling:
            errors.append(
                f'oracle channel trust_ceiling ({oracle_ch.trust_ceiling}) exceeds '
                f'copilot_trust_ceiling ({self.trust_policy.copilot_trust_ceiling})'
            )
        # Check for silent jurisdiction overlaps
        errors.extend(self._check_jurisdiction_overlaps())
        if self.replay_depth < 0:
            errors.append('replay_depth must be non-negative')
        if self.cache_slots < 0:
            errors.append('cache_slots must be non-negative')
        return errors

    def _check_jurisdiction_overlaps(self) -> list[str]:
        """Detect silent jurisdiction overlaps between evidence channels.

        Theory2.tex §Evidence-Bundles requires that channel jurisdictions
        either be disjoint or have an explicit arbitration policy.  Silent
        overlaps violate the non-ambiguity axiom.
        """
        errors: list[str] = []
        enabled = self.enabled_channels()
        for i, ch_a in enumerate(enabled):
            if not ch_a.jurisdiction:
                continue
            for ch_b in enabled[i + 1:]:
                if not ch_b.jurisdiction:
                    continue
                overlap = ch_a.jurisdiction & ch_b.jurisdiction
                if overlap:
                    errors.append(
                        f'jurisdiction overlap between {ch_a.kind.value} and '
                        f'{ch_b.kind.value} channels on: '
                        f'{", ".join(sorted(overlap))}. '
                        f'Explicit arbitration policy required.'
                    )
        return errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full configuration to a JSON-compatible dictionary."""
        return {
            'trust_policy': self.trust_policy.to_dict(),
            'evidence_channels': [ch.to_dict() for ch in self.evidence_channels],
            'descent': self.descent.to_dict(),
            'obstruction_retention': self.obstruction_retention.to_dict(),
            'copilot': self.copilot.to_dict(),
            'solver': self.solver.to_dict(),
            'coordinate_naming': self.coordinate_naming.value,
            'diagnostics_enabled': self.diagnostics_enabled,
            'diagnostics_verbosity': self.diagnostics_verbosity,
            'replay_enabled': self.replay_enabled,
            'replay_depth': self.replay_depth,
            'cache_slots': self.cache_slots,
            'kernel_version': self.kernel_version,
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConfigurationSchema:
        """Deserialize from a dictionary."""
        channels_data = data.get('evidence_channels', [])
        if isinstance(channels_data, Mapping):
            if all(isinstance(value, Mapping) for value in channels_data.values()):
                channels_iter = tuple(channels_data.values())
            else:
                channels_iter = tuple(
                    value
                    for value in channels_data.values()
                    if isinstance(value, (str, Mapping))
                )
        else:
            channels_iter = channels_data
        channels = tuple(
            EvidenceChannelConfiguration.from_dict(ch) for ch in channels_iter
        ) if channels_iter else ConfigurationSchema().evidence_channels
        return cls(
            trust_policy=TrustPolicyConfiguration.from_dict(
                data.get('trust_policy', {})
            ),
            evidence_channels=channels,
            descent=DescentConfiguration.from_dict(data.get('descent', {})),
            obstruction_retention=ObstructionRetentionPolicy.from_dict(
                data.get('obstruction_retention', {})
            ),
            copilot=CopilotIntegrationConfig.from_dict(
                data.get('copilot', {})
            ),
            solver=SolverFederationConfig.from_dict(data.get('solver', {})),
            coordinate_naming=CoordinateNamingConvention(
                data.get('coordinate_naming', 'dot')
            ),
            diagnostics_enabled=data.get('diagnostics_enabled', True),
            diagnostics_verbosity=data.get('diagnostics_verbosity', 1),
            replay_enabled=data.get('replay_enabled', True),
            replay_depth=data.get('replay_depth', 16),
            cache_slots=data.get('cache_slots', 128),
            kernel_version=data.get('kernel_version', '0.1.0'),
            provenance=tuple(data.get('provenance', ())),
        )


# ---------------------------------------------------------------------------
# Configuration validator
# ---------------------------------------------------------------------------


class ConfigurationValidator:
    """Validates a :class:`ConfigurationSchema` for internal consistency.

    Beyond the per-sub-configuration validations, this class checks global
    invariants such as:
    - Trust ceilings respect the ordered algebra across all sub-configurations.
    - Evidence channel jurisdictions do not overlap silently.
    - Copilot trust ceilings are coherent between the trust policy and the
      copilot integration config.
    - Descent and solver timeouts are compatible.
    - Obstruction retention archival happens before garbage collection.

    Usage::

        validator = ConfigurationValidator()
        errors = validator.validate(schema)
        if errors:
            raise JuGeoError(...)
    """

    def __init__(self) -> None:
        self._custom_rules: list[tuple[str, _ValidationRule]] = []

    def add_rule(self, name: str, rule: _ValidationRule) -> None:
        """Register a custom validation rule."""
        self._custom_rules.append((name, rule))

    def validate(self, schema: ConfigurationSchema) -> list[str]:
        """Run all validation checks and return a list of error messages."""
        errors: list[str] = []
        errors.extend(schema.validate())
        errors.extend(self._check_timeout_coherence(schema))
        errors.extend(self._check_obstruction_lifecycle(schema))
        errors.extend(self._check_trust_algebra_consistency(schema))
        for name, rule in self._custom_rules:
            try:
                rule_errors = rule(schema)
                errors.extend(rule_errors)
            except Exception as exc:
                errors.append(f'custom rule {name!r} raised: {exc}')
        return errors

    def validate_or_raise(self, schema: ConfigurationSchema) -> None:
        """Validate and raise :class:`JuGeoError` if any errors are found."""
        errors = self.validate(schema)
        if errors:
            raise JuGeoError(
                StructuredFailure(
                    message=f'Configuration validation failed with {len(errors)} error(s): '
                    + '; '.join(errors[:5]),
                    scope=FailureScope.CONFIGURATION,
                )
            )

    def _check_timeout_coherence(self, schema: ConfigurationSchema) -> list[str]:
        """Ensure solver and descent timeouts are compatible.

        The gluing timeout should be at least as large as the solver query
        timeout, since descent may invoke the solver during gluing.
        """
        errors: list[str] = []
        if schema.descent.gluing_timeout_seconds < schema.solver.query_timeout_seconds:
            errors.append(
                f'descent.gluing_timeout_seconds '
                f'({schema.descent.gluing_timeout_seconds}) should be >= '
                f'solver.query_timeout_seconds '
                f'({schema.solver.query_timeout_seconds}) since descent may '
                f'invoke the solver during gluing'
            )
        return errors

    def _check_obstruction_lifecycle(self, schema: ConfigurationSchema) -> list[str]:
        """Ensure archival happens before garbage collection."""
        errors: list[str] = []
        policy = schema.obstruction_retention
        if (
            policy.strategy == ObstructionRetentionStrategy.TIERED
            and policy.archival_seconds < policy.retention_seconds
        ):
            errors.append(
                f'obstruction_retention.archival_seconds '
                f'({policy.archival_seconds}) should be >= '
                f'retention_seconds ({policy.retention_seconds}) in tiered '
                f'strategy — archival must come after retention'
            )
        return errors

    def _check_trust_algebra_consistency(self, schema: ConfigurationSchema) -> list[str]:
        """Verify trust algebra consistency across all sub-configurations.

        Every evidence channel's trust ceiling must lie within the algebra's
        [floor, ceiling] range.  Copilot/oracle channels must additionally
        respect the copilot trust ceiling.
        """
        errors: list[str] = []
        tp = schema.trust_policy
        for ch in schema.evidence_channels:
            if ch.trust_ceiling > tp.ceiling_tier:
                errors.append(
                    f'{ch.kind.value} channel trust_ceiling ({ch.trust_ceiling}) '
                    f'exceeds global ceiling_tier ({tp.ceiling_tier})'
                )
            if ch.trust_ceiling < tp.floor_tier:
                errors.append(
                    f'{ch.kind.value} channel trust_ceiling ({ch.trust_ceiling}) '
                    f'is below global floor_tier ({tp.floor_tier})'
                )
        return errors


# Type alias for custom validation rules
_ValidationRule = type(lambda schema: [])  # noqa: E731 — placeholder for callable


# ---------------------------------------------------------------------------
# Configuration builder
# ---------------------------------------------------------------------------


class ConfigurationBuilder:
    """Fluent builder for constructing a :class:`ConfigurationSchema`.

    Usage::

        schema = (
            ConfigurationBuilder()
            .with_trust_policy(silent_promotion_allowed=False)
            .with_evidence_channel(EvidenceChannelKind.SOLVER, timeout_seconds=60)
            .with_descent(max_depth=12)
            .with_copilot(model_tier=CopilotModelTier.CAPABLE)
            .with_solver(pool_size=8)
            .build()
        )
    """

    def __init__(self) -> None:
        self._trust_policy_kwargs: dict[str, Any] = {}
        self._channels: dict[EvidenceChannelKind, dict[str, Any]] = {}
        self._descent_kwargs: dict[str, Any] = {}
        self._obstruction_kwargs: dict[str, Any] = {}
        self._copilot_kwargs: dict[str, Any] = {}
        self._solver_kwargs: dict[str, Any] = {}
        self._schema_kwargs: dict[str, Any] = {}

    def with_trust_policy(self, **kwargs: Any) -> ConfigurationBuilder:
        """Set trust policy parameters."""
        self._trust_policy_kwargs.update(kwargs)
        return self

    def with_evidence_channel(
        self, kind: EvidenceChannelKind, **kwargs: Any
    ) -> ConfigurationBuilder:
        """Configure a specific evidence channel."""
        if kind not in self._channels:
            self._channels[kind] = {'kind': kind}
        self._channels[kind].update(kwargs)
        return self

    def with_descent(self, **kwargs: Any) -> ConfigurationBuilder:
        """Set descent/gluing parameters."""
        self._descent_kwargs.update(kwargs)
        return self

    def with_obstruction_retention(self, **kwargs: Any) -> ConfigurationBuilder:
        """Set obstruction retention policy."""
        self._obstruction_kwargs.update(kwargs)
        return self

    def with_copilot(self, **kwargs: Any) -> ConfigurationBuilder:
        """Set copilot/LLM integration parameters."""
        self._copilot_kwargs.update(kwargs)
        return self

    def with_solver(self, **kwargs: Any) -> ConfigurationBuilder:
        """Set solver federation parameters."""
        self._solver_kwargs.update(kwargs)
        return self

    def with_schema(self, **kwargs: Any) -> ConfigurationBuilder:
        """Set top-level schema parameters (coordinate_naming, replay, etc.)."""
        self._schema_kwargs.update(kwargs)
        return self

    def build(self) -> ConfigurationSchema:
        """Construct the :class:`ConfigurationSchema` from accumulated settings.

        Channels not explicitly configured use their defaults.  The schema is
        validated before being returned.
        """
        trust_policy = TrustPolicyConfiguration(**self._trust_policy_kwargs)

        # Build channels: start from defaults, overlay explicit settings
        default_channels = {
            EvidenceChannelKind.SOLVER: {'kind': EvidenceChannelKind.SOLVER},
            EvidenceChannelKind.RUNTIME: {
                'kind': EvidenceChannelKind.RUNTIME,
                'timeout_seconds': 10.0,
            },
            EvidenceChannelKind.ORACLE: {
                'kind': EvidenceChannelKind.ORACLE,
                'trust_ceiling': 1,
            },
            EvidenceChannelKind.PROOF: {
                'kind': EvidenceChannelKind.PROOF,
                'timeout_seconds': 120.0,
            },
        }
        for kind, overrides in self._channels.items():
            if kind in default_channels:
                default_channels[kind].update(overrides)
            else:
                default_channels[kind] = overrides

        evidence_channels = tuple(
            EvidenceChannelConfiguration(**ch_kwargs)
            for ch_kwargs in default_channels.values()
        )

        descent = DescentConfiguration(**self._descent_kwargs)
        obstruction = ObstructionRetentionPolicy(**self._obstruction_kwargs)
        copilot_cfg = CopilotIntegrationConfig(**self._copilot_kwargs)
        solver_cfg = SolverFederationConfig(**self._solver_kwargs)

        schema = ConfigurationSchema(
            trust_policy=trust_policy,
            evidence_channels=evidence_channels,
            descent=descent,
            obstruction_retention=obstruction,
            copilot=copilot_cfg,
            solver=solver_cfg,
            **self._schema_kwargs,
        )
        return schema

    def build_validated(self) -> ConfigurationSchema:
        """Build and validate, raising on errors."""
        schema = self.build()
        validator = ConfigurationValidator()
        validator.validate_or_raise(schema)
        return schema


# ---------------------------------------------------------------------------
# Configuration layer (preserved from original API)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigurationLayer:
    """A single layer of configuration values with provenance tracking.

    Layers are merged in precedence order to produce the final runtime
    configuration.  Each layer records its source (default, file, environment,
    CLI, or override) and an optional provenance trail for auditability.
    """

    name: str
    source: ConfigSource
    values: Mapping[str, Any]
    provenance: tuple[str, ...] = ()

    @property
    def precedence(self) -> int:
        """Numeric precedence derived from the source."""
        return self.source.precedence

    def has_key(self, dotted_key: str) -> bool:
        """Check whether *dotted_key* exists in this layer's values."""
        current: Any = self.values
        for part in dotted_key.split('.'):
            if not isinstance(current, Mapping) or part not in current:
                return False
            current = current[part]
        return True

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Retrieve a value by dotted key path."""
        current: Any = self.values
        for part in dotted_key.split('.'):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return current

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'name': self.name,
            'source': self.source.value,
            'values': dict(self.values),
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConfigurationLayer:
        """Deserialize from a dictionary."""
        return cls(
            name=data['name'],
            source=ConfigSource(data['source']),
            values=data.get('values', {}),
            provenance=tuple(data.get('provenance', ())),
        )

    @classmethod
    def from_env(cls, prefix: str = 'JUGEO_') -> ConfigurationLayer:
        """Create a layer from environment variables with the given prefix.

        Environment variables are mapped to dotted keys by converting the
        prefix-stripped name to lowercase and replacing double underscores
        with dots.  For example, ``JUGEO_TRUST_POLICY__CEILING_TIER=3``
        becomes ``trust_policy.ceiling_tier = "3"``.
        """
        values: dict[str, Any] = {}
        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue
            stripped = key[len(prefix):].lower()
            parts = stripped.split('__')
            current = values
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            # Attempt numeric conversion
            try:
                current[parts[-1]] = int(value)
            except ValueError:
                try:
                    current[parts[-1]] = float(value)
                except ValueError:
                    if value.lower() in ('true', 'false'):
                        current[parts[-1]] = value.lower() == 'true'
                    else:
                        current[parts[-1]] = value
        return cls(
            name='environment',
            source=ConfigSource.ENVIRONMENT,
            values=values,
            provenance=(f'env-prefix:{prefix}',),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> ConfigurationLayer:
        """Create a layer by reading a JSON configuration file."""
        file_path = Path(path)
        if not file_path.exists():
            raise JuGeoError(
                StructuredFailure(
                    message=f'Configuration file not found: {file_path}',
                    scope=FailureScope.CONFIGURATION,
                )
            )
        with open(file_path) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise JuGeoError(
                StructuredFailure(
                    message=f'Configuration file must contain a JSON object: {file_path}',
                    scope=FailureScope.CONFIGURATION,
                )
            )
        return cls(
            name=file_path.stem,
            source=ConfigSource.FILE,
            values=data,
            provenance=(f'file:{file_path}',),
        )


# ---------------------------------------------------------------------------
# Configuration merger
# ---------------------------------------------------------------------------


def _deep_merge(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge *right* into *left*, with *right* taking precedence."""
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_configuration_layers(*layers: ConfigurationLayer) -> dict[str, Any]:
    """Merge multiple configuration layers into a single dictionary.

    Layers are merged in the order given.  Later layers override earlier ones.
    Nested dictionaries are merged recursively.
    """
    merged: dict[str, Any] = {}
    for layer in layers:
        merged = _deep_merge(merged, layer.values)
    return merged


class ConfigurationMerger:
    """Merges multiple configuration sources with explicit precedence rules.

    Sources are merged in precedence order: defaults < file < environment <
    CLI < override.  The merger tracks which source contributed each final
    value, enabling the copilot diagnostics layer to report configuration
    provenance.

    Usage::

        merger = ConfigurationMerger()
        merger.add_layer(defaults_layer)
        merger.add_layer(file_layer)
        merger.add_layer(env_layer)
        result = merger.merge()
    """

    def __init__(self) -> None:
        self._layers: list[ConfigurationLayer] = []
        self._provenance_map: dict[str, str] = {}

    @property
    def layer_count(self) -> int:
        """Number of layers currently registered."""
        return len(self._layers)

    @property
    def provenance_map(self) -> Mapping[str, str]:
        """Map from dotted key to the name of the source layer that provided it."""
        return dict(self._provenance_map)

    def add_layer(self, layer: ConfigurationLayer) -> ConfigurationMerger:
        """Add a configuration layer.  Returns self for chaining."""
        self._layers.append(layer)
        return self

    def add_layers(self, *layers: ConfigurationLayer) -> ConfigurationMerger:
        """Add multiple layers at once."""
        self._layers.extend(layers)
        return self

    def sorted_layers(self) -> list[ConfigurationLayer]:
        """Return layers sorted by precedence (lowest first)."""
        return sorted(self._layers, key=lambda l: l.precedence)

    def merge(self) -> dict[str, Any]:
        """Merge all layers and return the combined dictionary.

        Layers are sorted by precedence before merging, ensuring that higher-
        precedence sources always override lower ones.
        """
        sorted_layers = self.sorted_layers()
        merged: dict[str, Any] = {}
        self._provenance_map.clear()
        for layer in sorted_layers:
            self._record_provenance(layer, layer.values, prefix='')
            merged = _deep_merge(merged, layer.values)
        return merged

    def merge_to_schema(self) -> ConfigurationSchema:
        """Merge all layers and parse the result into a ConfigurationSchema."""
        merged = self.merge()
        return ConfigurationSchema.from_dict(merged)

    def _record_provenance(
        self,
        layer: ConfigurationLayer,
        values: Mapping[str, Any],
        prefix: str,
    ) -> None:
        """Record which layer contributed each leaf key."""
        for key, value in values.items():
            dotted = f'{prefix}{key}' if not prefix else f'{prefix}.{key}'
            if isinstance(value, Mapping):
                self._record_provenance(layer, value, dotted)
            else:
                self._provenance_map[dotted] = layer.name


# ---------------------------------------------------------------------------
# Configuration snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    """Immutable frozen snapshot of configuration at a point in time.

    Snapshots are used for reproducibility: when the kernel produces a result,
    the snapshot records exactly which configuration was in effect.  The
    content hash enables quick equality checks without deep comparison.

    The snapshot is safe to serialize into copilot orchestration logs,
    diagnostic reports, and reproducibility archives.
    """

    schema: ConfigurationSchema
    timestamp: str
    content_hash: str
    source_layers: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    @classmethod
    def capture(
        cls,
        schema: ConfigurationSchema,
        *,
        timestamp: str = '',
        source_layers: Sequence[str] = (),
    ) -> ConfigurationSnapshot:
        """Capture a snapshot of the current configuration.

        Computes a SHA-256 content hash from the serialized schema for
        identity tracking.
        """
        import time as _time

        ts = timestamp or _time.strftime('%Y-%m-%dT%H:%M:%SZ', _time.gmtime())
        serialized = json.dumps(schema.to_dict(), sort_keys=True)
        content_hash = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        return cls(
            schema=schema,
            timestamp=ts,
            content_hash=content_hash,
            source_layers=tuple(source_layers),
            provenance=schema.provenance,
        )

    @property
    def short_hash(self) -> str:
        """First 12 characters of the content hash."""
        return self.content_hash[:12]

    def matches(self, other: ConfigurationSnapshot) -> bool:
        """Check whether two snapshots have identical configuration content."""
        return self.content_hash == other.content_hash

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'schema': self.schema.to_dict(),
            'timestamp': self.timestamp,
            'content_hash': self.content_hash,
            'source_layers': list(self.source_layers),
            'provenance': list(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConfigurationSnapshot:
        """Deserialize from a dictionary."""
        return cls(
            schema=ConfigurationSchema.from_dict(data.get('schema', {})),
            timestamp=data.get('timestamp', ''),
            content_hash=data.get('content_hash', ''),
            source_layers=tuple(data.get('source_layers', ())),
            provenance=tuple(data.get('provenance', ())),
        )


# ---------------------------------------------------------------------------
# Configuration diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfigurationChange:
    """A single changed value between two configurations."""

    key: str
    old_value: Any
    new_value: Any
    change_type: str  # 'added', 'removed', 'modified'

    @property
    def is_trust_related(self) -> bool:
        """True if this change affects trust policy or ceilings."""
        trust_keys = ('trust_policy', 'trust_ceiling', 'copilot_trust_ceiling')
        return any(tk in self.key for tk in trust_keys)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'key': self.key,
            'old_value': self.old_value,
            'new_value': self.new_value,
            'change_type': self.change_type,
        }


@dataclass(frozen=True, slots=True)
class ConfigurationDiff:
    """Represents the differences between two configurations.

    This is used to audit configuration changes, especially those affecting
    trust boundaries.  Any change to copilot trust ceilings or evidence
    channel jurisdictions is flagged as trust-related for review.
    """

    changes: tuple[ConfigurationChange, ...]
    old_hash: str = ''
    new_hash: str = ''

    @property
    def is_empty(self) -> bool:
        """True if there are no differences."""
        return len(self.changes) == 0

    @property
    def trust_changes(self) -> tuple[ConfigurationChange, ...]:
        """Return only changes that affect trust policy."""
        return tuple(c for c in self.changes if c.is_trust_related)

    @property
    def has_trust_changes(self) -> bool:
        """True if any changes affect trust boundaries."""
        return len(self.trust_changes) > 0

    @property
    def change_count(self) -> int:
        """Total number of changes."""
        return len(self.changes)

    def summary(self) -> str:
        """Return a human-readable summary of the diff."""
        if self.is_empty:
            return 'No configuration changes.'
        parts: list[str] = [f'{self.change_count} change(s):']
        for change in self.changes:
            if change.change_type == 'added':
                parts.append(f'  + {change.key} = {change.new_value!r}')
            elif change.change_type == 'removed':
                parts.append(f'  - {change.key} (was {change.old_value!r})')
            else:
                parts.append(
                    f'  ~ {change.key}: {change.old_value!r} -> {change.new_value!r}'
                )
        if self.has_trust_changes:
            parts.append(
                f'  ⚠ {len(self.trust_changes)} trust-related change(s) — '
                f'review required before copilot orchestration accepts this diff'
            )
        return '\n'.join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'changes': [c.to_dict() for c in self.changes],
            'old_hash': self.old_hash,
            'new_hash': self.new_hash,
        }

    @classmethod
    def compute(
        cls,
        old: ConfigurationSchema,
        new: ConfigurationSchema,
    ) -> ConfigurationDiff:
        """Compute the diff between two configuration schemas.

        Performs a recursive comparison of the serialized dictionaries and
        records each added, removed, or modified leaf value.
        """
        old_dict = old.to_dict()
        new_dict = new.to_dict()
        old_hash = hashlib.sha256(
            json.dumps(old_dict, sort_keys=True).encode()
        ).hexdigest()
        new_hash = hashlib.sha256(
            json.dumps(new_dict, sort_keys=True).encode()
        ).hexdigest()
        changes: list[ConfigurationChange] = []
        cls._diff_recursive(old_dict, new_dict, '', changes)
        return cls(
            changes=tuple(changes),
            old_hash=old_hash,
            new_hash=new_hash,
        )

    @classmethod
    def _diff_recursive(
        cls,
        old: Any,
        new: Any,
        prefix: str,
        changes: list[ConfigurationChange],
    ) -> None:
        """Recursively compare two values and record differences."""
        if isinstance(old, dict) and isinstance(new, dict):
            all_keys = set(old.keys()) | set(new.keys())
            for key in sorted(all_keys):
                child_prefix = f'{prefix}.{key}' if prefix else key
                if key not in old:
                    changes.append(
                        ConfigurationChange(
                            key=child_prefix,
                            old_value=None,
                            new_value=new[key],
                            change_type='added',
                        )
                    )
                elif key not in new:
                    changes.append(
                        ConfigurationChange(
                            key=child_prefix,
                            old_value=old[key],
                            new_value=None,
                            change_type='removed',
                        )
                    )
                else:
                    cls._diff_recursive(old[key], new[key], child_prefix, changes)
        elif isinstance(old, list) and isinstance(new, list):
            if old != new:
                changes.append(
                    ConfigurationChange(
                        key=prefix,
                        old_value=old,
                        new_value=new,
                        change_type='modified',
                    )
                )
        elif old != new:
            changes.append(
                ConfigurationChange(
                    key=prefix,
                    old_value=old,
                    new_value=new,
                    change_type='modified',
                )
            )


# ---------------------------------------------------------------------------
# Runtime configuration (preserved and extended from original API)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    """Resolved runtime configuration combining defaults, manifest, and layers.

    This is the primary configuration object passed through the kernel.  It
    is constructed by :class:`ConfigurationLoader` and provides dotted-key
    access to merged values.  A :class:`ConfigurationSchema` can be extracted
    for typed access to sub-configurations.

    The configuration is frozen (immutable) after construction.  To capture
    a reproducibility record, use :meth:`snapshot`.
    """

    defaults: RuntimeDefaults
    manifest: PackageManifest
    layers: tuple[ConfigurationLayer, ...]
    values: Mapping[str, Any] = field(default_factory=dict)
    schema: ConfigurationSchema | None = None

    def get(self, dotted_key: str, default: Any | None = None) -> Any:
        """Retrieve a value by dotted key path from the merged values."""
        current: Any = self.values
        for part in dotted_key.split('.'):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return current

    def section(self, name: str) -> Mapping[str, Any]:
        """Retrieve a configuration section by name."""
        value = self.get(name, {})
        return value if isinstance(value, Mapping) else {}

    @property
    def trust_policy_section(self) -> Mapping[str, Any]:
        """Shortcut to the trust_policy section."""
        return self.section('trust_policy')

    @property
    def copilot_section(self) -> Mapping[str, Any]:
        """Shortcut to the copilot configuration section."""
        return self.section('copilot')

    def effective_schema(self) -> ConfigurationSchema:
        """Return the typed ConfigurationSchema, parsing from values if needed."""
        if self.schema is not None:
            return self.schema
        return ConfigurationSchema.from_dict(dict(self.values))

    def snapshot(self, timestamp: str = '') -> ConfigurationSnapshot:
        """Capture a reproducibility snapshot of this configuration."""
        return ConfigurationSnapshot.capture(
            self.effective_schema(),
            timestamp=timestamp,
            source_layers=tuple(layer.name for layer in self.layers),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return {
            'defaults': self.defaults.get_all(),
            'manifest': self.manifest.to_dict(),
            'layers': [
                {
                    'name': layer.name,
                    'source': layer.source.value,
                    'values': dict(layer.values),
                    'provenance': list(layer.provenance),
                }
                for layer in self.layers
            ],
            'values': dict(self.values),
        }


# ---------------------------------------------------------------------------
# Configuration loader (preserved and extended from original API)
# ---------------------------------------------------------------------------


class ConfigurationLoader:
    """Loads and validates configuration from multiple sources.

    The loader starts from :class:`RuntimeDefaults`, overlays any provided
    :class:`ConfigurationLayer` instances, validates the result (rejecting
    silent trust promotion), and produces a :class:`RuntimeConfiguration`.

    This loader is copilot-aware: it recognizes copilot-specific keys in the
    configuration and ensures they respect the trust governance rules from
    theory2.tex §Copilot-Governance.
    """

    def __init__(
        self,
        *,
        defaults: RuntimeDefaults | None = None,
        manifest: PackageManifest | None = None,
        strict: bool = True,
    ) -> None:
        self.defaults = defaults or default_runtime_options()
        self.manifest = manifest or build_package_manifest()
        self.strict = strict

    def load(self, *layers: ConfigurationLayer) -> RuntimeConfiguration:
        """Load configuration from the given layers.

        Creates a base layer from runtime defaults, merges all provided layers,
        validates the result, and constructs a RuntimeConfiguration.

        Raises :class:`JuGeoError` if silent trust promotion is configured.
        """
        base = ConfigurationLayer(
            'defaults',
            ConfigSource.DEFAULT,
            self.defaults.get_all(),
            ('runtime-defaults',),
        )
        merged = merge_configuration_layers(base, *layers)

        # Core invariant: no silent trust promotion
        if merged.get('trust_policy', {}).get('silent_promotion_allowed'):
            raise JuGeoError(
                StructuredFailure(
                    message='Configuration attempted to enable silent trust promotion.',
                    scope=FailureScope.CONFIGURATION,
                )
            )

        # Parse schema for typed validation
        schema = ConfigurationSchema.from_dict(merged)
        if self.strict:
            errors = schema.validate()
            if errors:
                raise JuGeoError(
                    StructuredFailure(
                        message=f'Configuration validation failed: {"; ".join(errors[:3])}',
                        scope=FailureScope.CONFIGURATION,
                    )
                )

        return RuntimeConfiguration(
            self.defaults, self.manifest, (base, *layers), merged, schema
        )

    def load_from_file(
        self,
        path: str | Path,
        *extra_layers: ConfigurationLayer,
    ) -> RuntimeConfiguration:
        """Load configuration from a JSON file plus optional extra layers."""
        file_layer = ConfigurationLayer.from_file(path)
        return self.load(file_layer, *extra_layers)

    def load_with_env(
        self,
        *layers: ConfigurationLayer,
        env_prefix: str = 'JUGEO_',
    ) -> RuntimeConfiguration:
        """Load configuration from layers plus environment variables.

        Environment variables are applied after explicit layers but before
        overrides.
        """
        env_layer = ConfigurationLayer.from_env(env_prefix)
        return self.load(*layers, env_layer)


# ---------------------------------------------------------------------------
# Top-level convenience functions
# ---------------------------------------------------------------------------


def load_configuration(
    *layers: ConfigurationLayer,
    defaults: RuntimeDefaults | None = None,
    manifest: PackageManifest | None = None,
    strict: bool = True,
) -> RuntimeConfiguration:
    """Load and validate a runtime configuration from layers.

    This is the primary entry point for configuration loading.  It merges
    the given layers with runtime defaults, validates the result, and returns
    an immutable :class:`RuntimeConfiguration`.
    """
    return ConfigurationLoader(
        defaults=defaults, manifest=manifest, strict=strict
    ).load(*layers)


def save_configuration(
    schema: ConfigurationSchema,
    path: str | Path,
    *,
    indent: int = 2,
) -> Path:
    """Serialize a ConfigurationSchema to a JSON file.

    Returns the resolved path of the written file.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data = schema.to_dict()
    with open(file_path, 'w') as fh:
        json.dump(data, fh, indent=indent, sort_keys=True)
        fh.write('\n')
    return file_path


def validate_configuration(schema: ConfigurationSchema) -> list[str]:
    """Validate a ConfigurationSchema and return any errors.

    Delegates to :class:`ConfigurationValidator` for comprehensive checking
    including cross-reference consistency and trust algebra invariants.
    """
    validator = ConfigurationValidator()
    return validator.validate(schema)


def merge_configurations(
    *schemas: ConfigurationSchema,
) -> ConfigurationSchema:
    """Merge multiple ConfigurationSchema instances.

    Later schemas override earlier ones.  The merge operates on the serialized
    dictionaries and re-parses the result.  This is useful for combining a
    base configuration with project-specific overrides while preserving the
    copilot trust governance invariants.
    """
    if not schemas:
        return ConfigurationSchema()
    merged: dict[str, Any] = {}
    for schema in schemas:
        merged = _deep_merge(merged, schema.to_dict())
    return ConfigurationSchema.from_dict(merged)


def default_configuration_schema() -> ConfigurationSchema:
    """Return the default ConfigurationSchema with all defaults.

    This schema is suitable for development and testing.  For production,
    use :func:`load_configuration` with explicit layers.
    """
    return ConfigurationSchema()


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------


__all__ = [
    # Enums
    'ConfigSource',
    'EvidenceChannelKind',
    'TrustComparisonOperator',
    'TrustCompositionRule',
    'ObstructionRetentionStrategy',
    'OverlapCheckStrategy',
    'CoordinateNamingConvention',
    'CopilotModelTier',
    # Sub-configurations
    'TrustPolicyConfiguration',
    'EvidenceChannelConfiguration',
    'DescentConfiguration',
    'ObstructionRetentionPolicy',
    'CopilotIntegrationConfig',
    'SolverFederationConfig',
    # Top-level schema
    'ConfigurationSchema',
    # Validator, builder, merger
    'ConfigurationValidator',
    'ConfigurationBuilder',
    'ConfigurationMerger',
    # Layer and runtime
    'ConfigurationLayer',
    'RuntimeConfiguration',
    'ConfigurationLoader',
    # Snapshot and diff
    'ConfigurationSnapshot',
    'ConfigurationChange',
    'ConfigurationDiff',
    # Top-level functions
    'load_configuration',
    'save_configuration',
    'validate_configuration',
    'merge_configurations',
    'merge_configuration_layers',
    'default_configuration_schema',
]
def _coerce_trust_tier_value(value: Any, default: int = 3) -> int:
    if isinstance(value, str):
        mapping = {
            'proposal': 1,
            'unverified': 1,
            'reviewed': 2,
            'runtime_witnessed': 2,
            'verified': 3,
            'solver_discharged': 3,
            'mechanically_verified': 3,
        }
        return mapping.get(value, default)
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    if 1 <= coerced <= 3:
        return coerced
    return default


# ---------------------------------------------------------------------------
# Cross-subsystem configuration helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry import site as _geo_site_cfg, descent as _geo_descent_cfg  # type: ignore[import]
    _GEOMETRY_CONFIG_AVAILABLE = True
except ImportError:
    _geo_site_cfg = None  # type: ignore[assignment]
    _geo_descent_cfg = None  # type: ignore[assignment]
    _GEOMETRY_CONFIG_AVAILABLE = False

try:
    from jugeo.evidence import trust as _ev_trust_cfg, channels as _ev_channels_cfg  # type: ignore[import]
    _EVIDENCE_CONFIG_AVAILABLE = True
except ImportError:
    _ev_trust_cfg = None  # type: ignore[assignment]
    _ev_channels_cfg = None  # type: ignore[assignment]
    _EVIDENCE_CONFIG_AVAILABLE = False

try:
    from jugeo.solver import config as _solver_cfg_mod  # type: ignore[import]
    _SOLVER_CONFIG_AVAILABLE = True
except ImportError:
    _solver_cfg_mod = None  # type: ignore[assignment]
    _SOLVER_CONFIG_AVAILABLE = False

try:
    from jugeo.encodings import registry as _enc_registry_cfg  # type: ignore[import]
    _ENCODING_CONFIG_AVAILABLE = True
except ImportError:
    _enc_registry_cfg = None  # type: ignore[assignment]
    _ENCODING_CONFIG_AVAILABLE = False


def geometry_configuration(
    *,
    descent_depth_limit: int = 10,
    overlap_strategy: str = "eager",
    coordinate_convention: str = "dot",
) -> dict[str, Any]:
    """Configure geometry subsystem parameters from ``jugeo.geometry``.

    Sets site topology, descent depth limits, and overlap checking strategy
    on the geometry subsystem.  Returns the applied configuration or an
    error report when the subsystem is unavailable.

    Parameters
    ----------
    descent_depth_limit:
        Maximum depth for descent operations (must be positive).
    overlap_strategy:
        Strategy for cover-overlap checking: ``"eager"``, ``"lazy"``, or
        ``"batched"``.
    coordinate_convention:
        Naming convention for coordinates: ``"dot"``, ``"slash"``, or ``"uri"``.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "applied": {...}, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _GEOMETRY_CONFIG_AVAILABLE,
        "applied": {},
        "errors": [],
    }
    if not _GEOMETRY_CONFIG_AVAILABLE:
        result["errors"].append("jugeo.geometry subsystem is not installed")
        return result
    try:
        config = {
            "descent_depth_limit": descent_depth_limit,
            "overlap_strategy": overlap_strategy,
            "coordinate_convention": coordinate_convention,
        }
        if hasattr(_geo_descent_cfg, "configure"):
            _geo_descent_cfg.configure(config)
        if hasattr(_geo_site_cfg, "configure"):
            _geo_site_cfg.configure(config)
        result["applied"] = config
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def evidence_configuration(
    *,
    trust_composition_rule: str = "meet",
    copilot_trust_ceiling: int = 1,
    channel_jurisdictions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Configure evidence subsystem parameters from ``jugeo.evidence``.

    Sets trust algebra composition rules, copilot trust ceilings, and
    evidence channel jurisdictions.

    Parameters
    ----------
    trust_composition_rule:
        Rule for composing trust values: ``"meet"`` or ``"join"``.
    copilot_trust_ceiling:
        Maximum trust tier for copilot-backed channels (1–3).
    channel_jurisdictions:
        Mapping from channel name to jurisdiction scope.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "applied": {...}, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _EVIDENCE_CONFIG_AVAILABLE,
        "applied": {},
        "errors": [],
    }
    if not _EVIDENCE_CONFIG_AVAILABLE:
        result["errors"].append("jugeo.evidence subsystem is not installed")
        return result
    try:
        config = {
            "trust_composition_rule": trust_composition_rule,
            "copilot_trust_ceiling": copilot_trust_ceiling,
            "channel_jurisdictions": channel_jurisdictions or {},
        }
        if hasattr(_ev_trust_cfg, "configure"):
            _ev_trust_cfg.configure(config)
        if hasattr(_ev_channels_cfg, "configure"):
            _ev_channels_cfg.configure(config)
        result["applied"] = config
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def solver_configuration(
    *,
    z3_timeout_ms: int = 30_000,
    z3_memory_limit_mb: int = 4096,
    max_concurrent_sessions: int = 4,
) -> dict[str, Any]:
    """Configure solver subsystem parameters from ``jugeo.solver``.

    Sets Z3 solver timeouts, memory limits, and concurrency constraints.

    Parameters
    ----------
    z3_timeout_ms:
        Per-query Z3 timeout in milliseconds (must be positive).
    z3_memory_limit_mb:
        Z3 memory limit in megabytes.
    max_concurrent_sessions:
        Maximum number of concurrent Z3 sessions.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "applied": {...}, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _SOLVER_CONFIG_AVAILABLE,
        "applied": {},
        "errors": [],
    }
    if not _SOLVER_CONFIG_AVAILABLE:
        result["errors"].append("jugeo.solver subsystem is not installed")
        return result
    try:
        config = {
            "z3_timeout_ms": z3_timeout_ms,
            "z3_memory_limit_mb": z3_memory_limit_mb,
            "max_concurrent_sessions": max_concurrent_sessions,
        }
        if hasattr(_solver_cfg_mod, "configure"):
            _solver_cfg_mod.configure(config)
        result["applied"] = config
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def encoding_configuration(
    *,
    families: Sequence[str] | None = None,
    default_family: str = "theorem_schemas",
) -> dict[str, Any]:
    """Configure encoding subsystem parameters from ``jugeo.encodings``.

    Sets the active encoding families and default family used for
    judgment serialization.

    Parameters
    ----------
    families:
        List of encoding family names to activate.  ``None`` activates all
        available families.
    default_family:
        Name of the default encoding family.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "applied": {...}, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _ENCODING_CONFIG_AVAILABLE,
        "applied": {},
        "errors": [],
    }
    if not _ENCODING_CONFIG_AVAILABLE:
        result["errors"].append("jugeo.encodings subsystem is not installed")
        return result
    try:
        config: dict[str, Any] = {
            "families": list(families) if families else [],
            "default_family": default_family,
        }
        if hasattr(_enc_registry_cfg, "configure"):
            _enc_registry_cfg.configure(config)
        result["applied"] = config
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result
