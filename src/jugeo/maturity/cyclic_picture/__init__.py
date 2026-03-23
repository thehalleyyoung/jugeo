"""JuGeo maturity/cyclic_picture sub-package — the cyclic picture functor and pipeline.

copilot: shared-core marker
Theory reference: theory2.tex Ch65

Overview
========

The ``jugeo.maturity.cyclic_picture`` sub-package is the implementation heart
of JuGeo's maturity model.  It provides the data models, algorithms, manifests,
formal theorems, integration bridges, and three pipeline stage modules that
together implement the cyclic picture functor CP: Sys → Mat.

This ``__init__.py`` re-exports the most commonly used symbols from all
sub-modules and provides three top-level convenience functions:
``quick_maturity_report``, ``run_full_cycle``, and ``describe_cycle``.

Mathematical Structure of the Cyclic Picture Functor
=====================================================

The cyclic picture functor CP is a functor from the category Sys of
well-typed JuGeo systems to the category Mat of maturity diagrams.  Concretely:

* **Objects**: Each system S ∈ Sys is mapped to a directed graph CP(S) whose
  nodes are the five maturity levels {PROTOTYPE, OPERATIONAL, FEDERATED,
  SELF_IMPROVING, MATURE} and whose edges are the improvement cycles
  applicable to S.

* **Morphisms**: A system morphism f: S → S' (an improvement cycle
  application) is mapped to a graph morphism CP(f): CP(S) → CP(S') that
  is an edge addition (or identity if the cycle does not advance the level).

* **Functoriality**: CP preserves composition and identities:
  - CP(id_S) = id_{CP(S)}
  - CP(g ∘ f) = CP(g) ∘ CP(f)

  These properties are enforced by the cycle validation logic in the
  ``models`` module and proved in Ch65, §65.3.

The Directed Cycle in the Maturity Lattice
==========================================

Although the maturity lattice M is a linear order
(PROTOTYPE ≤ OPERATIONAL ≤ FEDERATED ≤ SELF_IMPROVING ≤ MATURE),
the *cyclic picture* adds feedback edges that model the ongoing improvement
activity of a MATURE system.  Specifically, a MATURE system continues to
run improvement cycles that loop back through the lattice in the following
sense:

1. Each improvement cycle c at level L is an edge L → L in the maturity
   diagram, representing an improvement within the same level.
2. A *level transition cycle* is a special cycle c at level L such that
   applying c advances the system to level L+1.  This is an edge L → L+1.
3. After reaching MATURE, the system runs improvement cycles that are
   all self-loops at MATURE (edges MATURE → MATURE).

The collection of all edges in CP(S) forms a directed cycle:
   PROTOTYPE → OPERATIONAL → FEDERATED → SELF_IMPROVING → MATURE
        ↑                                                     |
        └─────────────── (implicit research feedback) ───────┘

The "research feedback" edge represents the flow of improvement data from
MATURE systems back into the JuGeo research corpus, which in turn influences
the improvement proposal generator for PROTOTYPE systems.  It is not a
runtime edge but a meta-level feedback loop.

The Three Pipeline Stages
=========================

The cyclic picture is implemented as a three-stage pipeline:

Stage S01 — Self-Improving System (``self_improving_system``)
-----------------------------------------------------------------

This stage models a single self-improving node operating in isolation.
It implements the improvement loop: metric collection, proposal generation,
proposal filtering, cycle application, and validation.  The output of S01 is
an ``ImprovementCycle`` record describing the completed cycle.

Key theorem: SelfImprovementSoundness (Ch65, §65.2) — every cycle applied
by S01 preserves the core capability set.

Stage S02 — Federated Deployment (``federated_deployment``)
---------------------------------------------------------------

This stage extends S01 to the federated setting.  It manages the federation
topology (the graph G = (V, E) of peer nodes), runs the consensus protocol,
and coordinates improvement cycles across all active nodes.  The output of
S02 is a ``FederationState`` record describing the current federation status.

Key theorems: FederationConsistency (Ch65, §65.4) and FederatedDeploymentSafety
(Ch65, §65.5).

Stage S03 — Mature Pipeline (``mature_pipeline``)
-----------------------------------------------------

This stage implements the full mature pipeline, combining S01 and S02 with
the maturity assessor that certifies level transitions.  It produces a
``MaturityReport`` and a ``MatureManifest`` as its primary outputs.

Key theorem: MaturityConvergence (Ch65, §65.1) — the system advances
monotonically through the lattice under the S03 protocol.

Module Structure
================

The sub-package consists of the following modules:

``models``
    Core data models: ``MaturityLevel``, ``ImprovementKind``, ``FederationRole``,
    ``DeploymentStatus``, ``ImprovementCycle``, ``FederationState``,
    ``MaturityReport``, ``MatureManifest``, ``MatureSystem``,
    ``SelfImprovingEngine``, ``FederatedDeployment``, ``MaturePipeline``.

``manifest``
    Manifest types and builders: ``ManifestStatus``, ``CyclicPictureManifest``,
    ``MaturityManifestBuilder``, ``build_maturity_manifest``,
    ``load_manifest_from_dict``, ``merge_manifests``, ``compare_manifests``.

``algorithms``
    Mathematical algorithms over the maturity model: ``MaturityAlgorithms``,
    ``estimate_improvement_gain``, ``rank_improvement_opportunities``,
    ``compute_federation_health``, ``score_maturity_level``,
    ``interpolate_maturity_path``, ``aggregate_improvement_gains``.

``integration``
    Cross-subsystem integration adapters connecting the maturity model to
    the evidence, orchestration, ideation, and geometry subsystems.

``theorems``
    Formal theorem registry: ``TheoremStatus``, ``MaturityTheorem``,
    ``MaturityTheoremRegistry``, ``build_maturity_theorem_registry``.

``self_improving_system``
    Stage S01 of the cyclic picture pipeline.

``federated_deployment``
    Stage S02 of the cyclic picture pipeline.

``mature_pipeline``
    Stage S03 of the cyclic picture pipeline.

Usage Examples
==============

.. code-block:: python

    from jugeo.maturity.cyclic_picture import (
        quick_maturity_report,
        run_full_cycle,
        describe_cycle,
    )

    # Generate a quick report without a full MatureSystem
    report = quick_maturity_report("my-system-id", "OPERATIONAL", num_cycles=3)
    print(report)

    # Run a full improvement cycle on an existing system object
    result = run_full_cycle(my_system, strategy="aggressive")
    print(result)

    # Get a human-readable description of a cycle object or dict
    cycle_dict = {"kind": "ALGORITHMIC", "gain": 0.15, "timestamp": "..."}
    print(describe_cycle(cycle_dict))
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

__all__ = [
    "quick_maturity_report",
    "run_full_cycle",
    "describe_cycle",
    # models
    "MaturityLevel", "ImprovementKind", "FederationRole", "DeploymentStatus",
    "ImprovementCycle", "FederationState", "MaturityReport", "MatureManifest",
    "MatureSystem", "SelfImprovingEngine", "FederatedDeployment", "MaturePipeline",
    # manifest
    "ManifestStatus", "CyclicPictureManifest", "MaturityManifestBuilder",
    "build_maturity_manifest",
    # algorithms
    "MaturityAlgorithms", "estimate_improvement_gain", "score_maturity_level",
    # theorems
    "TheoremStatus", "MaturityTheorem", "MaturityTheoremRegistry",
    "build_maturity_theorem_registry",
    # s01 — cyclic, not pipeline-linear
    "CyclePhase", "CycleRecord", "CycleTransition", "CycleMetrics", "CycleObstruction",
    "CyclicSystemAnalyzer", "CyclicSystemWitness", "CyclicSystemCoordinator",
    "run_cycle", "analyze_system_cyclicity", "build_cycle_witness",
    # s02 — ideation to orchestration to proof
    "IdeationRecord", "OrchestrationPlan", "ProofRecord", "FeedbackSignal",
    "IdeationCycleRecord", "IdeationToOrchestrationAnalyzer",
    "IdeationToOrchestrationWitness", "IdeationToOrchestrationCoordinator",
    "run_ideation_cycle", "assess_cycle_health", "extract_ideation_patterns",
    # s03 — beyond JuGeo
    "DomainProfile", "TransferAnalysis", "ImpactEstimate", "BeyondJuGeoReport",
    "BeyondJuGeoAnalyzer", "BeyondJuGeoWitness", "BeyondJuGeoCoordinator",
    "analyze_generalizability", "score_domain_fit", "list_candidate_domains",
    # s04 — final practical consequence
    "PracticalConsequence", "ConsequenceEvidence", "ConsequenceReport",
    "TrustAuditEntry", "TrustAuditTrail", "FinalPracticalConsequenceAnalyzer",
    "FinalPracticalConsequenceWitness", "FinalPracticalConsequenceCoordinator",
    "enumerate_practical_consequences", "run_consequence_analysis",
    "validate_trust_audit_trail",
    # cross-reference maturity functions
    "maturity_from_evidence",
    "maturity_from_descent",
    "maturity_from_certificates",
    "orchestration_maturity",
]

# ---------------------------------------------------------------------------
# Guarded sub-module imports
# ---------------------------------------------------------------------------

try:
    from jugeo.maturity.cyclic_picture.models import (
        MaturityLevel, ImprovementKind, FederationRole, DeploymentStatus,
        ImprovementCycle, FederationState, MaturityReport, MatureManifest,
        MatureSystem, SelfImprovingEngine, FederatedDeployment, MaturePipeline,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.manifest import (
        ManifestStatus, CyclicPictureManifest, MaturityManifestBuilder,
        build_maturity_manifest, load_manifest_from_dict, merge_manifests,
        compare_manifests,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.algorithms import (
        MaturityAlgorithms, estimate_improvement_gain,
        rank_improvement_opportunities, compute_federation_health,
        score_maturity_level, interpolate_maturity_path,
        aggregate_improvement_gains,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.integration import (
        MaturityEvidenceIntegrator, MaturityOrchestratorBridge,
        MaturityIdeationConnector, MaturityGeometryMapper,
        MaturityIntegrationFacade, integrate_maturity_evidence,
        connect_to_orchestrator, propose_ideation_improvements,
        map_to_geometry,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.theorems import (
        TheoremStatus, MaturityTheorem, MaturityTheoremRegistry,
        build_maturity_theorem_registry,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.self_improving_system import (
        run_s01_stage,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.federated_deployment import (
        run_s02_stage,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.mature_pipeline import (
        run_s03_stage,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.the_system_should_be_cyclic_not_pi import (
        CyclePhase, CycleRecord, CycleTransition, CycleMetrics, CycleObstruction,
        CyclicSystemAnalyzer, CyclicSystemWitness, CyclicSystemCoordinator,
        run_cycle, analyze_system_cyclicity, build_cycle_witness,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.from_ideation_to_orchestration_to import (
        IdeationRecord, OrchestrationPlan, ProofRecord, FeedbackSignal,
        IdeationCycleRecord, IdeationToOrchestrationAnalyzer,
        IdeationToOrchestrationWitness, IdeationToOrchestrationCoordinator,
        run_ideation_cycle, assess_cycle_health, extract_ideation_patterns,
    )
except Exception:
    pass

try:
    from jugeo.maturity.cyclic_picture.why_this_could_matter_beyond_jugeo import (
        DomainProfile, TransferAnalysis, ImpactEstimate, BeyondJuGeoReport,
        BeyondJuGeoAnalyzer, BeyondJuGeoWitness, BeyondJuGeoCoordinator,
        analyze_generalizability, score_domain_fit, list_candidate_domains,
    )
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
except Exception:
    pass


# ---------------------------------------------------------------------------
# Module-level helpers (private)
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string (YYYY-MM-DDTHH:MM:SSZ).

    Uses ``time.gmtime`` to avoid importing the ``datetime`` module.

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
    """Generate a compact unique identifier (16 hex chars).

    Returns
    -------
    str
        A 16-character hexadecimal string derived from a UUID4.
    """
    return uuid.uuid4().hex[:16]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to [*lo*, *hi*], raising ``ValueError`` if lo > hi.

    Parameters
    ----------
    value : float
    lo : float
    hi : float

    Returns
    -------
    float
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo={lo} > hi={hi}")
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Public convenience functions
# ---------------------------------------------------------------------------


def quick_maturity_report(
    system_id: str,
    level: Any,
    num_cycles: int = 0,
) -> dict:
    """Create a lightweight maturity report dict without a full MatureSystem.

    This function is designed for use in scripts and dashboards that need a
    quick snapshot of a system's maturity state without constructing the full
    object graph.  It tries to use ``MaturityReport.create()`` if the models
    sub-module is available, and falls back to building a plain dict if not.

    The returned dict always contains the following keys:

    * ``system_id``: the identifier passed by the caller.
    * ``level``: the normalised maturity level name (uppercase string).
    * ``cycle_count``: the number of completed improvement cycles.
    * ``timestamp``: the UTC creation time.
    * ``summary``: a one-line human-readable summary of the report.
    * ``production_ready``: ``True`` if the level is FEDERATED, SELF_IMPROVING,
      or MATURE.

    The function intentionally does not perform any I/O, metric collection, or
    subsystem calls.  For a full integration pass, use
    ``MaturityIntegrationFacade.run_full_integration()`` instead.

    Parameters
    ----------
    system_id:
        A string identifier for the system being reported on.
    level:
        The maturity level of the system.  Accepted in any supported form
        (``MaturityLevel`` enum, uppercase string, etc.).
    num_cycles:
        The number of completed improvement cycles.  Defaults to 0.

    Returns
    -------
    dict
        A lightweight report dictionary.

    Examples
    --------
    .. code-block:: python

        from jugeo.maturity.cyclic_picture import quick_maturity_report
        report = quick_maturity_report("svc-auth-001", "FEDERATED", num_cycles=12)
        print(report["summary"])
    """
    level_str = (
        level.value if hasattr(level, "value") else str(level).upper()
    )
    _production_levels = {"FEDERATED", "SELF_IMPROVING", "MATURE"}
    production_ready = level_str in _production_levels

    summary = (
        f"System {system_id!r} at level {level_str} "
        f"({num_cycles} cycle{'s' if num_cycles != 1 else ''} completed)"
        + (" — PRODUCTION READY" if production_ready else " — not production ready")
    )

    # Try to use the real MaturityReport if available
    try:
        real_report = MaturityReport.create(  # noqa: F821
            system_id=system_id,
            maturity_level=level,
            cycle_count=num_cycles,
        )
        d = real_report.to_dict() if hasattr(real_report, "to_dict") else {}
        d.setdefault("summary", summary)
        d.setdefault("production_ready", production_ready)
        return d
    except Exception:
        pass

    return {
        "system_id": system_id,
        "level": level_str,
        "cycle_count": num_cycles,
        "timestamp": _utcnow(),
        "summary": summary,
        "production_ready": production_ready,
        "report_id": _uid(),
    }


def run_full_cycle(system: Any, strategy: str = "default") -> dict:
    """Run one complete improvement cycle through all three pipeline stages.

    This function orchestrates a full cyclic picture pass for *system* by
    invoking stages S01, S02, and S03 in sequence.  Each stage is called via
    its ``run_sXX_stage()`` function (imported with a guarded import so that
    stages not yet implemented degrade gracefully).

    The result dictionary contains the outputs of each stage under keys
    ``"s01"``, ``"s02"``, and ``"s03"``, plus top-level metadata.

    The *strategy* parameter hints to each stage which improvement strategy
    to use.  Recognised values are:
    * ``"default"``: use the standard improvement strategy.
    * ``"aggressive"``: prefer high-gain proposals even at higher risk.
    * ``"conservative"``: prefer low-risk proposals even at lower gain.

    If a stage is unavailable (not yet implemented or import failed) its
    result key will contain ``{"status": "unavailable"}``.

    Parameters
    ----------
    system:
        The system to run the cycle on.  Must be compatible with the
        models sub-module's ``MatureSystem`` type (or any dict-like object
        with a ``system_id`` key).
    strategy:
        The improvement strategy hint.  Defaults to ``"default"``.

    Returns
    -------
    dict
        A combined cycle result dict with keys ``cycle_id``, ``timestamp``,
        ``strategy``, ``s01``, ``s02``, ``s03``, and ``system_id``.

    Examples
    --------
    .. code-block:: python

        from jugeo.maturity.cyclic_picture import run_full_cycle
        result = run_full_cycle(my_system, strategy="conservative")
        print(result["s01"])
    """
    cycle_id = _uid()
    system_id = (
        getattr(system, "system_id", None)
        or (system.get("system_id") if isinstance(system, dict) else None)
        or str(system)[:32]
    )
    result: dict = {
        "cycle_id": cycle_id,
        "timestamp": _utcnow(),
        "strategy": strategy,
        "system_id": system_id,
    }

    # Stage S01: self-improving system
    try:
        result["s01"] = run_s01_stage(system, strategy=strategy)  # noqa: F821
    except Exception as exc:
        result["s01"] = {"status": "unavailable", "error": str(exc)}

    # Stage S02: federated deployment
    try:
        result["s02"] = run_s02_stage(system, strategy=strategy)  # noqa: F821
    except Exception as exc:
        result["s02"] = {"status": "unavailable", "error": str(exc)}

    # Stage S03: mature pipeline
    try:
        result["s03"] = run_s03_stage(system, strategy=strategy)  # noqa: F821
    except Exception as exc:
        result["s03"] = {"status": "unavailable", "error": str(exc)}

    return result


def describe_cycle(cycle_or_dict: Any) -> str:
    """Return a human-readable description of an improvement cycle.

    This function accepts either an ``ImprovementCycle`` object or a plain
    dict and produces a concise one-paragraph description of the cycle
    including its kind, gain, status, and timestamp.

    The function is intentionally tolerant of partial or missing fields:
    any field that is absent or ``None`` is replaced with a placeholder
    string so that the description is always complete and readable.

    The description format is:

        ``Improvement cycle <id> | Kind: <kind> | Gain: <gain:.2%> |
        Status: <status> | Applied at: <timestamp>``

    For ``ImprovementCycle`` objects the ``to_dict()`` method is called
    first to obtain a normalised dict representation.

    Parameters
    ----------
    cycle_or_dict:
        An ``ImprovementCycle`` instance or a plain dict describing a cycle.
        Any object with a ``to_dict()`` method or dict-like access is
        accepted.

    Returns
    -------
    str
        A human-readable single-paragraph description of the cycle.

    Examples
    --------
    .. code-block:: python

        from jugeo.maturity.cyclic_picture import describe_cycle
        print(describe_cycle({"kind": "ALGORITHMIC", "gain": 0.12}))
    """
    if hasattr(cycle_or_dict, "to_dict"):
        d = cycle_or_dict.to_dict()
    elif isinstance(cycle_or_dict, dict):
        d = cycle_or_dict
    else:
        d = {}

    cid = d.get("cycle_id") or d.get("id") or "(unknown)"
    kind = d.get("kind") or d.get("improvement_kind") or "UNKNOWN"
    if hasattr(kind, "value"):
        kind = kind.value
    gain = d.get("gain") or d.get("improvement_gain") or 0.0
    try:
        gain_pct = f"{float(gain):.2%}"
    except (TypeError, ValueError):
        gain_pct = str(gain)
    status = d.get("status") or "unknown"
    if hasattr(status, "value"):
        status = status.value
    ts = d.get("timestamp") or d.get("applied_at") or _utcnow()

    return (
        f"Improvement cycle {cid} | Kind: {kind} | Gain: {gain_pct} | "
        f"Status: {status} | Applied at: {ts}"
    )


# ---------------------------------------------------------------------------
# Cross-reference maturity functions
# ---------------------------------------------------------------------------


def maturity_from_evidence(manifest: Any = None) -> dict:
    """Assess maturity based on evidence coverage from the evidence subsystem.

    Inspects a ``Manifest`` from ``jugeo.evidence.manifests`` and derives a
    maturity level based on the completeness of the judgment store, obligation
    store, evidence archive, and certificate store.

    Args:
        manifest: A ``Manifest`` object from ``jugeo.evidence.manifests``.
            If *None*, returns ``PROTOTYPE`` level.

    Returns:
        A dict with keys ``"maturity_level"``, ``"judgment_count"``,
        ``"obligation_count"``, ``"evidence_count"``,
        ``"certificate_count"``, and ``"rationale"``.
    """
    try:
        from jugeo.evidence.manifests import Manifest
    except ImportError:
        Manifest = None  # type: ignore[assignment,misc]

    if manifest is None:
        return {
            "maturity_level": "PROTOTYPE",
            "judgment_count": 0,
            "obligation_count": 0,
            "evidence_count": 0,
            "certificate_count": 0,
            "rationale": "No evidence manifest provided.",
        }

    j_store = getattr(manifest, "judgment_store", None)
    j_count = len(getattr(j_store, "entries", [])) if j_store else 0
    o_store = getattr(manifest, "obligation_store", None)
    o_count = len(getattr(o_store, "entries", [])) if o_store else 0
    archive = getattr(manifest, "evidence_archive", None)
    a_count = len(getattr(archive, "entries", [])) if archive else 0
    cert_store = getattr(manifest, "certificate_store", None)
    c_count = len(getattr(cert_store, "entries", [])) if cert_store else 0

    score = 0
    if j_count > 0:
        score += 1
    if a_count > 0:
        score += 1
    if c_count > 0:
        score += 1
    if o_count == 0 and j_count > 0:
        score += 1

    levels = ["PROTOTYPE", "OPERATIONAL", "FEDERATED", "SELF_IMPROVING", "MATURE"]
    level = levels[min(score, len(levels) - 1)]

    return {
        "maturity_level": level,
        "judgment_count": j_count,
        "obligation_count": o_count,
        "evidence_count": a_count,
        "certificate_count": c_count,
        "rationale": f"Evidence score {score}/4 → {level}",
    }


def maturity_from_descent(descent_results: list | None = None) -> dict:
    """Assess maturity based on descent success rate from the geometry subsystem.

    Examines a collection of ``DescentResult`` objects from
    ``jugeo.geometry.descent`` and computes a maturity level based on the
    fraction of successful gluings (i.e. results with a non-null global
    section and no unresolved obstructions).

    Args:
        descent_results: Optional list of ``DescentResult`` objects.  If
            *None*, returns ``PROTOTYPE`` level.

    Returns:
        A dict with keys ``"maturity_level"``, ``"total_descents"``,
        ``"successful"``, ``"success_rate"``, and ``"rationale"``.
    """
    try:
        from jugeo.geometry.descent import DescentResult
    except ImportError:
        DescentResult = None  # type: ignore[assignment,misc]

    descent_results = descent_results or []
    if not descent_results:
        return {
            "maturity_level": "PROTOTYPE",
            "total_descents": 0,
            "successful": 0,
            "success_rate": 0.0,
            "rationale": "No descent results provided.",
        }

    successful = 0
    for dr in descent_results:
        has_global = getattr(dr, "global_section", None) is not None
        obstructions = getattr(dr, "obstructions", []) or []
        if has_global and len(obstructions) == 0:
            successful += 1

    total = len(descent_results)
    rate = successful / max(total, 1)

    if rate >= 0.95:
        level = "MATURE"
    elif rate >= 0.80:
        level = "SELF_IMPROVING"
    elif rate >= 0.60:
        level = "FEDERATED"
    elif rate >= 0.30:
        level = "OPERATIONAL"
    else:
        level = "PROTOTYPE"

    return {
        "maturity_level": level,
        "total_descents": total,
        "successful": successful,
        "success_rate": rate,
        "rationale": f"Success rate {rate:.0%} → {level}",
    }


def maturity_from_certificates(certificates: list | None = None) -> dict:
    """Assess maturity based on certificate coverage from the evidence subsystem.

    Inspects ``Certificate`` objects from ``jugeo.evidence.certificates`` and
    derives a maturity level based on the count and verification status of
    certificates.

    Args:
        certificates: Optional list of ``Certificate`` objects.  If *None*,
            returns ``PROTOTYPE`` level.

    Returns:
        A dict with keys ``"maturity_level"``, ``"total_certificates"``,
        ``"verified"``, ``"verification_rate"``, and ``"rationale"``.
    """
    try:
        from jugeo.evidence.certificates import Certificate, CertificateStatus
    except ImportError:
        Certificate = None  # type: ignore[assignment,misc]
        CertificateStatus = None  # type: ignore[assignment,misc]

    certificates = certificates or []
    if not certificates:
        return {
            "maturity_level": "PROTOTYPE",
            "total_certificates": 0,
            "verified": 0,
            "verification_rate": 0.0,
            "rationale": "No certificates provided.",
        }

    verified = 0
    for cert in certificates:
        status = getattr(cert, "status", None)
        status_str = getattr(status, "value", str(status)) if status else ""
        if status_str in ("VERIFIED", "VALID", "SETTLED"):
            verified += 1

    total = len(certificates)
    rate = verified / max(total, 1)

    if rate >= 0.95:
        level = "MATURE"
    elif rate >= 0.80:
        level = "SELF_IMPROVING"
    elif rate >= 0.60:
        level = "FEDERATED"
    elif rate >= 0.30:
        level = "OPERATIONAL"
    else:
        level = "PROTOTYPE"

    return {
        "maturity_level": level,
        "total_certificates": total,
        "verified": verified,
        "verification_rate": rate,
        "rationale": f"Certificate verification rate {rate:.0%} → {level}",
    }


def orchestration_maturity(orchestrator: Any = None) -> dict:
    """Assess orchestration maturity from the orchestration subsystem.

    Checks the ``jugeo.orchestration`` package for key orchestration
    capabilities and derives a maturity level from the number of available
    features.

    Args:
        orchestrator: An optional orchestrator object from
            ``jugeo.orchestration``.  If *None*, the function probes the
            package for importability of key symbols.

    Returns:
        A dict with keys ``"maturity_level"``, ``"capabilities_found"``,
        ``"total_checked"``, and ``"rationale"``.
    """
    capabilities = [
        "jugeo.orchestration",
    ]
    found: list[str] = []
    for cap in capabilities:
        try:
            __import__(cap)
            found.append(cap)
        except ImportError:
            pass

    # Also check orchestrator object for capabilities
    orch_attrs = ["schedule", "run", "status", "cancel", "plan"]
    orch_found = 0
    if orchestrator is not None:
        for attr in orch_attrs:
            if hasattr(orchestrator, attr):
                orch_found += 1

    total_checked = len(capabilities) + len(orch_attrs)
    total_found = len(found) + orch_found
    score = total_found / max(total_checked, 1)

    if score >= 0.80:
        level = "MATURE"
    elif score >= 0.60:
        level = "SELF_IMPROVING"
    elif score >= 0.40:
        level = "FEDERATED"
    elif score >= 0.20:
        level = "OPERATIONAL"
    else:
        level = "PROTOTYPE"

    return {
        "maturity_level": level,
        "capabilities_found": found + [f"orchestrator.{a}" for a in orch_attrs if orchestrator and hasattr(orchestrator, a)],
        "total_checked": total_checked,
        "rationale": f"Orchestration capability score {score:.0%} → {level}",
    }



# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import federated_deployment
except Exception:
    pass
try:
    from . import from_ideation_to_orchestration_to
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
    from . import mature_pipeline
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import self_improving_system
except Exception:
    pass
try:
    from . import the_final_practical_consequence
except Exception:
    pass
try:
    from . import the_system_should_be_cyclic_not_pi
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import why_this_could_matter_beyond_jugeo
except Exception:
    pass
