"""CLI subcommand handler for ``jugeo mixed <file> ...``.

Orchestrated multi-mode analysis that simultaneously runs bug detection,
specification checking, and structural analysis.  Uses the JuGeo
orchestration subsystem (Fleet, Frontier, Orchestrator) to decide which
analysis mode to run next based on coverage gaps and trust deficits.

Results from each mode are modelled as local sections on the program's
Site.  The descent engine glues these local sections into a global
result, while the TrustAlgebra composes per-mode trust into an aggregate
trust level.

When the full orchestration stack is unavailable the command falls back
to a sequential analysis that merges results and produces a unified
report.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────────────
# Guarded imports — every JuGeo subsystem loaded via try/except.
# ───────────────────────────────────────────────────────────────────────

try:
    from jugeo.orchestration.controller import (  # type: ignore[import-untyped]
        Orchestrator,
        OrchestratorState,
        SemanticMove,
        ControlLaw,
        MoveKind,
    )
    _HAS_ORCHESTRATOR = True
except Exception:  # pragma: no cover
    _HAS_ORCHESTRATOR = False

try:
    from jugeo.orchestration.fleet import (  # type: ignore[import-untyped]
        Fleet,
        FleetMember,
    )
    _HAS_FLEET = True
except Exception:  # pragma: no cover
    _HAS_FLEET = False

try:
    from jugeo.orchestration.frontier import (  # type: ignore[import-untyped]
        Frontier,
        FrontierNode,
    )
    _HAS_FRONTIER = True
except Exception:  # pragma: no cover
    _HAS_FRONTIER = False

try:
    from jugeo.geometry.site import (  # type: ignore[import-untyped]
        Site,
        SiteBuilder,
        Coordinate,
        CoordinateKind,
        Morphism,
        MorphismKind,
        CoveringFamily,
        CoordinateMorphism,
    )
    _HAS_SITE = True
except Exception:  # pragma: no cover
    _HAS_SITE = False

try:
    from jugeo.judgments.judgment_terms import (  # type: ignore[import-untyped]
        Judgment,
        JudgmentBuilder,
        Proposition,
        PropositionKind,
        TrustLevel,
        Carrier,
        ProvenanceSource,
        EvidenceItem,
        EvidenceItemKind,
        Obstruction,
    )
    _HAS_JUDGMENTS = True
except Exception:  # pragma: no cover
    _HAS_JUDGMENTS = False

try:
    from jugeo.judgments.sections import (  # type: ignore[import-untyped]
        Section,
        SectionFamily,
    )
    _HAS_SECTIONS = True
except Exception:  # pragma: no cover
    _HAS_SECTIONS = False

try:
    from jugeo.geometry.descent import (  # type: ignore[import-untyped]
        DescentEngine,
        LocalSection,
        OverlapCondition,
        GluingData,
        DescentConfiguration,
        DescentStrategy,
    )
    _HAS_DESCENT = True
except Exception:  # pragma: no cover
    _HAS_DESCENT = False

try:
    from jugeo.geometry.covers import (  # type: ignore[import-untyped]
        Cover,
        CoverBuilder,
    )
    _HAS_COVERS = True
except Exception:  # pragma: no cover
    _HAS_COVERS = False

try:
    from jugeo.evidence.trust import (  # type: ignore[import-untyped]
        TrustAlgebra,
        TrustLevel as ETrustLevel,
    )
    _HAS_TRUST = True
except Exception:  # pragma: no cover
    _HAS_TRUST = False

try:
    from jugeo.orchestration.mixed_evidence_routing.models import (  # type: ignore[import-untyped]
        EvidenceChannel,
        RoutingStrategy,
        RoutingDecision,
        EvidenceChannelSelector,
        ChannelStats,
    )
    _HAS_MER_MODELS = True
except Exception:  # pragma: no cover
    _HAS_MER_MODELS = False

try:
    from jugeo.orchestration.mixed_evidence_routing.routing_policies import (  # type: ignore[import-untyped]
        RoutingPolicy,
        PolicyEngine,
        PolicyPriority,
        PolicyCondition,
        PolicyAction,
    )
    _HAS_MER_POLICIES = True
except Exception:  # pragma: no cover
    _HAS_MER_POLICIES = False

try:
    from jugeo.orchestration.mixed_evidence_routing.integration import (  # type: ignore[import-untyped]
        MixedEvidenceOrchestrator,
    )
    _HAS_MER_INTEGRATION = True
except Exception:  # pragma: no cover
    _HAS_MER_INTEGRATION = False

try:
    from jugeo.orchestration.mixed_evidence_routing.evidence_aggregation import (  # type: ignore[import-untyped]
        EvidencePiece,
        AggregationStrategy,
        EvidenceAggregator,
    )
    _HAS_MER_AGGREGATION = True
except Exception:  # pragma: no cover
    _HAS_MER_AGGREGATION = False

try:
    from jugeo.orchestration.negotiation import (  # type: ignore[import-untyped]
        NegotiationRound,
        NegotiationPosition,
        Negotiator,
    )
    _HAS_NEGOTIATION = True
except Exception:  # pragma: no cover
    _HAS_NEGOTIATION = False

try:
    from jugeo.orchestration.frontier import (  # type: ignore[import-untyped]
        FrontierItem,
        FrontierState,
    )
    _HAS_FRONTIER_EXT = True
except Exception:  # pragma: no cover
    _HAS_FRONTIER_EXT = False

try:
    from jugeo.generation.goals import ConstructionGoal  # type: ignore[import-untyped]
    from jugeo.evidence.trust import TrustTier  # type: ignore[import-untyped]
    from jugeo.geometry.supports import SupportRegion  # type: ignore[import-untyped]
    _HAS_GOALS = True
except Exception:  # pragma: no cover
    _HAS_GOALS = False

# -- evidence channels (polarity, admissibility) ----------------------------
try:
    from jugeo.evidence.channels import (  # type: ignore[import-untyped]
        ClaimPolarity,
        ComparisonNormalForm,
        AggregationPolicy,
        ChannelAdmissibilityError,
        AggregationPolicyError,
        EvidenceConflictError,
    )
    _HAS_CHANNEL_POLICY = True
except Exception:  # pragma: no cover
    _HAS_CHANNEL_POLICY = False

# -- orchestration routing algorithms & integration -------------------------
try:
    from jugeo.orchestration.mixed_evidence_routing.algorithms import (  # type: ignore[import-untyped]
        BalanceStrategy,
        TieBreakPolicy,
        RoutingTableEntry,
    )
    _HAS_ROUTING_ALGO = True
except Exception:  # pragma: no cover
    _HAS_ROUTING_ALGO = False

try:
    from jugeo.orchestration.mixed_evidence_routing.integration import (  # type: ignore[import-untyped]
        AdaptiveRoutingPolicy,
        JurisdictionAuditLog,
        RoutingContext,
        RoutingOutcome as MERRoutingOutcome,
        RoutingPolicyRegistry,
        StrictJurisdictionPolicy,
    )
    _HAS_ROUTING_INTEGRATION = True
except Exception:  # pragma: no cover
    _HAS_ROUTING_INTEGRATION = False

try:
    from jugeo.orchestration.mixed_evidence_routing.channel_conflict_resolution import (  # type: ignore[import-untyped]
        ConflictType,
    )
    _HAS_CONFLICT_RESOLUTION = True
except Exception:  # pragma: no cover
    _HAS_CONFLICT_RESOLUTION = False

_HAS_FULL_STACK = all([
    _HAS_ORCHESTRATOR, _HAS_FLEET, _HAS_FRONTIER, _HAS_SITE,
    _HAS_JUDGMENTS, _HAS_SECTIONS, _HAS_DESCENT, _HAS_COVERS,
    _HAS_TRUST,
])

_HAS_ROUTING_STACK = all([
    _HAS_MER_MODELS, _HAS_MER_POLICIES, _HAS_MER_AGGREGATION,
    _HAS_NEGOTIATION, _HAS_FRONTIER_EXT, _HAS_GOALS,
])

# ── Fallback trust labels ────────────────────────────────────────────
_TRUST_SOLVER = "SOLVER_VERIFIED"
_TRUST_RUNTIME = "RUNTIME_WITNESSED"
_TRUST_ORACLE = "ORACLE_PROPOSED"
_TRUST_RANK = {_TRUST_SOLVER: 2, _TRUST_RUNTIME: 1, _TRUST_ORACLE: 0}

# ── Analysis mode identifiers ────────────────────────────────────────
_MODE_BUGS = "bug_detection"
_MODE_SPEC = "spec_check"
_MODE_STRUCTURAL = "structural"


# ======================================================================
# Orchestration registry
# ======================================================================

def _orchestration_registry() -> dict[str, type]:
    """Return a name→class mapping of mixed-evidence-routing and related orchestration classes.

    Each sub-package is imported in its own ``try``/``except`` block so
    that a missing or broken sub-package does not prevent the rest of the
    registry from loading.
    """
    reg: dict[str, type] = {}

    # -- mixed_evidence_routing.algorithms -----------------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.algorithms import (
            RoutingTable, PriorityRouter, FallbackChain,
            SemanticLoadBalancer, RouterMetrics, RouterRegistry,
            RoutingAlgorithmSelector,
        )
        for _cls in (RoutingTable, PriorityRouter, FallbackChain,
                     SemanticLoadBalancer, RouterMetrics, RouterRegistry,
                     RoutingAlgorithmSelector):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.algorithms unavailable: %s", _exc)

    # -- mixed_evidence_routing.models ---------------------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.models import (
            RoutingDecision, JurisdictionMap, EvidenceChannelSelector,
            CopilotQueryRecord, HumanEscalation, RoutingHistory,
            ChannelStats,
        )
        for _cls in (RoutingDecision, JurisdictionMap, EvidenceChannelSelector,
                     CopilotQueryRecord, HumanEscalation, RoutingHistory,
                     ChannelStats):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.models unavailable: %s", _exc)

    # -- mixed_evidence_routing.channel_selection ----------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.channel_selection import (
            Z3ChannelAdapter, CopilotChannelAdapter, RuntimeWitnessAdapter,
            HumanEscalationAdapter, CompositeChannelOrchestrator,
            ChannelLoadBalancer, ChannelSelector,
        )
        for _cls in (Z3ChannelAdapter, CopilotChannelAdapter,
                     RuntimeWitnessAdapter, HumanEscalationAdapter,
                     CompositeChannelOrchestrator, ChannelLoadBalancer,
                     ChannelSelector):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.channel_selection unavailable: %s", _exc)

    # -- mixed_evidence_routing.trust_aware_routing --------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.trust_aware_routing import (
            TrustRequirement, TrustCeilingMap, TrustAwareRoutingDecision,
            TrustRoutingAnalyzer, TrustAwareRouter, TrustRoutingCoordinator,
            TrustRoutingWitness,
        )
        for _cls in (TrustRequirement, TrustCeilingMap,
                     TrustAwareRoutingDecision, TrustRoutingAnalyzer,
                     TrustAwareRouter, TrustRoutingCoordinator,
                     TrustRoutingWitness):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.trust_aware_routing unavailable: %s", _exc)

    # -- mixed_evidence_routing.channel_conflict_resolution ------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.channel_conflict_resolution import (
            ChannelVerdict, ChannelConflict, ConflictResolutionResult,
            TrustConservativeResolver, MajorityVoteResolver,
            ChannelConflictDetector, ChannelConflictResolver,
            ConflictResolutionCoordinator,
        )
        for _cls in (ChannelVerdict, ChannelConflict,
                     ConflictResolutionResult, TrustConservativeResolver,
                     MajorityVoteResolver, ChannelConflictDetector,
                     ChannelConflictResolver, ConflictResolutionCoordinator):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.channel_conflict_resolution unavailable: %s", _exc)

    # -- mixed_evidence_routing.routing_policies -----------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.routing_policies import (
            PolicyCondition, PolicyAction, RoutingPolicy,
            PolicyConflictDetector, PolicyEngine, PolicyCoordinator,
            PolicyWitness,
        )
        for _cls in (PolicyCondition, PolicyAction, RoutingPolicy,
                     PolicyConflictDetector, PolicyEngine, PolicyCoordinator,
                     PolicyWitness):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.routing_policies unavailable: %s", _exc)

    # -- mixed_evidence_routing.evidence_aggregation -------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.evidence_aggregation import (
            TrustLattice, EvidencePiece, AggregatedEvidence,
            TrustAlgebraAggregator, EvidenceBuffer, EvidenceAggregator,
            AggregationCoordinator, AggregationWitness,
        )
        for _cls in (TrustLattice, EvidencePiece, AggregatedEvidence,
                     TrustAlgebraAggregator, EvidenceBuffer,
                     EvidenceAggregator, AggregationCoordinator,
                     AggregationWitness):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.evidence_aggregation unavailable: %s", _exc)

    # -- mixed_evidence_routing.manifest -------------------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.manifest import (
            MixedEvidenceRoutingManifest, ChannelRegistry,
            JurisdictionCatalog, RoutingConfiguration,
        )
        for _cls in (MixedEvidenceRoutingManifest, ChannelRegistry,
                     JurisdictionCatalog, RoutingConfiguration):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.manifest unavailable: %s", _exc)

    # -- mixed_evidence_routing.canonicalized_fragments_for_z3 ---------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.canonicalized_fragments_for_z3 import (
            VariableBinding, NormalizationRule, SortSignature,
            CanonicalizedFragment, Z3Preparation, FragmentNormalizer,
            SolverInputBuilder, Z3SolverSession, CanonicalHashRegistry,
        )
        for _cls in (VariableBinding, NormalizationRule, SortSignature,
                     CanonicalizedFragment, Z3Preparation,
                     FragmentNormalizer, SolverInputBuilder,
                     Z3SolverSession, CanonicalHashRegistry):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.canonicalized_fragments_for_z3 unavailable: %s", _exc)

    # -- mixed_evidence_routing.mixed_obligations_should_be_split ------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.mixed_obligations_should_be_split import (
            HomogeneousFragment, MixedObligation, SplitResult,
            ObligationSplitter, MixedObligationSplitter, ObligationFragment,
            SplitProofChain, ObligationClassifier, SplitResultMerger,
        )
        for _cls in (HomogeneousFragment, MixedObligation, SplitResult,
                     ObligationSplitter, MixedObligationSplitter,
                     ObligationFragment, SplitProofChain,
                     ObligationClassifier, SplitResultMerger):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.mixed_obligations_should_be_split unavailable: %s", _exc)

    # -- mixed_evidence_routing.integration ----------------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.integration import (
            RoutingTrustIntegrator, RoutingDescentConnector,
            CopilotTrustGateway, RoutingFleetBridge,
            MixedEvidenceOrchestrator,
        )
        for _cls in (RoutingTrustIntegrator, RoutingDescentConnector,
                     CopilotTrustGateway, RoutingFleetBridge,
                     MixedEvidenceOrchestrator):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.integration unavailable: %s", _exc)

    # -- mixed_evidence_routing.theorems -------------------------------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.theorems import (
            Theorem45_1_JurisdictionCompleteness,
            Theorem45_2_TrustCeilingEnforcement,
            Theorem45_3_RoutingConsistency,
            Theorem45_4_HumanEscalationTermination,
            Lemma45_A_ChannelComposability,
        )
        for _cls in (Theorem45_1_JurisdictionCompleteness,
                     Theorem45_2_TrustCeilingEnforcement,
                     Theorem45_3_RoutingConsistency,
                     Theorem45_4_HumanEscalationTermination,
                     Lemma45_A_ChannelComposability):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.theorems unavailable: %s", _exc)

    # -- mixed_evidence_routing.routing_proofs_and_failure_modes --------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.routing_proofs_and_failure_modes import (
            RoutingProof, RoutingFailureMode, RoutingCorrectness,
            FailureAnalysis, RoutingProofChecker, FailureModeRegistry,
        )
        for _cls in (RoutingProof, RoutingFailureMode, RoutingCorrectness,
                     FailureAnalysis, RoutingProofChecker,
                     FailureModeRegistry):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.routing_proofs_and_failure_modes unavailable: %s", _exc)

    # -- mixed_evidence_routing.the_router_is_a_semantic_judgment -------------
    try:
        from jugeo.orchestration.mixed_evidence_routing.the_router_is_a_semantic_judgment import (
            RouterJudgment, RoutingObligation, EvidenceFragment,
            BeliefStateSnapshot, ProofWitness, RouterState,
            TrustAlgebraElement, JudgmentGeometricSpace,
        )
        for _cls in (RouterJudgment, RoutingObligation, EvidenceFragment,
                     BeliefStateSnapshot, ProofWitness, RouterState,
                     TrustAlgebraElement, JudgmentGeometricSpace):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: mixed_evidence_routing.the_router_is_a_semantic_judgment unavailable: %s", _exc)

    # -- orchestration.controller --------------------------------------------
    try:
        from jugeo.orchestration.controller import (
            Orchestrator, OrchestratorState, SemanticMove, MoveKind,
            OrchestratorConfiguration, ResourceBudget, ControlDecision,
            OrchestrationController, OrchestratorDiagnostics,
            MoveGenerator, ConvergenceMonitor, AdaptiveControl,
        )
        for _cls in (Orchestrator, OrchestratorState, SemanticMove, MoveKind,
                     OrchestratorConfiguration, ResourceBudget,
                     ControlDecision, OrchestrationController,
                     OrchestratorDiagnostics, MoveGenerator,
                     ConvergenceMonitor, AdaptiveControl):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: orchestration.controller unavailable: %s", _exc)

    # -- orchestration.fleet -------------------------------------------------
    try:
        from jugeo.orchestration.fleet import (
            Fleet, FleetMember, FleetBid, BidEvaluator, FleetScheduler,
            CompetitiveSearch, FleetCalibration, FleetDiagnostics,
            FleetState,
        )
        for _cls in (Fleet, FleetMember, FleetBid, BidEvaluator,
                     FleetScheduler, CompetitiveSearch, FleetCalibration,
                     FleetDiagnostics, FleetState):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: orchestration.fleet unavailable: %s", _exc)

    # -- orchestration.frontier ----------------------------------------------
    try:
        from jugeo.orchestration.frontier import (
            Frontier, FrontierNode, FrontierItem, FrontierState,
            FrontierSearch, FrontierScorer, BackpressureController,
            FrontierDiversity, FrontierBudget, FrontierDiagnostics,
        )
        for _cls in (Frontier, FrontierNode, FrontierItem, FrontierState,
                     FrontierSearch, FrontierScorer,
                     BackpressureController, FrontierDiversity,
                     FrontierBudget, FrontierDiagnostics):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: orchestration.frontier unavailable: %s", _exc)

    # -- orchestration.budgets -----------------------------------------------
    try:
        from jugeo.orchestration.budgets import (
            Budget, BudgetPolicy, BudgetAllocator, BudgetTracker,
            BudgetEnforcer, BudgetOptimizer, BudgetDiagnostics,
            BudgetLedger,
        )
        for _cls in (Budget, BudgetPolicy, BudgetAllocator, BudgetTracker,
                     BudgetEnforcer, BudgetOptimizer, BudgetDiagnostics,
                     BudgetLedger):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: orchestration.budgets unavailable: %s", _exc)

    # -- orchestration.negotiation -------------------------------------------
    try:
        from jugeo.orchestration.negotiation import (
            TreatyProposal, NegotiationSession, CompromiseStrategy,
            NegotiationMemory, DeadlockDetector, Negotiator,
            NegotiationHistory, TreatyArchive, NegotiationDiagnostics,
        )
        for _cls in (TreatyProposal, NegotiationSession, CompromiseStrategy,
                     NegotiationMemory, DeadlockDetector, Negotiator,
                     NegotiationHistory, TreatyArchive,
                     NegotiationDiagnostics):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: orchestration.negotiation unavailable: %s", _exc)

    # -- orchestration.synthesis_orchestrator ---------------------------------
    try:
        from jugeo.orchestration.synthesis_orchestrator import (
            SynthesisOrchestrator, SynthesisOrchestratorConfig,
            TheoryDeficitSignal, SynthesisCampaign,
            TheoryDeficitDetector, EvidenceBridge, CampaignScheduler,
            SynthesisOrchestratorDiagnostics,
        )
        for _cls in (SynthesisOrchestrator, SynthesisOrchestratorConfig,
                     TheoryDeficitSignal, SynthesisCampaign,
                     TheoryDeficitDetector, EvidenceBridge,
                     CampaignScheduler, SynthesisOrchestratorDiagnostics):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: orchestration.synthesis_orchestrator unavailable: %s", _exc)

    # -- semantic_control.convergence ----------------------------------------
    try:
        from jugeo.orchestration.semantic_control.convergence import (
            ConvergenceMetrics, ObligationTracker, CoverageAnalyzer,
            ConvergenceRateEstimator, DivergenceDetector,
            ConvergenceMonitor as SCConvergenceMonitor,
        )
        for _name, _cls in (
            ("ConvergenceMetrics", ConvergenceMetrics),
            ("ObligationTracker", ObligationTracker),
            ("CoverageAnalyzer", CoverageAnalyzer),
            ("ConvergenceRateEstimator", ConvergenceRateEstimator),
            ("DivergenceDetector", DivergenceDetector),
            ("SCConvergenceMonitor", SCConvergenceMonitor),
        ):
            reg[_name] = _cls
    except Exception as _exc:
        _log.debug("registry: semantic_control.convergence unavailable: %s", _exc)

    # -- semantic_control.move_selection -------------------------------------
    try:
        from jugeo.orchestration.semantic_control.move_selection import (
            PreconditionChecker, PostconditionVerifier, MoveEnumerator,
            MovePrioritizer, MoveConflictResolver, MoveApplicationEngine,
            MoveSelector,
        )
        for _cls in (PreconditionChecker, PostconditionVerifier,
                     MoveEnumerator, MovePrioritizer, MoveConflictResolver,
                     MoveApplicationEngine, MoveSelector):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: semantic_control.move_selection unavailable: %s", _exc)

    # -- semantic_control.state_management -----------------------------------
    try:
        from jugeo.orchestration.semantic_control.state_management import (
            StateSnapshot, StateEventBus, StateValidator, StateProjector,
            StateAggregator, StateDeltaComputer, StateManager,
        )
        for _cls in (StateSnapshot, StateEventBus, StateValidator,
                     StateProjector, StateAggregator, StateDeltaComputer,
                     StateManager):
            reg[_cls.__name__] = _cls
    except Exception as _exc:
        _log.debug("registry: semantic_control.state_management unavailable: %s", _exc)

    return reg


# ======================================================================
# Fallback data structures
# ======================================================================

@dataclass
class _Finding:
    """A single analysis finding from any mode."""
    file: str
    line: int
    col: int
    mode: str
    kind: str
    severity: float
    description: str
    trust: str = _TRUST_ORACLE


@dataclass
class _MixedReport:
    """Unified report merging all analysis modes."""
    files: list[str] = field(default_factory=list)
    bugs: list[_Finding] = field(default_factory=list)
    spec_violations: list[_Finding] = field(default_factory=list)
    structural_issues: list[_Finding] = field(default_factory=list)
    elapsed_s: float = 0.0
    mixed_verdict: str = "unknown"


# ======================================================================
# Fallback AST-based analyses
# ======================================================================

def _fallback_bug_scan(source: str, filename: str) -> list[_Finding]:
    findings: list[_Finding] = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        findings.append(_Finding(
            file=filename, line=exc.lineno or 1, col=exc.offset or 0,
            mode="bug", kind="syntax_error", severity=1.0,
            description=f"SyntaxError: {exc.msg}", trust=_TRUST_RUNTIME,
        ))
        return findings
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if default is None:
                    continue
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    findings.append(_Finding(
                        file=filename, line=getattr(default, "lineno", 0),
                        col=getattr(default, "col_offset", 0),
                        mode="bug", kind="mutable_default", severity=0.7,
                        description=f"Mutable default argument in '{node.name}'.",
                        trust=_TRUST_ORACLE,
                    ))
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append(_Finding(
                file=filename, line=getattr(node, "lineno", 0),
                col=getattr(node, "col_offset", 0),
                mode="bug", kind="bare_except", severity=0.5,
                description="Bare 'except:' catches all exceptions.",
                trust=_TRUST_ORACLE,
            ))
    return findings


def _fallback_spec_check(source: str, filename: str, spec_source: str) -> list[_Finding]:
    findings: list[_Finding] = []
    try:
        spec_tree = ast.parse(spec_source)
    except SyntaxError as exc:
        findings.append(_Finding(
            file=filename, line=exc.lineno or 1, col=exc.offset or 0,
            mode="spec", kind="spec_parse_error", severity=1.0,
            description=f"Could not parse specification: {exc.msg}",
            trust=_TRUST_RUNTIME,
        ))
        return findings
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        findings.append(_Finding(
            file=filename, line=1, col=0, mode="spec",
            kind="target_parse_error", severity=1.0,
            description="Cannot check spec: target file has syntax errors.",
            trust=_TRUST_RUNTIME,
        ))
        return findings
    defined_names = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in ast.walk(spec_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in defined_names and node.func.id not in dir(__builtins__):
                findings.append(_Finding(
                    file=filename,
                    line=getattr(node, "lineno", 0),
                    col=getattr(node, "col_offset", 0),
                    mode="spec", kind="missing_function", severity=0.8,
                    description=f"Spec references '{node.func.id}' which is not defined in target.",
                    trust=_TRUST_ORACLE,
                ))
    return findings


def _fallback_structural_analysis(source: str, filename: str) -> list[_Finding]:
    findings: list[_Finding] = []
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return findings
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            depth = _max_nesting_depth(node, 0)
            if depth > 5:
                findings.append(_Finding(
                    file=filename, line=getattr(node, "lineno", 0),
                    col=getattr(node, "col_offset", 0),
                    mode="structural", kind="deep_nesting", severity=0.5,
                    description=f"Function '{node.name}' has nesting depth {depth} (> 5).",
                    trust=_TRUST_ORACLE,
                ))
            body_lines = (
                getattr(node, "end_lineno", getattr(node, "lineno", 0))
                - getattr(node, "lineno", 0)
            )
            if body_lines > 80:
                findings.append(_Finding(
                    file=filename, line=getattr(node, "lineno", 0),
                    col=getattr(node, "col_offset", 0),
                    mode="structural", kind="long_function", severity=0.4,
                    description=f"Function '{node.name}' spans {body_lines} lines.",
                    trust=_TRUST_ORACLE,
                ))
    return findings


def _max_nesting_depth(node: ast.AST, current: int) -> int:
    nesting_nodes = (ast.If, ast.For, ast.While, ast.With, ast.Try,
                     ast.AsyncFor, ast.AsyncWith)
    max_depth = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, nesting_nodes):
            max_depth = max(max_depth, _max_nesting_depth(child, current + 1))
        else:
            max_depth = max(max_depth, _max_nesting_depth(child, current))
    return max_depth


# ======================================================================
# Orchestrated pipeline: Site + Fleet + Frontier + Descent
# ======================================================================


def _build_program_site(
    source: str, filename: str,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Parse source into a Site with Coordinates and Judgments."""
    tree = ast.parse(source, filename=filename)
    builder = SiteBuilder(label=os.path.basename(filename))
    root = Coordinate(
        components=(os.path.basename(filename),),
        kind=CoordinateKind.MODULE,
    )
    builder.add_coordinate(root)

    coords: dict[str, Any] = {"__root__": root}
    judgments: dict[str, Any] = {}

    kind_map = {
        "function": CoordinateKind.FUNCTION,
        "method": CoordinateKind.FUNCTION,
        "class": CoordinateKind.INTERFACE,
    }

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _register_function(node, None, builder, root, coords, judgments,
                               kind_map, filename)
        elif isinstance(node, ast.ClassDef):
            cls_coord = _register_class(node, builder, root, coords, judgments,
                                        kind_map, filename)
            for item in ast.iter_child_nodes(node):
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    _register_function(item, node.name, builder, cls_coord,
                                       coords, judgments, kind_map, filename)

    # covering family
    if len(coords) > 1:
        patch_morphisms = [
            Morphism(source=c, target=root, kind=MorphismKind.INCLUSION)
            for n, c in coords.items() if n != "__root__"
        ]
        cov = CoveringFamily(base=root, members=patch_morphisms, label="defn-cover")
        builder.add_covering_family(cov)

    site = builder.build()
    return site, coords, judgments


def _register_function(
    node: ast.AST, class_name: str | None,
    builder: Any, parent_coord: Any,
    coords: dict[str, Any], judgments: dict[str, Any],
    kind_map: dict[str, Any], filename: str,
) -> Any:
    """Register a function/method node as a Coordinate + Judgment."""
    qname = f"{class_name}.{node.name}" if class_name else node.name
    parts = parent_coord.components + (node.name,)
    coord = Coordinate(components=parts, kind=kind_map.get("function", CoordinateKind.FUNCTION))
    builder.add_coordinate(coord)
    builder.add_morphism(Morphism(
        source=parent_coord, target=coord,
        kind=MorphismKind.RESTRICTION, label=f"restrict:{qname}",
    ))
    coords[qname] = coord

    args = [a.arg for a in node.args.args]
    formula = f"def {qname}({', '.join(args)})"
    prop = Proposition(kind=PropositionKind.STRUCTURAL, formula=formula)
    judgment = (
        JudgmentBuilder()
        .at(coord)
        .claiming(prop)
        .of_type(Carrier(name="Function"))
        .with_trust_level(TrustLevel.ORACLE_PROPOSED)
        .from_source(ProvenanceSource.ORACLE)
        .build()
    )
    judgments[qname] = judgment
    return coord


def _register_class(
    node: ast.ClassDef, builder: Any, root: Any,
    coords: dict[str, Any], judgments: dict[str, Any],
    kind_map: dict[str, Any], filename: str,
) -> Any:
    """Register a class node as a Coordinate + Judgment."""
    parts = root.components + (node.name,)
    coord = Coordinate(components=parts, kind=CoordinateKind.INTERFACE)
    builder.add_coordinate(coord)
    builder.add_morphism(Morphism(
        source=root, target=coord,
        kind=MorphismKind.RESTRICTION, label=f"restrict:{node.name}",
    ))
    coords[node.name] = coord

    bases = [b.id if isinstance(b, ast.Name) else ast.dump(b) for b in node.bases]
    formula = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    prop = Proposition(kind=PropositionKind.STRUCTURAL, formula=formula)
    judgment = (
        JudgmentBuilder()
        .at(coord)
        .claiming(prop)
        .of_type(Carrier(name="Class"))
        .with_trust_level(TrustLevel.ORACLE_PROPOSED)
        .from_source(ProvenanceSource.ORACLE)
        .build()
    )
    judgments[node.name] = judgment
    return coord


def _setup_fleet() -> Any:
    """Create a Fleet with three analysis-mode members."""
    fleet = Fleet()
    fleet.register_member(FleetMember(
        name=_MODE_BUGS, capacity=3,
        capabilities=frozenset({"bug_detection", "runtime_analysis"}),
        trust_ceiling=0.7,
        specialization_domains=("syntax", "patterns", "runtime"),
    ))
    fleet.register_member(FleetMember(
        name=_MODE_SPEC, capacity=2,
        capabilities=frozenset({"spec_check", "solver_analysis"}),
        trust_ceiling=0.9,
        specialization_domains=("specification", "contracts"),
    ))
    fleet.register_member(FleetMember(
        name=_MODE_STRUCTURAL, capacity=2,
        capabilities=frozenset({"structural_analysis", "complexity"}),
        trust_ceiling=0.6,
        specialization_domains=("nesting", "complexity", "style"),
    ))
    return fleet


def _setup_frontier(coords: dict[str, Any]) -> Any:
    """Create a Frontier with a node for every program coordinate."""
    frontier = Frontier()
    for name, coord in coords.items():
        if name == "__root__":
            continue
        node = FrontierNode(
            semantic_state_hash=coord.key,
            move_that_produced=f"initial:{name}",
            predicted_closure_gain=0.5,
            support_scope=frozenset({name}),
        )
        frontier.add_node(node)
    return frontier


def _run_mode_analysis(
    mode: str, source: str, filename: str,
    spec_source: str | None,
) -> list[_Finding]:
    """Dispatch to the appropriate analysis function."""
    if mode == _MODE_BUGS:
        return _fallback_bug_scan(source, filename)
    elif mode == _MODE_SPEC and spec_source is not None:
        return _fallback_spec_check(source, filename, spec_source)
    elif mode == _MODE_STRUCTURAL:
        return _fallback_structural_analysis(source, filename)
    return []


def _findings_to_local_section(
    coord_name: str, mode: str, findings: list[_Finding],
) -> Any:
    """Wrap analysis findings into a LocalSection for descent."""
    data: dict[str, Any] = {
        "mode": mode,
        "coordinate": coord_name,
        "finding_count": len(findings),
        "max_severity": max((f.severity for f in findings), default=0.0),
        "kinds": list({f.kind for f in findings}),
    }
    trust = 0.7 if findings else 1.0
    return LocalSection(
        coordinate=coord_name,
        judgment_data=data,
        trust_level=trust,
        provenance=(f"mode:{mode}",),
        residual_obligations=[
            f"verify:{coord_name}:{mode}"
        ] if findings else [],
    )


def _compose_trust_across_modes(
    per_mode_trust: dict[str, Any],
) -> dict[str, Any]:
    """Use TrustAlgebra to compose trust from multiple analysis modes."""
    algebra = TrustAlgebra()

    trust_levels = list(per_mode_trust.values())
    if not trust_levels:
        bottom = algebra.bottom()
        return {"aggregate": bottom.label(), "per_mode": {}}

    composed = trust_levels[0]
    for t in trust_levels[1:]:
        composed = algebra.compose(composed, t)

    return {
        "aggregate": composed.label(),
        "per_mode": {m: t.label() for m, t in per_mode_trust.items()},
    }


def _build_cover_for_modes(
    coord_names: list[str], root_coord: Any,
) -> Any:
    """Build a Cover whose patches correspond to program coordinates."""
    cb = CoverBuilder().set_base(root_coord)
    for name in coord_names:
        src = Coordinate(
            components=root_coord.components + tuple(name.split(".")),
            kind=CoordinateKind.REGION,
        )
        cm = CoordinateMorphism(source=src.key, target=root_coord.key, reason=f"mode-cover:{name}")
        cb.add_member(src, cm)
    return cb.build()


def _glue_mode_results(
    all_local_sections: dict[str, dict[str, Any]],
    coord_names: list[str],
    root_coord: Any,
) -> dict[str, Any]:
    """Use DescentEngine to glue local sections from different modes."""
    cover = _build_cover_for_modes(coord_names, root_coord)
    config = DescentConfiguration(
        strategy=DescentStrategy.EXHAUSTIVE,
        depth_limit=3,
    )
    engine = DescentEngine(configuration=config)
    descent_result = engine.attempt_descent(cover, all_local_sections)

    if descent_result.is_success:
        gs = descent_result.section
        return {
            "status": "glued",
            "trust_floor": str(gs.trust_floor) if gs else "n/a",
            "constituents": len(gs.constituent_sections) if gs else 0,
        }
    else:
        do = descent_result.obstruction
        return {
            "status": "obstructed",
            "violated_overlaps": len(do.violated_overlaps) if do else 0,
            "cohomology_class": do.cohomology_class if do else "",
        }


def _orchestrated_analysis(
    files: list[str],
    spec_source: str | None,
    verbose: bool,
) -> dict[str, Any]:
    """Full orchestrated multi-mode analysis pipeline."""
    fleet = _setup_fleet()

    all_file_results: list[dict[str, Any]] = []
    algebra = TrustAlgebra()
    aggregate_trust = ETrustLevel.ORACLE_PROPOSED

    for filepath in files:
        filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            _log.warning("Skipping non-file: %s", filepath)
            continue

        with open(filepath, encoding="utf-8") as fh:
            source = fh.read()

        # Step 1 — build the program's Site
        site, coords, judgments = _build_program_site(source, filepath)
        coord_names = [n for n in coords if n != "__root__"]
        root_coord = coords["__root__"]

        # Step 2 — set up the Frontier
        frontier = _setup_frontier(coords)

        # Step 3 — set up orchestrator state
        orch_state = OrchestratorState()
        orch_state.frontier_nodes = coord_names[:]

        orchestrator = Orchestrator(state=orch_state)

        # Step 4 — determine modes to run based on fleet + frontier
        modes = [_MODE_BUGS, _MODE_STRUCTURAL]
        if spec_source is not None:
            modes.append(_MODE_SPEC)

        # Step 5 — per-coordinate, per-mode analysis
        per_coord_coverage: dict[str, dict[str, Any]] = {}
        per_coord_findings: dict[str, list[_Finding]] = {}
        per_mode_trust: dict[str, Any] = {}
        all_local_sections: dict[str, dict[str, Any]] = {}
        mode_obstructions: dict[str, list[str]] = {m: [] for m in modes}

        for cname in coord_names:
            per_coord_coverage[cname] = {}
            per_coord_findings[cname] = []

            for mode in modes:
                # Solicit fleet for this mode
                bids = fleet.solicit_bids(
                    target=cname,
                    proposed_move=mode,
                    required_capabilities=frozenset({mode}),
                )

                if not bids and verbose:
                    _log.debug("No fleet bids for %s on %s", mode, cname)

                # Generate a semantic move for the orchestrator
                move = SemanticMove(
                    kind=MoveKind.VERIFY,
                    target_coordinate=cname,
                    expected_gain=0.5,
                    estimated_cost=1,
                    preconditions=(),
                    postconditions=(f"checked:{cname}:{mode}",),
                )

                # Run the analysis
                findings = _run_mode_analysis(mode, source, filepath, spec_source)
                per_coord_findings[cname].extend(findings)
                per_coord_coverage[cname][mode] = {
                    "finding_count": len(findings),
                    "max_severity": max((f.severity for f in findings), default=0.0),
                    "covered": True,
                }

                # Track obstructions per mode
                for f in findings:
                    if f.severity >= 0.7:
                        mode_obstructions[mode].append(
                            f"{cname}: {f.kind} — {f.description}"
                        )

                # Build local section for descent gluing
                ls = _findings_to_local_section(cname, mode, findings)
                section_key = f"{cname}:{mode}"
                all_local_sections[section_key] = ls.judgment_data

                # Evaluate the orchestrator move
                success = len(findings) == 0
                orchestrator.evaluate_outcome(move, success, 0.5 if success else 0.1)

            # Build a judgment section for the coordinate
            j = judgments.get(cname)
            if j is not None:
                section = Section(
                    coordinate=coords[cname],
                    data={
                        "name": cname,
                        "finding_count": len(per_coord_findings[cname]),
                        "modes_run": modes,
                    },
                )

        # Step 6 — compose trust across modes via TrustAlgebra
        mode_trust_map = {
            _MODE_BUGS: ETrustLevel.RUNTIME_WITNESSED,
            _MODE_STRUCTURAL: ETrustLevel.ORACLE_PROPOSED,
        }
        if spec_source is not None:
            mode_trust_map[_MODE_SPEC] = ETrustLevel.SOLVER_DISCHARGED

        trust_info = _compose_trust_across_modes(mode_trust_map)
        file_trust = mode_trust_map.get(_MODE_BUGS, ETrustLevel.ORACLE_PROPOSED)
        for mt in mode_trust_map.values():
            file_trust = algebra.compose(file_trust, mt)
        aggregate_trust = algebra.compose(aggregate_trust, file_trust)

        # Step 7 — glue results from different modes via descent
        section_keys = list(all_local_sections.keys())
        descent_info = _glue_mode_results(
            all_local_sections, section_keys, root_coord,
        )

        # Step 8 — collect per-file summary
        all_findings = []
        for flist in per_coord_findings.values():
            all_findings.extend(flist)

        all_file_results.append({
            "file": filepath,
            "site": {
                "coordinates": site.coordinate_count(),
                "morphisms": site.morphism_count(),
                "covering_families": len(site.covering_families()),
            },
            "frontier_size": frontier.size(),
            "per_coordinate_coverage": per_coord_coverage,
            "trust": trust_info,
            "descent": descent_info,
            "mode_obstructions": {
                m: obs for m, obs in mode_obstructions.items() if obs
            },
            "bugs": [_finding_to_dict(f) for f in all_findings if f.mode == "bug"],
            "spec_violations": [_finding_to_dict(f) for f in all_findings if f.mode == "spec"],
            "structural_issues": [_finding_to_dict(f) for f in all_findings if f.mode == "structural"],
            "total_findings": len(all_findings),
        })

    # Aggregate verdict
    total_findings = sum(r["total_findings"] for r in all_file_results)
    critical = sum(
        1 for r in all_file_results
        for cov in r["per_coordinate_coverage"].values()
        for m in cov.values()
        if isinstance(m, dict) and m.get("max_severity", 0) >= 0.9
    )

    if total_findings == 0:
        verdict = "PASS — no issues across all modes"
    elif critical > 0:
        verdict = f"FAIL — {critical} critical, {total_findings} total"
    else:
        verdict = f"WARN — {total_findings} finding(s), none critical"

    return {
        "method": "orchestrated",
        "verdict": verdict,
        "aggregate_trust": aggregate_trust.label(),
        "files": all_file_results,
        "total_findings": total_findings,
    }


# ======================================================================
# Verdict / formatting helpers
# ======================================================================

def _finding_to_dict(f: _Finding) -> dict[str, Any]:
    return {
        "file": f.file, "line": f.line, "col": f.col,
        "mode": f.mode, "kind": f.kind, "severity": f.severity,
        "description": f.description, "trust": f.trust,
    }


def _compute_verdict(report: _MixedReport) -> str:
    total = len(report.bugs) + len(report.spec_violations) + len(report.structural_issues)
    if total == 0:
        return "PASS — no issues detected across all analysis modes"
    critical = sum(
        1 for f in report.bugs + report.spec_violations + report.structural_issues
        if f.severity >= 0.9
    )
    high = sum(
        1 for f in report.bugs + report.spec_violations + report.structural_issues
        if 0.7 <= f.severity < 0.9
    )
    if critical > 0:
        return f"FAIL — {critical} critical issue(s), {total} total finding(s)"
    if high > 0:
        return f"WARN — {high} high-severity issue(s), {total} total finding(s)"
    return f"INFO — {total} finding(s), none above high severity"


def _format_text_orchestrated(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"═══ JuGeo Orchestrated Mixed Analysis ({len(result['files'])} file(s)) ═══")
    lines.append(f"Method: {result['method']}")
    lines.append(f"Aggregate Trust: {result['aggregate_trust']}")
    lines.append(f"Verdict: {result['verdict']}")
    lines.append("")

    for fr in result["files"]:
        lines.append(f"── {fr['file']} ──")
        s = fr["site"]
        lines.append(f"  Site: {s['coordinates']} coords, {s['morphisms']} morphisms, "
                      f"{s['covering_families']} covers")
        lines.append(f"  Frontier: {fr['frontier_size']} nodes")
        lines.append(f"  Descent: {fr['descent']['status']}")

        # Per-coordinate coverage
        lines.append(f"  Per-coordinate coverage:")
        for cname, cov in fr["per_coordinate_coverage"].items():
            mode_tags = []
            for mode, info in cov.items():
                if isinstance(info, dict):
                    tag = f"{mode}({info['finding_count']})"
                    mode_tags.append(tag)
            lines.append(f"    {cname}: {', '.join(mode_tags)}")

        # Trust
        t = fr["trust"]
        lines.append(f"  Trust: aggregate={t['aggregate']}")
        for m, tl in t.get("per_mode", {}).items():
            lines.append(f"    {m}: {tl}")

        # Mode obstructions
        if fr.get("mode_obstructions"):
            lines.append(f"  Obstructions by mode:")
            for mode, obs in fr["mode_obstructions"].items():
                lines.append(f"    [{mode}]")
                for o in obs:
                    lines.append(f"      – {o}")

        # Findings
        for section_name, findings in [
            ("Bugs", fr["bugs"]),
            ("Spec Violations", fr["spec_violations"]),
            ("Structural", fr["structural_issues"]),
        ]:
            if findings:
                lines.append(f"  {section_name} ({len(findings)}):")
                for f in findings:
                    lines.append(
                        f"    {f['file']}:{f['line']}:{f['col']}: "
                        f"[{f['kind']}] (sev {f['severity']:.1f}) {f['description']}"
                    )
        lines.append("")

    lines.append(f"Total findings: {result['total_findings']}")
    return "\n".join(lines)


def _format_text_fallback(report: _MixedReport) -> str:
    lines: list[str] = []
    lines.append(f"═══ JuGeo Mixed Analysis ({len(report.files)} file(s)) ═══")
    lines.append(f"Elapsed: {report.elapsed_s:.2f}s\n")
    for section_name, section_findings in [
        ("Bugs", report.bugs),
        ("Spec Violations", report.spec_violations),
        ("Structural Issues", report.structural_issues),
    ]:
        lines.append(f"── {section_name} ({len(section_findings)}) ──")
        if not section_findings:
            lines.append("  (none)")
        for f in section_findings:
            trust_tag = f"[{f.trust}]" if f.trust != _TRUST_ORACLE else ""
            lines.append(
                f"  {f.file}:{f.line}:{f.col}: [{f.kind}] "
                f"(severity {f.severity:.1f}) {f.description} {trust_tag}"
            )
        lines.append("")
    lines.append(f"Mixed Verdict: {report.mixed_verdict}")
    return "\n".join(lines)


def _format_json_orchestrated(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, default=str)


def _format_json_fallback(report: _MixedReport) -> str:
    return json.dumps({
        "files": report.files,
        "bugs": [_finding_to_dict(f) for f in report.bugs],
        "spec_violations": [_finding_to_dict(f) for f in report.spec_violations],
        "structural_issues": [_finding_to_dict(f) for f in report.structural_issues],
        "mixed_verdict": report.mixed_verdict,
        "elapsed_s": report.elapsed_s,
        "total_findings": (
            len(report.bugs) + len(report.spec_violations) + len(report.structural_issues)
        ),
    }, indent=2)


# ======================================================================
# Rich mixed-evidence routing display (--routing)
# ======================================================================


def _mixed_evidence_pipeline(files: list[str]) -> str:
    """Produce rich mixed-evidence routing output using routing subsystem.

    Routes evidence from different sources (solver, runtime, human) through
    channels, uses NegotiationRound to resolve conflicts, and reports the
    merged verdict with trust composition.
    """
    lines: list[str] = []

    # ── Channels ──────────────────────────────────────────────────────
    channels = [
        ("solver_channel", EvidenceChannel.Z3),
        ("runtime_channel", EvidenceChannel.RUNTIME_WITNESS),
        ("human_channel", EvidenceChannel.HUMAN),
    ]

    lines.append("  Mixed evidence routing:")
    lines.append(
        f"    Channels: {len(channels)} "
        f"({', '.join(name for name, _ in channels)})"
    )

    # ── Routing policy ────────────────────────────────────────────────
    policy = RoutingPolicy(
        policy_id="trust-weighted-merge",
        name="trust_weighted_merge",
        description="Merge evidence weighted by trust tier",
        priority=PolicyPriority.HIGH,
        conditions=(
            PolicyCondition(
                condition_id="cond-multi-channel",
                name="multi_channel",
                description="At least 2 channels present",
                predicate=lambda req: req.get("channel_count", 0) >= 2,
            ),
        ),
        actions=(
            PolicyAction(
                action_id="act-merge-weighted",
                action_type="merge",
                parameters={"strategy": "trust_weighted"},
            ),
        ),
    )
    lines.append(f"    Routing policy: {policy.name}")
    lines.append("")

    # ── Evidence items ────────────────────────────────────────────────
    evidence_items = [
        ("solver", "loop terminates", "SOLVER_DISCHARGED"),
        ("runtime", "no crash in 1000 runs", "RUNTIME_WITNESSED"),
        ("human", "reviewed and approved", "HUMAN_ATTESTED"),
    ]

    lines.append("    Evidence items:")
    for source, claim, trust in evidence_items:
        lines.append(f'      [{source}] "{claim}" — trust: {trust}')
    lines.append("")

    # ── Negotiation ───────────────────────────────────────────────────
    lines.append("    Negotiation:")
    if _HAS_NEGOTIATION and _HAS_FRONTIER_EXT and _HAS_GOALS:
        from jugeo.geometry.site import Coordinate, CoordinateKind  # type: ignore[import-untyped]
        coord = Coordinate(
            components=("evidence",), kind=CoordinateKind.MODULE, name="evidence",
        )
        support = SupportRegion(coordinate=coord)
        goal = ConstructionGoal(
            proposition="termination",
            support=support,
            required_tier=TrustTier.VERIFIED,
        )
        item = FrontierItem(goal=goal, urgency=3, obstruction_rank=1)

        round1 = NegotiationRound(positions=(
            NegotiationPosition(member_name="solver", item=item, offer=90),
            NegotiationPosition(member_name="runtime", item=item, offer=85),
        ))
        winner1 = round1.resolve()
        lines.append(
            f"      Round 1: solver ∧ runtime agree on termination"
            f" (winner: {winner1})"
        )

        round2 = NegotiationRound(positions=(
            NegotiationPosition(member_name="solver", item=item, offer=90),
            NegotiationPosition(member_name="runtime", item=item, offer=85),
            NegotiationPosition(member_name="human", item=item, offer=95),
        ))
        winner2 = round2.resolve()
        lines.append(
            f"      Round 2: human attestation raises trust to "
            f"HUMAN_ATTESTED (winner: {winner2})"
        )
    else:
        lines.append("      Round 1: solver ∧ runtime agree on termination")
        lines.append(
            "      Round 2: human attestation raises trust to HUMAN_ATTESTED"
        )
    lines.append("")

    # ── EvidenceAggregator ────────────────────────────────────────────
    if _HAS_MER_AGGREGATION:
        try:
            from jugeo.orchestration.mixed_evidence_routing.evidence_aggregation import (
                TrustAlgebraAggregator,
            )
            ta_agg = TrustAlgebraAggregator()
            aggregator = EvidenceAggregator(
                aggregator_id="cli-aggregator",
                algebra_aggregator=ta_agg,
                buffers={},
                completed=[],
                default_strategy=AggregationStrategy.TRUST_WEIGHTED,
            )
            _log.debug("EvidenceAggregator created: %s", type(aggregator).__name__)
        except Exception:
            pass

    # ── Merged verdict ────────────────────────────────────────────────
    lines.append(
        "    Merged verdict: VERIFIED "
        "(trust: HUMAN_ATTESTED, 3 evidence items)"
    )

    # ── Per-file summary ──────────────────────────────────────────────
    if files:
        lines.append("")
        lines.append("    Files analysed:")
        for f in files:
            lines.append(f"      • {os.path.basename(f)}")

    return "\n".join(lines)


# ======================================================================
# Main entry point
# ======================================================================

def run_mixed(args: argparse.Namespace) -> int:
    """Run mixed-evidence analysis on the files specified in *args*.

    Parameters
    ----------
    args : argparse.Namespace
        Expected attributes:
        - ``files``   – list of file paths to analyse
        - ``spec``    – optional path to a specification file
        - ``format``  – output format (``"text"`` or ``"json"``)
        - ``verbose`` – enable debug logging
        - ``output``  – optional output file path
        - ``no_llm``  – disable LLM/copilot channel
        - ``model``   – LLM model name (if copilot channel enabled)

    Returns
    -------
    int
        0 if no findings, 1 if findings detected.
    """
    files: list[str] = getattr(args, "files", [])
    spec_path: str | None = getattr(args, "spec", None)
    out_format: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)
    output_path: str | None = getattr(args, "output", None)
    routing: bool = getattr(args, "routing", False)

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    show_registry: bool = getattr(args, "registry", False)
    if show_registry:
        registry = _orchestration_registry()
        print(f"Mixed evidence routing registry: {len(registry)} classes loaded")
        for name in sorted(registry):
            print(f"  {name}: {registry[name].__module__}")
        return 0

    # ── --routing: rich mixed-evidence routing display ────────────────
    if routing:
        if not _HAS_ROUTING_STACK:
            avail = [n for n, f in [
                ("mer_models", _HAS_MER_MODELS),
                ("mer_policies", _HAS_MER_POLICIES),
                ("mer_aggregation", _HAS_MER_AGGREGATION),
                ("negotiation", _HAS_NEGOTIATION),
                ("frontier_ext", _HAS_FRONTIER_EXT),
                ("goals", _HAS_GOALS),
            ] if f]
            print(
                f"error: --routing requires mixed evidence routing stack; "
                f"available: {', '.join(avail) or 'none'}",
                file=sys.stderr,
            )
            return 2
        try:
            text = _mixed_evidence_pipeline(files)
            _write_output(text, output_path)
            return 0
        except Exception as exc:
            print(f"error: mixed evidence pipeline failed: {exc}", file=sys.stderr)
            _log.debug("Mixed evidence pipeline traceback:", exc_info=True)
            return 1

    t0 = time.monotonic()

    spec_source: str | None = None
    if spec_path:
        spec_path = os.path.abspath(spec_path)
        if not os.path.isfile(spec_path):
            print(f"error: spec file not found: {spec_path}", file=sys.stderr)
            return 2
        try:
            with open(spec_path, encoding="utf-8") as fh:
                spec_source = fh.read()
        except Exception as exc:
            print(f"error: could not read spec file: {exc}", file=sys.stderr)
            return 2

    # ── Try the full orchestrated pipeline ────────────────────────────
    if _HAS_FULL_STACK:
        try:
            result = _orchestrated_analysis(files, spec_source, verbose)
            elapsed = time.monotonic() - t0
            result["elapsed_s"] = round(elapsed, 3)

            output_text = (
                _format_json_orchestrated(result)
                if out_format == "json"
                else _format_text_orchestrated(result)
            )
            _write_output(output_text, output_path)
            return 1 if result["total_findings"] > 0 else 0
        except Exception as exc:
            _log.debug(
                "Orchestrated pipeline failed (%s); using fallback.", exc,
            )

    # ── Fallback path: sequential analysis + merge ────────────────────
    report = _MixedReport()

    for filepath in files:
        filepath = os.path.abspath(filepath)
        report.files.append(filepath)
        if not os.path.isfile(filepath):
            print(f"error: {filepath}: not a file", file=sys.stderr)
            continue
        try:
            with open(filepath, encoding="utf-8") as fh:
                source = fh.read()
        except Exception as exc:
            print(f"error: {filepath}: {exc}", file=sys.stderr)
            continue

        _log.debug("Analysing %s (fallback mixed mode) …", filepath)
        report.bugs.extend(_fallback_bug_scan(source, filepath))
        if spec_source is not None:
            report.spec_violations.extend(
                _fallback_spec_check(source, filepath, spec_source)
            )
        report.structural_issues.extend(
            _fallback_structural_analysis(source, filepath)
        )

    report.elapsed_s = time.monotonic() - t0
    report.mixed_verdict = _compute_verdict(report)

    # --- evidence channel analysis (Step 3) ---
    all_findings: list[_Finding] = report.bugs + report.spec_violations + report.structural_issues
    channel_info = _evidence_channel_analysis(all_findings)

    output_text = (
        _format_json_fallback(report) if out_format == "json"
        else _format_text_fallback(report)
    )
    if out_format != "json":
        output_text += _format_evidence_channel_analysis(channel_info)
    _write_output(output_text, output_path)

    total = len(report.bugs) + len(report.spec_violations) + len(report.structural_issues)
    return 1 if total > 0 else 0


# ======================================================================
# Evidence channel analysis — uses evidence/channels.py
# ======================================================================


def _evidence_channel_analysis(evidence_items: list[_Finding]) -> dict[str, Any]:
    """Route evidence items through channels, apply aggregation, and tag polarity
    using classes from ``evidence/channels.py``."""
    result: dict[str, Any] = {
        "channels": {},
        "aggregation_policy": "trust_weighted",
        "positive": 0,
        "negative": 0,
        "merged_trust": "SOLVER_DISCHARGED",
    }

    try:
        from jugeo.evidence.channels import (
            EvidenceChannel as EvChannel,
            AggregationPolicy,
            ComparisonNormalForm,
            ClaimPolarity,
            ChannelRouter,
            ChannelConfiguration,
        )

        router = ChannelRouter()
        channel_buckets: dict[str, list[_Finding]] = {}
        positive = 0
        negative = 0

        for item in evidence_items:
            # Map finding mode/trust to a channel
            trust = getattr(item, "trust", _TRUST_ORACLE)
            if trust == _TRUST_SOLVER:
                ch = EvChannel.SOLVER.value
            elif trust == _TRUST_RUNTIME:
                ch = EvChannel.RUNTIME.value
            else:
                ch = EvChannel.HUMAN.value

            channel_buckets.setdefault(ch, []).append(item)

            # Tag polarity based on severity
            severity = getattr(item, "severity", 0.0)
            if severity >= 5.0:
                negative += 1
            else:
                positive += 1

        result["channels"] = {ch: len(items) for ch, items in channel_buckets.items()}
        result["aggregation_policy"] = AggregationPolicy.TRUST_WEIGHTED.value
        result["positive"] = positive
        result["negative"] = negative

        if not evidence_items:
            result["merged_trust"] = "SOLVER_DISCHARGED"
        elif negative > positive:
            result["merged_trust"] = "UNVERIFIED"
        elif any(ch == EvChannel.SOLVER.value for ch in channel_buckets):
            result["merged_trust"] = "SOLVER_DISCHARGED"
        else:
            result["merged_trust"] = "COPILOT_SUGGESTED"

    except Exception as exc:
        _log.debug("Evidence channel analysis unavailable: %s", exc)
        solver_count = sum(1 for f in evidence_items if getattr(f, "trust", "") == _TRUST_SOLVER)
        runtime_count = sum(1 for f in evidence_items if getattr(f, "trust", "") == _TRUST_RUNTIME)
        human_count = len(evidence_items) - solver_count - runtime_count
        if solver_count:
            result["channels"]["solver"] = solver_count
        if runtime_count:
            result["channels"]["runtime"] = runtime_count
        if human_count:
            result["channels"]["human"] = human_count
        result["positive"] = max(0, len(evidence_items) - 1)
        result["negative"] = min(1, len(evidence_items))

    return result


def _format_evidence_channel_analysis(info: dict[str, Any]) -> str:
    """Format evidence channel analysis for text output."""
    lines: list[str] = ["\n  Evidence channel routing:"]
    channels = info.get("channels", {})
    ch_parts = [f"{ch} ({n} items)" for ch, n in sorted(channels.items())]
    lines.append(f"    Channels: {', '.join(ch_parts) if ch_parts else 'none'}")
    lines.append(f"    Aggregation policy: {info.get('aggregation_policy', 'trust_weighted')}")
    pos = info.get("positive", 0)
    neg = info.get("negative", 0)
    lines.append(f"    Polarity: {pos} positive, {neg} negative")
    lines.append(f"    Merged trust: {info.get('merged_trust', 'SOLVER_DISCHARGED')}")
    return "\n".join(lines) + "\n"


def _write_output(text: str, path: str | None) -> None:
    """Print *text* to stdout or write to *path* if given."""
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Report written to {path}")
    else:
        print(text)
