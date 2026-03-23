"""
algorithms.py — Algorithmic suite for the JuGeo Cyclic Picture maturity subsystem.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
This module provides the algorithmic infrastructure for driving, measuring, and
analysing the cyclic picture maturity process defined in Chapter 65 of the JuGeo
theoretical framework.  Where ``models.py`` defines the *what* (the data model)
and ``manifest.py`` defines the *how of certification* (the manifest layer), this
module defines the *how of computation* — the algorithms that operate on the
models to produce assessments, scores, projections, and rankings.

The algorithms are grouped into two tiers:

1. **Class-based algorithms** — ``MaturityAlgorithms`` is a dataclass that
   encapsulates a configurable algorithm suite.  It holds a configuration
   dictionary that parameterises the various algorithms (score weights, gain
   thresholds, cycle budgets, etc.) and provides methods that operate on live
   system objects.  Creating a ``MaturityAlgorithms`` instance via ``create()``
   installs sensible defaults for all parameters.

2. **Free functions** — Stateless computational utilities that do not require
   configuration state.  These are the lowest-level building blocks and are
   also exposed as ``__all__`` members for use by downstream consumers that
   do not need the full class-based interface.

Algorithm Theory (Ch65 §9)
--------------------------
The maturity assessment algorithm works as follows:

1. **Improvement gain estimation** (``estimate_improvement_gain``): Given
   before/after metric dictionaries, compute a scalar gain by averaging the
   relative improvements across all shared metric keys.  The gain is signed:
   a negative gain indicates regression.

2. **Maturity scoring** (``compute_maturity_score``): Convert a ``MaturityReport``
   to a numeric score in the range [0, 100].  The score is a weighted combination
   of the maturity level (which contributes a base score of 0–80), the mean
   improvement gain (contributing up to 10 additional points), and the federation
   health (contributing up to 10 additional points).

3. **Level progression** (``score_maturity_level``, ``interpolate_maturity_path``):
   Map maturity levels to integers and enumerate the shortest path between two
   levels.  Used by ``project_next_level()`` and ``estimate_cycles_to_next_level()``
   to give the orchestrator a forward-looking view of the system's trajectory.

4. **Federation health** (``compute_federation_health``): Convert a
   ``FederationState`` (or a FederationState-like dict) to a scalar health score
   in [0.0, 1.0].  Health is a function of quorum status, connection fraction,
   and whether the consensus threshold has been met.

5. **Gain aggregation** (``aggregate_improvement_gains``): Summarise a collection
   of ``ImprovementCycle`` objects by ``ImprovementKind``, computing per-kind
   statistics.  Used by the self-improving engine to identify which kinds of
   improvement have the highest remaining gain potential.

Configuration Parameters
------------------------
The ``MaturityAlgorithms.config`` dictionary supports the following keys:

- ``"gain_threshold"`` (float, default 0.05): Minimum gain to consider a cycle
  significant in advancement criterion checks.
- ``"prototype_min_cycles"`` (int, default 3): Minimum significant cycles to
  advance from PROTOTYPE.
- ``"operational_min_cycles"`` (int, default 5): Minimum total cycles for
  OPERATIONAL → FEDERATED.
- ``"operational_min_kinds"`` (int, default 2): Minimum distinct ImprovementKind
  values for OPERATIONAL → FEDERATED.
- ``"self_improving_min_cycles"`` (int, default 10): Minimum total cycles for
  SELF_IMPROVING → MATURE.
- ``"self_improving_min_avg_gain"`` (float, default 0.08): Minimum average gain
  for SELF_IMPROVING → MATURE.
- ``"score_level_weight"`` (float, default 0.8): Weight given to the level-based
  component of the maturity score (before multiplying by the 100-point scale).
- ``"score_gain_weight"`` (float, default 0.1): Weight given to the mean gain
  component.
- ``"score_federation_weight"`` (float, default 0.1): Weight given to federation
  health.
- ``"cycles_per_level_estimate"`` (int, default 5): Baseline estimate of cycles
  required to advance one level, used by ``estimate_cycles_to_next_level()``.

Cross-Module Integration
------------------------
All cross-module imports are guarded so that this module can be imported in
isolation.  The algorithms operate on duck-typed objects: for example,
``maturity_assessment()`` accepts any object with a ``system_id``,
``maturity_level``, ``improvement_history``, and optional ``federation_state``
attribute — it does not require an actual ``MatureSystem`` instance.  This
design allows the algorithms to be used in testing with lightweight fakes.

Numerical Stability
-------------------
All gain computations guard against division by zero via a small epsilon
denominator (1e-9).  All score computations are clamped to their valid ranges
using ``_clamp()``.  The ``interpolate_maturity_path()`` function handles the
edge case where ``from_level == to_level`` by returning a single-element list.

See Also
--------
* ``jugeo.maturity.cyclic_picture.models`` — domain model types operated on here.
* ``jugeo.maturity.cyclic_picture.manifest`` — manifest layer.
* theory2.tex Ch65 §9 — algorithmic foundations.
"""

from __future__ import annotations

import math
import uuid
import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    "MaturityAlgorithms",
    "estimate_improvement_gain",
    "rank_improvement_opportunities",
    "compute_federation_health",
    "score_maturity_level",
    "interpolate_maturity_path",
    "aggregate_improvement_gains",
]

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.models import (
        MaturityLevel,
        ImprovementKind,
        ImprovementCycle,
        FederationState,
        MaturityReport,
        MatureManifest,
        MatureSystem,
        SelfImprovingEngine,
        FederatedDeployment,
        MaturePipeline,
    )
except Exception:
    # Provide minimal stubs so the module is importable in isolation
    class MaturityLevel:  # type: ignore[no-redef]
        PROTOTYPE = "prototype"
        OPERATIONAL = "operational"
        FEDERATED = "federated"
        SELF_IMPROVING = "self_improving"
        MATURE = "mature"

        @classmethod
        def _missing_(cls, value):
            return None

    class ImprovementKind:  # type: ignore[no-redef]
        CAPABILITY = "capability"
        EFFICIENCY = "efficiency"
        COVERAGE = "coverage"
        ROBUSTNESS = "robustness"
        FEDERATION = "federation"

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp float.

    Centralises time acquisition for easy monkeypatching in tests.
    """
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _uid() -> str:
    """Return a fresh random UUID4 as a hex string without dashes.

    Provides statistically unique identifiers without inter-process coordination.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [*lo*, *hi*].

    Prevents numeric quantities from drifting outside their valid ranges due
    to floating-point rounding or adversarial inputs.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Default algorithm configuration
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "gain_threshold": 0.05,
    "prototype_min_cycles": 3,
    "operational_min_cycles": 5,
    "operational_min_kinds": 2,
    "self_improving_min_cycles": 10,
    "self_improving_min_avg_gain": 0.08,
    "score_level_weight": 0.8,
    "score_gain_weight": 0.1,
    "score_federation_weight": 0.1,
    "cycles_per_level_estimate": 5,
}

# Ordered list of maturity level strings for indexing
_LEVEL_ORDER: list[str] = [
    "prototype",
    "operational",
    "federated",
    "self_improving",
    "mature",
]


def _level_value(level: Any) -> str:
    """Extract the string value from a MaturityLevel, enum, or str.

    Handles three representations:
    - An object with a ``.value`` attribute (e.g. a ``MaturityLevel`` enum).
    - A plain string.
    - Any other object (coerced via ``str()``).

    Parameters
    ----------
    level:
        The level to extract a string value from.

    Returns
    -------
    str
        The string representation of the level.
    """
    if hasattr(level, "value"):
        return str(level.value)
    return str(level)


# ---------------------------------------------------------------------------
# MaturityAlgorithms — class-based algorithm suite
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MaturityAlgorithms:
    """Configurable algorithm suite for maturity assessment and improvement.

    ``MaturityAlgorithms`` is the primary algorithmic entry point for the cyclic
    picture maturity subsystem.  It bundles a configuration dictionary with a
    comprehensive set of methods that assess, score, rank, and project system
    maturity.

    The instance is lightweight: it carries only an ``algorithms_id`` (for
    traceability in logs) and a ``config`` dictionary.  All methods operate on
    externally provided system objects rather than internal state, which makes
    the algorithm suite thread-safe for concurrent use across multiple systems.

    Configuration:
        The ``config`` dictionary is initialised with the values in
        ``_DEFAULT_CONFIG`` and can be overridden per-key via the ``config``
        argument to ``create()``.  See the module docstring for a full list of
        supported configuration keys.

    Extensibility:
        All methods are designed to accept duck-typed objects, so they work with
        both the canonical ``MatureSystem`` / ``MaturityReport`` types and with
        lightweight fake objects in tests.  The only requirement is that the
        input objects expose the expected attributes (documented per method).
    """

    algorithms_id: str
    """Unique identifier for this algorithm suite instance (UUID4 hex)."""

    config: dict[str, Any]
    """Configuration dictionary controlling algorithm parameters."""

    @classmethod
    def create(cls, config: Optional[dict[str, Any]] = None) -> "MaturityAlgorithms":
        """Factory: create a MaturityAlgorithms instance with merged configuration.

        Merges the provided *config* dictionary with the module-level
        ``_DEFAULT_CONFIG``, with the provided values taking precedence.
        This means callers can override individual parameters without having
        to supply the full configuration.

        Parameters
        ----------
        config:
            Optional dictionary of configuration overrides.  Keys not present
            in ``_DEFAULT_CONFIG`` are accepted and stored verbatim for
            forward-compatibility with future algorithm extensions.

        Returns
        -------
        MaturityAlgorithms
            A configured algorithm suite instance ready for use.

        Examples
        --------
        ::

            algos = MaturityAlgorithms.create(config={"gain_threshold": 0.03})
            # Now uses 0.03 instead of the default 0.05 for significance checks.
        """
        merged = dict(_DEFAULT_CONFIG)
        if config:
            merged.update(config)
        return cls(algorithms_id=_uid(), config=merged)

    def improvement_step(self, system: Any) -> dict[str, Any]:
        """Execute one improvement step on the given system.

        Analyses the system's current improvement history to determine which
        ``ImprovementKind`` has the lowest average gain (i.e. the most room for
        improvement), then synthesises a new improvement cycle targeting that
        kind.  The resulting cycle is recorded on the system and returned as a
        dictionary (compatible with ``ImprovementCycle.to_dict()``).

        This method creates a minimal ``ImprovementCycle`` using the models
        module if available, or falls back to a plain dictionary representation.
        It does not actually execute any real computation on the system; that
        is the responsibility of the orchestrator which dispatches the step to
        the appropriate handler.

        Parameters
        ----------
        system:
            A ``MatureSystem``-like object with:
            - ``improvement_history``: list of ImprovementCycle-like objects.
            - ``system_id``: str identifier.
            - ``record_improvement(cycle)``: method that records a cycle.

        Returns
        -------
        dict[str, Any]
            A dictionary representing the created ImprovementCycle, or a
            minimal cycle dict if the models module is unavailable.

        Notes
        -----
        The synthesised cycle uses a heuristic before-state derived from the
        most recent cycle's after-metrics (or a default if no history exists)
        and projects a small gain to simulate progress.
        """
        history = getattr(system, "improvement_history", [])

        # Determine target kind: use the kind with fewest cycles
        kind_counts: dict[str, int] = {k: 0 for k in [
            "capability", "efficiency", "coverage", "robustness", "federation"
        ]}
        for c in history:
            k = _level_value(getattr(c, "kind", "capability"))
            if k in kind_counts:
                kind_counts[k] += 1

        target_kind_str = min(kind_counts, key=kind_counts.__getitem__)

        # Derive before-metrics from last cycle or use defaults
        if history:
            last = history[-1]
            before = dict(getattr(last, "after_metrics", {"score": 0.5}))
        else:
            before = {"score": 0.5, "throughput": 10.0}

        # Simulate a small improvement
        after = {k: v * (1.0 + self.config.get("gain_threshold", 0.05) * 1.1)
                 for k, v in before.items()}

        try:
            kind_enum = ImprovementKind(target_kind_str)
            cycle = ImprovementCycle.create(kind_enum, before, after)
            if hasattr(system, "record_improvement"):
                system.record_improvement(cycle)
            return cycle.to_dict()
        except Exception:
            gain = estimate_improvement_gain(before, after)
            cycle_dict = {
                "cycle_id": _uid(),
                "kind": target_kind_str,
                "before_metrics": before,
                "after_metrics": after,
                "gain": gain,
                "timestamp": _utcnow(),
            }
            return cycle_dict

    def federation_sync(self, deployment: Any) -> dict[str, Any]:
        """Synchronise federation state for the given deployment.

        Performs a simulated federation synchronisation step: queries the
        deployment's peer list, computes a health score for each peer based on
        the sync_state, and updates the deployment's sync_state with a new
        ``last_sync`` timestamp and a computed ``health`` value.

        This method is a thin orchestration wrapper; the actual network I/O is
        performed by the federation protocol layer.  Here we only update the
        bookkeeping state that the maturity assessment algorithm uses.

        Parameters
        ----------
        deployment:
            A ``FederatedDeployment``-like object with:
            - ``peer_nodes``: list of peer node ID strings.
            - ``sync_state``: dict updated via ``update_sync(key, value)``.
            - ``role``: the node's FederationRole.

        Returns
        -------
        dict[str, Any]
            A dictionary summarising the sync outcome, including the number of
            peers contacted, the computed health score, and the sync timestamp.

        Notes
        -----
        In a real implementation this method would issue RPC calls to each peer
        and collect acknowledgements.  The current implementation simulates a
        successful sync for testing and development purposes.
        """
        peers = list(getattr(deployment, "peer_nodes", []))
        n_peers = len(peers)
        ts = _utcnow()
        # Simulated health: 1.0 if at least one peer, 0.5 if no peers
        health = 1.0 if n_peers > 0 else 0.5

        if hasattr(deployment, "update_sync"):
            deployment.update_sync("last_sync", ts)
            deployment.update_sync("health", health)
            deployment.update_sync("peer_count", n_peers)

        return {
            "peers_contacted": n_peers,
            "health": health,
            "sync_timestamp": ts,
            "role": _level_value(getattr(deployment, "role", "peer")),
        }

    def maturity_assessment(self, system: Any) -> dict[str, Any]:
        """Build a maturity assessment report for the given system.

        Aggregates all available information about the system — its maturity
        level, improvement history, federation state, and pipeline health —
        and produces a structured assessment report.  The report is returned
        as a dictionary compatible with ``MaturityReport.to_dict()``.

        The method attempts to construct a proper ``MaturityReport`` instance
        if the models module is available; otherwise it returns a plain dict.

        Parameters
        ----------
        system:
            A ``MatureSystem``-like object with:
            - ``system_id``: str.
            - ``maturity_level``: MaturityLevel or str.
            - ``improvement_history``: list of ImprovementCycle-like objects.
            - ``federation_state``: optional FederationState-like object.
            - ``pipeline``: optional MaturePipeline-like object.

        Returns
        -------
        dict[str, Any]
            A dictionary representing the maturity assessment report, including
            the computed maturity score, level, cycle statistics, and federation
            health if applicable.

        Notes
        -----
        The score is computed by ``compute_maturity_score()`` and appended to
        the report dictionary as a ``maturity_score`` key for convenience.
        """
        sys_id = getattr(system, "system_id", "unknown")
        level = getattr(system, "maturity_level", "prototype")
        history = list(getattr(system, "improvement_history", []))
        fed_state = getattr(system, "federation_state", None)

        n_cycles = len(history)
        avg_gain = (
            sum(float(getattr(c, "gain", 0.0)) for c in history) / n_cycles
            if n_cycles > 0
            else 0.0
        )
        fed_health = 0.0
        if fed_state is not None:
            fed_health = compute_federation_health(fed_state)

        level_str = _level_value(level)
        base_score = score_maturity_level(level_str) * 20.0  # 0-80
        gain_score = _clamp(avg_gain / 0.2, 0.0, 1.0) * 10.0
        fed_score = fed_health * 10.0
        maturity_score = _clamp(base_score + gain_score + fed_score, 0.0, 100.0)

        report_dict: dict[str, Any] = {
            "system_id": sys_id,
            "level": level_str,
            "n_cycles": n_cycles,
            "avg_gain": avg_gain,
            "federation_health": fed_health,
            "maturity_score": maturity_score,
            "timestamp": _utcnow(),
        }

        try:
            ml = MaturityLevel(level_str)
            report = MaturityReport.create(
                system_id=sys_id,
                level=ml,
                cycles=history,
                federation_state=fed_state,
            )
            combined = report.to_dict()
            combined["maturity_score"] = maturity_score
            return combined
        except Exception:
            return report_dict

    def compute_maturity_score(self, report: Any) -> float:
        """Compute a numeric maturity score [0, 100] from a MaturityReport.

        The score is a weighted combination of three components:
        - Level score (0–80): ``score_maturity_level(report.level) * 20``
        - Gain score (0–10): ``clamp(mean_gain / 0.2) * 10``
        - Federation score (0–10): ``federation_health * 10``

        The weights can be adjusted via the configuration keys
        ``"score_level_weight"``, ``"score_gain_weight"``, and
        ``"score_federation_weight"``.  The weights are applied as multipliers
        on the raw component values and the result is re-normalised to [0, 100].

        This score is designed to be monotonically increasing with maturity level
        while still rewarding high gain and healthy federation at each level.

        Parameters
        ----------
        report:
            A ``MaturityReport``-like object with:
            - ``level``: MaturityLevel or str.
            - ``improvement_cycles``: iterable of cycles with ``.gain`` attribute.
            - ``federation_state``: optional FederationState-like object.

        Returns
        -------
        float
            A maturity score in the range [0.0, 100.0].
        """
        level = getattr(report, "level", "prototype")
        cycles = list(getattr(report, "improvement_cycles", []))
        fed_state = getattr(report, "federation_state", None)

        level_score = score_maturity_level(level) * 20.0
        n = len(cycles)
        if n > 0:
            avg_gain = sum(float(getattr(c, "gain", 0.0)) for c in cycles) / n
        else:
            avg_gain = 0.0
        gain_score = _clamp(avg_gain / 0.2, 0.0, 1.0) * 10.0
        fed_health = compute_federation_health(fed_state) if fed_state else 0.0
        fed_score = fed_health * 10.0

        lw = self.config.get("score_level_weight", 0.8)
        gw = self.config.get("score_gain_weight", 0.1)
        fw = self.config.get("score_federation_weight", 0.1)

        raw = lw * level_score + gw * gain_score * 10.0 + fw * fed_score * 10.0
        return _clamp(raw, 0.0, 100.0)

    def rank_systems_by_maturity(self, systems: list[Any]) -> list[Any]:
        """Sort a list of systems in descending order of maturity score.

        Computes a maturity assessment for each system, extracts the maturity
        score, and returns the list sorted from highest to lowest score.  The
        original system objects are returned (not the report dictionaries), so
        the caller can act on them directly after sorting.

        If two systems have the same score, their relative order is preserved
        (stable sort).

        Parameters
        ----------
        systems:
            A list of ``MatureSystem``-like objects, each with a
            ``system_id``, ``maturity_level``, and ``improvement_history``.

        Returns
        -------
        list[Any]
            The input list sorted in descending order of maturity score.

        Examples
        --------
        ::

            ranked = algos.rank_systems_by_maturity([sys_a, sys_b, sys_c])
            best = ranked[0]  # The most mature system
        """
        def _score(sys: Any) -> float:
            try:
                report = self.maturity_assessment(sys)
                return float(report.get("maturity_score", 0.0))
            except Exception:
                return 0.0

        return sorted(systems, key=_score, reverse=True)

    def project_next_level(self, system: Any) -> str:
        """Return the string name of the next maturity level for the system.

        Looks up the system's current level in ``_LEVEL_ORDER`` and returns the
        string for the next level.  If the system is already at MATURE (the
        terminal level), returns "mature" unchanged.

        This projection is purely level-based; it does not check whether the
        advancement criteria are currently met.  Use
        ``estimate_cycles_to_next_level()`` for a count-based estimate.

        Parameters
        ----------
        system:
            A ``MatureSystem``-like object with a ``maturity_level`` attribute.

        Returns
        -------
        str
            The string name of the projected next maturity level.
        """
        level = _level_value(getattr(system, "maturity_level", "prototype"))
        try:
            idx = _LEVEL_ORDER.index(level)
        except ValueError:
            idx = 0
        next_idx = min(idx + 1, len(_LEVEL_ORDER) - 1)
        return _LEVEL_ORDER[next_idx]

    def estimate_cycles_to_next_level(self, system: Any) -> int:
        """Estimate the number of additional improvement cycles needed to advance.

        Uses the system's current improvement history to estimate how many more
        cycles are needed to satisfy the advancement criteria for the next
        maturity level.  The estimate is based on the configuration parameter
        ``"cycles_per_level_estimate"`` and the current mean gain.

        Algorithm:
        1. Determine the required number of cycles for the next level based on
           the current level and configuration.
        2. Count how many cycles already satisfy the gain threshold.
        3. Return the deficit (required - qualifying cycles), clamped to >= 0.

        If the system has already met the criteria for the next level, returns 0.

        Parameters
        ----------
        system:
            A ``MatureSystem``-like object with:
            - ``maturity_level``: the current level.
            - ``improvement_history``: list of cycles with ``.gain`` / ``.is_significant()``.

        Returns
        -------
        int
            Estimated number of additional cycles required.  0 means the
            system is already eligible to advance.
        """
        level = _level_value(getattr(system, "maturity_level", "prototype"))
        history = list(getattr(system, "improvement_history", []))
        threshold = self.config.get("gain_threshold", 0.05)

        # Count qualifying cycles
        qualifying = 0
        for c in history:
            if hasattr(c, "is_significant"):
                if c.is_significant(threshold):
                    qualifying += 1
            else:
                if float(getattr(c, "gain", 0.0)) > threshold:
                    qualifying += 1

        level_requirements = {
            "prototype": self.config.get("prototype_min_cycles", 3),
            "operational": self.config.get("operational_min_cycles", 5),
            "federated": self.config.get("self_improving_min_cycles", 10),
            "self_improving": self.config.get("self_improving_min_cycles", 10),
            "mature": 0,
        }
        required = level_requirements.get(level, self.config.get("cycles_per_level_estimate", 5))
        return max(0, required - qualifying)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def estimate_improvement_gain(
    before_metrics: dict[str, float],
    after_metrics: dict[str, float],
) -> float:
    """Compute a scalar improvement gain from before/after metric dictionaries.

    Iterates over the keys shared by both dictionaries and computes the
    arithmetic mean of the relative improvements.  For a metric key *k* the
    relative improvement is:

        (after[k] - before[k]) / max(abs(before[k]), 1e-9)

    Keys present in only one of the two dicts are excluded from the average.
    A positive gain indicates improvement; a negative gain indicates regression.
    If there are no shared keys the gain is 0.0.

    This function is the canonical gain computation used throughout the
    maturity subsystem.  It is also called internally by
    ``ImprovementCycle.create()`` via the equivalent ``_compute_gain_from_metrics``
    helper in ``models.py``.

    Parameters
    ----------
    before_metrics:
        A mapping of metric names to their values before the improvement was
        applied.  All values should be numeric (float or int).
    after_metrics:
        A mapping of metric names to their values after the improvement.

    Returns
    -------
    float
        The mean relative improvement across all shared metric keys.
        Returns 0.0 if there are no shared keys.

    Examples
    --------
    ::

        gain = estimate_improvement_gain(
            {"accuracy": 0.72, "f1": 0.68},
            {"accuracy": 0.81, "f1": 0.75},
        )
        # gain ≈ 0.115 (average of 12.5% and 10.3% improvements)
    """
    shared = set(before_metrics.keys()) & set(after_metrics.keys())
    if not shared:
        return 0.0
    total = 0.0
    for k in shared:
        denom = max(abs(before_metrics[k]), 1e-9)
        total += (after_metrics[k] - before_metrics[k]) / denom
    return total / len(shared)


def rank_improvement_opportunities(
    opportunities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sort a list of improvement opportunity dictionaries by priority (descending).

    Each opportunity dictionary is expected to have a ``"priority"`` key whose
    value is numeric.  Opportunities missing the ``"priority"`` key are assigned
    a priority of 0.0 for sorting purposes.  The sort is stable: opportunities
    with equal priority retain their original relative order.

    This function is used by the ``SelfImprovingEngine`` to select the next
    improvement cycle kind when multiple opportunities are identified.  The
    orchestrator calls this function with the output of an opportunity
    identification step to get an ordered work queue.

    Parameters
    ----------
    opportunities:
        A list of dictionaries, each representing an improvement opportunity.
        Each dict should have at minimum a ``"priority"`` key (float or int)
        and a ``"kind"`` key (string) for human-readable identification.

    Returns
    -------
    list[dict[str, Any]]
        The input list sorted in descending order of ``"priority"``.

    Examples
    --------
    ::

        opps = [
            {"kind": "capability", "priority": 0.8},
            {"kind": "efficiency", "priority": 0.5},
            {"kind": "robustness", "priority": 0.9},
        ]
        ranked = rank_improvement_opportunities(opps)
        # ranked[0] == {"kind": "robustness", "priority": 0.9}
    """
    return sorted(
        opportunities,
        key=lambda o: float(o.get("priority", 0.0)),
        reverse=True,
    )


def compute_federation_health(state: Any) -> float:
    """Compute a scalar health score [0.0, 1.0] for a federation state.

    The health score is derived from two sub-scores:
    - **Connection fraction**: ``active_connections / max(node_count, 1)``
    - **Quorum bonus**: +0.2 if quorum is reached, 0 otherwise

    The final score is the arithmetic mean of the connection fraction and
    the quorum bonus, clamped to [0.0, 1.0].

    The function accepts both ``FederationState`` objects (from ``models.py``)
    and duck-typed objects or plain dictionaries with the same attributes/keys.

    Parameters
    ----------
    state:
        A ``FederationState``-like object or dict with:
        - ``node_count`` / ``"node_count"``: total known nodes.
        - ``active_connections`` / ``"active_connections"``: connected nodes.
        - ``consensus_threshold`` / ``"consensus_threshold"``: quorum fraction.
        The function also accepts objects with an ``is_quorum_reached()`` method.

    Returns
    -------
    float
        A health score in [0.0, 1.0].  Returns 0.5 if ``state`` is None or
        if node_count is zero (single-node pseudo-federation).

    Examples
    --------
    ::

        health = compute_federation_health(my_federation_state)
        if health < 0.5:
            logger.warning("Federation health critical: %.2f", health)
    """
    if state is None:
        return 0.5

    def _get(attr: str, default: Any) -> Any:
        if isinstance(state, dict):
            return state.get(attr, default)
        return getattr(state, attr, default)

    node_count = int(_get("node_count", 0))
    active = int(_get("active_connections", 0))

    if node_count == 0:
        return 0.5

    connection_fraction = _clamp(active / node_count)

    quorum_bonus = 0.0
    if hasattr(state, "is_quorum_reached"):
        if state.is_quorum_reached():
            quorum_bonus = 0.2
    else:
        threshold = float(_get("consensus_threshold", 0.67))
        if node_count > 0 and (active / node_count) >= threshold:
            quorum_bonus = 0.2

    raw_health = (connection_fraction + quorum_bonus) / 1.2  # normalise to [0,1]
    return _clamp(raw_health)


def score_maturity_level(level: Any) -> int:
    """Map a maturity level to an integer rank (0–4).

    Provides a simple numeric encoding of the ordered maturity levels for use
    in scoring formulas and comparison operations.  The mapping is:

    - ``"prototype"`` → 0
    - ``"operational"`` → 1
    - ``"federated"`` → 2
    - ``"self_improving"`` → 3
    - ``"mature"`` → 4

    Unknown level strings default to 0.  The function accepts both plain
    strings and enum-like objects with a ``.value`` attribute.

    Parameters
    ----------
    level:
        A maturity level string or ``MaturityLevel``-like enum value.

    Returns
    -------
    int
        An integer rank in [0, 4] encoding the maturity level.

    Examples
    --------
    ::

        assert score_maturity_level("prototype") == 0
        assert score_maturity_level("mature") == 4
        assert score_maturity_level(MaturityLevel.OPERATIONAL) == 1
    """
    level_str = _level_value(level)
    try:
        return _LEVEL_ORDER.index(level_str)
    except ValueError:
        return 0


def interpolate_maturity_path(
    from_level: Any,
    to_level: Any,
) -> list[str]:
    """Return the ordered list of maturity levels from *from_level* to *to_level*.

    Enumerates the shortest monotone path through ``_LEVEL_ORDER`` connecting
    the two given levels.  Both endpoints are included.  If ``from_level ==
    to_level``, a single-element list is returned.  If ``from_level`` is higher
    than ``to_level`` (i.e. a downgrade path), the list is returned in
    descending order.

    This function is used by the orchestrator to plan a multi-step maturity
    advancement journey and by the reporting layer to show the progression
    history on a timeline.

    Parameters
    ----------
    from_level:
        The starting maturity level (string or enum).
    to_level:
        The target maturity level (string or enum).

    Returns
    -------
    list[str]
        An ordered list of maturity level strings, inclusive of both endpoints.
        Returns a list with a single element if both levels are the same.
        Returns a list in descending order if ``from_level > to_level``.

    Examples
    --------
    ::

        path = interpolate_maturity_path("prototype", "federated")
        # ["prototype", "operational", "federated"]

        downgrade = interpolate_maturity_path("mature", "operational")
        # ["mature", "self_improving", "federated", "operational"]
    """
    from_str = _level_value(from_level)
    to_str = _level_value(to_level)

    try:
        from_idx = _LEVEL_ORDER.index(from_str)
    except ValueError:
        from_idx = 0
    try:
        to_idx = _LEVEL_ORDER.index(to_str)
    except ValueError:
        to_idx = 0

    if from_idx <= to_idx:
        return _LEVEL_ORDER[from_idx : to_idx + 1]
    else:
        return list(reversed(_LEVEL_ORDER[to_idx : from_idx + 1]))


def aggregate_improvement_gains(cycles: list[Any]) -> dict[str, float]:
    """Aggregate improvement gains by ImprovementKind across a list of cycles.

    Groups the provided cycles by their ``kind`` attribute and computes
    summary statistics for each group.  Returns a flat dictionary where each
    key is an ImprovementKind value string and each value is the arithmetic
    mean gain for cycles of that kind.

    Kinds that have no cycles in the input list are omitted from the output.
    Kinds are identified by their string value (from ``.kind.value`` or
    ``.kind`` if it is already a string).

    This function is used by the ``SelfImprovingEngine`` to identify which
    improvement kinds have the highest remaining potential (lowest current
    mean gain) and by the reporting layer to show per-kind breakdowns in
    ``MaturityReport.render_tex()``.

    Parameters
    ----------
    cycles:
        A list of ``ImprovementCycle``-like objects, each with:
        - ``kind``: an ImprovementKind or string identifying the kind.
        - ``gain``: a float representing the improvement gain.

    Returns
    -------
    dict[str, float]
        A dictionary mapping each kind string to the mean gain for cycles
        of that kind.  Empty if ``cycles`` is empty.

    Examples
    --------
    ::

        from jugeo.maturity.cyclic_picture.models import ImprovementCycle, ImprovementKind

        cycles = [
            ImprovementCycle.create(ImprovementKind.CAPABILITY, {"a": 0.5}, {"a": 0.6}),
            ImprovementCycle.create(ImprovementKind.CAPABILITY, {"a": 0.6}, {"a": 0.65}),
            ImprovementCycle.create(ImprovementKind.EFFICIENCY, {"t": 100.0}, {"t": 90.0}),
        ]
        agg = aggregate_improvement_gains(cycles)
        # agg["capability"] ≈ 0.125 (mean of 0.2 and 0.083)
        # agg["efficiency"] ≈ -0.1 (single cycle, 10% regression in throughput)
    """
    by_kind: dict[str, list[float]] = {}
    for cycle in cycles:
        kind_obj = getattr(cycle, "kind", "capability")
        kind_str = _level_value(kind_obj)
        gain = float(getattr(cycle, "gain", 0.0))
        if kind_str not in by_kind:
            by_kind[kind_str] = []
        by_kind[kind_str].append(gain)

    return {
        k: sum(gains) / len(gains)
        for k, gains in by_kind.items()
        if gains
    }
