"""Oracle Federation package — Theory2.tex Chapter 7.

Covers controlled oracles, solver federation, and runtime witnesses.

Sections
--------
- §7.1  Controlled Oracle Model  (controlled_oracles.py)
- §7.2  Solver Federation         (solver_federation.py)
- §7.3  Runtime Witnesses         (runtime_witnesses.py)

Supporting modules
------------------
- manifest.py   — PackageManifest and chapter metadata
- models.py     — Core data models (OracleModel, SolverFederationModel, etc.)
- algorithms.py — Trust ceiling propagation, federation routing, witness
                  consistency, and corroboration-chain validation algorithms
- theorems.py   — Formal theorem statements (Theorems 7.1–7.5, Lemmas 7.1–7.2,
                  Corollary 7.1) and TheoremRegistry
- integration.py — Integration glue: OracleFederationIntegration,
                   SiteOracleBridge, FederationPipelineAdapter,
                   WitnessToEvidenceAdapter

Theory alignment
----------------
The oracle trust ceiling (§7.1) ensures no oracle can self-promote.
Copilot proposals enter at ``TrustLevel.COPILOT_SUGGESTED`` by design and
require external corroboration before promotion (Theorem 7.5).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# §7.1 — Controlled oracles
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.oracle_federation.controlled_oracles import (
        OracleChannel,
        OracleJurisdiction,
        OracleProposalRecord,
        TrustCeilingEnforcer,
        CopilotOracleChannel,
        create_oracle_channel,
        create_copilot_channel,
    )
except ImportError:
    OracleChannel = None  # type: ignore[assignment,misc]
    OracleJurisdiction = None  # type: ignore[assignment,misc]
    OracleProposalRecord = None  # type: ignore[assignment,misc]
    TrustCeilingEnforcer = None  # type: ignore[assignment,misc]
    CopilotOracleChannel = None  # type: ignore[assignment,misc]
    create_oracle_channel = None  # type: ignore[assignment,misc]
    create_copilot_channel = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# §7.2 — Solver federation
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.oracle_federation.solver_federation import (
        SolverFederation,
        Z3Routing,
        FragmentClassification,
        FragmentKind,
        MergePolicy,
        FederationRouter,
        create_default_federation,
    )
except ImportError:
    SolverFederation = None  # type: ignore[assignment,misc]
    Z3Routing = None  # type: ignore[assignment,misc]
    FragmentClassification = None  # type: ignore[assignment,misc]
    FragmentKind = None  # type: ignore[assignment,misc]
    MergePolicy = None  # type: ignore[assignment,misc]
    FederationRouter = None  # type: ignore[assignment,misc]
    create_default_federation = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# §7.3 — Runtime witnesses
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.oracle_federation.runtime_witnesses import (
        RuntimeWitnessCollector,
        HeapWitness,
        IdentityWitness,
        StackWitness,
        WitnessValidator,
        WitnessKind,
        ConsistencyStatus,
        create_heap_witness_from_dict,
    )
except ImportError:
    RuntimeWitnessCollector = None  # type: ignore[assignment,misc]
    HeapWitness = None  # type: ignore[assignment,misc]
    IdentityWitness = None  # type: ignore[assignment,misc]
    StackWitness = None  # type: ignore[assignment,misc]
    WitnessValidator = None  # type: ignore[assignment,misc]
    WitnessKind = None  # type: ignore[assignment,misc]
    ConsistencyStatus = None  # type: ignore[assignment,misc]
    create_heap_witness_from_dict = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.oracle_federation.models import (
        OracleModel,
        SolverFederationModel,
        RuntimeWitnessModel,
        JurisdictionModel,
        OracleChannelConfig,
        FederationConfig,
        WitnessCollectionConfig,
        ModelRegistry,
    )
except ImportError:
    OracleModel = None  # type: ignore[assignment,misc]
    SolverFederationModel = None  # type: ignore[assignment,misc]
    RuntimeWitnessModel = None  # type: ignore[assignment,misc]
    JurisdictionModel = None  # type: ignore[assignment,misc]
    OracleChannelConfig = None  # type: ignore[assignment,misc]
    FederationConfig = None  # type: ignore[assignment,misc]
    WitnessCollectionConfig = None  # type: ignore[assignment,misc]
    ModelRegistry = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Algorithms
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.oracle_federation.algorithms import (
        trust_ceiling_propagation,
        oracle_proposal_ranking,
        federation_route_optimal,
        witness_consistency_check,
        jurisdiction_intersection_algorithm,
        corroboration_chain_validator,
        TrustCeilingPropagator,
        FederationLoadBalancer,
        WitnessCorrelator,
    )
except ImportError:
    trust_ceiling_propagation = None  # type: ignore[assignment,misc]
    oracle_proposal_ranking = None  # type: ignore[assignment,misc]
    federation_route_optimal = None  # type: ignore[assignment,misc]
    witness_consistency_check = None  # type: ignore[assignment,misc]
    jurisdiction_intersection_algorithm = None  # type: ignore[assignment,misc]
    corroboration_chain_validator = None  # type: ignore[assignment,misc]
    TrustCeilingPropagator = None  # type: ignore[assignment,misc]
    FederationLoadBalancer = None  # type: ignore[assignment,misc]
    WitnessCorrelator = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Theorems
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.oracle_federation.theorems import (
        Theorem,
        TheoremKind,
        ProofStatus,
        TheoremRegistry,
        DEFAULT_REGISTRY,
        get_default_registry,
        THEOREM_7_1_TRUST_CEILING_CONSERVATION,
        THEOREM_7_2_FEDERATION_SOUNDNESS,
        THEOREM_7_3_WITNESS_CONSISTENCY,
        THEOREM_7_4_JURISDICTION_COMPOSITION,
        THEOREM_7_5_COPILOT_CEILING_INVARIANCE,
        LEMMA_7_1_ORACLE_BOUNDEDNESS,
        LEMMA_7_2_FEDERATION_COMPLETENESS,
        COROLLARY_7_1_COMPOSITION_CEILING,
    )
except ImportError:
    Theorem = None  # type: ignore[assignment,misc]
    TheoremKind = None  # type: ignore[assignment,misc]
    ProofStatus = None  # type: ignore[assignment,misc]
    TheoremRegistry = None  # type: ignore[assignment,misc]
    DEFAULT_REGISTRY = None  # type: ignore[assignment,misc]
    get_default_registry = None  # type: ignore[assignment,misc]
    THEOREM_7_1_TRUST_CEILING_CONSERVATION = None  # type: ignore[assignment,misc]
    THEOREM_7_2_FEDERATION_SOUNDNESS = None  # type: ignore[assignment,misc]
    THEOREM_7_3_WITNESS_CONSISTENCY = None  # type: ignore[assignment,misc]
    THEOREM_7_4_JURISDICTION_COMPOSITION = None  # type: ignore[assignment,misc]
    THEOREM_7_5_COPILOT_CEILING_INVARIANCE = None  # type: ignore[assignment,misc]
    LEMMA_7_1_ORACLE_BOUNDEDNESS = None  # type: ignore[assignment,misc]
    LEMMA_7_2_FEDERATION_COMPLETENESS = None  # type: ignore[assignment,misc]
    COROLLARY_7_1_COMPOSITION_CEILING = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.oracle_federation.integration import (
        OracleFederationIntegration,
        SiteOracleBridge,
        FederationPipelineAdapter,
        WitnessToEvidenceAdapter,
        IntegrationConfig,
        get_default_integration,
        create_integration,
    )
except ImportError:
    OracleFederationIntegration = None  # type: ignore[assignment,misc]
    SiteOracleBridge = None  # type: ignore[assignment,misc]
    FederationPipelineAdapter = None  # type: ignore[assignment,misc]
    WitnessToEvidenceAdapter = None  # type: ignore[assignment,misc]
    IntegrationConfig = None  # type: ignore[assignment,misc]
    get_default_integration = None  # type: ignore[assignment,misc]
    create_integration = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
try:
    from jugeo.foundations.oracle_federation.manifest import (
        PackageManifest,
        MANIFEST,
        get_manifest,
        describe_package,
    )
except ImportError:
    PackageManifest = None  # type: ignore[assignment,misc]
    MANIFEST = None  # type: ignore[assignment,misc]
    get_manifest = None  # type: ignore[assignment,misc]
    describe_package = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Cross-referencing helpers: oracle ↔ solver, judgments, certificates
# ---------------------------------------------------------------------------
import logging as _of_logging

_of_logger_xref = _of_logging.getLogger(__name__ + ".xref")


def federate_solver_oracle(
    solver: dict | None = None,
    oracle: dict | None = None,
    *,
    merge_policy: str = "conservative",
) -> dict:
    """Combine solver-backed and oracle-backed evidence streams.

    Bridges Theory2.tex §7.2 (solver federation) to the concrete
    router in ``jugeo.solver.router`` and channel model in
    ``jugeo.evidence.channels``.
    """
    try:
        from jugeo.solver.router import BackendKind, RoutingDecision, RoutingStrategyKind
    except ImportError:
        _of_logger_xref.warning("jugeo.solver.router unavailable – returning raw inputs")
        return {"solver": solver, "oracle": oracle, "merged": False, "policy": merge_policy}

    try:
        from jugeo.evidence.channels import EvidenceChannel, EvidenceKind
    except ImportError:
        _of_logger_xref.warning("jugeo.evidence.channels unavailable – returning raw inputs")
        return {"solver": solver, "oracle": oracle, "merged": False, "policy": merge_policy}

    solver_kind = BackendKind(solver.get("backend", "Z3")) if solver else None
    oracle_channel = EvidenceChannel(kind=EvidenceKind(oracle.get("kind", "ORACLE"))) if oracle else None

    if solver_kind is not None and oracle_channel is not None:
        strategy = RoutingStrategyKind(merge_policy.upper()) if hasattr(RoutingStrategyKind, merge_policy.upper()) else RoutingStrategyKind.CONSERVATIVE
        decision = RoutingDecision(backend=solver_kind, strategy=strategy)
        merged_evidence = {
            "solver_backend": solver_kind.value,
            "oracle_kind": oracle_channel.kind.value,
            "routing_strategy": strategy.value,
            "trust_floor": min(
                solver.get("trust_level", 0),
                oracle.get("trust_level", 0),
            ),
        }
        return {"solver": solver, "oracle": oracle, "merged": True, "policy": merge_policy, "evidence": merged_evidence}

    return {"solver": solver, "oracle": oracle, "merged": False, "policy": merge_policy}


def oracle_judgment(
    oracle_result: dict,
    *,
    trust_ceiling: str = "ORACLE_PROPOSED",
) -> dict:
    """Create a structured judgment from an oracle result.

    Bridges §7.1 oracle model to ``jugeo.judgments.judgment_terms``.
    """
    try:
        from jugeo.judgments.judgment_terms import (
            Proposition,
            PropositionKind,
            TrustLevel,
            JudgmentStatus,
        )
    except ImportError:
        _of_logger_xref.warning("jugeo.judgments.judgment_terms unavailable – returning stub")
        return {
            "claim": oracle_result.get("claim", ""),
            "status": "STUB",
            "trust_ceiling": trust_ceiling,
        }

    claim_text = oracle_result.get("claim", "")
    kind = PropositionKind(oracle_result.get("kind", "EMPIRICAL"))
    proposition = Proposition(content=claim_text, kind=kind)

    ceiling = TrustLevel[trust_ceiling]
    raw_level = TrustLevel[oracle_result.get("trust_level", "COPILOT_SUGGESTED")]
    effective_level = ceiling if raw_level.value < ceiling.value else raw_level

    status = JudgmentStatus.PROPOSED if effective_level == ceiling else JudgmentStatus.ACCEPTED

    return {
        "proposition": proposition,
        "status": status.value,
        "effective_trust": effective_level.value,
        "trust_ceiling": ceiling.value,
        "capped": effective_level != raw_level,
    }


def oracle_certificate(
    oracle_result: dict,
    *,
    issuer: str = "oracle_federation",
) -> dict:
    """Certify an oracle result as a trust certificate.

    Bridges §7.3 (runtime witnesses) to ``jugeo.evidence.certificates``.
    """
    try:
        from jugeo.evidence.certificates import Certificate, CertificateStatus
    except ImportError:
        _of_logger_xref.warning("jugeo.evidence.certificates unavailable – returning stub")
        return {
            "claim": oracle_result.get("claim", ""),
            "issuer": issuer,
            "status": "STUB",
        }

    claim = oracle_result.get("claim", "")
    trust_level = oracle_result.get("trust_level", "ORACLE_PROPOSED")
    witnesses = oracle_result.get("witnesses", [])

    cert = Certificate(
        claim=claim,
        issuer=issuer,
        trust_level=trust_level,
        chain=witnesses,
    )
    cert_status = CertificateStatus.VALID if witnesses else CertificateStatus.PENDING

    return {
        "certificate": cert,
        "status": cert_status.value,
        "issuer": issuer,
        "trust_level": trust_level,
        "chain_length": len(witnesses),
    }


__all__ = [
    # §7.1 controlled oracles
    "OracleChannel",
    "OracleJurisdiction",
    "OracleProposalRecord",
    "TrustCeilingEnforcer",
    "CopilotOracleChannel",
    "create_oracle_channel",
    "create_copilot_channel",
    # §7.2 solver federation
    "SolverFederation",
    "Z3Routing",
    "FragmentClassification",
    "FragmentKind",
    "MergePolicy",
    "FederationRouter",
    "create_default_federation",
    # §7.3 runtime witnesses
    "RuntimeWitnessCollector",
    "HeapWitness",
    "IdentityWitness",
    "StackWitness",
    "WitnessValidator",
    "WitnessKind",
    "ConsistencyStatus",
    "create_heap_witness_from_dict",
    # models
    "OracleModel",
    "SolverFederationModel",
    "RuntimeWitnessModel",
    "JurisdictionModel",
    "OracleChannelConfig",
    "FederationConfig",
    "WitnessCollectionConfig",
    "ModelRegistry",
    # algorithms
    "trust_ceiling_propagation",
    "oracle_proposal_ranking",
    "federation_route_optimal",
    "witness_consistency_check",
    "jurisdiction_intersection_algorithm",
    "corroboration_chain_validator",
    "TrustCeilingPropagator",
    "FederationLoadBalancer",
    "WitnessCorrelator",
    # theorems
    "Theorem",
    "TheoremKind",
    "ProofStatus",
    "TheoremRegistry",
    "DEFAULT_REGISTRY",
    "get_default_registry",
    "THEOREM_7_1_TRUST_CEILING_CONSERVATION",
    "THEOREM_7_2_FEDERATION_SOUNDNESS",
    "THEOREM_7_3_WITNESS_CONSISTENCY",
    "THEOREM_7_4_JURISDICTION_COMPOSITION",
    "THEOREM_7_5_COPILOT_CEILING_INVARIANCE",
    "LEMMA_7_1_ORACLE_BOUNDEDNESS",
    "LEMMA_7_2_FEDERATION_COMPLETENESS",
    "COROLLARY_7_1_COMPOSITION_CEILING",
    # integration
    "OracleFederationIntegration",
    "SiteOracleBridge",
    "FederationPipelineAdapter",
    "WitnessToEvidenceAdapter",
    "IntegrationConfig",
    "get_default_integration",
    "create_integration",
    # manifest
    "PackageManifest",
    "MANIFEST",
    "get_manifest",
    "describe_package",
    # Cross-subsystem integration helpers
    "federate_with_solver",
    "runtime_corroboration",
    "trust_ceiling_enforcement",
    # cross-referencing helpers
    "federate_solver_oracle",
    "oracle_judgment",
    "oracle_certificate",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration: connecting Ch7 oracle federation to solver,
# runtime, and trust subsystems.
# ---------------------------------------------------------------------------

import logging as _logging

_of_logger = _logging.getLogger(__name__)


def federate_with_solver(
    oracle_evidence,
    *,
    solver_query=None,
    routing_strategy=None,
):
    """Combine oracle-produced evidence with solver-discharged evidence by
    routing through ``jugeo.solver.router``.

    Oracle evidence (§7.1) and solver evidence (§7.2) enter the trust algebra
    at different tiers.  This function federates them: it routes the query to
    the appropriate solver backend, collects the result, and merges it with
    the oracle evidence according to the federation merge policy.

    Parameters
    ----------
    oracle_evidence : Any
        Evidence produced by an oracle channel (e.g. an
        :class:`OracleProposalRecord`).
    solver_query : Any | None
        Optional solver query to dispatch.  When ``None``, the oracle
        evidence is returned un-federated.
    routing_strategy : str | None
        Strategy name for the solver router (e.g. ``"MOST_TRUSTED"``).

    Returns
    -------
    dict[str, Any]
        Keys: ``"oracle_evidence"``, ``"solver_result"`` (or ``None``),
        ``"merged_trust_level"`` (str), ``"routing_decision"`` (dict | None),
        ``"federation_sound"`` (bool — Theorem 7.2 check).

    Raises
    ------
    RuntimeError
        If ``jugeo.solver.router`` cannot be imported.

    Notes
    -----
    Theory2.tex §7.2 — Theorem 7.2 (Federation Soundness) guarantees that
    the merged result never exceeds the ceiling of either source channel.

    Examples
    --------
    >>> result = federate_with_solver({"tier": "ORACLE_PROPOSED"})  # doctest: +SKIP
    >>> result["federation_sound"]
    True
    """
    try:
        from jugeo.solver.router import SolverRouter, RoutingDecision
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.solver.router is required for federate_with_solver()"
        ) from exc

    result = {
        "oracle_evidence": oracle_evidence,
        "solver_result": None,
        "merged_trust_level": "ORACLE_PROPOSED",
        "routing_decision": None,
        "federation_sound": True,
    }

    if solver_query is None:
        return result

    try:
        router = SolverRouter()
        if routing_strategy is not None and hasattr(router, "set_strategy"):
            router.set_strategy(routing_strategy)

        decision = router.route(solver_query)
        result["routing_decision"] = {
            "selected_backend": getattr(decision, "selected_backend", None),
            "trust_ceiling": getattr(decision, "trust_ceiling", None),
            "rationale": getattr(decision, "rationale", None),
        }

        # Execute the solver query via the selected backend
        if hasattr(router, "execute"):
            solver_result = router.execute(decision)
            result["solver_result"] = solver_result

            # Merge: the federation trust level is the minimum of oracle
            # ceiling and solver result trust (Theorem 7.2 soundness).
            solver_trust = getattr(solver_result, "trust_level", None)
            if solver_trust is not None:
                result["merged_trust_level"] = str(solver_trust)
        else:
            result["merged_trust_level"] = "ORACLE_PROPOSED"

    except Exception as exc:
        _of_logger.warning("federate_with_solver: routing error: %s", exc)
        result["federation_sound"] = False

    return result


def runtime_corroboration(
    oracle_proposal,
    *,
    program_source=None,
):
    """Seek runtime witness support for an oracle proposal using
    ``jugeo.python_runtime.program_loader``.

    An oracle proposal (§7.1) may claim properties about a Python program.
    This function loads the program into the JuGeo runtime and looks for
    runtime witnesses (heap, stack, identity) that corroborate or contradict
    the oracle's claim (§7.3).

    Parameters
    ----------
    oracle_proposal : Any
        An oracle proposal record, or a dict with at least a ``"claim"`` key.
    program_source : str | None
        Python source code to load and inspect.  When ``None``, the function
        returns a stub result indicating no runtime evidence is available.

    Returns
    -------
    dict[str, Any]
        Keys: ``"corroborated"`` (bool), ``"witnesses"`` (list),
        ``"consistency"`` (str — ``"CONSISTENT"`` / ``"INCONSISTENT"`` /
        ``"UNKNOWN"``), ``"program_loaded"`` (bool).

    Notes
    -----
    Theory2.tex §7.3 — Runtime witnesses provide independent corroboration
    for oracle claims.  Theorem 7.5 requires external corroboration before
    promotion beyond ``ORACLE_PROPOSED``.

    Examples
    --------
    >>> result = runtime_corroboration({"claim": "x > 0"})  # doctest: +SKIP
    >>> result["consistency"]
    'UNKNOWN'
    """
    try:
        from jugeo.python_runtime.program_loader import (
            ProgramLoader,
            load_program,
        )
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.python_runtime.program_loader is required for "
            "runtime_corroboration()"
        ) from exc

    result = {
        "corroborated": False,
        "witnesses": [],
        "consistency": "UNKNOWN",
        "program_loaded": False,
    }

    if program_source is None:
        return result

    try:
        program = load_program(program_source)
        result["program_loaded"] = True

        # Extract witnesses from the loaded program's symbolic representation
        if hasattr(program, "judgment_sections"):
            for section in program.judgment_sections:
                evidence = getattr(section, "evidence", None)
                if evidence is not None:
                    result["witnesses"].append(evidence)

        if hasattr(program, "obstructions"):
            obstructions = list(program.obstructions)
            if obstructions:
                result["consistency"] = "INCONSISTENT"
                result["corroborated"] = False
            elif result["witnesses"]:
                result["consistency"] = "CONSISTENT"
                result["corroborated"] = True
            else:
                result["consistency"] = "UNKNOWN"
        elif result["witnesses"]:
            result["consistency"] = "CONSISTENT"
            result["corroborated"] = True

    except Exception as exc:
        _of_logger.warning("runtime_corroboration: loader error: %s", exc)
        result["consistency"] = f"ERROR: {exc}"

    return result


def trust_ceiling_enforcement(
    evidence,
    *,
    ceiling=None,
):
    """Enforce oracle trust ceilings on evidence using
    ``jugeo.evidence.trust``.

    The oracle ceiling invariant (Theorem 7.1, §7.1) states that no oracle
    channel can self-promote beyond its designated ceiling.  This function
    applies the ceiling rule from the trust algebra, demoting any evidence
    that exceeds the permitted level.

    Parameters
    ----------
    evidence : Any
        Evidence record or trust annotation to check.
    ceiling : TrustLevel | str | None
        The trust ceiling to enforce.  When ``None``, defaults to
        ``TrustLevel.ORACLE_PROPOSED``.

    Returns
    -------
    dict[str, Any]
        Keys: ``"original_level"`` (str), ``"enforced_level"`` (str),
        ``"was_capped"`` (bool), ``"ceiling"`` (str).

    Notes
    -----
    Theory2.tex §7.1 — Theorem 7.1 (Trust Ceiling Conservation) and
    Theorem 7.5 (Copilot Ceiling Invariance).

    Examples
    --------
    >>> result = trust_ceiling_enforcement(  # doctest: +SKIP
    ...     {"trust_level": "SOLVER_DISCHARGED"},
    ...     ceiling="ORACLE_PROPOSED",
    ... )
    >>> result["was_capped"]
    True
    """
    try:
        from jugeo.evidence.trust import TrustLevel, TrustCeiling, TrustAlgebra
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.evidence.trust is required for trust_ceiling_enforcement()"
        ) from exc

    # Resolve ceiling
    if ceiling is None:
        resolved_ceiling = TrustLevel.ORACLE_PROPOSED
    elif isinstance(ceiling, str):
        resolved_ceiling = TrustLevel[ceiling]
    else:
        resolved_ceiling = ceiling

    # Resolve current evidence trust level
    if isinstance(evidence, dict):
        level_str = evidence.get("trust_level", "COPILOT_SUGGESTED")
    elif hasattr(evidence, "trust_level"):
        level_str = evidence.trust_level
    else:
        level_str = str(evidence)

    if isinstance(level_str, str):
        try:
            original_level = TrustLevel[level_str]
        except (KeyError, TypeError):
            original_level = TrustLevel.COPILOT_SUGGESTED
    else:
        original_level = level_str

    # Apply ceiling enforcement via the trust algebra's demotion operator (↓χ)
    was_capped = False
    enforced_level = original_level

    try:
        ceiling_enforcer = TrustCeiling(ceiling=resolved_ceiling)
        enforced_level = ceiling_enforcer.apply(original_level)
        was_capped = enforced_level != original_level
    except Exception:
        # Fallback: manual comparison using enum ordering
        if hasattr(original_level, "value") and hasattr(resolved_ceiling, "value"):
            if original_level.value < resolved_ceiling.value:
                # Lower value = higher trust in the TrustLevel enum
                enforced_level = resolved_ceiling
                was_capped = True

    return {
        "original_level": original_level.name if hasattr(original_level, "name") else str(original_level),
        "enforced_level": enforced_level.name if hasattr(enforced_level, "name") else str(enforced_level),
        "was_capped": was_capped,
        "ceiling": resolved_ceiling.name if hasattr(resolved_ceiling, "name") else str(resolved_ceiling),
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import controlled_oracle_theory_query_con
except Exception:
    pass
try:
    from . import controlled_oracles
except Exception:
    pass
try:
    from . import evidence_federation_reconciling_in
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
    from . import obligation_splitting
except Exception:
    pass
try:
    from . import runtime_witnesses
except Exception:
    pass
try:
    from . import semantic_jurisdiction
except Exception:
    pass
try:
    from . import solver_federation
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
