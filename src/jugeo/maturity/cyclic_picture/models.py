"""
models.py — Core domain models for the JuGeo Cyclic Picture maturity subsystem.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
--------
This module defines the canonical data model layer for the *cyclic picture* view of
system maturity as developed in Chapter 65 of the JuGeo theoretical framework
(theory2.tex).  The cyclic picture treats a system's evolution not as a monotone
linear progression but as a sequence of closed improvement cycles, each of which
advances the system's observable properties while preserving an invariant structural
skeleton — the "cyclic picture" itself.

Theoretical Background
----------------------
In the JuGeo framework a *mature* computational system is one that has passed
through a succession of verifiable improvement cycles, each cycle witnessed by an
evidence chain stored in the evidence subsystem (``jugeo.evidence``).  The cycles
are *cyclic* in the sense that the measurement apparatus used to assess progress is
itself updated as part of each cycle, so the whole system is self-referential in a
controlled way.

Chapter 65 introduces five canonical maturity levels:

1. **PROTOTYPE** — An initial working sketch.  The system demonstrates the core
   computational concept but lacks the robustness, coverage, or federation needed for
   wider deployment.  Improvement cycles at this level focus on proving basic
   feasibility and establishing baseline metrics.

2. **OPERATIONAL** — The system has passed rigorous internal validation and is
   deployed for real tasks within a single organisational boundary.  Improvement
   cycles at this level focus on efficiency, reliability, and the accumulation of
   evidence records linking system behaviour to formal claims.

3. **FEDERATED** — The system participates in a multi-node federation with at least
   one remote peer.  Consensus protocols govern state synchronisation; improvement
   cycles now include cross-node coordination and the negotiation of shared trust
   profiles (``jugeo.evidence.trust``).

4. **SELF_IMPROVING** — The system has closed the loop: its own improvement
   machinery is guided by metrics it produces, mediated by a ``SelfImprovingEngine``
   instance.  At this level the cyclic picture becomes fully self-referential and the
   improvement schedule is derived algorithmically rather than by human intervention.

5. **MATURE** — The system satisfies all formal maturity claims encoded in a
   ``MatureManifest``, has accumulated sufficient evidence across all
   ``ImprovementKind`` categories, and participates stably in a federated topology.
   The mature state is not terminal; the system continues to execute improvement
   cycles but they are now oriented towards sustaining the mature equilibrium rather
   than advancing through levels.

Data Model Architecture
-----------------------
The module is built around a small set of immutable value objects (frozen dataclasses
with ``slots=True``) and a complementary set of mutable aggregate roots (non-frozen
dataclasses with ``slots=True`` for memory efficiency).

*Immutable value objects*: ``ImprovementCycle``, ``MaturityReport``,
``MatureManifest``.  These are the canonical "facts" of the system — once created
they must not change.  They carry ``to_dict`` / ``render_tex`` methods for
serialisation and publication.

*Mutable aggregates*: ``FederationState``, ``MatureSystem``, ``SelfImprovingEngine``,
``FederatedDeployment``, ``MaturePipeline``.  These represent the live operational
state of the system and are updated in place as cycles execute.

Cross-Module Integration
------------------------
The models in this file form the *destination* of several cross-module evidence
flows.  A ``Manifest`` from ``jugeo.evidence.manifests`` is the lightweight upstream
trigger; a ``MatureManifest`` here is the heavyweight downstream artefact that
certifies the system's claims.  The bridge subsystem (``jugeo.packs.bridges``) is
responsible for composing ``BridgeTheorem`` instances that link formal improvement
gains to the claims encoded in a ``MatureManifest``.

The ``MaturityReport`` produced after each major cycle is handed off to the
orchestrator (``jugeo.orchestration.controller``) which uses it to decide whether to
advance the system to the next maturity level or to trigger additional remediation
cycles.

Usage Example
-------------
::

    from jugeo.maturity.cyclic_picture.models import (
        ImprovementCycle, ImprovementKind, MatureSystem, MaturityLevel,
    )

    # Start a new system at PROTOTYPE level
    sys = MatureSystem.create("my-system")

    # Record an improvement cycle
    before = {"accuracy": 0.72, "throughput": 45.0}
    after  = {"accuracy": 0.81, "throughput": 53.0}
    cycle  = ImprovementCycle.create(ImprovementKind.CAPABILITY, before, after)
    sys.record_improvement(cycle)

    # Attempt to advance to OPERATIONAL
    advanced = sys.advance_level()
    print(sys.summary())

Design Principles
-----------------
* All timestamps are UTC POSIX floats produced by ``_utcnow()``.
* All identifiers are random UUIDs produced by ``_uid()``.
* Numeric quantities that represent ratios or probabilities are clamped to [0, 1]
  by ``_clamp()``.
* Every public class is listed in ``__all__``.
* Cross-module imports are guarded with ``try/except Exception: pass`` so that this
  module may be imported in isolation for testing or documentation generation without
  triggering import errors when optional subsystems are not installed.

See Also
--------
* ``jugeo.maturity.cyclic_picture.algorithms`` — algorithmic helpers operating on
  these models.
* ``jugeo.maturity.cyclic_picture.manifest`` — manifest management layer.
* ``jugeo.evidence.manifests`` — upstream evidence manifest infrastructure.
* theory2.tex Ch65 — full theoretical exposition.
"""

from __future__ import annotations

import json
import math
import uuid
import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    "MaturityLevel",
    "ImprovementKind",
    "FederationRole",
    "DeploymentStatus",
    "ImprovementCycle",
    "FederationState",
    "MaturityReport",
    "MatureManifest",
    "MatureSystem",
    "SelfImprovingEngine",
    "FederatedDeployment",
    "MaturePipeline",
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

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds since epoch).

    This helper centralises time acquisition so that it can be monkeypatched
    cleanly in tests without affecting the rest of the standard library.
    """
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _uid() -> str:
    """Return a freshly minted random UUID string (hex, no dashes).

    Using UUID4 ensures that generated identifiers are statistically unique
    without any coordination between processes, which is important in a
    federated setting where multiple nodes may generate IDs simultaneously.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Used throughout the module to ensure that ratio/probability quantities never
    escape their valid numerical range due to floating-point rounding or
    adversarial inputs.

    Parameters
    ----------
    value:
        The raw numeric value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to 0.0.
    hi:
        Upper bound (inclusive).  Defaults to 1.0.

    Returns
    -------
    float
        The clamped value.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MaturityLevel(str, Enum):
    """Canonical maturity levels for a JuGeo system as defined in Ch65.

    Each level represents a qualitatively distinct phase in the system's
    lifecycle, characterised by different evidence requirements, federation
    topology constraints, and improvement cycle semantics.
    """

    PROTOTYPE = "prototype"
    """Early-stage proof-of-concept.

    A PROTOTYPE system has demonstrated the core computational idea but has not
    yet undergone rigorous validation.  Metrics are approximate, coverage is
    limited, and no external peers have been involved.  Improvement cycles at
    this level are primarily exploratory: the goal is to discover which
    capabilities are feasible and to establish baseline measurements.

    Advancement criterion: at least three recorded ImprovementCycles with a
    combined average gain exceeding 0.05, plus at least one CAPABILITY cycle.
    """

    OPERATIONAL = "operational"
    """Production-ready within a single organisational boundary.

    An OPERATIONAL system has passed internal validation gates and is being used
    for real tasks.  Its metrics are tracked systematically and its improvement
    cycles are governed by a documented methodology.  Evidence records link each
    claimed improvement to observable measurements, and those records are
    accessible to the evidence subsystem.

    Advancement criterion: at least five recorded ImprovementCycles spanning at
    least two different ImprovementKind categories, with reliability_score >= 0.95
    in the associated MaturePipeline.
    """

    FEDERATED = "federated"
    """Multi-node deployment with active peer participation.

    A FEDERATED system coordinates with at least one external peer node via the
    federation protocol.  Consensus thresholds govern state updates, and
    improvement cycles now include a ``FEDERATION`` kind entry that records the
    health of the cross-node synchronisation.  Trust profiles from
    ``jugeo.evidence.trust`` are exchanged with peers and merged.

    Advancement criterion: FederationState.is_quorum_reached() is True for at
    least three consecutive improvement cycles, and at least one FEDERATION kind
    cycle has been recorded with gain > 0.0.
    """

    SELF_IMPROVING = "self_improving"
    """Autonomously guided improvement loop active.

    A SELF_IMPROVING system hosts a ``SelfImprovingEngine`` that selects and
    schedules improvement cycles without human intervention.  The engine analyses
    historical metrics, identifies the highest-priority improvement opportunities,
    and dispatches the appropriate cycle kind.  Human oversight is retained at
    the level of reviewing the engine's strategy and auditing its cycle history.

    Advancement criterion: SelfImprovingEngine.average_gain() >= 0.08 over the
    most recent ten cycles, with coverage of all five ImprovementKind values.
    """

    MATURE = "mature"
    """Stable, self-sustaining, fully evidenced system.

    A MATURE system satisfies all claims in its MatureManifest, participates
    stably in a federation, and maintains an active SelfImprovingEngine.  The
    mature state is the attractor of the cyclic picture dynamics: the system
    continues to cycle but the cycles now conserve the mature equilibrium rather
    than driving qualitative change.  Regression is possible if evidence chains
    are invalidated or federation health drops critically.

    Advancement criterion: terminal level — no further advancement is defined,
    though the system may be re-assessed if evidence is revoked.
    """


class ImprovementKind(str, Enum):
    """Categories of improvement that a single ImprovementCycle can represent.

    These categories partition the space of possible system improvements and are
    used both for analysis (aggregating gains by kind) and for advancement
    criteria (certain maturity level transitions require specific kinds).
    """

    CAPABILITY = "capability"
    """A new functional capability has been added or an existing one extended.

    Capability improvements are measured by changes in task-completion metrics,
    coverage breadth, or the addition of new supported input/output modalities.
    They represent horizontal expansion of what the system can do.
    """

    EFFICIENCY = "efficiency"
    """The system performs the same tasks faster, cheaper, or with less resource.

    Efficiency improvements are measured by throughput, latency, memory
    footprint, or cost-per-operation metrics.  They represent vertical
    optimisation of how the system does what it already does.
    """

    COVERAGE = "coverage"
    """The system now handles a broader range of inputs or edge cases correctly.

    Coverage improvements are measured by test-suite pass rates, fuzzing
    coverage statistics, or the breadth of documented input distributions the
    system has been validated against.  They represent the reduction of blind
    spots.
    """

    ROBUSTNESS = "robustness"
    """The system is more resilient to adversarial inputs, failures, and drift.

    Robustness improvements are measured by fault-injection survival rates,
    error recovery latency, or degradation curves under load.  They represent
    defensive hardening of the system's operational envelope.
    """

    FEDERATION = "federation"
    """The system's multi-node coordination quality has improved.

    Federation improvements are measured by consensus latency, quorum
    availability, and the fraction of synchronisation events that complete
    within deadline.  They represent advances in the collective behaviour of
    the distributed system rather than any single node's performance.
    """


class FederationRole(str, Enum):
    """The role a node plays within a federated JuGeo deployment.

    Federation topology is dynamic: a node may change its role as the
    federation evolves, but at any instant each node occupies exactly one
    role as recorded in its FederationState.
    """

    LEADER = "leader"
    """Coordinates consensus rounds and propagates state changes to followers.

    A LEADER node is responsible for initiating the periodic synchronisation
    protocol and for resolving conflicts between divergent state versions.  There
    is at most one LEADER per federation partition at any time.
    """

    FOLLOWER = "follower"
    """Accepts state updates from the LEADER and participates in voting.

    A FOLLOWER node applies state changes proposed by the LEADER after validating
    them against local evidence.  If the LEADER becomes unreachable a FOLLOWER
    may promote itself to LEADER via the election protocol.
    """

    PEER = "peer"
    """Equal participant in a leaderless consensus topology.

    In PEER mode all nodes have equal weight in consensus rounds.  This role is
    appropriate for small federations (2–5 nodes) where the overhead of leader
    election is undesirable.
    """

    OBSERVER = "observer"
    """Receives state updates but does not vote or propose changes.

    An OBSERVER node is useful for audit, monitoring, or warm-standby purposes.
    It maintains a full replica of the federation state without contributing to
    the quorum count.
    """


class DeploymentStatus(str, Enum):
    """Lifecycle status of a JuGeo system deployment.

    Tracks where in the deployment lifecycle a system currently sits, from
    initial local testing through to production federation and eventual
    retirement.
    """

    LOCAL = "local"
    """Running only on the developer's local machine; not yet staged.

    A LOCAL deployment is ephemeral and not subject to the evidence chain
    requirements that apply to STAGED or PRODUCTION deployments.
    """

    STAGED = "staged"
    """Deployed to a staging environment for pre-production validation.

    Evidence records are collected in STAGED mode but are marked as provisional
    until the deployment transitions to PRODUCTION.
    """

    PRODUCTION = "production"
    """Live, user-facing deployment with full evidence chain enforcement.

    All improvement cycles recorded while in PRODUCTION status carry full
    evidentiary weight and are subject to external audit.
    """

    FEDERATED = "federated"
    """Production deployment that additionally participates in a federation.

    A FEDERATED deployment status indicates that the system is both serving
    users and coordinating state with peer nodes in a multi-node topology.
    """

    RETIRED = "retired"
    """Decommissioned; no longer serving requests.

    A RETIRED system retains its historical evidence records for audit but
    does not accept new improvement cycles.  Its MatureManifest is archived
    with status SUPERSEDED.
    """


# ---------------------------------------------------------------------------
# Dataclasses — immutable value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ImprovementCycle:
    """An immutable record of a single completed improvement cycle.

    An ImprovementCycle captures the before/after state of a system's metrics
    across one discrete improvement episode.  It is the atomic unit of the
    cyclic picture: the entire maturity history of a system is represented as
    a sequence of ImprovementCycle instances.

    The ``gain`` field is a scalar summary of the improvement, computed as the
    average relative improvement across all metric keys present in both
    ``before_metrics`` and ``after_metrics``.

    Immutability guarantees:
        Once created via ``create()``, an ImprovementCycle cannot be modified.
        This ensures that the historical record is tamper-evident: any attempt
        to retroactively alter a cycle would require constructing a new object,
        which would receive a new ``cycle_id`` and ``timestamp``.

    Serialisation:
        ``to_dict()`` produces a JSON-serialisable dictionary that can be stored
        in the evidence subsystem or transmitted to a remote peer node.
        ``render_tex()`` produces a LaTeX snippet suitable for inclusion in
        automated technical reports.
    """

    cycle_id: str
    """Unique identifier for this cycle (UUID4 hex)."""

    kind: ImprovementKind
    """The category of improvement this cycle represents."""

    before_metrics: dict[str, float]
    """Snapshot of the relevant metrics immediately before the improvement."""

    after_metrics: dict[str, float]
    """Snapshot of the relevant metrics immediately after the improvement."""

    gain: float
    """Scalar gain summary: average relative improvement across shared metric keys."""

    timestamp: float
    """UTC POSIX timestamp of when this cycle was recorded."""

    @classmethod
    def create(
        cls,
        kind: ImprovementKind,
        before_metrics: dict[str, float],
        after_metrics: dict[str, float],
    ) -> "ImprovementCycle":
        """Factory method: create a new ImprovementCycle from raw metric snapshots.

        Generates a fresh ``cycle_id`` via ``_uid()`` and records the current
        UTC time via ``_utcnow()``.  The ``gain`` is computed automatically
        from the supplied metric dictionaries using ``compute_gain_from_metrics()``.

        Parameters
        ----------
        kind:
            The ImprovementKind category for this cycle.
        before_metrics:
            A mapping of metric names to their values before the improvement
            was applied.  Keys should be stable across cycles for meaningful
            gain computation.
        after_metrics:
            A mapping of metric names to their values after the improvement.
            Keys not present in ``before_metrics`` are ignored for gain
            computation purposes (they represent newly introduced metrics).

        Returns
        -------
        ImprovementCycle
            A fully initialised, frozen ImprovementCycle instance.

        Examples
        --------
        ::

            cycle = ImprovementCycle.create(
                ImprovementKind.CAPABILITY,
                before_metrics={"accuracy": 0.72},
                after_metrics={"accuracy": 0.81},
            )
            assert cycle.gain > 0
        """
        gain = _compute_gain_from_metrics(before_metrics, after_metrics)
        return cls(
            cycle_id=_uid(),
            kind=kind,
            before_metrics=dict(before_metrics),
            after_metrics=dict(after_metrics),
            gain=gain,
            timestamp=_utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this ImprovementCycle to a JSON-serialisable dictionary.

        The resulting dictionary contains all fields of the dataclass in a form
        that is safe to pass to ``json.dumps()`` or to store in a document
        database.  Enum values are serialised as their string values so that the
        output is self-describing.

        Returns
        -------
        dict[str, Any]
            A flat dictionary representation of this cycle.
        """
        return {
            "cycle_id": self.cycle_id,
            "kind": self.kind.value,
            "before_metrics": dict(self.before_metrics),
            "after_metrics": dict(self.after_metrics),
            "gain": self.gain,
            "timestamp": self.timestamp,
        }

    def compute_gain(self) -> float:
        """Recompute the gain from the stored metric snapshots.

        This method replicates the gain computation that was performed at
        creation time.  It is useful for verifying that a deserialized cycle
        has consistent data, or for testing the gain algorithm in isolation
        without needing to call ``create()``.

        The gain is defined as the arithmetic mean of the relative improvements
        across all metric keys that appear in both ``before_metrics`` and
        ``after_metrics``.  For a key *k*, the relative improvement is:

            (after[k] - before[k]) / max(abs(before[k]), 1e-9)

        Keys present in only one of the two dicts are excluded from the average.
        If no shared keys exist the gain is 0.0.

        Returns
        -------
        float
            The recomputed scalar gain.
        """
        return _compute_gain_from_metrics(self.before_metrics, self.after_metrics)

    def render_tex(self) -> str:
        """Render this ImprovementCycle as a LaTeX snippet.

        Produces a ``\\subsection`` block describing the cycle, suitable for
        inclusion in a technical report generated from a ``MaturityReport``.
        Metric deltas are shown in a tabular environment for clarity.

        Returns
        -------
        str
            A LaTeX string that can be pasted into a ``\\section{Improvement Cycles}``
            environment.
        """
        lines = [
            r"\subsection{Improvement Cycle \texttt{" + self.cycle_id[:8] + r"}}",
            r"\begin{description}",
            r"  \item[Kind] " + self.kind.value.replace("_", r"\_"),
            r"  \item[Gain] " + f"{self.gain:.4f}",
            r"  \item[Timestamp] " + str(self.timestamp),
            r"\end{description}",
            r"\begin{tabular}{lrr}",
            r"\hline",
            r"Metric & Before & After \\",
            r"\hline",
        ]
        all_keys = sorted(set(self.before_metrics) | set(self.after_metrics))
        for k in all_keys:
            bv = self.before_metrics.get(k, float("nan"))
            av = self.after_metrics.get(k, float("nan"))
            lines.append(
                r"\texttt{" + k.replace("_", r"\_") + r"} & "
                + f"{bv:.4f}" + r" & " + f"{av:.4f}" + r" \\"
            )
        lines += [r"\hline", r"\end{tabular}"]
        return "\n".join(lines)

    def is_significant(self, threshold: float = 0.05) -> bool:
        """Determine whether this cycle's gain is practically significant.

        A cycle is considered significant if its ``gain`` exceeds the given
        threshold.  The default threshold of 0.05 (5% relative improvement)
        is the value recommended in Ch65 §4.3 for advancement criterion checks.

        Parameters
        ----------
        threshold:
            Minimum gain required for the cycle to be considered significant.
            Defaults to 0.05.

        Returns
        -------
        bool
            True if ``self.gain > threshold``, False otherwise.
        """
        return self.gain > threshold


def _compute_gain_from_metrics(
    before: dict[str, float], after: dict[str, float]
) -> float:
    """Compute a scalar gain from before/after metric dictionaries.

    Iterates over the keys shared by both dictionaries and computes the mean
    relative improvement.  Keys with a before-value of zero are handled by
    using a small epsilon denominator to avoid division by zero.

    Parameters
    ----------
    before:
        Metric values before the improvement.
    after:
        Metric values after the improvement.

    Returns
    -------
    float
        The mean relative improvement, or 0.0 if there are no shared keys.
    """
    shared = set(before.keys()) & set(after.keys())
    if not shared:
        return 0.0
    total = 0.0
    for k in shared:
        denom = max(abs(before[k]), 1e-9)
        total += (after[k] - before[k]) / denom
    return total / len(shared)


# ---------------------------------------------------------------------------
# FederationState — mutable aggregate
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FederationState:
    """Mutable record of the current federation topology and consensus state.

    FederationState is the live operational view of how many nodes are known to
    this system, how many are actively connected, and what the consensus
    threshold is.  It is intentionally mutable because the federation topology
    changes frequently as nodes join, leave, or become temporarily unreachable.

    Unlike ``ImprovementCycle`` (which is a historical fact), ``FederationState``
    represents the current belief about the world and must be updated in place
    as that belief changes.

    Consensus semantics:
        ``is_quorum_reached()`` returns True when the fraction of known nodes
        that are actively connected meets or exceeds ``consensus_threshold``.
        The default threshold of 0.67 (two-thirds majority) follows the
        recommendation in Ch65 §7.1 for Byzantine-fault-tolerant federations.
    """

    node_count: int
    """Total number of known peer nodes (including this node)."""

    consensus_threshold: float
    """Fraction of nodes required for quorum.  Clamped to [0.5, 1.0]."""

    known_nodes: list[str]
    """List of node identifiers known to this federation member."""

    active_connections: int
    """Number of peer nodes currently reachable and connected."""

    @classmethod
    def create(cls, consensus_threshold: float = 0.67) -> "FederationState":
        """Create a new, empty FederationState with the given consensus threshold.

        Initialises with an empty ``known_nodes`` list and zero connections.
        The threshold is clamped to [0.5, 1.0] to ensure it is both meaningful
        (strictly more than half) and achievable (not requiring perfect
        connectivity).

        Parameters
        ----------
        consensus_threshold:
            The fraction of known nodes required for quorum.  Defaults to 0.67.

        Returns
        -------
        FederationState
            A freshly initialised FederationState instance.
        """
        return cls(
            node_count=0,
            consensus_threshold=_clamp(consensus_threshold, 0.5, 1.0),
            known_nodes=[],
            active_connections=0,
        )

    def add_node(self, node_id: str) -> None:
        """Register a new peer node with this federation member.

        Adds ``node_id`` to ``known_nodes`` if not already present, then
        updates ``node_count`` to reflect the new total.  This method is
        idempotent: adding the same node_id twice has no additional effect.

        Parameters
        ----------
        node_id:
            The unique identifier of the peer node to add.
        """
        if node_id not in self.known_nodes:
            self.known_nodes.append(node_id)
        self.node_count = len(self.known_nodes)

    def remove_node(self, node_id: str) -> None:
        """Deregister a peer node from this federation member.

        Removes ``node_id`` from ``known_nodes`` if present, then updates
        ``node_count``.  Also decrements ``active_connections`` if it would
        otherwise exceed the new node count, preventing an impossible state.

        Parameters
        ----------
        node_id:
            The unique identifier of the peer node to remove.
        """
        if node_id in self.known_nodes:
            self.known_nodes.remove(node_id)
        self.node_count = len(self.known_nodes)
        if self.active_connections > self.node_count:
            self.active_connections = self.node_count

    def is_quorum_reached(self) -> bool:
        """Check whether the current active connections satisfy the quorum requirement.

        Returns True if ``active_connections / node_count >= consensus_threshold``.
        If ``node_count`` is zero (no known peers), quorum is considered trivially
        reached (a single node is its own majority) and True is returned.

        This method is the primary gate used by the maturity advancement logic
        in ``MatureSystem.advance_level()`` to determine whether the federation
        is healthy enough to support a FEDERATED or higher level designation.

        Returns
        -------
        bool
            True if quorum is reached or if there are no known peers.
        """
        if self.node_count == 0:
            return True
        fraction = self.active_connections / self.node_count
        return fraction >= self.consensus_threshold

    def to_dict(self) -> dict[str, Any]:
        """Serialise this FederationState to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            A dictionary containing all fields of this FederationState.
        """
        return {
            "node_count": self.node_count,
            "consensus_threshold": self.consensus_threshold,
            "known_nodes": list(self.known_nodes),
            "active_connections": self.active_connections,
        }


# ---------------------------------------------------------------------------
# MaturityReport — immutable value object
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MaturityReport:
    """An immutable snapshot of a system's maturity at a point in time.

    A MaturityReport aggregates all information needed to assess and communicate
    the current maturity level of a JuGeo system.  It is produced by the
    maturity assessment algorithm (``algorithms.MaturityAlgorithms.maturity_assessment``)
    and consumed by the orchestrator to make advancement decisions.

    Because it is frozen, a MaturityReport is safe to cache, transmit, and store
    in an evidence chain without risk of inadvertent mutation.  Each report has
    a unique ``system_id`` and a ``timestamp`` so that a sequence of reports can
    be ordered chronologically.

    The ``improvement_cycles`` field is a tuple (not a list) to enforce
    immutability at the collection level as well as at the object level.
    """

    system_id: str
    """The identifier of the system this report describes."""

    level: MaturityLevel
    """The maturity level assessed at the time this report was generated."""

    improvement_cycles: tuple[ImprovementCycle, ...]
    """All improvement cycles recorded up to the time of this report."""

    federation_state: Optional[FederationState]
    """Snapshot of the federation state at report time, or None if not federated."""

    timestamp: float
    """UTC POSIX timestamp when this report was generated."""

    @classmethod
    def create(
        cls,
        system_id: str,
        level: MaturityLevel,
        cycles: list[ImprovementCycle],
        federation_state: Optional[FederationState] = None,
    ) -> "MaturityReport":
        """Factory: create a MaturityReport from the given parameters.

        Captures the current UTC time and converts the ``cycles`` list to an
        immutable tuple.  The ``federation_state`` is stored by reference (not
        deep-copied), so callers should not mutate it after passing it here if
        they require the report to remain an accurate historical snapshot; in
        practice the orchestrator always creates a new FederationState snapshot
        before constructing a report.

        Parameters
        ----------
        system_id:
            Identifier of the system being reported on.
        level:
            The MaturityLevel being recorded.
        cycles:
            List of all ImprovementCycle instances recorded by the system.
        federation_state:
            Optional current federation state.

        Returns
        -------
        MaturityReport
            A frozen, immutable MaturityReport instance.
        """
        return cls(
            system_id=system_id,
            level=level,
            improvement_cycles=tuple(cycles),
            federation_state=federation_state,
            timestamp=_utcnow(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this MaturityReport to a JSON-safe dictionary.

        Recursively serialises nested objects: ImprovementCycle instances are
        converted via their own ``to_dict()`` method, and FederationState is
        likewise converted if present.

        Returns
        -------
        dict[str, Any]
            A fully serialisable dictionary representation of this report.
        """
        return {
            "system_id": self.system_id,
            "level": self.level.value,
            "improvement_cycles": [c.to_dict() for c in self.improvement_cycles],
            "federation_state": (
                self.federation_state.to_dict() if self.federation_state else None
            ),
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """Produce a concise human-readable summary of this report.

        Returns a single-paragraph string suitable for logging or display in a
        dashboard.  Includes the system ID, maturity level, number of recorded
        cycles, total average gain, and federation status.

        Returns
        -------
        str
            A human-readable summary paragraph.
        """
        n_cycles = len(self.improvement_cycles)
        if n_cycles > 0:
            avg_gain = sum(c.gain for c in self.improvement_cycles) / n_cycles
        else:
            avg_gain = 0.0
        fed_summary = "not federated"
        if self.federation_state:
            fed_summary = (
                f"{self.federation_state.active_connections}/"
                f"{self.federation_state.node_count} nodes active"
            )
        return (
            f"System '{self.system_id}' | Level: {self.level.value} | "
            f"Cycles: {n_cycles} | Avg gain: {avg_gain:.4f} | "
            f"Federation: {fed_summary}"
        )

    def render_tex(self) -> str:
        """Render this MaturityReport as a LaTeX document section.

        Produces a ``\\section{Maturity Report}`` block with a summary table and
        subsections for each improvement cycle.  The output is intended to be
        included in a larger document via ``\\input{}``.

        Returns
        -------
        str
            A LaTeX string representing the full maturity report section.
        """
        lines = [
            r"\section{Maturity Report for \texttt{" + self.system_id + r"}}",
            r"\begin{description}",
            r"  \item[Level] " + self.level.value.replace("_", r"\_"),
            r"  \item[Timestamp] " + str(self.timestamp),
            r"  \item[Cycles] " + str(len(self.improvement_cycles)),
            r"\end{description}",
            "",
        ]
        for cycle in self.improvement_cycles:
            lines.append(cycle.render_tex())
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# MatureManifest — immutable value object
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MatureManifest:
    """Immutable certificate that a system has attained a declared maturity level.

    A MatureManifest is the authoritative claim that a particular system has
    satisfied all the maturity criteria for a given ``MaturityLevel``.  It lists
    the capabilities the system claims to have, the evidence chain identifiers
    that substantiate those claims, and produces LaTeX output for formal reports.

    Manifests are created by the ``MatureSystem.create_manifest()`` method (not
    present here; see ``manifest.py``) after the system has accumulated sufficient
    improvement cycles.  They are stored in the evidence subsystem and linked to
    the system's ``BridgeTheorem`` network.

    Immutability note:
        A MatureManifest, once issued, cannot be amended.  If evidence is
        revoked or capabilities are removed, a new manifest at a lower level
        must be issued and the old one superseded.  The evidence chain preserves
        the full audit history of manifest transitions.
    """

    manifest_id: str
    """Unique identifier for this manifest (UUID4 hex)."""

    system_id: str
    """Identifier of the system this manifest certifies."""

    maturity_level: MaturityLevel
    """The maturity level this manifest certifies."""

    capabilities: tuple[str, ...]
    """Tuple of capability identifiers claimed by this system."""

    evidence_chain: tuple[str, ...]
    """Tuple of evidence record identifiers supporting the claims."""

    @classmethod
    def create(
        cls,
        system_id: str,
        maturity_level: MaturityLevel,
        capabilities: list[str],
        evidence_chain: list[str],
    ) -> "MatureManifest":
        """Factory: create a new MatureManifest for the given system.

        Generates a fresh ``manifest_id`` and converts the supplied lists to
        immutable tuples.  The factory does not validate that the evidence chain
        is sufficient for the claimed maturity level; that validation is the
        responsibility of the caller (typically ``MaturityAlgorithms.maturity_assessment``).

        Parameters
        ----------
        system_id:
            The identifier of the system being certified.
        maturity_level:
            The maturity level being claimed.
        capabilities:
            List of capability identifiers the system claims.
        evidence_chain:
            List of evidence record identifiers that substantiate the claims.

        Returns
        -------
        MatureManifest
            A frozen, immutable MatureManifest instance.
        """
        return cls(
            manifest_id=_uid(),
            system_id=system_id,
            maturity_level=maturity_level,
            capabilities=tuple(capabilities),
            evidence_chain=tuple(evidence_chain),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this MatureManifest to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            A dictionary with all fields in JSON-serialisable form.
        """
        return {
            "manifest_id": self.manifest_id,
            "system_id": self.system_id,
            "maturity_level": self.maturity_level.value,
            "capabilities": list(self.capabilities),
            "evidence_chain": list(self.evidence_chain),
        }

    def has_capability(self, cap: str) -> bool:
        """Check whether a specific capability is claimed by this manifest.

        Parameters
        ----------
        cap:
            The capability identifier to look up.

        Returns
        -------
        bool
            True if ``cap`` is in ``self.capabilities``, False otherwise.
        """
        return cap in self.capabilities

    def render_tex(self) -> str:
        """Render this MatureManifest as a LaTeX certificate block.

        Produces a formal LaTeX representation suitable for inclusion in a
        technical report or audit document.  Lists all claimed capabilities and
        evidence chain references in an itemised form.

        Returns
        -------
        str
            A LaTeX string representing this manifest as a formal certificate.
        """
        lines = [
            r"\section{Mature Manifest \texttt{" + self.manifest_id[:8] + r"}}",
            r"\begin{description}",
            r"  \item[System] \texttt{" + self.system_id + r"}",
            r"  \item[Level] " + self.maturity_level.value.replace("_", r"\_"),
            r"\end{description}",
            r"\subsection{Capabilities}",
            r"\begin{itemize}",
        ]
        for cap in self.capabilities:
            lines.append(r"  \item \texttt{" + cap.replace("_", r"\_") + r"}")
        lines += [r"\end{itemize}", r"\subsection{Evidence Chain}", r"\begin{itemize}"]
        for ref in self.evidence_chain:
            lines.append(r"  \item \texttt{" + ref + r"}")
        lines.append(r"\end{itemize}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# MatureSystem — mutable aggregate root
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MatureSystem:
    """The central mutable aggregate representing a JuGeo system under maturity management.

    A MatureSystem is the live operational entity that accumulates improvement
    cycles, tracks its current maturity level, and manages its federation state
    and pipeline.  It is the primary domain object that client code interacts with
    when driving a system through the cyclic picture maturity progression.

    Lifecycle:
        1. Create via ``MatureSystem.create(system_id)`` at PROTOTYPE level.
        2. Record improvement cycles via ``record_improvement()``.
        3. Call ``advance_level()`` periodically; it returns True if the system
           has met the criteria to advance to the next level.
        4. Inspect ``summary()`` or ``to_dict()`` for reporting.

    The system does not validate improvement cycles itself; that is the
    responsibility of ``MaturityAlgorithms``.  The system merely stores cycles
    and applies level-transition logic based on aggregate statistics.
    """

    system_id: str
    """Unique identifier for this system."""

    maturity_level: MaturityLevel
    """Current maturity level of the system."""

    pipeline: Optional[Any]
    """Optional reference to the system's MaturePipeline instance."""

    federation_state: Optional[FederationState]
    """Optional reference to the system's FederationState instance."""

    improvement_history: list[ImprovementCycle]
    """Ordered list of all improvement cycles recorded by this system."""

    @classmethod
    def create(
        cls,
        system_id: str,
        initial_level: MaturityLevel = MaturityLevel.PROTOTYPE,
    ) -> "MatureSystem":
        """Factory: create a new MatureSystem at the given initial maturity level.

        Initialises with an empty improvement history, no pipeline, and no
        federation state.  Clients should attach a MaturePipeline and a
        FederationState before attempting to record cycles of the corresponding
        kinds.

        Parameters
        ----------
        system_id:
            The unique identifier for the new system.
        initial_level:
            The starting maturity level.  Defaults to PROTOTYPE.

        Returns
        -------
        MatureSystem
            A freshly initialised MatureSystem instance.
        """
        return cls(
            system_id=system_id,
            maturity_level=initial_level,
            pipeline=None,
            federation_state=None,
            improvement_history=[],
        )

    def record_improvement(self, cycle: ImprovementCycle) -> None:
        """Append a completed improvement cycle to this system's history.

        The cycle is appended to ``improvement_history`` in order.  No
        deduplication is performed; if the same cycle_id appears twice it will
        be stored twice.  Callers are responsible for ensuring uniqueness.

        This method does NOT trigger level advancement; call ``advance_level()``
        separately after recording one or more cycles.

        Parameters
        ----------
        cycle:
            The completed ImprovementCycle to record.
        """
        self.improvement_history.append(cycle)

    def advance_level(self) -> bool:
        """Attempt to advance the system to the next maturity level.

        Evaluates the advancement criteria for the current level against the
        accumulated improvement history and federation state.  If the criteria
        are met, updates ``self.maturity_level`` to the next level and returns
        True.  If the criteria are not met, returns False without modifying the
        level.

        Advancement criteria (see also MaturityLevel docstrings):
        - PROTOTYPE → OPERATIONAL: >= 3 significant cycles
        - OPERATIONAL → FEDERATED: >= 5 cycles across >= 2 kinds
        - FEDERATED → SELF_IMPROVING: federation quorum reached
        - SELF_IMPROVING → MATURE: >= 10 cycles with mean gain >= 0.08
        - MATURE: no further advancement; always returns False

        Returns
        -------
        bool
            True if the level was advanced, False otherwise.
        """
        _level_order = [
            MaturityLevel.PROTOTYPE,
            MaturityLevel.OPERATIONAL,
            MaturityLevel.FEDERATED,
            MaturityLevel.SELF_IMPROVING,
            MaturityLevel.MATURE,
        ]
        current_idx = _level_order.index(self.maturity_level)
        if current_idx >= len(_level_order) - 1:
            return False

        significant = [c for c in self.improvement_history if c.is_significant()]
        n = len(self.improvement_history)
        kinds = {c.kind for c in self.improvement_history}
        avg_gain = (
            sum(c.gain for c in self.improvement_history) / n if n > 0 else 0.0
        )

        can_advance = False
        if self.maturity_level == MaturityLevel.PROTOTYPE:
            can_advance = len(significant) >= 3
        elif self.maturity_level == MaturityLevel.OPERATIONAL:
            can_advance = n >= 5 and len(kinds) >= 2
        elif self.maturity_level == MaturityLevel.FEDERATED:
            fs = self.federation_state
            can_advance = fs is not None and fs.is_quorum_reached()
        elif self.maturity_level == MaturityLevel.SELF_IMPROVING:
            can_advance = n >= 10 and avg_gain >= 0.08

        if can_advance:
            self.maturity_level = _level_order[current_idx + 1]
        return can_advance

    def to_dict(self) -> dict[str, Any]:
        """Serialise this MatureSystem to a JSON-safe dictionary.

        Recursively serialises nested objects.  The ``pipeline`` field is
        serialised via its ``to_dict()`` method if it is a MaturePipeline
        instance; otherwise it is represented as None.

        Returns
        -------
        dict[str, Any]
            A fully serialisable dictionary representation.
        """
        pipeline_dict = None
        if self.pipeline is not None and hasattr(self.pipeline, "to_dict"):
            pipeline_dict = self.pipeline.to_dict()
        return {
            "system_id": self.system_id,
            "maturity_level": self.maturity_level.value,
            "pipeline": pipeline_dict,
            "federation_state": (
                self.federation_state.to_dict() if self.federation_state else None
            ),
            "improvement_history": [c.to_dict() for c in self.improvement_history],
        }

    def summary(self) -> str:
        """Return a concise human-readable summary of this system's current state.

        Produces a single line suitable for logging or CLI output.  Includes
        system ID, current maturity level, number of recorded cycles, and
        federation quorum status.

        Returns
        -------
        str
            A one-line summary string.
        """
        n = len(self.improvement_history)
        avg = (
            sum(c.gain for c in self.improvement_history) / n if n > 0 else 0.0
        )
        fed = "no federation"
        if self.federation_state:
            q = "quorum" if self.federation_state.is_quorum_reached() else "no quorum"
            fed = f"{self.federation_state.node_count} nodes ({q})"
        return (
            f"[MatureSystem {self.system_id}] level={self.maturity_level.value} "
            f"cycles={n} avg_gain={avg:.4f} fed={fed}"
        )


# ---------------------------------------------------------------------------
# SelfImprovingEngine — mutable aggregate
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SelfImprovingEngine:
    """Mutable engine that autonomously drives the cyclic improvement process.

    A SelfImprovingEngine is attached to a MatureSystem once it reaches the
    SELF_IMPROVING level.  It maintains a count of completed cycles and a
    history of the metric dictionaries observed at each cycle, from which it
    computes summary statistics to guide the next cycle's strategy.

    The engine does not directly execute improvements; it provides the analytical
    infrastructure (cycle counting, metrics history, gain averaging) that the
    orchestrator uses to dispatch improvement tasks to the appropriate subsystems.

    Strategy strings:
        The ``improvement_strategy`` field is an opaque string that the
        orchestrator passes to the appropriate handler.  Predefined strategies
        include "default", "greedy_gain", "coverage_first", and
        "federation_priority", but the field accepts any string.
    """

    engine_id: str
    """Unique identifier for this engine instance."""

    improvement_strategy: str
    """The strategy identifier used to select improvement cycle kinds."""

    cycle_count: int
    """Total number of improvement cycles driven by this engine."""

    metrics_history: list[dict[str, Any]]
    """Ordered list of metric snapshots, one per completed cycle."""

    @classmethod
    def create(cls, improvement_strategy: str = "default") -> "SelfImprovingEngine":
        """Factory: create a new SelfImprovingEngine with the given strategy.

        Initialises with zero cycles and an empty metrics history.

        Parameters
        ----------
        improvement_strategy:
            The strategy string.  Defaults to "default".

        Returns
        -------
        SelfImprovingEngine
            A freshly initialised engine instance.
        """
        return cls(
            engine_id=_uid(),
            improvement_strategy=improvement_strategy,
            cycle_count=0,
            metrics_history=[],
        )

    def increment_cycle(self) -> None:
        """Increment the engine's cycle counter by one.

        Called by the orchestrator after each improvement cycle completes
        successfully.  Does not update metrics_history; call ``record_metrics()``
        separately to record the cycle's metric snapshot.
        """
        self.cycle_count += 1

    def record_metrics(self, metrics: dict[str, Any]) -> None:
        """Append a metric snapshot to the engine's history.

        Stores a copy of the provided metrics dictionary so that the engine's
        history is not affected by subsequent mutations to the original dict.

        Parameters
        ----------
        metrics:
            A dictionary of metric names to values observed at this cycle.
        """
        self.metrics_history.append(dict(metrics))

    def average_gain(self) -> float:
        """Compute the average 'gain' value across all recorded metric snapshots.

        Iterates over ``metrics_history`` and extracts the 'gain' key from each
        entry.  Entries missing the 'gain' key contribute 0.0 to the average.
        Returns 0.0 if the history is empty.

        This value is used by ``MatureSystem.advance_level()`` to check the
        advancement criterion for the SELF_IMPROVING → MATURE transition.

        Returns
        -------
        float
            The arithmetic mean of 'gain' values across the metrics history.
        """
        if not self.metrics_history:
            return 0.0
        total = sum(float(m.get("gain", 0.0)) for m in self.metrics_history)
        return total / len(self.metrics_history)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this SelfImprovingEngine to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            A dictionary with all engine fields.
        """
        return {
            "engine_id": self.engine_id,
            "improvement_strategy": self.improvement_strategy,
            "cycle_count": self.cycle_count,
            "metrics_history": [dict(m) for m in self.metrics_history],
        }


# ---------------------------------------------------------------------------
# FederatedDeployment — mutable aggregate
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FederatedDeployment:
    """Mutable record of a node's participation in a federated JuGeo topology.

    A FederatedDeployment tracks the specific peers this node is aware of, the
    role it plays in the federation, and an arbitrary synchronisation state
    dictionary that accumulates during the consensus protocol.

    The ``sync_state`` dictionary is updated incrementally via ``update_sync()``
    and may hold any JSON-serialisable values.  It is not validated; the
    consensus protocol layer is responsible for ensuring consistency.
    """

    deployment_id: str
    """Unique identifier for this deployment instance."""

    role: FederationRole
    """This node's current role in the federation."""

    peer_nodes: list[str]
    """List of peer node identifiers currently known to this deployment."""

    sync_state: dict[str, Any]
    """Arbitrary key-value synchronisation state accumulated during consensus."""

    @classmethod
    def create(cls, role: FederationRole = FederationRole.PEER) -> "FederatedDeployment":
        """Factory: create a new FederatedDeployment with the given role.

        Initialises with an empty ``peer_nodes`` list and an empty
        ``sync_state`` dictionary.

        Parameters
        ----------
        role:
            The federation role for this deployment.  Defaults to PEER.

        Returns
        -------
        FederatedDeployment
            A freshly initialised deployment instance.
        """
        return cls(
            deployment_id=_uid(),
            role=role,
            peer_nodes=[],
            sync_state={},
        )

    def add_peer(self, node_id: str) -> None:
        """Register a new peer node with this deployment.

        Adds ``node_id`` to ``peer_nodes`` if not already present.  Idempotent.

        Parameters
        ----------
        node_id:
            The unique identifier of the peer node to register.
        """
        if node_id not in self.peer_nodes:
            self.peer_nodes.append(node_id)

    def remove_peer(self, node_id: str) -> None:
        """Deregister a peer node from this deployment.

        Removes ``node_id`` from ``peer_nodes`` if present.  No-op if absent.

        Parameters
        ----------
        node_id:
            The unique identifier of the peer node to remove.
        """
        if node_id in self.peer_nodes:
            self.peer_nodes.remove(node_id)

    def update_sync(self, key: str, value: Any) -> None:
        """Update a single key in the synchronisation state dictionary.

        This method is called by the consensus protocol layer to propagate
        agreed-upon state changes to the local ``sync_state`` store.

        Parameters
        ----------
        key:
            The state key to update.
        value:
            The new value for the key.  Must be JSON-serialisable.
        """
        self.sync_state[key] = value

    def to_dict(self) -> dict[str, Any]:
        """Serialise this FederatedDeployment to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            A dictionary with all deployment fields.
        """
        return {
            "deployment_id": self.deployment_id,
            "role": self.role.value,
            "peer_nodes": list(self.peer_nodes),
            "sync_state": dict(self.sync_state),
        }


# ---------------------------------------------------------------------------
# MaturePipeline — mutable aggregate
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MaturePipeline:
    """Mutable record of a processing pipeline within a JuGeo mature system.

    A MaturePipeline represents the sequence of computational stages through
    which a system processes its inputs.  It tracks throughput (items per second)
    and a reliability score (fraction of requests completed without error).

    The pipeline is considered *healthy* when the reliability score is at or
    above 0.95 and the throughput is strictly positive.  Health status is the
    primary signal used by the orchestrator when deciding whether to proceed with
    a maturity level advancement.

    Stage management:
        Stages are added in order via ``add_stage()``.  The ``stages`` list is
        ordered and represents the execution sequence of the pipeline.  Stage
        names are opaque strings; the pipeline object does not validate them.
    """

    pipeline_id: str
    """Unique identifier for this pipeline instance."""

    stages: list[str]
    """Ordered list of stage names composing this pipeline."""

    throughput: float
    """Measured throughput in items per second (must be > 0 to be healthy)."""

    reliability_score: float
    """Fraction of requests processed without error.  Clamped to [0.0, 1.0]."""

    @classmethod
    def create(
        cls,
        initial_throughput: float = 1.0,
        initial_reliability: float = 0.99,
    ) -> "MaturePipeline":
        """Factory: create a new MaturePipeline with the given initial metrics.

        Initialises with an empty ``stages`` list and the provided throughput
        and reliability values.  Both values are validated: throughput is
        clamped to a minimum of 0.0, and reliability is clamped to [0.0, 1.0].

        Parameters
        ----------
        initial_throughput:
            The starting throughput in items per second.  Defaults to 1.0.
        initial_reliability:
            The starting reliability score.  Defaults to 0.99.

        Returns
        -------
        MaturePipeline
            A freshly initialised pipeline instance.
        """
        return cls(
            pipeline_id=_uid(),
            stages=[],
            throughput=max(0.0, initial_throughput),
            reliability_score=_clamp(initial_reliability),
        )

    def add_stage(self, stage_name: str) -> None:
        """Append a new processing stage to the end of the pipeline.

        The stage name is appended to ``self.stages`` unconditionally; duplicate
        stage names are permitted as they may represent parallel branches of the
        same processing step.

        Parameters
        ----------
        stage_name:
            The name of the stage to add.
        """
        self.stages.append(stage_name)

    def update_throughput(self, new_throughput: float) -> None:
        """Update the pipeline's throughput measurement.

        Clamps the new value to a minimum of 0.0 to prevent negative throughput
        values that would arise from measurement artefacts.

        Parameters
        ----------
        new_throughput:
            The new throughput measurement in items per second.
        """
        self.throughput = max(0.0, new_throughput)

    def to_dict(self) -> dict[str, Any]:
        """Serialise this MaturePipeline to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            A dictionary with all pipeline fields.
        """
        return {
            "pipeline_id": self.pipeline_id,
            "stages": list(self.stages),
            "throughput": self.throughput,
            "reliability_score": self.reliability_score,
        }

    def is_healthy(self) -> bool:
        """Determine whether this pipeline is in a healthy operating state.

        A pipeline is healthy when its reliability score is at or above 0.95
        (the threshold recommended in Ch65 §6.2) and its throughput is strictly
        positive (indicating that it is actually processing requests).

        Returns
        -------
        bool
            True if the pipeline meets both health criteria, False otherwise.
        """
        return self.reliability_score >= 0.95 and self.throughput > 0.0
