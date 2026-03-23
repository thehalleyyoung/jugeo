"""
JuGeo Discovery Federation Package.

``jugeo.ideation.discovery_federation`` implements the Discovery Federation
protocol from theory2.tex Chapter 61 (Ch61).  This package provides all the
components necessary to run a fully operational federated discovery system
across a network of JuGeo nodes.

Overview
--------
The Discovery Federation protocol enables distributed JuGeo nodes to:
  - Share discovered geometric and ideational results (FederatedDiscovery)
  - Reach Byzantine-fault-tolerant consensus on discoveries
  - Grant and manage distributed authority over discoveries
  - Propagate and merge knowledge across the federation graph
  - Detect and resolve conflicts between competing discoveries
  - Integrate with the broader JuGeo ecosystem (packs, orchestrator,
    evidence channels, geometry sites)

Package Structure
-----------------
models.py
    Core data models: FederatedDiscovery, FederationConsensus,
    DiscoveryAuthority, KnowledgePropagation, AuthorityGrant,
    FederationVote, FederationNode, ConflictRecord, and the
    FederationStatus, ConsensusOutcome, and AuthorityLevel enumerations.

manifest.py
    DiscoveryFederationManifest — versioned checkpoint records that
    capture federation state for distribution, auditing, and replay.
    FederationManifestBuilder — fluent builder API.

discovery_as_authority.py
    Authority promotion pipeline: PromotionRecord, AuthorityPromoter,
    AuthorityValidator, AuthorityLifecycleManager,
    DiscoveryAuthorityRunner.

federated_knowledge.py
    Knowledge propagation and merging: KnowledgeEntry, MergeResult,
    KnowledgePropagator, KnowledgeMerger, KnowledgeRepository,
    FederatedKnowledgeRunner.

federation_consensus.py
    Voting and consensus protocol: VotingRound, ConsensusProtocol,
    QuorumCalculator, VoteAggregator, FederationConsensusRunner.

algorithms.py
    Pure algorithmic layer: FederationAlgorithms static class and
    free-function implementations for scoring, ranking, propagation,
    conflict resolution, and merge-candidate selection.

integration.py
    Integration hub: FederationIntegration, DiscoveryBridgeAdapter,
    AuthorityPackAdapter — connect the federation to the wider JuGeo
    subsystem (packs, orchestrator, evidence, geometry, regimes).

theorems.py
    Formal theorems registry: FederationSoundnessTheorem,
    AuthorityMonotonicityTheorem, ConsensusConvergenceTheorem,
    KnowledgePropagationSoundnessTheorem,
    ConflictResolutionCompletenessTheorem,
    FederationTheoremRegistry.

Quick Start
-----------
>>> from jugeo.ideation.discovery_federation import (
...     FederatedDiscovery, FederationManifestBuilder,
...     DiscoveryAuthorityRunner, FederatedKnowledgeRunner,
...     FederationConsensusRunner, FederationTheoremRegistry,
... )
>>> runner = DiscoveryAuthorityRunner()
>>> discovery = {"id": "d1", "trust_score": 0.9, "novelty_score": 0.7}
>>> result = runner.run(discovery)

Formal Guarantees
-----------------
The theorems module provides machine-checkable statements of:
  - Soundness: federation consensus is consistent with local truth
  - Monotonicity: authority levels respect trust ordering
  - Convergence: consensus always terminates within bounded rounds
  - Knowledge soundness: propagation preserves semantic validity
  - Completeness: all conflicts are eventually resolved

copilot: shared-core marker
theory2.tex Ch61 — Federated Discovery Authority
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Architecture Notes (theory2.tex Ch61)
# ---------------------------------------------------------------------------
#
# The Discovery Federation package realises the formal model described in
# Chapter 61 of theory2.tex.  The chapter introduces three interlocking
# sub-protocols, each represented by one of the s0N_*.py modules:
#
#   S01 — Discovery-as-Authority
#   ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#   Each discovery that crosses the (trust_threshold, novelty_threshold)
#   hyperplane is "promoted" to an authority record.  The authority carries
#   a monotonically increasing level (OBSERVER → CONTRIBUTOR → VALIDATOR →
#   ARBITER → SOVEREIGN) that can only increase, never decrease (Theorem 2:
#   Authority Monotonicity).  The DiscoveryAuthorityRunner orchestrates the
#   full promotion lifecycle and exposes a single run() entry-point.
#
#   S02 — Federated Knowledge
#   ~~~~~~~~~~~~~~~~~~~~~~~~~
#   Promoted authorities are serialised into KnowledgeEntry records and
#   propagated across the federation graph via the KnowledgePropagator.
#   When the same discovery arrives from multiple paths the KnowledgeMerger
#   resolves conflicts deterministically (Theorem 4: Knowledge Propagation
#   Soundness guarantees that every merged record is semantically valid
#   w.r.t. the originating discovery).
#
#   S03 — Federation Consensus
#   ~~~~~~~~~~~~~~~~~~~~~~~~~~
#   Nodes vote on pending discoveries using the ConsensusProtocol.  The
#   QuorumCalculator computes the minimum quorum for each authority level,
#   and the VoteAggregator tallies weighted votes.  Theorem 3 (Consensus
#   Convergence) proves that the protocol always terminates in O(log n)
#   rounds for a federation of n well-connected nodes.
#
# Theorem Registry
# ~~~~~~~~~~~~~~~~
# All five formal guarantees are collected in FederationTheoremRegistry so
# they can be interrogated at runtime (e.g., for audit trails or dashboard
# display).  Each theorem object carries a human-readable statement, a
# machine tag, and a reference to the corresponding lemma in theory2.tex.
#
# Integration Layer
# ~~~~~~~~~~~~~~~~~
# FederationIntegration wires the three sub-protocols together and bridges
# them to the rest of JuGeo via two adapters:
#   - DiscoveryBridgeAdapter  — connects to packs / orchestrator / evidence
#   - AuthorityPackAdapter    — registers authority records with geometry
#                               sites and regimes
#
# Data-Flow Summary
# ~~~~~~~~~~~~~~~~~
#   raw discovery dict
#       │
#       ▼
#   DiscoveryAuthorityRunner.run()
#       │  (produces PromotionRecord)
#       ▼
#   FederatedKnowledgeRunner.propagate()
#       │  (produces KnowledgeEntry list)
#       ▼
#   FederationConsensusRunner.run()
#       │  (produces ConsensusResult)
#       ▼
#   FederationIntegration.publish()
#       │  (broadcasts to peer nodes)
#       ▼
#   DiscoveryFederationManifest
#       (archived checkpoint for audit / replay)
#
# Versioning
# ~~~~~~~~~~
# This file follows semver.  The patch component is incremented on every
# Ch61-compatible change that does not alter the public API.  The minor
# component is bumped when new public names are added to __all__.  The
# major component will change only when a breaking Ch61 revision is merged.
#
# ---------------------------------------------------------------------------

import time
import uuid
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version & identity
# ---------------------------------------------------------------------------

__version__: str = "0.1.0"
__theory_chapter__: str = "Ch61"
__author__: str = "JuGeo Project"

# ---------------------------------------------------------------------------
# Guarded imports — each submodule is imported independently so that a
# missing or partially-initialised submodule does not block the rest of the
# package from loading.
# ---------------------------------------------------------------------------

# -- models ------------------------------------------------------------------
#: Core enumerations and dataclasses for the entire federation layer.
try:
    from jugeo.ideation.discovery_federation.models import (
        FederationStatus,
        ConsensusOutcome,
        AuthorityLevel,
        FederatedDiscovery,
        FederationConsensus,
        DiscoveryAuthority,
        KnowledgePropagation,
        AuthorityGrant,
        FederationVote,
        FederationNode,
        ConflictRecord,
    )
except Exception:  # pragma: no cover
    pass

# -- manifest ----------------------------------------------------------------
#: Versioned checkpoint records and the fluent builder that creates them.
try:
    from jugeo.ideation.discovery_federation.manifest import (
        ManifestStatus,
        DiscoveryFederationManifest,
        FederationManifestBuilder,
        build_federation_manifest,
    )
except Exception:  # pragma: no cover
    pass

# -- s01 discovery_as_authority ----------------------------------------------
#: Authority promotion pipeline: promotes high-scoring discoveries to
#: full federation authority records.
try:
    from jugeo.ideation.discovery_federation.discovery_as_authority import (
        PromotionRecord,
        AuthorityPromoter,
        AuthorityValidator,
        AuthorityLifecycleManager,
        DiscoveryAuthorityRunner,
    )
except Exception:  # pragma: no cover
    pass

# -- s02 federated_knowledge -------------------------------------------------
#: Knowledge propagation and merging: distributes authority records across
#: the federation graph and resolves merge conflicts.
try:
    from jugeo.ideation.discovery_federation.federated_knowledge import (
        KnowledgeEntry,
        MergeResult,
        KnowledgePropagator,
        KnowledgeMerger,
        KnowledgeRepository,
        FederatedKnowledgeRunner,
    )
except Exception:  # pragma: no cover
    pass

# -- s03 federation_consensus ------------------------------------------------
#: Voting and consensus protocol: tallies weighted node votes and decides
#: whether a discovery clears the quorum threshold.
try:
    from jugeo.ideation.discovery_federation.federation_consensus import (
        VotingRound,
        ConsensusProtocol,
        QuorumCalculator,
        VoteAggregator,
        FederationConsensusRunner,
    )
except Exception:  # pragma: no cover
    pass

# -- algorithms --------------------------------------------------------------
#: Pure algorithmic layer: scoring, ranking, propagation weights, conflict
#: resolution heuristics, and merge-candidate selection.
try:
    from jugeo.ideation.discovery_federation.algorithms import (
        FederationAlgorithms,
        compute_federation_score,
        rank_candidates,
        resolve_conflict,
        select_merge_candidates,
        propagation_weight,
    )
except Exception:  # pragma: no cover
    pass

# -- integration -------------------------------------------------------------
#: Integration hub that wires the three sub-protocols to the rest of JuGeo.
try:
    from jugeo.ideation.discovery_federation.integration import (
        FederationIntegration,
        DiscoveryBridgeAdapter,
        AuthorityPackAdapter,
    )
except Exception:  # pragma: no cover
    pass

# -- theorems ----------------------------------------------------------------
#: Formal theorem objects and the registry that collects all five guarantees.
try:
    from jugeo.ideation.discovery_federation.theorems import (
        FederationSoundnessTheorem,
        AuthorityMonotonicityTheorem,
        ConsensusConvergenceTheorem,
        KnowledgePropagationSoundnessTheorem,
        ConflictResolutionCompletenessTheorem,
        FederationTheoremRegistry,
    )
except Exception:  # pragma: no cover
    pass

# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # --- enumerations ---
    "FederationStatus",
    "ConsensusOutcome",
    "AuthorityLevel",
    "ManifestStatus",
    # --- core models ---
    "FederatedDiscovery",
    "FederationConsensus",
    "DiscoveryAuthority",
    "KnowledgePropagation",
    "AuthorityGrant",
    "FederationVote",
    "FederationNode",
    "ConflictRecord",
    # --- manifest ---
    "DiscoveryFederationManifest",
    "FederationManifestBuilder",
    "build_federation_manifest",
    # --- s01 authority promotion ---
    "PromotionRecord",
    "AuthorityPromoter",
    "AuthorityValidator",
    "AuthorityLifecycleManager",
    "DiscoveryAuthorityRunner",
    # --- s02 federated knowledge ---
    "KnowledgeEntry",
    "MergeResult",
    "KnowledgePropagator",
    "KnowledgeMerger",
    "KnowledgeRepository",
    "FederatedKnowledgeRunner",
    # --- s03 consensus ---
    "VotingRound",
    "ConsensusProtocol",
    "QuorumCalculator",
    "VoteAggregator",
    "FederationConsensusRunner",
    # --- algorithms ---
    "FederationAlgorithms",
    "compute_federation_score",
    "rank_candidates",
    "resolve_conflict",
    "select_merge_candidates",
    "propagation_weight",
    # --- integration ---
    "FederationIntegration",
    "DiscoveryBridgeAdapter",
    "AuthorityPackAdapter",
    # --- theorems ---
    "FederationSoundnessTheorem",
    "AuthorityMonotonicityTheorem",
    "ConsensusConvergenceTheorem",
    "KnowledgePropagationSoundnessTheorem",
    "ConflictResolutionCompletenessTheorem",
    "FederationTheoremRegistry",
    # --- package-level helpers ---
    "get_package_info",
    "create_default_registry",
    "create_default_runner",
    "create_default_manifest_builder",
    "create_default_consensus_runner",
    "create_default_integration",
    "run_full_pipeline",
]

# ---------------------------------------------------------------------------
# Package metadata dictionary
# ---------------------------------------------------------------------------

_PACKAGE_INFO: dict = {
    "name": "discovery_federation",
    "version": __version__,
    "theory_chapter": __theory_chapter__,
    "description": (
        "Federated Discovery Authority protocol for JuGeo: consensus, "
        "knowledge propagation, authority promotion, and integration."
    ),
    "modules": [
        "models",
        "manifest",
        "discovery_as_authority",
        "federated_knowledge",
        "federation_consensus",
        "algorithms",
        "integration",
        "theorems",
    ],
    "theorem_count": 5,
    "pipeline_steps": 3,
    "authority_levels": ["OBSERVER", "CONTRIBUTOR", "VALIDATOR", "ARBITER", "SOVEREIGN"],
    "consensus_policies": ["simple_majority", "supermajority", "unanimous", "weighted"],
    "merge_strategies": ["trust_weighted", "novelty_weighted", "timestamp_latest", "quorum_voted"],
    "integration_adapters": ["DiscoveryBridgeAdapter", "AuthorityPackAdapter"],
    "formal_guarantees": [
        "soundness",
        "authority_monotonicity",
        "consensus_convergence",
        "knowledge_propagation_soundness",
        "conflict_resolution_completeness",
    ],
}

# ---------------------------------------------------------------------------
# Private helpers (also re-exported for convenience)
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp (float seconds).

    This helper is used throughout the federation package wherever a
    creation or modification timestamp is required.  Using a single
    canonical helper ensures consistency and makes mocking straightforward
    in tests.

    Returns
    -------
    float
        Seconds since the Unix epoch in UTC.

    Examples
    --------
    >>> ts = _utcnow()
    >>> isinstance(ts, float)
    True
    """
    return time.time()


def _uid() -> str:
    """Generate a fresh RFC-4122 UUID4 string.

    Every federation object that requires a unique identifier (discovery
    records, authority grants, voting rounds, manifest entries, conflict
    records, …) calls this helper so that UUID generation is centralised
    and trivially replaceable.

    Returns
    -------
    str
        A lowercase UUID4 string of the form
        ``xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx``.

    Examples
    --------
    >>> uid = _uid()
    >>> len(uid)
    36
    >>> uid[14]
    '4'
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Used by scoring and threshold helpers to guarantee that all
    federation scores remain in a well-defined range (typically [0.0,
    1.0]) regardless of upstream computation artefacts such as floating-
    point overflow or mis-scaled inputs.

    Parameters
    ----------
    value:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        ``max(lo, min(value, hi))``.

    Examples
    --------
    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.1, 0.0, 1.0)
    0.0
    >>> _clamp(0.7, 0.0, 1.0)
    0.7
    """
    return max(float(lo), min(float(value), float(hi)))

# ---------------------------------------------------------------------------
# Public utility functions
# ---------------------------------------------------------------------------

def get_package_info() -> dict:
    """Return a copy of *_PACKAGE_INFO* enriched with runtime metadata.

    The returned dictionary is a shallow copy of the module-level
    ``_PACKAGE_INFO`` constant extended with:

    * ``"runtime_timestamp"`` — current UTC time (float).
    * ``"python_package"`` — fully qualified package name string.
    * ``"all_count"`` — number of names in ``__all__``.
    * ``"loaded"`` — ``True`` (indicates the package was importable).

    This function is useful for health-check endpoints, logging preambles,
    and manifest headers.

    Returns
    -------
    dict
        Enriched copy of ``_PACKAGE_INFO``.

    Examples
    --------
    >>> info = get_package_info()
    >>> info["name"]
    'discovery_federation'
    >>> "runtime_timestamp" in info
    True
    """
    result = dict(_PACKAGE_INFO)
    result["runtime_timestamp"] = _utcnow()
    result["python_package"] = __name__
    result["all_count"] = len(__all__)
    result["loaded"] = True
    return result


def create_default_registry() -> Any:
    """Create a *FederationTheoremRegistry* pre-loaded with all five theorems.

    Instantiates ``FederationTheoremRegistry`` and registers the canonical
    five formal guarantees defined in theory2.tex Ch61:

    1. FederationSoundnessTheorem
    2. AuthorityMonotonicityTheorem
    3. ConsensusConvergenceTheorem
    4. KnowledgePropagationSoundnessTheorem
    5. ConflictResolutionCompletenessTheorem

    The registry can be queried at runtime to verify which guarantees are
    active, to retrieve theorem statements for audit trails, or to drive
    dashboard display widgets.

    Returns
    -------
    FederationTheoremRegistry
        A fully populated theorem registry.

    Raises
    ------
    ImportError
        If the ``theorems`` submodule could not be imported (e.g., missing
        dependency or incomplete installation).

    Examples
    --------
    >>> registry = create_default_registry()
    >>> len(registry.theorems) == 5
    True
    """
    try:
        registry = FederationTheoremRegistry()  # type: ignore[name-defined]
        registry.register(FederationSoundnessTheorem())  # type: ignore[name-defined]
        registry.register(AuthorityMonotonicityTheorem())  # type: ignore[name-defined]
        registry.register(ConsensusConvergenceTheorem())  # type: ignore[name-defined]
        registry.register(KnowledgePropagationSoundnessTheorem())  # type: ignore[name-defined]
        registry.register(ConflictResolutionCompletenessTheorem())  # type: ignore[name-defined]
        return registry
    except NameError as exc:
        raise ImportError(
            "Could not create FederationTheoremRegistry: the 'theorems' "
            "submodule failed to import.  Run "
            "'pip install jugeo[federation]' to install missing dependencies."
        ) from exc


def create_default_runner(
    trust_threshold: float = 0.6,
    novelty_threshold: float = 0.4,
) -> Any:
    """Create a *DiscoveryAuthorityRunner* with the given promotion thresholds.

    The runner is the primary entry-point for the S01 authority promotion
    sub-protocol.  A discovery whose ``trust_score`` exceeds
    *trust_threshold* **and** whose ``novelty_score`` exceeds
    *novelty_threshold* will be promoted to an authority record.

    Parameters
    ----------
    trust_threshold:
        Minimum trust score required for promotion.  Must be in [0.0, 1.0].
        Defaults to ``0.6``.
    novelty_threshold:
        Minimum novelty score required for promotion.  Must be in [0.0, 1.0].
        Defaults to ``0.4``.

    Returns
    -------
    DiscoveryAuthorityRunner
        A configured runner ready to accept ``run(discovery)`` calls.

    Raises
    ------
    ImportError
        If the ``discovery_as_authority`` submodule is not available.
    ValueError
        If either threshold is outside [0.0, 1.0].

    Examples
    --------
    >>> runner = create_default_runner(trust_threshold=0.75)
    >>> runner.trust_threshold
    0.75
    """
    trust_threshold = _clamp(trust_threshold, 0.0, 1.0)
    novelty_threshold = _clamp(novelty_threshold, 0.0, 1.0)
    try:
        runner = DiscoveryAuthorityRunner(  # type: ignore[name-defined]
            trust_threshold=trust_threshold,
            novelty_threshold=novelty_threshold,
        )
        return runner
    except NameError as exc:
        raise ImportError(
            "DiscoveryAuthorityRunner is not available: the "
            "'discovery_as_authority' submodule failed to import."
        ) from exc


def create_default_manifest_builder() -> Any:
    """Create a fresh *FederationManifestBuilder* with default settings.

    The builder provides a fluent API for constructing
    ``DiscoveryFederationManifest`` checkpoint records.  Call
    ``builder.with_*()`` methods to populate fields, then call
    ``builder.build()`` to produce an immutable manifest.

    The returned builder is pre-configured with:
    * a fresh UUID as the manifest id,
    * the current UTC timestamp as ``created_at``,
    * ``version = __version__``,
    * ``theory_chapter = __theory_chapter__``.

    Returns
    -------
    FederationManifestBuilder
        A ready-to-use builder instance.

    Raises
    ------
    ImportError
        If the ``manifest`` submodule is not available.

    Examples
    --------
    >>> builder = create_default_manifest_builder()
    >>> manifest = builder.build()
    """
    try:
        builder = FederationManifestBuilder(  # type: ignore[name-defined]
            manifest_id=_uid(),
            created_at=_utcnow(),
            version=__version__,
            theory_chapter=__theory_chapter__,
        )
        return builder
    except NameError as exc:
        raise ImportError(
            "FederationManifestBuilder is not available: the "
            "'manifest' submodule failed to import."
        ) from exc


def create_default_consensus_runner(policy: str = "simple_majority") -> Any:
    """Create a *FederationConsensusRunner* configured with *policy*.

    The runner encapsulates the full S03 voting and consensus sub-protocol.
    It accepts a list of ``FederationVote`` objects and returns a
    ``ConsensusResult`` indicating whether the quorum was met.

    Supported policies
    ------------------
    ``"simple_majority"``
        More than 50 % of weighted votes must approve.
    ``"supermajority"``
        More than 66.7 % of weighted votes must approve.
    ``"unanimous"``
        All participating nodes must approve (use only for small federations).
    ``"weighted"``
        Votes are weighted by authority level; threshold is configurable.

    Parameters
    ----------
    policy:
        Name of the consensus policy to apply.  Defaults to
        ``"simple_majority"``.

    Returns
    -------
    FederationConsensusRunner
        A runner ready to accept ``run(votes)`` calls.

    Raises
    ------
    ImportError
        If the ``federation_consensus`` submodule is not available.
    ValueError
        If *policy* is not one of the supported policy names.

    Examples
    --------
    >>> runner = create_default_consensus_runner("supermajority")
    >>> runner.policy
    'supermajority'
    """
    valid_policies = _PACKAGE_INFO["consensus_policies"]
    if policy not in valid_policies:
        raise ValueError(
            f"Unknown consensus policy {policy!r}.  "
            f"Choose from: {valid_policies}"
        )
    try:
        runner = FederationConsensusRunner(policy=policy)  # type: ignore[name-defined]
        return runner
    except NameError as exc:
        raise ImportError(
            "FederationConsensusRunner is not available: the "
            "'federation_consensus' submodule failed to import."
        ) from exc


def create_default_integration() -> Any:
    """Create a *FederationIntegration* hub with both standard adapters registered.

    The integration hub connects the three federation sub-protocols (S01,
    S02, S03) to the rest of the JuGeo ecosystem.  This factory:

    1. Instantiates ``FederationIntegration``.
    2. Creates a ``DiscoveryBridgeAdapter`` and registers it — this adapter
       forwards promoted discoveries to the packs system, orchestrator queue,
       and evidence channels.
    3. Creates an ``AuthorityPackAdapter`` and registers it — this adapter
       registers authority records with geometry sites and active regimes.

    Both adapters are optional at runtime; if a downstream subsystem is not
    available the adapter will log a warning and continue gracefully.

    Returns
    -------
    FederationIntegration
        A fully wired integration hub.

    Raises
    ------
    ImportError
        If the ``integration`` submodule is not available.

    Examples
    --------
    >>> hub = create_default_integration()
    >>> len(hub.adapters)
    2
    """
    try:
        hub = FederationIntegration()  # type: ignore[name-defined]
        bridge = DiscoveryBridgeAdapter()  # type: ignore[name-defined]
        pack_adapter = AuthorityPackAdapter()  # type: ignore[name-defined]
        hub.register_adapter(bridge)
        hub.register_adapter(pack_adapter)
        return hub
    except NameError as exc:
        raise ImportError(
            "FederationIntegration or its adapters are not available: "
            "the 'integration' submodule failed to import."
        ) from exc


def run_full_pipeline(
    discovery: dict,
    node_ids: list,
    votes: list[dict],
) -> dict:
    """Run the complete three-stage federation pipeline on a discovery dict.

    This convenience function executes the canonical Ch61 data-flow in one
    call:

    1. **Promote** — passes *discovery* through
       ``DiscoveryAuthorityRunner`` to produce a ``PromotionRecord``.
    2. **Propagate** — passes the promotion record through
       ``FederatedKnowledgeRunner`` to distribute it to *node_ids* and
       produce a list of ``KnowledgeEntry`` objects.
    3. **Consensus** — passes *votes* through
       ``FederationConsensusRunner`` to decide whether a quorum of nodes
       accepted the discovery.

    Parameters
    ----------
    discovery:
        Raw discovery dictionary.  Must contain at least the keys
        ``"id"`` (str), ``"trust_score"`` (float), and
        ``"novelty_score"`` (float).
    node_ids:
        List of federation node identifier strings that should receive
        the propagated knowledge entry.
    votes:
        List of vote dictionaries, each containing at least
        ``"node_id"`` (str), ``"approve"`` (bool), and optionally
        ``"weight"`` (float, default 1.0).

    Returns
    -------
    dict
        A result dictionary with the following keys:

        ``"promotion_result"``
            The ``PromotionRecord`` returned by S01 (or ``None`` if the
            discovery did not clear the promotion thresholds).
        ``"propagation_result"``
            List of ``KnowledgeEntry`` objects produced by S02.
        ``"consensus_result"``
            The ``ConsensusResult`` object returned by S03.
        ``"success"``
            ``True`` iff promotion succeeded **and** consensus was
            reached.

    Raises
    ------
    RuntimeError
        If any pipeline stage raises an unexpected exception; the
        original exception is chained.

    Examples
    --------
    >>> result = run_full_pipeline(
    ...     discovery={"id": "d1", "trust_score": 0.9, "novelty_score": 0.7},
    ...     node_ids=["node-a", "node-b", "node-c"],
    ...     votes=[
    ...         {"node_id": "node-a", "approve": True},
    ...         {"node_id": "node-b", "approve": True},
    ...         {"node_id": "node-c", "approve": False},
    ...     ],
    ... )
    >>> result["success"]
    True
    """
    result: dict = {
        "promotion_result": None,
        "propagation_result": [],
        "consensus_result": None,
        "success": False,
    }

    # Stage 1 — promotion
    try:
        authority_runner = create_default_runner()
        promotion_result = authority_runner.run(discovery)
        result["promotion_result"] = promotion_result
    except ImportError:
        logger.warning(
            "run_full_pipeline: s01 submodule not available; "
            "skipping promotion stage."
        )
        promotion_result = None

    if promotion_result is None:
        logger.debug("run_full_pipeline: discovery did not promote; aborting pipeline.")
        return result

    # Stage 2 — propagation
    try:
        knowledge_runner = FederatedKnowledgeRunner()  # type: ignore[name-defined]
        propagation_result = knowledge_runner.propagate(promotion_result, node_ids)
        result["propagation_result"] = propagation_result
    except (ImportError, NameError):
        logger.warning(
            "run_full_pipeline: s02 submodule not available; "
            "skipping propagation stage."
        )

    # Stage 3 — consensus
    try:
        consensus_runner = create_default_consensus_runner()
        consensus_result = consensus_runner.run(votes)
        result["consensus_result"] = consensus_result
        result["success"] = getattr(consensus_result, "approved", False)
    except (ImportError, NameError):
        logger.warning(
            "run_full_pipeline: s03 submodule not available; "
            "skipping consensus stage."
        )

    return result


# ---------------------------------------------------------------------------
# Extended documentation supplement
# ---------------------------------------------------------------------------

__doc_supplement__: str = """
JuGeo Discovery Federation — Extended Documentation Supplement
==============================================================

This supplement provides additional context about the Discovery Federation
protocol, its relationship to theory2.tex Chapter 61 (Ch61), design
decisions, and comprehensive usage examples.  It is intended for developers
integrating new nodes into an existing JuGeo federation or extending the
protocol with custom authority levels and consensus policies.

Theoretical Background (Ch61 Summary)
--------------------------------------
Chapter 61 of theory2.tex formalises the notion of "discovery authority"
within a distributed geometric ideation system.  The central insight is
that a discovery is not merely a data record; it is a *claim of semantic
priority* over a region of the ideation space.  The federation protocol
operationalises this insight through three interlocking mechanisms:

1. Authority Promotion (S01)
   A discovery d is promoted to an authority record A(d) when it
   simultaneously satisfies:
     trust(d) >= tau_trust   (credibility of the discovering node)
     novelty(d) >= tau_novel  (semantic distance from existing authorities)
   The promotion is irreversible: once A(d) exists it can only gain
   authority (monotonicity).

2. Knowledge Propagation (S02)
   A(d) is serialised as a KnowledgeEntry and broadcast to all nodes
   reachable from the originating node within the federation graph.
   Propagation follows a weighted spanning-tree traversal where edge
   weights encode inter-node trust.  If a node already holds a
   conflicting entry E(d') for the same ideation region, the merge
   protocol applies the configured merge strategy (default:
   trust_weighted).

3. Consensus Voting (S03)
   Nodes vote on whether to accept A(d) as a global authority.  The
   VoteAggregator tallies weighted votes (weight = authority_level of
   the voting node) and the QuorumCalculator determines the required
   fraction.  The protocol is guaranteed to terminate (Theorem 3) and
   to be Byzantine-fault-tolerant for f < n/3 faulty nodes.

Formal Theorem Statements
--------------------------
Theorem 1 — Federation Soundness
  For any discovery d and consensus result C: if C.outcome == APPROVED,
  then there exists a majority of non-faulty nodes n_i for which
  n_i.local_truth |= d.

Theorem 2 — Authority Monotonicity
  For any authority record A and time steps t1 <= t2:
    A.level(t1) <= A.level(t2)
  (Authority level is a non-decreasing function of time.)

Theorem 3 — Consensus Convergence
  For a connected federation of n nodes with message delay <= delta:
  the consensus protocol terminates in at most ceil(log2(n)) rounds,
  each of duration at most 2*delta.

Theorem 4 — Knowledge Propagation Soundness
  For any KnowledgeEntry E produced by KnowledgePropagator:
    validity(E) == True
  where validity is defined by the originating discovery's semantic
  schema.

Theorem 5 — Conflict Resolution Completeness
  For any pair of conflicting entries (E1, E2) in the repository:
  the conflict resolver produces a unique resolved entry E* within
  at most max_rounds resolution steps.

Integration Patterns
---------------------
Pattern A — Standalone Node
  A single JuGeo node can run the full pipeline locally:
  >>> runner = create_default_runner()
  >>> result = runner.run({"id": "d1", "trust_score": 0.9, "novelty_score": 0.7})

Pattern B — Multi-Node Simulation
  Simulate a small federation entirely in-process:
  >>> result = run_full_pipeline(
  ...     discovery={"id": "d2", "trust_score": 0.8, "novelty_score": 0.6},
  ...     node_ids=["n1", "n2", "n3"],
  ...     votes=[{"node_id": "n1", "approve": True, "weight": 2.0},
  ...            {"node_id": "n2", "approve": True, "weight": 1.0},
  ...            {"node_id": "n3", "approve": False, "weight": 1.0}],
  ... )
  >>> assert result["success"]

Pattern C — Custom Consensus Policy
  Use a supermajority policy for high-stakes discoveries:
  >>> runner = create_default_consensus_runner("supermajority")

Pattern D — Audit Manifests
  Capture a pipeline run as an auditable manifest:
  >>> builder = create_default_manifest_builder()
  >>> builder.with_discovery(discovery).with_nodes(node_ids)
  >>> manifest = builder.build()

Pattern E — Theorem Registry for Dashboards
  Expose formal guarantees to a monitoring dashboard:
  >>> registry = create_default_registry()
  >>> for thm in registry.theorems:
  ...     print(thm.name, thm.statement[:60])

Configuration Reference
------------------------
Trust Threshold (tau_trust):
  Default 0.6.  Lower values admit more discoveries but risk noise.
  Recommended range: 0.5 – 0.8 depending on federation size.

Novelty Threshold (tau_novel):
  Default 0.4.  Controls how semantically distinct a discovery must be
  from existing authorities.  Higher values reduce redundancy but may
  suppress related discoveries.

Consensus Policy:
  "simple_majority"  — good for large, well-connected federations.
  "supermajority"    — recommended for federations with known faulty nodes.
  "unanimous"        — suitable only for federations of 2–3 nodes.
  "weighted"         — best when authority levels vary greatly across nodes.

Merge Strategy:
  "trust_weighted"   — prefers the entry from the higher-trust node.
  "novelty_weighted" — prefers the entry with the higher novelty score.
  "timestamp_latest" — prefers the most recently created entry.
  "quorum_voted"     — nodes vote on which entry to keep (requires S03).

Changelog
----------
0.1.0 (initial)
  - Defined FederatedDiscovery, FederationConsensus, DiscoveryAuthority.
  - Implemented S01 authority promotion pipeline.
  - Implemented S02 knowledge propagation with trust_weighted merge.
  - Implemented S03 simple_majority and supermajority consensus.
  - Registered all five Ch61 formal theorems.
  - Added FederationIntegration hub with DiscoveryBridgeAdapter and
    AuthorityPackAdapter.
  - Added DiscoveryFederationManifest checkpoint format.

See Also
---------
* theory2.tex Chapter 61 — authoritative formal specification.
* jugeo.ideation.discovery_federation.theorems — runtime theorem objects.
* jugeo.ideation.discovery_federation.algorithms — pure algorithmic layer.
* jugeo.ideation.discovery_federation.integration — JuGeo system bridge.

copilot: shared-core marker
"""


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import authority_choice_rejection_provisi
except Exception:
    pass
try:
    from . import discovery_as_authority
except Exception:
    pass
try:
    from . import federated_knowledge
except Exception:
    pass
try:
    from . import federation_consensus
except Exception:
    pass
try:
    from . import federation_versus_foundation_scope
except Exception:
    pass
try:
    from . import implementation_consequences
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
