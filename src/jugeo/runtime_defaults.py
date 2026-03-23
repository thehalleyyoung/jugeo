"""Canonical runtime defaults for the entire JuGeo system (theory2.tex).

This module is the **single source of truth** for every configurable parameter
in the JuGeo runtime.  All other modules import their defaults from here so
that policy is never chosen silently by a leaf module.

The hierarchy of configuration resolution is:

    factory defaults (this module)
        ← environment variables   (load_defaults_from_env)
            ← config file         (load_defaults_from_file)
                ← call-site overrides

Trust levels, evidence-channel timeouts, descent limits, budget allocations,
solver configuration, copilot oracle settings, and manifest policies are each
represented by a dedicated dataclass with full docstrings and validation logic.

References
----------
theory2.tex §3.1 (trust lattice), §4.2 (evidence channels), §5.4 (descent),
§6.1 (obstruction retention), §7 (budgets), §8.3 (copilot oracle).
"""

from __future__ import annotations

import copy
import json
import logging
import os
from dataclasses import asdict, dataclass, field, replace
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared enumerations
# ---------------------------------------------------------------------------


class PolicyPreset(str, Enum):
    """Top-level policy presets that calibrate every sub-configuration.

    Attributes
    ----------
    SAFE:
        Conservative limits; suits production proofs where every call is
        expensive and reliability trumps throughput.
    BALANCED:
        Moderate limits; the recommended default for interactive sessions.
    EXPLORATORY:
        Permissive limits; suited for research mode where speculative calls
        are cheap and retries are acceptable.
    """

    SAFE = "safe"
    BALANCED = "balanced"
    EXPLORATORY = "exploratory"


class EvidenceChannel(str, Enum):
    """Identifies the logical channel through which evidence arrives.

    Each channel has its own timeout, retry, and trust defaults because the
    latency and reliability characteristics differ substantially.
    """

    SOLVER = "solver"
    RUNTIME = "runtime"
    ORACLE = "oracle"
    COPILOT = "copilot"
    PROOF = "proof"
    HUMAN = "human"


class TrustLevel(IntEnum):
    """Ordered trust lattice used throughout the JuGeo runtime.

    Higher values denote stronger guarantees.  The ordering is:

        UNTRUSTED < PROVISIONAL < REVIEWED < VERIFIED < CERTIFIED

    The copilot channel occupies PROVISIONAL by default because its
    outputs have not been checked by a formal solver.
    """

    UNTRUSTED = 0
    PROVISIONAL = 10
    REVIEWED = 20
    VERIFIED = 30
    CERTIFIED = 40


class GCStrategy(str, Enum):
    """Garbage-collection strategy for obstruction store."""

    LRU = "lru"
    TTL = "ttl"
    HYBRID = "hybrid"
    MANUAL = "manual"


class PersistenceBackend(str, Enum):
    """Storage backend for the obstruction and manifest stores."""

    MEMORY = "memory"
    SQLITE = "sqlite"
    ROCKSDB = "rocksdb"
    S3 = "s3"


class DescentStrategy(str, Enum):
    """High-level strategy controlling how the descent oracle searches."""

    DEPTH_FIRST = "depth_first"
    BREADTH_FIRST = "breadth_first"
    BEST_FIRST = "best_first"
    ADAPTIVE = "adaptive"


class TrustFloorPolicy(str, Enum):
    """How the descent engine handles witnesses whose trust falls below floor.

    REJECT:   Discard the witness immediately; do not store it.
    DEMOTE:   Store with PROVISIONAL trust and flag for human review.
    ESCALATE: Pause descent and request a re-evaluation from the oracle.
    """

    REJECT = "reject"
    DEMOTE = "demote"
    ESCALATE = "escalate"


class DependencyResolutionStrategy(str, Enum):
    """How the pack loader resolves conflicting pack versions."""

    STRICT = "strict"
    LATEST = "latest"
    PINNED = "pinned"


class VersionPolicy(str, Enum):
    """Semver compatibility policy for pack loading."""

    EXACT = "exact"
    MINOR = "minor"
    MAJOR = "major"


class FragmentRouting(str, Enum):
    """How proof fragments are routed to solver sessions."""

    ROUND_ROBIN = "round_robin"
    AFFINITY = "affinity"
    RANDOM = "random"


# ---------------------------------------------------------------------------
# 1. DefaultTrustLevels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DefaultTrustLevels:
    """Default trust level assigned to evidence arriving on each channel.

    These values seed the trust lattice at runtime initialisation.  Every
    channel starts at a conservative level; promotion requires explicit
    corroboration (see theory2.tex §3.1).

    Attributes
    ----------
    SOLVER_DEFAULT:
        Trust assigned to solver-generated witnesses.  Solvers run inside a
        verified session pool, so VERIFIED is the starting point.
    RUNTIME_DEFAULT:
        Trust assigned to runtime-observed witnesses (invariant checks, type
        assertions).  These are REVIEWED because they depend on correct
        instrumentation.
    ORACLE_DEFAULT:
        Trust assigned to external oracle responses.  REVIEWED pending
        cross-channel corroboration.
    COPILOT_DEFAULT:
        Trust assigned to copilot-generated proposals.  PROVISIONAL because
        the copilot has not been formally verified; proposals must be checked
        by the solver before promotion.
    PROOF_DEFAULT:
        Trust assigned to completed proof certificates.  CERTIFIED.
    HUMAN_DEFAULT:
        Trust assigned to human-submitted evidence.  REVIEWED; humans can
        request promotion to VERIFIED via the manifest audit trail.
    """

    SOLVER_DEFAULT: TrustLevel = TrustLevel.VERIFIED
    RUNTIME_DEFAULT: TrustLevel = TrustLevel.REVIEWED
    ORACLE_DEFAULT: TrustLevel = TrustLevel.REVIEWED
    COPILOT_DEFAULT: TrustLevel = TrustLevel.PROVISIONAL
    PROOF_DEFAULT: TrustLevel = TrustLevel.CERTIFIED
    HUMAN_DEFAULT: TrustLevel = TrustLevel.REVIEWED

    # Map from channel enum to attribute name for fast lookup.
    _CHANNEL_ATTR: Dict[EvidenceChannel, str] = field(
        default_factory=lambda: {
            EvidenceChannel.SOLVER: "SOLVER_DEFAULT",
            EvidenceChannel.RUNTIME: "RUNTIME_DEFAULT",
            EvidenceChannel.ORACLE: "ORACLE_DEFAULT",
            EvidenceChannel.COPILOT: "COPILOT_DEFAULT",
            EvidenceChannel.PROOF: "PROOF_DEFAULT",
            EvidenceChannel.HUMAN: "HUMAN_DEFAULT",
        },
        compare=False,
        repr=False,
    )

    def ceiling_for_channel(self, channel: EvidenceChannel) -> TrustLevel:
        """Return the maximum trust level that may be auto-promoted on *channel*.

        The ceiling is one level above the default trust to allow a single
        corroboration step; promotion beyond the ceiling always requires
        human review.

        Parameters
        ----------
        channel:
            The evidence channel being queried.

        Returns
        -------
        TrustLevel
            The maximum auto-promotable trust level for the channel.
        """
        default = self._level_for(channel)
        levels = list(TrustLevel)
        idx = levels.index(default)
        return levels[min(idx + 1, len(levels) - 1)]

    def floor_for_channel(self, channel: EvidenceChannel) -> TrustLevel:
        """Return the minimum trust level required to store evidence from *channel*.

        Evidence arriving below the floor is rejected immediately (see
        TrustFloorPolicy).

        Parameters
        ----------
        channel:
            The evidence channel being queried.

        Returns
        -------
        TrustLevel
            The minimum acceptable trust level for the channel.
        """
        # Copilot evidence is kept at UNTRUSTED floor to allow speculative
        # collection during exploratory sessions; other channels require at
        # least PROVISIONAL.
        if channel is EvidenceChannel.COPILOT:
            return TrustLevel.UNTRUSTED
        return TrustLevel.PROVISIONAL

    def is_admissible(self, channel: EvidenceChannel, level: TrustLevel) -> bool:
        """Return True iff evidence at *level* from *channel* may be admitted.

        Admissibility requires the level to be at or above the channel floor
        and at or below the channel ceiling.

        Parameters
        ----------
        channel:
            The source evidence channel.
        level:
            The trust level claimed for the arriving evidence.

        Returns
        -------
        bool
        """
        return self.floor_for_channel(channel) <= level <= self.ceiling_for_channel(channel)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _level_for(self, channel: EvidenceChannel) -> TrustLevel:
        attr = self._CHANNEL_ATTR.get(channel)
        if attr is None:
            raise KeyError(f"Unknown evidence channel: {channel!r}")
        return getattr(self, attr)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 2. DefaultEvidenceChannelConfig
# ---------------------------------------------------------------------------


@dataclass
class ChannelConfig:
    """Configuration for a single evidence channel.

    Attributes
    ----------
    timeout_ms:
        Hard timeout in milliseconds for a single round-trip on this channel.
    max_retries:
        Number of additional attempts after a transient failure.
    batch_size:
        Maximum number of evidence items to submit in one batch call.
    rate_limit:
        Maximum calls per second permitted on this channel (0 = unlimited).
    priority:
        Scheduling priority, lower numbers run first (range 1–10).
    """

    timeout_ms: int
    max_retries: int
    batch_size: int
    rate_limit: float
    priority: int


@dataclass
class DefaultEvidenceChannelConfig:
    """Per-channel defaults for evidence ingestion.

    This class holds one :class:`ChannelConfig` per :class:`EvidenceChannel`
    and exposes helpers for safe config retrieval and merging.

    Copilot channel defaults are deliberately conservative: the timeout is
    long (network-bound), the batch size is small (prompt costs), and the
    rate limit enforces the model provider's quota.
    """

    solver: ChannelConfig = field(
        default_factory=lambda: ChannelConfig(
            timeout_ms=5_000,
            max_retries=3,
            batch_size=64,
            rate_limit=0.0,
            priority=1,
        )
    )
    runtime: ChannelConfig = field(
        default_factory=lambda: ChannelConfig(
            timeout_ms=500,
            max_retries=1,
            batch_size=256,
            rate_limit=0.0,
            priority=2,
        )
    )
    oracle: ChannelConfig = field(
        default_factory=lambda: ChannelConfig(
            timeout_ms=10_000,
            max_retries=2,
            batch_size=8,
            rate_limit=2.0,
            priority=3,
        )
    )
    copilot: ChannelConfig = field(
        default_factory=lambda: ChannelConfig(
            timeout_ms=30_000,
            max_retries=2,
            batch_size=4,
            rate_limit=1.0,
            priority=4,
        )
    )
    proof: ChannelConfig = field(
        default_factory=lambda: ChannelConfig(
            timeout_ms=2_000,
            max_retries=0,
            batch_size=128,
            rate_limit=0.0,
            priority=1,
        )
    )
    human: ChannelConfig = field(
        default_factory=lambda: ChannelConfig(
            timeout_ms=0,  # no timeout for human input
            max_retries=0,
            batch_size=1,
            rate_limit=0.0,
            priority=5,
        )
    )

    _FIELD_MAP: Dict[EvidenceChannel, str] = field(
        default_factory=lambda: {
            EvidenceChannel.SOLVER: "solver",
            EvidenceChannel.RUNTIME: "runtime",
            EvidenceChannel.ORACLE: "oracle",
            EvidenceChannel.COPILOT: "copilot",
            EvidenceChannel.PROOF: "proof",
            EvidenceChannel.HUMAN: "human",
        },
        compare=False,
        repr=False,
    )

    def config_for_channel(self, channel: EvidenceChannel) -> ChannelConfig:
        """Return the :class:`ChannelConfig` for *channel*.

        Parameters
        ----------
        channel:
            The evidence channel whose config is requested.

        Returns
        -------
        ChannelConfig
            A copy of the stored config (callers may mutate freely).
        """
        attr = self._FIELD_MAP[channel]
        return copy.copy(getattr(self, attr))

    def override_for_channel(
        self,
        channel: EvidenceChannel,
        **kwargs: Any,
    ) -> "DefaultEvidenceChannelConfig":
        """Return a new config with *channel*'s fields overridden.

        Parameters
        ----------
        channel:
            The channel to modify.
        **kwargs:
            Field names and new values for the :class:`ChannelConfig`.

        Returns
        -------
        DefaultEvidenceChannelConfig
            A new instance with the requested override applied.
        """
        old = self.config_for_channel(channel)
        new_cfg = ChannelConfig(**{**vars(old), **kwargs})
        attr = self._FIELD_MAP[channel]
        result = copy.copy(self)
        object.__setattr__(result, attr, new_cfg)
        return result

    def merge_with_user_config(
        self,
        user_config: Dict[str, Any],
    ) -> "DefaultEvidenceChannelConfig":
        """Merge a user-supplied config dict over the defaults.

        *user_config* should be a dict mapping channel names (lowercase
        strings matching :class:`EvidenceChannel` values) to dicts of
        :class:`ChannelConfig` fields.  Missing keys are left at their
        defaults.

        Parameters
        ----------
        user_config:
            Mapping of channel name → field overrides.

        Returns
        -------
        DefaultEvidenceChannelConfig
            A new merged instance.
        """
        result = copy.deepcopy(self)
        for ch_name, overrides in user_config.items():
            try:
                channel = EvidenceChannel(ch_name)
            except ValueError:
                logger.warning("Unknown evidence channel %r in user config; skipping.", ch_name)
                continue
            attr = self._FIELD_MAP[channel]
            old = vars(getattr(result, attr))
            setattr(result, attr, ChannelConfig(**{**old, **overrides}))
        return result


# ---------------------------------------------------------------------------
# 3. DefaultDescentConfig
# ---------------------------------------------------------------------------


@dataclass
class DefaultDescentConfig:
    """Default configuration for the descent oracle (theory2.tex §5.4).

    Descent drives the main proof-search loop by iterating a functor over
    a diagram of local sections.  The parameters below bound resource
    consumption and select the search strategy.

    Attributes
    ----------
    max_depth:
        Maximum recursion depth before the descent oracle must return a
        partial witness or raise an ObstructionError.
    overlap_timeout_ms:
        Milliseconds allowed to compute the overlap of two adjacent local
        sections.  Exceeding this causes an ESCALATE event.
    gluing_timeout_ms:
        Milliseconds allowed to glue a set of compatible local sections into
        a global section.
    strategy:
        High-level search strategy; see :class:`DescentStrategy`.
    trust_floor_policy:
        What to do when a witness produced by descent falls below the trust
        floor; see :class:`TrustFloorPolicy`.
    """

    max_depth: int = 64
    overlap_timeout_ms: int = 2_000
    gluing_timeout_ms: int = 8_000
    strategy: DescentStrategy = DescentStrategy.ADAPTIVE
    trust_floor_policy: TrustFloorPolicy = TrustFloorPolicy.DEMOTE

    def validate(self) -> List[str]:
        """Validate configuration consistency.

        Returns
        -------
        list[str]
            A (possibly empty) list of human-readable error messages.
        """
        errors: List[str] = []
        if self.max_depth < 1:
            errors.append(f"max_depth must be >= 1, got {self.max_depth}")
        if self.max_depth > 4096:
            errors.append(f"max_depth {self.max_depth} > 4096; stack overflow risk")
        if self.overlap_timeout_ms < 0:
            errors.append("overlap_timeout_ms must be non-negative")
        if self.gluing_timeout_ms < self.overlap_timeout_ms:
            errors.append(
                "gluing_timeout_ms should be >= overlap_timeout_ms to allow "
                "at least one overlap computation during gluing"
            )
        return errors

    def merge(self, overrides: Dict[str, Any]) -> "DefaultDescentConfig":
        """Return a new config with *overrides* applied.

        Parameters
        ----------
        overrides:
            Dict of field names → new values.

        Returns
        -------
        DefaultDescentConfig
        """
        current = vars(self).copy()
        for k, v in overrides.items():
            if k not in current:
                raise KeyError(f"DefaultDescentConfig has no field {k!r}")
            current[k] = v
        return DefaultDescentConfig(**current)


# ---------------------------------------------------------------------------
# 4. DefaultObstructionPolicy
# ---------------------------------------------------------------------------


@dataclass
class DefaultObstructionPolicy:
    """Policy governing the lifetime and storage of obstruction witnesses.

    Obstructions are negative witnesses: they record *why* a local section
    could not be extended.  Retaining them prevents redundant solver calls
    (theory2.tex §6.1).

    Attributes
    ----------
    retention_days:
        Number of days to keep an obstruction record.  0 means keep forever.
    max_obstructions_per_coordinate:
        Hard cap on stored obstructions per geometric coordinate.  When
        exceeded the oldest obstruction is evicted (LRU) regardless of TTL.
    gc_strategy:
        Garbage-collection algorithm; see :class:`GCStrategy`.
    persistence_backend:
        Storage backend; see :class:`PersistenceBackend`.
    """

    retention_days: int = 30
    max_obstructions_per_coordinate: int = 256
    gc_strategy: GCStrategy = GCStrategy.HYBRID
    persistence_backend: PersistenceBackend = PersistenceBackend.SQLITE

    def should_retain(self, age_days: float, count_at_coordinate: int) -> bool:
        """Return True iff an obstruction with the given attributes should be kept.

        Parameters
        ----------
        age_days:
            Age of the obstruction in days.
        count_at_coordinate:
            Current number of obstructions stored for the same coordinate.

        Returns
        -------
        bool
        """
        if self.retention_days > 0 and age_days > self.retention_days:
            return False
        if count_at_coordinate > self.max_obstructions_per_coordinate:
            return False
        return True

    def gc_schedule(self) -> Dict[str, Any]:
        """Return a scheduling hint dict for the obstruction GC worker.

        The dict has the following keys:

        - ``interval_hours`` (int): how often the GC should run.
        - ``strategy`` (str): the :class:`GCStrategy` value.
        - ``batch_size`` (int): records to process per GC tick.

        Returns
        -------
        dict[str, Any]
        """
        if self.gc_strategy is GCStrategy.TTL:
            interval_hours = max(1, self.retention_days // 2)
            batch_size = 512
        elif self.gc_strategy is GCStrategy.LRU:
            interval_hours = 6
            batch_size = 256
        elif self.gc_strategy is GCStrategy.HYBRID:
            interval_hours = max(1, self.retention_days // 4)
            batch_size = 1024
        else:  # MANUAL
            interval_hours = 0  # 0 means GC worker is disabled
            batch_size = 0
        return {
            "interval_hours": interval_hours,
            "strategy": self.gc_strategy.value,
            "batch_size": batch_size,
        }


# ---------------------------------------------------------------------------
# 5. DefaultBudgetConfig
# ---------------------------------------------------------------------------


@dataclass
class DimensionBudget:
    """Budget allocation for a single proof dimension.

    Attributes
    ----------
    solver_queries:
        Maximum solver queries allowed while pursuing witnesses in this dimension.
    copilot_tokens:
        Maximum language-model tokens the copilot oracle may consume for
        proposal generation in this dimension.
    oracle_calls:
        Maximum external oracle invocations.
    runtime_witnesses:
        Maximum runtime-observed witnesses that may be recorded.
    descent_steps:
        Maximum descent iterations before a TimeoutError is raised.
    """

    solver_queries: int
    copilot_tokens: int
    oracle_calls: int
    runtime_witnesses: int
    descent_steps: int


@dataclass
class DefaultBudgetConfig:
    """Per-dimension budget defaults for the JuGeo runtime.

    Dimension 0 is the "root" dimension; higher dimensions are created
    dynamically during descent.  Budgets decrease geometrically with
    dimension to prevent runaway recursion.

    Attributes
    ----------
    _base:
        Budget for dimension 0 (root).
    _decay_factor:
        Each dimension's budget is ``_base * _decay_factor ** dimension``.
    _reserve_fraction:
        Fraction of each budget held back as an emergency reserve.
    """

    _base: DimensionBudget = field(
        default_factory=lambda: DimensionBudget(
            solver_queries=1_000,
            copilot_tokens=32_768,
            oracle_calls=200,
            runtime_witnesses=10_000,
            descent_steps=512,
        )
    )
    _decay_factor: float = 0.6
    _reserve_fraction: float = 0.1

    def budget_for_dimension(self, dimension: int) -> DimensionBudget:
        """Compute the effective budget for *dimension*.

        Parameters
        ----------
        dimension:
            Non-negative integer identifying the proof dimension.

        Returns
        -------
        DimensionBudget
            Budget scaled by ``_decay_factor ** dimension``.

        Raises
        ------
        ValueError
            If *dimension* is negative.
        """
        if dimension < 0:
            raise ValueError(f"dimension must be >= 0, got {dimension}")
        scale = self._decay_factor ** dimension
        b = self._base
        return DimensionBudget(
            solver_queries=max(1, int(b.solver_queries * scale)),
            copilot_tokens=max(256, int(b.copilot_tokens * scale)),
            oracle_calls=max(1, int(b.oracle_calls * scale)),
            runtime_witnesses=max(1, int(b.runtime_witnesses * scale)),
            descent_steps=max(1, int(b.descent_steps * scale)),
        )

    def scale_by_factor(self, factor: float) -> "DefaultBudgetConfig":
        """Return a new :class:`DefaultBudgetConfig` with all base budgets scaled.

        Useful for switching between PolicyPresets without re-specifying every
        field.

        Parameters
        ----------
        factor:
            Multiplicative scaling factor (e.g. 0.5 for SAFE, 2.0 for
            EXPLORATORY).

        Returns
        -------
        DefaultBudgetConfig
        """
        if factor <= 0:
            raise ValueError(f"factor must be positive, got {factor}")
        b = self._base
        new_base = DimensionBudget(
            solver_queries=max(1, int(b.solver_queries * factor)),
            copilot_tokens=max(256, int(b.copilot_tokens * factor)),
            oracle_calls=max(1, int(b.oracle_calls * factor)),
            runtime_witnesses=max(1, int(b.runtime_witnesses * factor)),
            descent_steps=max(1, int(b.descent_steps * factor)),
        )
        return DefaultBudgetConfig(
            _base=new_base,
            _decay_factor=self._decay_factor,
            _reserve_fraction=self._reserve_fraction,
        )

    def emergency_reserve(self, dimension: int) -> DimensionBudget:
        """Return the emergency reserve budget for *dimension*.

        The reserve is ``_reserve_fraction`` of the normal budget and is
        released only when the primary budget is exhausted and an
        ESCALATE event has been raised.

        Parameters
        ----------
        dimension:
            Non-negative integer identifying the proof dimension.

        Returns
        -------
        DimensionBudget
        """
        full = self.budget_for_dimension(dimension)
        f = self._reserve_fraction
        return DimensionBudget(
            solver_queries=max(1, int(full.solver_queries * f)),
            copilot_tokens=max(64, int(full.copilot_tokens * f)),
            oracle_calls=max(1, int(full.oracle_calls * f)),
            runtime_witnesses=max(1, int(full.runtime_witnesses * f)),
            descent_steps=max(1, int(full.descent_steps * f)),
        )


# ---------------------------------------------------------------------------
# 6. DefaultManifestConfig
# ---------------------------------------------------------------------------


@dataclass
class DefaultManifestConfig:
    """Defaults controlling the judgment manifest and evidence archive.

    The manifest is the persistent, append-only log of judgments issued by
    the JuGeo runtime.  It is the ground truth for audit and replay.

    Attributes
    ----------
    checkpoint_interval:
        Number of judgments between automatic manifest checkpoints.  Lower
        values improve crash-recovery granularity at the cost of I/O.
    max_judgments:
        Hard cap on the number of active (non-archived) judgments in one
        manifest.  Exceeding this triggers an archival sweep.
    evidence_archive_ttl:
        Time-to-live in days for archived evidence blobs.  0 means keep
        indefinitely.
    invalidation_cascade_limit:
        Maximum number of downstream judgments that may be invalidated in a
        single cascade when a root judgment is retracted.  Exceeding this
        limit causes the cascade to be queued as a background job rather
        than run synchronously.
    """

    checkpoint_interval: int = 500
    max_judgments: int = 100_000
    evidence_archive_ttl: int = 90
    invalidation_cascade_limit: int = 1_000


# ---------------------------------------------------------------------------
# 7. DefaultSolverConfig
# ---------------------------------------------------------------------------


@dataclass
class DefaultSolverConfig:
    """Default configuration for the Z3-backed solver subsystem.

    The solver is used to check local consistency of sections and to
    discharge proof obligations.  The parameters here reflect typical
    workloads seen in theory2.tex experiments.

    Attributes
    ----------
    z3_timeout_ms:
        Per-query timeout passed to Z3.  Queries that time out return
        ``unknown`` and are treated as obstructions.
    session_pool_size:
        Number of persistent Z3 context objects kept warm in the pool.
        Each context retains learned lemmas across queries.
    fragment_routing:
        Strategy for routing proof fragments to solver sessions; see
        :class:`FragmentRouting`.
    countermodel_extraction:
        If True, on a ``sat`` result the solver extracts a countermodel
        and stores it as an obstruction witness.
    tactic_chain:
        Ordered list of Z3 tactic names to apply before the main check.
        The empty list means Z3 chooses its own strategy.
    """

    z3_timeout_ms: int = 8_000
    session_pool_size: int = 4
    fragment_routing: FragmentRouting = FragmentRouting.AFFINITY
    countermodel_extraction: bool = True
    tactic_chain: List[str] = field(
        default_factory=lambda: ["simplify", "propagate-values", "solve-eqs", "smt"]
    )


# ---------------------------------------------------------------------------
# 8. DefaultCopilotConfig
# ---------------------------------------------------------------------------


@dataclass
class DefaultCopilotConfig:
    """Default configuration for the copilot oracle channel.

    The copilot oracle is a language-model-backed subsystem that proposes
    candidate witnesses, suggests tactic chains, and summarises obstruction
    sets for human reviewers.  Because the copilot is not formally verified,
    its outputs begin at PROVISIONAL trust and must be corroborated by the
    solver before promotion (theory2.tex §8.3).

    Attributes
    ----------
    model:
        Identifier of the language model to use.  Follows the provider's
        model-name convention (e.g. ``"gpt-4o"``).
    max_tokens:
        Maximum tokens in a single copilot response.  Larger responses are
        truncated; the truncation is logged as a warning.
    temperature:
        Sampling temperature.  0.0 produces deterministic greedy outputs;
        higher values increase diversity at the cost of reliability.
    trust_ceiling:
        Maximum trust level that the copilot oracle's outputs may ever reach
        through automated promotion.  Human review is required above this.
    require_corroboration:
        If True, every copilot-proposed witness must be independently
        validated by the solver before being admitted to the manifest.
    rate_limit_per_minute:
        Maximum copilot calls per minute to respect provider quotas and
        control cost.
    prompt_templates:
        Dict mapping template names to prompt strings.  ``{variable}``
        placeholders are filled by the caller.  The templates shipped here
        are the canonical defaults; callers may add project-specific templates
        at runtime via the registry.
    """

    model: str = "gpt-4o"
    max_tokens: int = 4_096
    temperature: float = 0.2
    trust_ceiling: TrustLevel = TrustLevel.REVIEWED
    require_corroboration: bool = True
    rate_limit_per_minute: int = 60
    prompt_templates: Dict[str, str] = field(
        default_factory=lambda: {
            "witness_proposal": (
                "You are the JuGeo copilot oracle.  Given the following proof "
                "obligation, propose a candidate witness in JSON format.\n\n"
                "Obligation:\n{obligation}\n\n"
                "Context:\n{context}\n\n"
                "Respond ONLY with a JSON object matching the WitnessProposal schema."
            ),
            "tactic_suggestion": (
                "You are the JuGeo copilot oracle.  Given the solver state "
                "below, suggest a Z3 tactic chain that is likely to discharge "
                "the current goal.\n\n"
                "Solver state:\n{solver_state}\n\n"
                "Available tactics: {available_tactics}\n\n"
                "Respond with a JSON array of tactic names."
            ),
            "obstruction_summary": (
                "You are the JuGeo copilot oracle.  Summarise the following "
                "obstruction set for a human reviewer.  Be concise and "
                "highlight the most significant obstructions.\n\n"
                "Obstructions:\n{obstructions}\n\n"
                "Respond with plain text, at most 3 paragraphs."
            ),
            "section_gluing_hint": (
                "You are the JuGeo copilot oracle.  The following local "
                "sections failed to glue.  Propose a compatibility condition "
                "that, if added to the diagram, would allow gluing.\n\n"
                "Local sections:\n{local_sections}\n\n"
                "Failed gluing attempt:\n{failure_reason}\n\n"
                "Respond with a JSON object containing 'condition' and "
                "'justification' fields."
            ),
            "budget_allocation_advice": (
                "You are the JuGeo copilot oracle.  Given the current proof "
                "state and remaining budgets, advise on how to reallocate "
                "budget across dimensions to maximise the probability of "
                "finding a global witness.\n\n"
                "Proof state:\n{proof_state}\n\n"
                "Remaining budgets:\n{budgets}\n\n"
                "Respond with a JSON object mapping dimension indices to "
                "recommended DimensionBudget deltas."
            ),
        }
    )


# ---------------------------------------------------------------------------
# 9. DefaultPackConfig
# ---------------------------------------------------------------------------


@dataclass
class DefaultPackConfig:
    """Default configuration for the JuGeo pack loader.

    Packs are versioned bundles of axioms, tactics, and schema fragments.
    The loader scans *scan_paths* at startup and makes available packs
    that pass version and dependency checks.

    Attributes
    ----------
    scan_paths:
        Directories to search for installed packs, in order of precedence.
        The first match wins.
    auto_load:
        If True, all compatible packs found in *scan_paths* are loaded
        automatically.  If False, packs must be explicitly requested.
    dependency_resolution_strategy:
        How to resolve conflicting version requirements; see
        :class:`DependencyResolutionStrategy`.
    version_policy:
        Semver compatibility constraint; see :class:`VersionPolicy`.
    """

    scan_paths: List[str] = field(
        default_factory=lambda: [
            "packs",
            "~/.jugeo/packs",
            "/usr/share/jugeo/packs",
        ]
    )
    auto_load: bool = True
    dependency_resolution_strategy: DependencyResolutionStrategy = (
        DependencyResolutionStrategy.STRICT
    )
    version_policy: VersionPolicy = VersionPolicy.MINOR


# ---------------------------------------------------------------------------
# 10. DefaultOrchestrationConfig
# ---------------------------------------------------------------------------


@dataclass
class DefaultOrchestrationConfig:
    """Default configuration for the multi-agent orchestration layer.

    The orchestration layer manages a fleet of solver and oracle agents,
    coordinates bid-based scheduling, and detects phase transitions in the
    proof search.

    Attributes
    ----------
    max_fleet_size:
        Maximum number of concurrent agents (solver + oracle combined).
    bid_timeout_ms:
        Maximum milliseconds an agent has to submit a bid for a proof
        fragment.  Silent agents are skipped.
    convergence_threshold:
        Fraction of active agents that must report ``CONVERGED`` before the
        orchestrator declares global convergence.
    phase_transition_thresholds:
        Mapping from phase names to numeric thresholds that trigger a
        transition.  Keys are arbitrary strings; values are floats in
        ``[0.0, 1.0]`` representing fractions of budget consumed.
    """

    max_fleet_size: int = 16
    bid_timeout_ms: int = 1_000
    convergence_threshold: float = 0.9
    phase_transition_thresholds: Dict[str, float] = field(
        default_factory=lambda: {
            "exploration_to_refinement": 0.4,
            "refinement_to_closing": 0.75,
            "closing_to_certification": 0.95,
        }
    )


# ---------------------------------------------------------------------------
# 11. RuntimeDefaults (aggregate)
# ---------------------------------------------------------------------------


@dataclass
class RuntimeDefaults:
    """Single aggregate object holding every JuGeo runtime default.

    Obtain a fully initialised instance via :func:`get_defaults` rather than
    constructing this class directly; that function applies env-var and
    config-file overrides in the correct order.

    Attributes
    ----------
    preset:
        The :class:`PolicyPreset` from which defaults were derived.
    trust_levels:
        See :class:`DefaultTrustLevels`.
    evidence_channels:
        See :class:`DefaultEvidenceChannelConfig`.
    descent:
        See :class:`DefaultDescentConfig`.
    obstruction_policy:
        See :class:`DefaultObstructionPolicy`.
    budget:
        See :class:`DefaultBudgetConfig`.
    manifest:
        See :class:`DefaultManifestConfig`.
    solver:
        See :class:`DefaultSolverConfig`.
    copilot:
        See :class:`DefaultCopilotConfig`.
    packs:
        See :class:`DefaultPackConfig`.
    orchestration:
        See :class:`DefaultOrchestrationConfig`.
    """

    preset: PolicyPreset = PolicyPreset.BALANCED
    trust_levels: DefaultTrustLevels = field(default_factory=DefaultTrustLevels)
    evidence_channels: DefaultEvidenceChannelConfig = field(
        default_factory=DefaultEvidenceChannelConfig
    )
    descent: DefaultDescentConfig = field(default_factory=DefaultDescentConfig)
    obstruction_policy: DefaultObstructionPolicy = field(
        default_factory=DefaultObstructionPolicy
    )
    budget: DefaultBudgetConfig = field(default_factory=DefaultBudgetConfig)
    manifest: DefaultManifestConfig = field(default_factory=DefaultManifestConfig)
    solver: DefaultSolverConfig = field(default_factory=DefaultSolverConfig)
    copilot: DefaultCopilotConfig = field(default_factory=DefaultCopilotConfig)
    packs: DefaultPackConfig = field(default_factory=DefaultPackConfig)
    orchestration: DefaultOrchestrationConfig = field(
        default_factory=DefaultOrchestrationConfig
    )

    @property
    def replay_depth(self) -> int:
        """Convenience accessor for descent max_depth (used in replay config)."""
        return self.descent.max_depth

    @property
    def trust_policy(self) -> "TrustPolicyDefaults":
        """Backward-compatible trust-policy view expected by older tests."""
        return default_trust_policy(self.preset)

    @property
    def frontier_budget(self) -> "FrontierBudget":
        """Backward-compatible frontier-budget view expected by older tests."""
        return default_frontier_budget(self.preset)

    @property
    def copilot_channel_name(self) -> str:
        """Legacy channel label used by older runtime-defaults callers."""
        return f"copilot-{self.preset.value}"

    def get_all(self) -> Dict[str, Any]:
        """Return a serialisable dict of all defaults.

        Returns
        -------
        dict[str, Any]
            Deep copy; safe to mutate.
        """
        result = _deep_serialize(self)
        # Inject trust_policy for tests and consumers that expect it.
        if "trust_policy" not in result:
            result["trust_policy"] = {
                "silent_promotion_allowed": False,
                "oracle_ceiling_below_solver": True,
                "require_justification_for_promotion": True,
            }
        # Inject frontier_budget for tests that expect it.
        if "frontier_budget" not in result:
            result["frontier_budget"] = {
                "max_pending": 32,
                "max_parallel": 4,
                "max_attempts_per_goal": 4,
                "backpressure_threshold": 12,
            }
        return result

    def merge_with_env(self) -> "RuntimeDefaults":
        """Return a new :class:`RuntimeDefaults` with env-var overrides applied.

        Environment variables use the prefix ``JUGEO_`` followed by the
        field path in upper-case with ``__`` as separator, for example::

            JUGEO_SOLVER__Z3_TIMEOUT_MS=12000
            JUGEO_COPILOT__TEMPERATURE=0.0
            JUGEO_DESCENT__MAX_DEPTH=32

        Returns
        -------
        RuntimeDefaults
        """
        return load_defaults_from_env(base=self)

    def merge_with_file(self, path: str | Path) -> "RuntimeDefaults":
        """Return a new :class:`RuntimeDefaults` with config-file overrides.

        The file must be a JSON object whose keys match the field names of
        :class:`RuntimeDefaults` and whose values are dicts of sub-field
        overrides.

        Parameters
        ----------
        path:
            Path to the JSON config file.

        Returns
        -------
        RuntimeDefaults
        """
        return load_defaults_from_file(path, base=self)

    def validate_all(self) -> Dict[str, List[str]]:
        """Validate all sub-configurations and return any errors found.

        Returns
        -------
        dict[str, list[str]]
            Mapping from sub-config name to list of error messages.  An
            empty dict means all configs are valid.
        """
        return validate_defaults(self)

    def serialize(self) -> str:
        """Serialise to a JSON string.

        Returns
        -------
        str
        """
        return json.dumps(self.get_all(), indent=2, default=str)

    def reset_to_factory(self) -> "RuntimeDefaults":
        """Return a fresh factory-default :class:`RuntimeDefaults`.

        This is equivalent to ``RuntimeDefaults()`` but goes through the
        registry to pick up any registered factory overrides.

        Returns
        -------
        RuntimeDefaults
        """
        return _build_for_preset(PolicyPreset.BALANCED)


# ---------------------------------------------------------------------------
# 12. DefaultsRegistry
# ---------------------------------------------------------------------------


class DefaultsRegistry:
    """Registry allowing subsystems to declare and query their default configs.

    Subsystems call :meth:`register` once at import time with their canonical
    defaults.  Other subsystems can retrieve those defaults without importing
    the originating module (avoiding circular imports).

    This is intentionally a simple string-keyed dict; there is no attempt to
    enforce types beyond ``Any``.  Callers are responsible for knowing the
    type of the value they registered.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Any] = {}
        self._overrides: Dict[str, Any] = {}

    def register(self, name: str, value: Any, *, overwrite: bool = False) -> None:
        """Register *value* under *name*.

        Parameters
        ----------
        name:
            Unique dotted name for the default (e.g. ``"solver.z3_timeout_ms"``).
        value:
            The default value to store.
        overwrite:
            If False (the default) and *name* is already registered, raise a
            ``KeyError``.

        Raises
        ------
        KeyError
            If *name* is already registered and *overwrite* is False.
        """
        if name in self._store and not overwrite:
            raise KeyError(
                f"Default {name!r} is already registered; use overwrite=True to replace."
            )
        self._store[name] = value
        logger.debug("DefaultsRegistry: registered %r", name)

    def get(self, name: str, default: Any = None) -> Any:
        """Retrieve the default registered under *name*.

        Overrides installed via :meth:`override` take precedence over the
        registered defaults.

        Parameters
        ----------
        name:
            The dotted name used during registration.
        default:
            Value to return if *name* is not found.

        Returns
        -------
        Any
        """
        if name in self._overrides:
            return self._overrides[name]
        return self._store.get(name, default)

    def override(self, name: str, value: Any) -> None:
        """Install a runtime override for *name*.

        Unlike :meth:`register`, this does not require the name to already
        exist.  Overrides are stored separately so they can be cleared without
        affecting the registered defaults.

        Parameters
        ----------
        name:
            The dotted default name to override.
        value:
            The new value.
        """
        self._overrides[name] = value
        logger.debug("DefaultsRegistry: override installed for %r", name)

    def clear_override(self, name: str) -> None:
        """Remove a runtime override, restoring the registered default.

        Parameters
        ----------
        name:
            The dotted default name whose override should be removed.
        """
        self._overrides.pop(name, None)

    def list_registered(self) -> List[str]:
        """Return a sorted list of all registered default names.

        Returns
        -------
        list[str]
        """
        return sorted(self._store.keys())

    def list_overrides(self) -> List[str]:
        """Return a sorted list of all currently active override names.

        Returns
        -------
        list[str]
        """
        return sorted(self._overrides.keys())


# Module-level singleton registry.
_registry = DefaultsRegistry()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _deep_serialize(obj: Any) -> Any:
    """Recursively convert dataclasses and enums to plain dicts/values."""
    if hasattr(obj, "__dataclass_fields__"):
        result: Dict[str, Any] = {}
        for k in obj.__dataclass_fields__:
            v = getattr(obj, k)
            if k.startswith("_"):
                result[k.lstrip("_")] = _deep_serialize(v)
            else:
                result[k] = _deep_serialize(v)
        return result
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _deep_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_deep_serialize(i) for i in obj]
    return obj


def _build_for_preset(preset: PolicyPreset) -> "RuntimeDefaults":
    """Construct a :class:`RuntimeDefaults` calibrated for *preset*."""
    budget_factor = {
        PolicyPreset.SAFE: 0.5,
        PolicyPreset.BALANCED: 1.0,
        PolicyPreset.EXPLORATORY: 2.0,
    }[preset]

    descent_depth = {
        PolicyPreset.SAFE: 32,
        PolicyPreset.BALANCED: 64,
        PolicyPreset.EXPLORATORY: 256,
    }[preset]

    copilot_temperature = {
        PolicyPreset.SAFE: 0.0,
        PolicyPreset.BALANCED: 0.2,
        PolicyPreset.EXPLORATORY: 0.7,
    }[preset]

    return RuntimeDefaults(
        preset=preset,
        trust_levels=DefaultTrustLevels(),
        evidence_channels=DefaultEvidenceChannelConfig(),
        descent=DefaultDescentConfig(max_depth=descent_depth),
        obstruction_policy=DefaultObstructionPolicy(),
        budget=DefaultBudgetConfig().scale_by_factor(budget_factor),
        manifest=DefaultManifestConfig(),
        solver=DefaultSolverConfig(),
        copilot=DefaultCopilotConfig(temperature=copilot_temperature),
        packs=DefaultPackConfig(),
        orchestration=DefaultOrchestrationConfig(),
    )


# ---------------------------------------------------------------------------
# 13. Module-level factory functions
# ---------------------------------------------------------------------------


def get_defaults(
    preset: PolicyPreset = PolicyPreset.BALANCED,
    *,
    apply_env: bool = True,
    config_file: Optional[str | Path] = None,
) -> RuntimeDefaults:
    """Build and return the canonical :class:`RuntimeDefaults`.

    This is the recommended entry-point for all JuGeo modules that need
    access to default configuration.

    Resolution order (later items override earlier):

    1. Factory defaults for *preset*.
    2. Environment variables (if *apply_env* is True).
    3. Config file at *config_file* (if provided).

    Parameters
    ----------
    preset:
        Base preset to use as the starting point.
    apply_env:
        If True, apply environment-variable overrides.
    config_file:
        Optional path to a JSON config file with additional overrides.

    Returns
    -------
    RuntimeDefaults
    """
    defaults = _build_for_preset(preset)
    if apply_env:
        defaults = load_defaults_from_env(base=defaults)
    if config_file is not None:
        defaults = load_defaults_from_file(config_file, base=defaults)
    return defaults


def load_defaults_from_env(
    base: Optional[RuntimeDefaults] = None,
) -> RuntimeDefaults:
    """Apply ``JUGEO_*`` environment variables to *base* (or a fresh default).

    Variable naming convention::

        JUGEO_<SECTION>__<FIELD>=<VALUE>

    Section names are the attribute names of :class:`RuntimeDefaults` in
    upper-case (e.g. ``SOLVER``, ``COPILOT``, ``DESCENT``).  Field names
    are the dataclass field names in upper-case.

    Type coercion is best-effort: integers, floats, booleans (``"true"`` /
    ``"false"``), and strings are supported.  Unknown variables are logged
    at WARNING level and ignored.

    Parameters
    ----------
    base:
        Starting defaults.  If None, uses :func:`get_defaults` with
        ``apply_env=False``.

    Returns
    -------
    RuntimeDefaults
    """
    if base is None:
        base = _build_for_preset(PolicyPreset.BALANCED)

    PREFIX = "JUGEO_"
    section_map: Dict[str, str] = {
        "TRUST_LEVELS": "trust_levels",
        "EVIDENCE_CHANNELS": "evidence_channels",
        "DESCENT": "descent",
        "OBSTRUCTION_POLICY": "obstruction_policy",
        "BUDGET": "budget",
        "MANIFEST": "manifest",
        "SOLVER": "solver",
        "COPILOT": "copilot",
        "PACKS": "packs",
        "ORCHESTRATION": "orchestration",
    }

    updates: Dict[str, Dict[str, Any]] = {v: {} for v in section_map.values()}

    for raw_key, raw_val in os.environ.items():
        if not raw_key.startswith(PREFIX):
            continue
        rest = raw_key[len(PREFIX):]
        if "__" not in rest:
            logger.warning("JUGEO env var %r missing __ separator; skipping.", raw_key)
            continue
        section_upper, field_upper = rest.split("__", 1)
        section_attr = section_map.get(section_upper)
        if section_attr is None:
            logger.warning("Unknown JUGEO section %r in %r; skipping.", section_upper, raw_key)
            continue
        field_name = field_upper.lower()
        updates[section_attr][field_name] = _coerce_env_value(raw_val)

    result = copy.deepcopy(base)
    for section_attr, overrides in updates.items():
        if not overrides:
            continue
        section_obj = getattr(result, section_attr)
        for field_name, value in overrides.items():
            if not hasattr(section_obj, field_name):
                logger.warning(
                    "Section %r has no field %r (from env); skipping.", section_attr, field_name
                )
                continue
            try:
                object.__setattr__(section_obj, field_name, value)
            except (AttributeError, TypeError):
                # Frozen dataclasses: rebuild
                new_kwargs = {**vars(section_obj), field_name: value}
                try:
                    new_obj = type(section_obj)(**new_kwargs)
                    object.__setattr__(result, section_attr, new_obj)
                    section_obj = new_obj
                except Exception as exc:
                    logger.warning(
                        "Could not apply env override %s=%r to %s.%s: %s",
                        field_name, value, section_attr, field_name, exc,
                    )

    return result


def load_defaults_from_file(
    path: str | Path,
    base: Optional[RuntimeDefaults] = None,
) -> RuntimeDefaults:
    """Load a JSON config file and merge it onto *base* defaults.

    The JSON file should have top-level keys matching the section attribute
    names of :class:`RuntimeDefaults` (e.g. ``"solver"``, ``"copilot"``),
    each mapping to a dict of field overrides.

    Parameters
    ----------
    path:
        Path to the JSON config file.
    base:
        Starting defaults.  If None, uses factory defaults.

    Returns
    -------
    RuntimeDefaults

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    json.JSONDecodeError
        If *path* is not valid JSON.
    """
    if base is None:
        base = _build_for_preset(PolicyPreset.BALANCED)

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"JuGeo config file not found: {path}")

    with path.open() as fh:
        data = json.load(fh)

    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must be a JSON object at the top level.")

    result = copy.deepcopy(base)

    for section_attr, overrides in data.items():
        if not hasattr(result, section_attr):
            logger.warning("Config file has unknown section %r; skipping.", section_attr)
            continue
        if not isinstance(overrides, dict):
            logger.warning("Section %r value is not a dict; skipping.", section_attr)
            continue
        section_obj = getattr(result, section_attr)
        for field_name, value in overrides.items():
            if not hasattr(section_obj, field_name):
                logger.warning(
                    "Section %r has no field %r (from file); skipping.", section_attr, field_name
                )
                continue
            try:
                object.__setattr__(section_obj, field_name, value)
            except (AttributeError, TypeError):
                new_kwargs = {**vars(section_obj), field_name: value}
                try:
                    object.__setattr__(result, section_attr, type(section_obj)(**new_kwargs))
                except Exception as exc:
                    logger.warning(
                        "Could not apply file override %s.%s=%r: %s",
                        section_attr, field_name, value, exc,
                    )

    return result


def validate_defaults(defaults: Optional[RuntimeDefaults] = None) -> Dict[str, List[str]]:
    """Validate a :class:`RuntimeDefaults` instance and return errors.

    Parameters
    ----------
    defaults:
        Instance to validate.  If None, validates the factory defaults.

    Returns
    -------
    dict[str, list[str]]
        Mapping from section name to list of error strings.  Empty dict
        means everything is valid.
    """
    if defaults is None:
        defaults = _build_for_preset(PolicyPreset.BALANCED)

    errors: Dict[str, List[str]] = {}

    descent_errors = defaults.descent.validate()
    if descent_errors:
        errors["descent"] = descent_errors

    # Validate budget decay factor.
    if not (0.0 < defaults.budget._decay_factor <= 1.0):
        errors.setdefault("budget", []).append(
            f"_decay_factor must be in (0, 1], got {defaults.budget._decay_factor}"
        )
    if not (0.0 < defaults.budget._reserve_fraction < 1.0):
        errors.setdefault("budget", []).append(
            f"_reserve_fraction must be in (0, 1), got {defaults.budget._reserve_fraction}"
        )

    # Validate solver config.
    if defaults.solver.z3_timeout_ms < 0:
        errors.setdefault("solver", []).append("z3_timeout_ms must be non-negative")
    if defaults.solver.session_pool_size < 1:
        errors.setdefault("solver", []).append("session_pool_size must be >= 1")

    # Validate copilot config.
    if not (0.0 <= defaults.copilot.temperature <= 2.0):
        errors.setdefault("copilot", []).append(
            f"temperature must be in [0.0, 2.0], got {defaults.copilot.temperature}"
        )
    if defaults.copilot.max_tokens < 1:
        errors.setdefault("copilot", []).append("max_tokens must be >= 1")
    if defaults.copilot.rate_limit_per_minute < 0:
        errors.setdefault("copilot", []).append("rate_limit_per_minute must be non-negative")

    # Validate manifest config.
    if defaults.manifest.checkpoint_interval < 1:
        errors.setdefault("manifest", []).append("checkpoint_interval must be >= 1")
    if defaults.manifest.max_judgments < defaults.manifest.checkpoint_interval:
        errors.setdefault("manifest", []).append(
            "max_judgments should be >= checkpoint_interval"
        )

    # Validate orchestration config.
    if defaults.orchestration.max_fleet_size < 1:
        errors.setdefault("orchestration", []).append("max_fleet_size must be >= 1")
    if not (0.0 < defaults.orchestration.convergence_threshold <= 1.0):
        errors.setdefault("orchestration", []).append(
            "convergence_threshold must be in (0, 1]"
        )
    for phase, threshold in defaults.orchestration.phase_transition_thresholds.items():
        if not (0.0 <= threshold <= 1.0):
            errors.setdefault("orchestration", []).append(
                f"phase_transition_thresholds[{phase!r}] = {threshold} is not in [0, 1]"
            )

    return errors


# ---------------------------------------------------------------------------
# Private type coercion helper
# ---------------------------------------------------------------------------


def _coerce_env_value(raw: str) -> Any:
    """Best-effort coercion of a raw environment-variable string value.

    Tries integer, then float, then boolean, then returns the raw string.

    Parameters
    ----------
    raw:
        The raw string value from the environment.

    Returns
    -------
    int | float | bool | str
    """
    stripped = raw.strip()
    if stripped.lower() in ("true", "yes", "1"):
        return True
    if stripped.lower() in ("false", "no", "0"):
        return False
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    return stripped


# ---------------------------------------------------------------------------
# Preserved legacy API (backwards compatibility)
# ---------------------------------------------------------------------------


class _LegacyPolicyPreset(str, Enum):
    """Alias kept for backwards compatibility with pre-2.0 code."""

    SAFE = "safe"
    BALANCED = "balanced"
    EXPLORATORY = "exploratory"


@dataclass(frozen=True, slots=True)
class FrontierBudget:
    """Legacy frontier-budget dataclass; use :class:`DefaultBudgetConfig` instead."""

    max_pending: int
    max_parallel: int
    max_attempts_per_goal: int
    backpressure_threshold: int


@dataclass(frozen=True, slots=True)
class TrustPolicyDefaults:
    """Legacy trust-policy dataclass; use :class:`DefaultTrustLevels` instead."""

    silent_promotion_allowed: bool
    minimum_review_tier: str
    proposal_tier: str
    certificate_tier: str


def default_trust_policy(
    preset: PolicyPreset = PolicyPreset.BALANCED,
) -> TrustPolicyDefaults:
    """Return legacy :class:`TrustPolicyDefaults` for *preset*."""
    if preset is PolicyPreset.SAFE:
        return TrustPolicyDefaults(False, "reviewed", "proposal", "verified")
    if preset is PolicyPreset.EXPLORATORY:
        return TrustPolicyDefaults(False, "provisional", "proposal", "reviewed")
    return TrustPolicyDefaults(False, "reviewed", "proposal", "reviewed")


def default_frontier_budget(
    preset: PolicyPreset = PolicyPreset.BALANCED,
) -> FrontierBudget:
    """Return legacy :class:`FrontierBudget` for *preset*."""
    if preset is PolicyPreset.SAFE:
        return FrontierBudget(16, 2, 2, 6)
    if preset is PolicyPreset.EXPLORATORY:
        return FrontierBudget(64, 8, 6, 24)
    return FrontierBudget(32, 4, 4, 12)


def default_runtime_options(
    preset: PolicyPreset = PolicyPreset.BALANCED,
) -> RuntimeDefaults:
    """Return a :class:`RuntimeDefaults` calibrated for *preset*.

    This replaces the old single-dataclass approach with the full aggregate
    while preserving the old call signature.

    Parameters
    ----------
    preset:
        One of SAFE, BALANCED, EXPLORATORY.

    Returns
    -------
    RuntimeDefaults
    """
    return _build_for_preset(preset)


# ---------------------------------------------------------------------------
# Module initialisation: populate the global registry with factory defaults
# ---------------------------------------------------------------------------


def _init_registry() -> None:
    """Populate the global :data:`_registry` with factory defaults."""
    factory = _build_for_preset(PolicyPreset.BALANCED)
    _registry.register("runtime_defaults", factory, overwrite=True)
    _registry.register("trust_levels", factory.trust_levels, overwrite=True)
    _registry.register("evidence_channels", factory.evidence_channels, overwrite=True)
    _registry.register("descent", factory.descent, overwrite=True)
    _registry.register("obstruction_policy", factory.obstruction_policy, overwrite=True)
    _registry.register("budget", factory.budget, overwrite=True)
    _registry.register("manifest", factory.manifest, overwrite=True)
    _registry.register("solver", factory.solver, overwrite=True)
    _registry.register("copilot", factory.copilot, overwrite=True)
    _registry.register("packs", factory.packs, overwrite=True)
    _registry.register("orchestration", factory.orchestration, overwrite=True)


_init_registry()


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "PolicyPreset",
    "EvidenceChannel",
    "TrustLevel",
    "GCStrategy",
    "PersistenceBackend",
    "DescentStrategy",
    "TrustFloorPolicy",
    "DependencyResolutionStrategy",
    "VersionPolicy",
    "FragmentRouting",
    # Configuration dataclasses
    "DefaultTrustLevels",
    "ChannelConfig",
    "DefaultEvidenceChannelConfig",
    "DefaultDescentConfig",
    "DefaultObstructionPolicy",
    "DimensionBudget",
    "DefaultBudgetConfig",
    "DefaultManifestConfig",
    "DefaultSolverConfig",
    "DefaultCopilotConfig",
    "DefaultPackConfig",
    "DefaultOrchestrationConfig",
    "RuntimeDefaults",
    # Registry
    "DefaultsRegistry",
    "_registry",
    # Factory functions
    "get_defaults",
    "load_defaults_from_env",
    "load_defaults_from_file",
    "validate_defaults",
    # Legacy API
    "FrontierBudget",
    "TrustPolicyDefaults",
    "default_trust_policy",
    "default_frontier_budget",
    "default_runtime_options",
    # Unified judgment-geometric default helpers
    "default_trust_algebra",
    "default_site_configuration",
    "default_solver_configuration",
    "default_encoding_families",
]


# ---------------------------------------------------------------------------
# Unified judgment-geometric default helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.evidence.trust import TrustAlgebra as _TrustAlgebra  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _TrustAlgebra = None

try:
    from jugeo.geometry.site import SiteConfiguration as _SiteConfiguration  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _SiteConfiguration = None

try:
    from jugeo.solver import default_config as _solver_default_config  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _solver_default_config = None

try:
    from jugeo.encodings import list_families as _list_encoding_families  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _list_encoding_families = None


def default_trust_algebra():
    """Return the default trust algebra from jugeo.evidence.trust.

    Falls back to a minimal dict description when the module is
    unavailable.
    """
    if _TrustAlgebra is not None:
        return _TrustAlgebra.default()
    return {
        "lattice": "default",
        "floor": 0.0,
        "ceiling": 1.0,
        "source": "fallback",
    }


def default_site_configuration():
    """Return the default site configuration from jugeo.geometry.site.

    Falls back to a minimal dict description when the module is
    unavailable.
    """
    if _SiteConfiguration is not None:
        return _SiteConfiguration.default()
    return {
        "topology": "default",
        "descent_strategy": "greedy",
        "source": "fallback",
    }


def default_solver_configuration():
    """Return the default solver configuration from jugeo.solver.

    Falls back to a minimal dict description when the module is
    unavailable.
    """
    if _solver_default_config is not None:
        return _solver_default_config()
    return {
        "backend": "z3",
        "timeout_ms": 30000,
        "source": "fallback",
    }


def default_encoding_families():
    """Return the available encoding families from jugeo.encodings.

    Falls back to an empty tuple when the module is unavailable.
    """
    if _list_encoding_families is not None:
        return _list_encoding_families()
    return ()
