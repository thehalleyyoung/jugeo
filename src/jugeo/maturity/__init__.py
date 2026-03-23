"""JuGeo maturity package — self-improving systems and the cyclic picture.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
========

The ``jugeo.maturity`` package implements JuGeo's formal model of system
maturity, as defined in Chapter 65 of theory2.tex.  The core idea is that
any complex software system passes through a sequence of maturity levels on
its way to being a fully autonomous, self-improving, federated deployment.
JuGeo formalises this journey as a traversal of a finite *maturity lattice*,
guided by a categorical construct called the *cyclic picture functor*.

The package is structured around the ``cyclic_picture`` sub-package, which
provides the detailed implementation of the maturity model including models,
algorithms, manifests, integration bridges, and formal theorems.  The
``jugeo.maturity`` top-level package re-exports the most commonly used
symbols and provides three convenience helper functions:
``describe_maturity_level``, ``is_production_ready``, and
``maturity_progression_path``.

The Five Maturity Levels
========================

The maturity lattice M is a totally ordered set with five elements:

    PROTOTYPE ≤ OPERATIONAL ≤ FEDERATED ≤ SELF_IMPROVING ≤ MATURE

Each level represents a qualitatively distinct mode of operation.

PROTOTYPE
---------

The PROTOTYPE level is the starting point for all systems that have been
created within the JuGeo framework but have not yet satisfied the minimum
criteria for operational use.  At this level the system exists primarily as
a proof-of-concept or early integration test.  The core capability set is
defined but may not be fully implemented; the improvement engine is
dormant; federation infrastructure is absent; and the deployment is
typically confined to a single developer workstation or a scratch
environment.

A PROTOTYPE system is characterised by:

* Rapid iteration: the implementation is expected to change frequently as
  the core capability set is refined.
* No stability guarantees: the system may crash, produce incorrect results,
  or exhibit undefined behaviour under edge-case inputs.
* No monitoring: the improvement engine's metric collectors are not yet
  activated, so no improvement cycle data is accumulated.
* No federation: the node has no peer connections and cannot participate in
  a federated quorum.
* Developer-facing tooling: the primary consumers are the system's own
  developers, not external users or downstream systems.

Advancement from PROTOTYPE to OPERATIONAL requires passing the JuGeo
operational readiness checklist: all declared capabilities must be
implemented and tested, basic monitoring must be active, and the system must
have survived at least one simulated failure injection test.

OPERATIONAL
-----------

At the OPERATIONAL level the system has been validated for use in a
controlled production environment.  The core capability set is fully
implemented and tested; the improvement engine is active and accumulating
metric data; the deployment is stable enough to serve real traffic; and
basic monitoring and alerting are in place.

Key characteristics of an OPERATIONAL system:

* Stability contract: the system must maintain a minimum uptime of 99%
  over any rolling 24-hour window.  If uptime falls below this threshold
  the system is automatically demoted to PROTOTYPE until the root cause
  is identified and remediated.
* Metric collection: the improvement engine's metric collectors are
  running and feeding data into the improvement proposal generator.
* Limited federation readiness: the node has generated its federation
  identity keys and has been registered in the JuGeo peer directory, but
  has not yet joined any active federation.
* Improvement cycle accumulation: the system must complete at least five
  successful improvement cycles before it becomes eligible to advance to
  FEDERATED.
* Human-in-the-loop governance: all improvement proposals are reviewed by
  a human operator before being applied.  Automated application of
  proposals is not permitted at this level.

FEDERATED
---------

The FEDERATED level is reached when the system joins an active federation
of two or more peer nodes.  Federation is the process by which independent
JuGeo nodes discover each other, establish trust relationships, agree on a
shared state schema, and begin participating in the JuGeo consensus protocol.

At the FEDERATED level:

* The system participates in a live quorum of at least τ·N nodes, where τ
  is the consensus threshold (typically 2/3) and N is the total number of
  registered nodes in the federation.
* All local system invariants are preserved across federation operations,
  as guaranteed by Theorem 65.5 (FederatedDeploymentSafety).
* The shared state S* is agreed upon by all active nodes within a bounded
  number of consensus rounds, as guaranteed by Theorem 65.4
  (FederationConsistency).
* Improvement proposals may now be generated collaboratively by the
  ideation engines of all peer nodes, with the proposal ranked and
  filtered by the collective regime.
* Human-in-the-loop governance remains required for improvement proposals
  that modify the federation topology (adding or removing nodes), but may
  be relaxed for capability improvements that affect only the local node.

Federation is a production-ready level: systems at FEDERATED or above may
be used to serve real user traffic with the full JuGeo reliability and
safety guarantees in effect.

SELF_IMPROVING
--------------

At the SELF_IMPROVING level the system's improvement engine has been
granted sufficient trust and capability to apply improvement proposals
autonomously, without requiring human review for every cycle.  This is the
most operationally complex level and requires the most stringent safety
preconditions.

Key characteristics:

* Automated improvement cycles: the scheduler selects and applies
  improvement proposals from the ranked list without human intervention,
  subject to a configurable risk threshold.
* Capability preservation guarantee: every automated cycle is validated by
  the schema validator to ensure it satisfies SelfImprovementSoundness
  (Theorem 65.2) before being applied.  Cycles that would violate the
  capability preservation invariant are rejected and logged.
* Rollback capability: the system maintains a snapshot of its state before
  each automated cycle.  If the post-cycle monitoring detects a regression,
  the system automatically rolls back to the pre-cycle snapshot and flags
  the cycle as failed.
* Federated coordination: in a federated deployment, automated cycles are
  proposed to the full quorum and must achieve consensus before being
  applied.  This prevents partitioned nodes from diverging.
* Audit trail: every automated cycle is recorded in the improvement cycle
  log with full provenance, evidence chain, and outcome metadata.

A SELF_IMPROVING system is considered production-ready for high-stakes
deployments and is a prerequisite for advancing to MATURE.

MATURE
------

The MATURE level is the apex of the maturity lattice.  A MATURE system has
demonstrated sustained, autonomous self-improvement across multiple
federated nodes over an extended period (typically 90 days) without any
capability regressions, consensus failures, or safety violations.

Characteristics of a MATURE system:

* Self-certified stability: the maturity assessor has validated that the
  system's improvement trajectory is monotonically increasing and that no
  regressions have been observed in the certification window.
* Full autonomy: all improvement proposal categories are approved for
  automated application, subject only to the consensus protocol.
* Ecosystem participation: the system can act as a trust anchor for
  newly onboarded PROTOTYPE systems, providing certified capability
  attestations and bootstrapping their evidence chains.
* Research-grade outputs: a MATURE system's improvement cycles are
  considered high-quality data for the JuGeo research corpus and may be
  cited in theory2.tex extensions.

The Cyclic Picture
==================

The *cyclic picture* is the categorical structure that formalises the
maturity improvement process.  It is defined as a functor:

    CP: Sys → Mat

that maps each well-typed system S to a directed graph CP(S) whose nodes are
the maturity levels and whose edges are the improvement cycles applicable to S.

The key insight of the cyclic picture is that improvement is not a linear
march from PROTOTYPE to MATURE; it is a *cycle*.  After a system reaches
MATURE it continues to run improvement cycles, each of which reinforces
and deepens its capabilities.  The "picture" of this ongoing improvement
activity forms a directed cycle in the maturity lattice.

The cyclic picture functor is proved complete by Theorem 65.3
(CyclicPictureCompleteness): every improvement mode achievable by a
well-typed system appears as an edge in CP(S).

Self-Improvement Loops
======================

The self-improvement loop is the core runtime mechanism of the maturity
engine.  The loop runs on a configurable schedule and performs the following
steps in each cycle:

1. **Metric collection**: the metric collectors gather performance, quality,
   and stability metrics from the running system.
2. **Proposal generation**: the ideation engine analyses the collected
   metrics and generates a ranked list of improvement proposals.
3. **Proposal filtering**: proposals below the configured risk threshold are
   discarded.
4. **Consensus** (federated systems only): the top-ranked proposal is
   submitted to the federated quorum for consensus.
5. **Application**: the approved proposal is applied to the system.
6. **Validation**: the post-application monitoring checks that the
   capability set has not been degraded (SelfImprovementSoundness check).
7. **Logging**: the completed cycle is logged with full provenance.

Federation Topology
===================

A JuGeo federation is an undirected graph G = (V, E) where each vertex v ∈ V
is a JuGeo node and each edge (u, v) ∈ E represents a trust-verified peer
connection between nodes u and v.  The federation topology is managed by the
``FederatedDeployment`` class in the ``cyclic_picture`` sub-package.

Consensus is achieved using a quorum-based protocol with threshold τ (default
2/3).  A quorum Q ⊆ V is a subset of nodes satisfying |Q| ≥ τ·|V|.  The
quorum intersection property (any two quorums share at least one common
member) is the key invariant that makes the FederationConsistency theorem
provable.

Convergence Guarantees
======================

The convergence properties of the maturity model are established by the
theorems in ``jugeo.maturity.cyclic_picture.theorems``:

* **MaturityConvergence** (§65.1): monotonic traversal of the lattice.
* **SelfImprovementSoundness** (§65.2): capability preservation across cycles.
* **CyclicPictureCompleteness** (§65.3): no improvement mode escapes the functor.
* **FederationConsistency** (§65.4): federated nodes converge in finite rounds.
* **FederatedDeploymentSafety** (§65.5): federation preserves local invariants.

Together these five theorems provide a comprehensive formal foundation for
the maturity model that is both mathematically rigorous and practically
implementable.

Usage Examples
==============

.. code-block:: python

    from jugeo.maturity import (
        describe_maturity_level,
        is_production_ready,
        maturity_progression_path,
    )

    # Get a description of the OPERATIONAL level
    print(describe_maturity_level("OPERATIONAL"))

    # Check if a level qualifies as production-ready
    print(is_production_ready("PROTOTYPE"))   # False
    print(is_production_ready("FEDERATED"))   # True

    # Get the progression path from PROTOTYPE to MATURE
    for level, description in maturity_progression_path("PROTOTYPE"):
        print(f"  {level}: {description}")

Advanced usage with the cyclic picture sub-package:

.. code-block:: python

    from jugeo.maturity.cyclic_picture.models import MatureSystem, MaturityLevel
    from jugeo.maturity.cyclic_picture.theorems import build_maturity_theorem_registry

    system = MatureSystem.create(
        name="my-system",
        maturity_level=MaturityLevel.OPERATIONAL,
    )
    registry = build_maturity_theorem_registry()
    for thm in registry.list_proved():
        print(thm.render_tex())
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional, List

__all__ = [
    "MATURITY_VERSION",
    "MATURITY_SCHEMA_VERSION",
    "describe_maturity_level",
    "is_production_ready",
    "maturity_progression_path",
]

MATURITY_VERSION: str = "1.0.0"
MATURITY_SCHEMA_VERSION: str = "2025.1"

# ---------------------------------------------------------------------------
# Guarded imports from cyclic_picture sub-package
# ---------------------------------------------------------------------------

try:
    from jugeo.maturity.cyclic_picture.models import (
        MaturityLevel,
        ImprovementKind,
        FederationRole,
        DeploymentStatus,
        ImprovementCycle,
        FederationState,
        MaturityReport,
        MatureManifest,
        MatureSystem,
        SelfImprovingEngine,
        FederatedDeployment,
        MaturePipeline,
    )
    __all__ += [
        "MaturityLevel", "ImprovementKind", "FederationRole", "DeploymentStatus",
        "ImprovementCycle", "FederationState", "MaturityReport", "MatureManifest",
        "MatureSystem", "SelfImprovingEngine", "FederatedDeployment", "MaturePipeline",
    ]
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.manifest import (
        ManifestStatus,
        CyclicPictureManifest,
        MaturityManifestBuilder,
        build_maturity_manifest,
        load_manifest_from_dict,
        merge_manifests,
        compare_manifests,
    )
    __all__ += [
        "ManifestStatus", "CyclicPictureManifest", "MaturityManifestBuilder",
        "build_maturity_manifest", "load_manifest_from_dict",
        "merge_manifests", "compare_manifests",
    ]
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.algorithms import (
        MaturityAlgorithms,
        estimate_improvement_gain,
        rank_improvement_opportunities,
        compute_federation_health,
        score_maturity_level,
        interpolate_maturity_path,
        aggregate_improvement_gains,
    )
    __all__ += [
        "MaturityAlgorithms", "estimate_improvement_gain",
        "rank_improvement_opportunities", "compute_federation_health",
        "score_maturity_level", "interpolate_maturity_path",
        "aggregate_improvement_gains",
    ]
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.theorems import (
        TheoremStatus,
        MaturityTheorem,
        MaturityTheoremRegistry,
        build_maturity_theorem_registry,
    )
    __all__ += [
        "TheoremStatus", "MaturityTheorem", "MaturityTheoremRegistry",
        "build_maturity_theorem_registry",
    ]
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.the_system_should_be_cyclic_not_pi import (
        CyclePhase, CycleRecord, CycleTransition, CycleMetrics, CycleObstruction,
        CyclicSystemAnalyzer, CyclicSystemWitness, CyclicSystemCoordinator,
        run_cycle, analyze_system_cyclicity, build_cycle_witness,
    )
    __all__ += [
        "CyclePhase", "CycleRecord", "CycleTransition", "CycleMetrics",
        "CycleObstruction", "CyclicSystemAnalyzer", "CyclicSystemWitness",
        "CyclicSystemCoordinator", "run_cycle", "analyze_system_cyclicity",
        "build_cycle_witness",
    ]
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.from_ideation_to_orchestration_to import (
        IdeationRecord, OrchestrationPlan, ProofRecord, FeedbackSignal,
        IdeationCycleRecord, IdeationToOrchestrationAnalyzer,
        IdeationToOrchestrationWitness, IdeationToOrchestrationCoordinator,
        run_ideation_cycle, assess_cycle_health, extract_ideation_patterns,
    )
    __all__ += [
        "IdeationRecord", "OrchestrationPlan", "ProofRecord", "FeedbackSignal",
        "IdeationCycleRecord", "IdeationToOrchestrationAnalyzer",
        "IdeationToOrchestrationWitness", "IdeationToOrchestrationCoordinator",
        "run_ideation_cycle", "assess_cycle_health", "extract_ideation_patterns",
    ]
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.why_this_could_matter_beyond_jugeo import (
        DomainProfile, TransferAnalysis, ImpactEstimate, BeyondJuGeoReport,
        BeyondJuGeoAnalyzer, BeyondJuGeoWitness, BeyondJuGeoCoordinator,
        analyze_generalizability, score_domain_fit, list_candidate_domains,
    )
    __all__ += [
        "DomainProfile", "TransferAnalysis", "ImpactEstimate", "BeyondJuGeoReport",
        "BeyondJuGeoAnalyzer", "BeyondJuGeoWitness", "BeyondJuGeoCoordinator",
        "analyze_generalizability", "score_domain_fit", "list_candidate_domains",
    ]
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.the_final_practical_consequence import (
        PracticalConsequence, ConsequenceEvidence, ConsequenceReport,
        TrustAuditEntry, TrustAuditTrail, FinalPracticalConsequenceAnalyzer,
        FinalPracticalConsequenceWitness, FinalPracticalConsequenceCoordinator,
        enumerate_practical_consequences, run_consequence_analysis,
        validate_trust_audit_trail,
    )
    __all__ += [
        "PracticalConsequence", "ConsequenceEvidence", "ConsequenceReport",
        "TrustAuditEntry", "TrustAuditTrail", "FinalPracticalConsequenceAnalyzer",
        "FinalPracticalConsequenceWitness", "FinalPracticalConsequenceCoordinator",
        "enumerate_practical_consequences", "run_consequence_analysis",
        "validate_trust_audit_trail",
    ]
except Exception:
    pass


# ---------------------------------------------------------------------------
# Module-level helpers (private)
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Uses ``time.gmtime`` rather than ``datetime`` to remain compatible with
    restricted execution environments.  The returned string is in the format
    ``YYYY-MM-DDTHH:MM:SSZ``.

    Returns
    -------
    str
        ISO-8601 UTC timestamp.
    """
    t = time.gmtime()
    return (
        f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        f"T{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z"
    )


def _uid() -> str:
    """Generate a compact unique identifier (16 hex chars, 64-bit random).

    Returns
    -------
    str
        A 16-character hexadecimal string.
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Raises ``ValueError`` when *lo* > *hi* to prevent silent logic errors.

    Parameters
    ----------
    value:
        The value to constrain.
    lo:
        Inclusive lower bound.
    hi:
        Inclusive upper bound.

    Returns
    -------
    float
        The clamped value.
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo} > hi={hi}")
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Level ordinal mapping (used by helpers when MaturityLevel enum is absent)
# ---------------------------------------------------------------------------

_LEVEL_ORDER: list = [
    "PROTOTYPE",
    "OPERATIONAL",
    "FEDERATED",
    "SELF_IMPROVING",
    "MATURE",
]

_LEVEL_INDEX: dict = {lvl: i for i, lvl in enumerate(_LEVEL_ORDER)}


def _normalise_level(level: Any) -> str:
    """Normalise a level argument to an uppercase string.

    Accepts ``MaturityLevel`` enum instances, plain strings, and any object
    with a ``.value`` attribute (such as other str-Enum instances).  Returns
    the uppercase string name of the level.  Falls back to ``"PROTOTYPE"`` if
    the input cannot be recognised.

    Parameters
    ----------
    level:
        A maturity level as enum, string, or any compatible type.

    Returns
    -------
    str
        Uppercase level name, one of ``_LEVEL_ORDER``.
    """
    if hasattr(level, "value"):
        raw = str(level.value)
    elif isinstance(level, str):
        raw = level
    else:
        raw = str(level)
    raw = raw.upper()
    if raw in _LEVEL_INDEX:
        return raw
    return "PROTOTYPE"


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def describe_maturity_level(level: Any) -> str:
    """Return a detailed multi-paragraph description of the given maturity level.

    This function is the primary documentation utility for the maturity model.
    It accepts a maturity level in any of the supported forms (``MaturityLevel``
    enum, string, or any object with a ``.value`` attribute) and returns a
    multi-paragraph string describing what the level means, what criteria a
    system must satisfy to be at that level, and what is required to advance
    to the next level.

    The returned text is intended for display in dashboards, help systems,
    and generated documentation.  It is formatted as plain text with
    paragraph breaks (double newline) between sections.

    Parameters
    ----------
    level:
        The maturity level to describe.  Accepted types:
        - ``MaturityLevel`` enum value
        - Uppercase string such as ``"OPERATIONAL"``
        - Any object whose ``.value`` attribute is one of the level names

    Returns
    -------
    str
        A multi-paragraph textual description of the maturity level including
        its characteristics and advancement criteria.  If the level is not
        recognised, a generic fallback description is returned.

    Examples
    --------
    .. code-block:: python

        from jugeo.maturity import describe_maturity_level
        print(describe_maturity_level("OPERATIONAL"))
    """
    lvl = _normalise_level(level)

    descriptions = {
        "PROTOTYPE": (
            "PROTOTYPE — Early development phase.\n\n"
            "A PROTOTYPE system has been created within the JuGeo framework but has not yet "
            "satisfied the minimum criteria for operational use.  The core capability set is "
            "defined but may not be fully implemented.  The improvement engine is dormant, "
            "federation infrastructure is absent, and the deployment is typically confined to "
            "a developer workstation or scratch environment.\n\n"
            "Advancement criteria: all declared capabilities must be implemented and tested; "
            "basic monitoring must be active; the system must pass at least one simulated "
            "failure injection test.  See theory2.tex Ch65, §65.1 for the formal criteria."
        ),
        "OPERATIONAL": (
            "OPERATIONAL — Controlled production deployment.\n\n"
            "An OPERATIONAL system has been validated for use in a controlled production "
            "environment.  The core capability set is fully implemented and tested; the "
            "improvement engine is active and accumulating metric data; and basic monitoring "
            "and alerting are in place.  The system must maintain 99% uptime over any rolling "
            "24-hour window.\n\n"
            "The system must complete at least five successful improvement cycles before "
            "advancing to FEDERATED.  All improvement proposals require human-operator review "
            "before application at this level.  See theory2.tex Ch65, §65.1."
        ),
        "FEDERATED": (
            "FEDERATED — Active participation in a peer federation.\n\n"
            "A FEDERATED system has joined an active federation of two or more peer nodes and "
            "participates in the JuGeo consensus protocol with quorum threshold τ.  All local "
            "system invariants are preserved across federation operations (Theorem 65.5: "
            "FederatedDeploymentSafety).  The shared state converges within a bounded number "
            "of rounds (Theorem 65.4: FederationConsistency).\n\n"
            "This is the first production-ready maturity level.  Systems at FEDERATED or "
            "above may serve real user traffic with full JuGeo reliability guarantees.  "
            "Advancement to SELF_IMPROVING requires demonstrating sustained federation "
            "stability and passing the automated self-improvement safety checks."
        ),
        "SELF_IMPROVING": (
            "SELF_IMPROVING — Autonomous improvement cycles without human review.\n\n"
            "A SELF_IMPROVING system has been granted sufficient trust and capability to apply "
            "improvement proposals autonomously, subject to a configurable risk threshold.  "
            "Every automated cycle is validated against SelfImprovementSoundness (Theorem "
            "65.2) before application, and rollback is automatic if a regression is detected.\n\n"
            "This level is appropriate for high-stakes deployments that require continuous "
            "improvement without operational overhead.  The system maintains a full audit "
            "trail of all automated cycles.  Advancement to MATURE requires 90 days of "
            "sustained self-improvement without regressions."
        ),
        "MATURE": (
            "MATURE — Apex of the maturity lattice; fully autonomous and self-certified.\n\n"
            "A MATURE system has demonstrated sustained, autonomous self-improvement across "
            "multiple federated nodes over an extended period without capability regressions, "
            "consensus failures, or safety violations.  The system can act as a trust anchor "
            "for newly onboarded PROTOTYPE systems and contributes improvement cycle data to "
            "the JuGeo research corpus.\n\n"
            "MATURE is not a terminal state: improvement cycles continue indefinitely, "
            "deepening and refining the system's capabilities.  The 'cyclic picture' of these "
            "ongoing cycles forms the directed graph CP(S) whose completeness is guaranteed by "
            "Theorem 65.3 (CyclicPictureCompleteness).  See theory2.tex Ch65, §65.1–65.5."
        ),
    }

    return descriptions.get(
        lvl,
        f"Maturity level '{lvl}' is not recognised.  Valid levels are: "
        + ", ".join(_LEVEL_ORDER),
    )


def is_production_ready(system_or_level: Any) -> bool:
    """Return ``True`` if the system or level qualifies as production-ready.

    A maturity level is considered production-ready if it is ``FEDERATED``,
    ``SELF_IMPROVING``, or ``MATURE``.  ``PROTOTYPE`` and ``OPERATIONAL``
    systems are not production-ready because they lack federation safety
    guarantees or have not yet passed the federation onboarding process.

    The function accepts either a ``MatureSystem`` object (in which case its
    ``maturity_level`` attribute is extracted) or a level value directly (as
    a string, ``MaturityLevel`` enum, or any compatible type).

    Production-readiness criteria (per Ch65):
    - The system must have completed federation onboarding (FEDERATED+).
    - All local invariants must be certified by the schema validator.
    - The evidence chain must be non-empty and validated.
    - The consensus quorum must be active and healthy.

    Parameters
    ----------
    system_or_level:
        Either a ``MatureSystem`` instance with a ``maturity_level``
        attribute, or a maturity level value in any supported form.

    Returns
    -------
    bool
        ``True`` if the level is ``FEDERATED``, ``SELF_IMPROVING``, or
        ``MATURE``; ``False`` otherwise.

    Examples
    --------
    .. code-block:: python

        from jugeo.maturity import is_production_ready
        assert not is_production_ready("PROTOTYPE")
        assert not is_production_ready("OPERATIONAL")
        assert is_production_ready("FEDERATED")
        assert is_production_ready("SELF_IMPROVING")
        assert is_production_ready("MATURE")
    """
    # Extract level from a MatureSystem if needed
    raw_level = (
        getattr(system_or_level, "maturity_level", None)
        or system_or_level
    )
    lvl = _normalise_level(raw_level)
    return lvl in {"FEDERATED", "SELF_IMPROVING", "MATURE"}


def maturity_progression_path(from_level: Any, to_level: Any = None) -> list:
    """Return the ordered list of (level, description) steps to reach *to_level*.

    This function computes the advancement path through the maturity lattice
    starting from *from_level*.  If *to_level* is not provided, the path
    continues all the way to ``MATURE``.

    Each element of the returned list is a two-tuple:
    ``(level_name: str, step_description: str)`` where *level_name* is the
    name of the maturity level and *step_description* explains what is
    required to advance *from* that level to the next.

    The function uses the linearly ordered lattice
    ``PROTOTYPE → OPERATIONAL → FEDERATED → SELF_IMPROVING → MATURE`` and
    returns only the steps from *from_level* onwards (inclusive).  If
    *from_level* == *to_level* an empty list is returned.

    This function is useful for generating upgrade roadmaps in dashboards and
    progress reports.  It consults the formal advancement criteria defined in
    theory2.tex Ch65, §65.1.

    Parameters
    ----------
    from_level:
        The current maturity level.  Accepted in any supported form.
    to_level:
        The target maturity level.  Defaults to ``"MATURE"``.

    Returns
    -------
    list
        A list of ``(level_name, step_description)`` two-tuples describing
        each step on the path from *from_level* to *to_level* (exclusive of
        *from_level* itself, inclusive of *to_level*).  Returns an empty list
        if *from_level* >= *to_level*.

    Examples
    --------
    .. code-block:: python

        from jugeo.maturity import maturity_progression_path
        for level, description in maturity_progression_path("OPERATIONAL"):
            print(f"  → {level}: {description[:60]}...")
    """
    _step_descriptions = {
        "PROTOTYPE": (
            "OPERATIONAL",
            "Implement all declared capabilities; activate metric collectors; "
            "pass the operational readiness checklist including at least one "
            "failure injection test.  See Ch65 §65.1 operational criteria."
        ),
        "OPERATIONAL": (
            "FEDERATED",
            "Complete five successful improvement cycles; register node in the "
            "JuGeo peer directory; onboard into an active federation with "
            "quorum threshold τ ≥ 2/3; pass the FederatedDeploymentSafety "
            "pre-flight check (Theorem 65.5).  See Ch65 §65.4."
        ),
        "FEDERATED": (
            "SELF_IMPROVING",
            "Demonstrate 30 days of stable federation operation without consensus "
            "failures; pass the self-improvement safety pre-flight checks; configure "
            "the risk threshold for automated cycle application; obtain federation "
            "quorum approval for autonomous operation mode.  See Ch65 §65.2."
        ),
        "SELF_IMPROVING": (
            "MATURE",
            "Sustain 90 days of autonomous self-improvement without capability "
            "regressions, consensus failures, or safety violations; pass the "
            "maturity assessor certification; contribute improvement cycle data "
            "to the JuGeo research corpus.  See Ch65 §65.1."
        ),
        "MATURE": (
            None,
            "MATURE is the apex of the maturity lattice.  Continue running "
            "improvement cycles to deepen capabilities.  Consider acting as "
            "a trust anchor for newly onboarded PROTOTYPE systems."
        ),
    }

    from_norm = _normalise_level(from_level)
    to_norm = _normalise_level(to_level) if to_level is not None else "MATURE"

    from_idx = _LEVEL_INDEX.get(from_norm, 0)
    to_idx = _LEVEL_INDEX.get(to_norm, len(_LEVEL_ORDER) - 1)

    if from_idx >= to_idx:
        return []

    path = []
    for i in range(from_idx, to_idx):
        current = _LEVEL_ORDER[i]
        next_level, description = _step_descriptions.get(
            current, (None, "No advancement path defined.")
        )
        if next_level is None:
            break
        path.append((next_level, description))
    return path



# --- auto-registered submodules ---
try:
    from . import cyclic_picture
except Exception:
    pass
