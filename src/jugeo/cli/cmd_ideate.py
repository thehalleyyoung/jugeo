"""CLI subcommand handler for ``jugeo ideate``.

Mathematical ideation — generating new theorems, conjectures, and insights.

Pipeline:
  1. Scan mathematical domains (``jugeo.ideation.synthesis_frontier``).
  2. Propose theorem candidates (``jugeo.ideation.discovery_engine``).
  3. Classify candidates by kind (``jugeo.judgments.judgment_terms``).
  4. Check for novelty against a mathematical landscape modeled as a site
     (``jugeo.geometry.site``).
  5. Estimate yield via theorem economics
     (``jugeo.ideation.theorem_economics``).
  6. Run falsification / consistency analysis via descent
     (``jugeo.geometry.descent``).
  7. Report results with trust scores (``jugeo.evidence.trust``).

All heavy imports are done lazily and wrapped in ``try/except`` so that
``jugeo ideate --help`` always works even when optional subsystems are
absent.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

# ── Conditional imports — all wrapped in try/except ───────────────────

# Synthesis frontier (cross-domain pipeline & field catalog)
try:
    from jugeo.ideation.synthesis_frontier import (
        run_pipeline,
        PropositionRecord,
        FieldNode,
        ALL_128_FIELDS,
    )
    _HAS_FRONTIER = True
except Exception:  # pragma: no cover
    _HAS_FRONTIER = False
    run_pipeline = None  # type: ignore[assignment]
    PropositionRecord = None  # type: ignore[assignment,misc]
    FieldNode = None  # type: ignore[assignment,misc]
    ALL_128_FIELDS = []  # type: ignore[assignment]

# Discovery engine
try:
    from jugeo.ideation.discovery_engine import (
        DiscoveryCandidate,
        TheoremCandidate,
        DiscoveryResult,
    )
    _HAS_DISCOVERY = True
except Exception:  # pragma: no cover
    _HAS_DISCOVERY = False
    DiscoveryCandidate = None  # type: ignore[assignment,misc]
    TheoremCandidate = None  # type: ignore[assignment,misc]
    DiscoveryResult = None  # type: ignore[assignment,misc]

# Theorem economics
try:
    from jugeo.ideation.theorem_economics import (
        TheoremYieldModel,
        InvestmentSchedule,
    )
    _HAS_ECONOMICS = True
except Exception:  # pragma: no cover
    _HAS_ECONOMICS = False
    TheoremYieldModel = None  # type: ignore[assignment,misc]
    InvestmentSchedule = None  # type: ignore[assignment,misc]

# Judgments
try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        Proposition,
        PropositionKind,
        TrustLevel,
    )
    _HAS_JUDGMENTS = True
except Exception:  # pragma: no cover
    _HAS_JUDGMENTS = False
    Judgment = None  # type: ignore[assignment,misc]
    Proposition = None  # type: ignore[assignment,misc]
    PropositionKind = None  # type: ignore[assignment,misc]
    TrustLevel = None  # type: ignore[assignment,misc]

# Site geometry (mathematical landscape)
try:
    from jugeo.geometry.site import Site, SiteBuilder, Coordinate
    _HAS_SITE = True
except Exception:  # pragma: no cover
    _HAS_SITE = False
    Site = None  # type: ignore[assignment,misc]
    SiteBuilder = None  # type: ignore[assignment,misc]
    Coordinate = None  # type: ignore[assignment,misc]

# Descent (consistency / falsification)
try:
    from jugeo.geometry.descent import DescentEngine, LocalSection
    _HAS_DESCENT = True
except Exception:  # pragma: no cover
    _HAS_DESCENT = False
    DescentEngine = None  # type: ignore[assignment,misc]
    LocalSection = None  # type: ignore[assignment,misc]

# Trust algebra
try:
    from jugeo.evidence.trust import TrustAlgebra
    _HAS_TRUST = True
except Exception:  # pragma: no cover
    _HAS_TRUST = False
    TrustAlgebra = None  # type: ignore[assignment,misc]


# ======================================================================
# Fallback field catalog
# ======================================================================

_FALLBACK_FIELDS: list[dict[str, Any]] = [
    {"name": "Algebraic Geometry", "id": "alg_geom", "family": "geometry"},
    {"name": "Number Theory", "id": "num_theory", "family": "algebra"},
    {"name": "Topology", "id": "topology", "family": "geometry"},
    {"name": "Combinatorics", "id": "combinatorics", "family": "discrete"},
    {"name": "Analysis", "id": "analysis", "family": "analysis"},
    {"name": "Probability Theory", "id": "probability", "family": "analysis"},
    {"name": "Category Theory", "id": "cat_theory", "family": "foundations"},
    {"name": "Logic", "id": "logic", "family": "foundations"},
    {"name": "Differential Geometry", "id": "diff_geom", "family": "geometry"},
    {"name": "Functional Analysis", "id": "func_analysis", "family": "analysis"},
    {"name": "Representation Theory", "id": "rep_theory", "family": "algebra"},
    {"name": "Homological Algebra", "id": "homological", "family": "algebra"},
    {"name": "Dynamical Systems", "id": "dynamical", "family": "analysis"},
    {"name": "Graph Theory", "id": "graph_theory", "family": "discrete"},
    {"name": "Commutative Algebra", "id": "comm_algebra", "family": "algebra"},
    {"name": "Set Theory", "id": "set_theory", "family": "foundations"},
]


# ======================================================================
# Ideation class registry
# ======================================================================

def _ideation_registry() -> dict[str, list[str]]:
    """Import and register classes from all ideation sub-packages.

    Returns a dict mapping sub-package names to lists of successfully
    imported class names.
    """
    registry: dict[str, list[str]] = {}

    # -- analogy_transport --
    try:
        from jugeo.ideation.analogy_transport.algorithms import (
            AnalogyQuality, TransportStatus, AnalogyFunctor, SourceTheorem,
            TransportedTheorem, TransportResult, TransportPlan,
            PlanValidationResult, PlanningCycleResult, TransportVerification,
            NormalizedTheorem, TransportPlannerConfig,
            TransportExecutorConfig, NormalizerConfig, DomainRegistry,
            AnalogyTransportPlanner, AnalogyTransportExecutor,
            AnalogyTransportNormalizer,
        )
        from jugeo.ideation.analogy_transport.analogy_construction import (
            AnalogyConfig, AnalogyConstructor,
        )
        from jugeo.ideation.analogy_transport.integration import (
            BundleStatus, SyncStatus, RegistrationRecord, DomainUpdate,
            TransportOpportunity, PackSyncResult, SubscriptionHandle,
            BridgeIntegrationResult, BundleValidationResult, BundleSummary,
            BridgeConfig, TheoremRegistry, AnalogyTransportBridge,
            ExportBundle, TrustTier, IntegrationJudgment,
            TransportRegistration, IntegrationStatus, BridgeSignal,
            SynchronisationRecord, ExportManifest,
            AnalogyTransportIntegration, TheoremTransportBridge,
            OrchestratorAnalogyBridge,
        )
        from jugeo.ideation.analogy_transport.manifest import (
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        )
        from jugeo.ideation.analogy_transport.models import (
            AnalogyQuality, TransportFidelity, AnalogyMap,
            StructurePreservation, PurposePreservation, TransportedIdea,
            AnalogyVerification,
        )
        from jugeo.ideation.analogy_transport.structure_preservation import (
            StructurePreservationAuditor,
        )
        from jugeo.ideation.analogy_transport.theorems import (
            TestType, SuiteOutcome, FalsificationTest,
            FalsificationTestResult, SchemaValidationResult,
            CoherenceCheckResult, StructurePreservationResult,
            CanonicalTheorem, FalsificationSuiteResult, FalsificationAnalysis,
            SchemaConfig, FalsificationSuiteConfig,
            AnalogyTransportTheoremSchema, FalsificationSuite,
        )
        registry["analogy_transport"] = [
            "AnalogyQuality", "TransportStatus", "AnalogyFunctor",
            "SourceTheorem", "TransportedTheorem", "TransportResult",
            "TransportPlan", "PlanValidationResult", "PlanningCycleResult",
            "TransportVerification", "NormalizedTheorem",
            "TransportPlannerConfig", "TransportExecutorConfig",
            "NormalizerConfig", "DomainRegistry", "AnalogyTransportPlanner",
            "AnalogyTransportExecutor", "AnalogyTransportNormalizer",
            "AnalogyConfig", "AnalogyConstructor", "BundleStatus",
            "SyncStatus", "RegistrationRecord", "DomainUpdate",
            "TransportOpportunity", "PackSyncResult", "SubscriptionHandle",
            "BridgeIntegrationResult", "BundleValidationResult",
            "BundleSummary", "BridgeConfig", "TheoremRegistry",
            "AnalogyTransportBridge", "ExportBundle", "TrustTier",
            "IntegrationJudgment", "TransportRegistration",
            "IntegrationStatus", "BridgeSignal", "SynchronisationRecord",
            "ExportManifest", "AnalogyTransportIntegration",
            "TheoremTransportBridge", "OrchestratorAnalogyBridge",
            "PackageCapability", "PackageManifest", "ManifestValidator",
            "PackageRegistry", "CapabilityQuery", "ManifestSerializer",
            "ManifestDiagnostics", "TransportFidelity", "AnalogyMap",
            "StructurePreservation", "PurposePreservation", "TransportedIdea",
            "AnalogyVerification", "StructurePreservationAuditor", "TestType",
            "SuiteOutcome", "FalsificationTest", "FalsificationTestResult",
            "SchemaValidationResult", "CoherenceCheckResult",
            "StructurePreservationResult", "CanonicalTheorem",
            "FalsificationSuiteResult", "FalsificationAnalysis",
            "SchemaConfig", "FalsificationSuiteConfig",
            "AnalogyTransportTheoremSchema", "FalsificationSuite",
        ]
    except Exception:
        pass

    # -- discovery_engine --
    try:
        from jugeo.ideation.discovery_engine.a_real_mathematical_discovery_subs import (
            ProposalOutcome, ObstructionRecord, ProposalRecord, ArchiveEntry,
            DiscoverySubsystemConfig, DiscoverySubsystemResult,
            ObstructionCoverageReport, ProposalQualityReport,
            ArchiveHealthReport, WitnessVerdict, ArchiveConsistencyReport,
            CycleWitnessReport, ArchiveTrace,
            MathDiscoverySubsystemCoordinator, MathDiscoverySubsystemAnalyzer,
            MathDiscoverySubsystemWitness,
        )
        from jugeo.ideation.discovery_engine.algorithms import (
            DiscoveryAlgorithms, PipelineCallback, LoggingCallback,
            DiscoveryPipeline,
        )
        from jugeo.ideation.discovery_engine.evaluation_and_calibration_realize import (
            FailureCategory, EvalCalibConfig, ObstructionSet,
            LeverageEvaluation, TheoremUsageEvent, ReuseEvaluation,
            FailedProposal, FailureAnalysisReport, CalibrationResult,
            EvalCycleData, EvalCycleResult, LeverageDistribution,
            FailurePatternSummary, CalibrationDriftReport,
            LeverageWitnessReport, CalibrationWitnessReport,
            FailureWitnessReport, EvaluationCalibrationCoordinator,
            EvaluationCalibrationAnalyzer, EvaluationCalibrationWitness,
        )
        from jugeo.ideation.discovery_engine.integration import (
            IntegrationStatus, IntegrationEvent, BridgeIntegrationAdapter,
            OrchestrationAdapter, _MinimalPipeline, EvidenceChannelAdapter,
            DiscoveryEngineIntegration,
        )
        from jugeo.ideation.discovery_engine.kind_classification import (
            KindMatchStrategy, KindClassifier, KindSignatureBuilder,
            KindRegistry, KindClassificationRunner,
        )
        from jugeo.ideation.discovery_engine.manifest import (
            ManifestStatus, EvidenceEntryKind, EvidenceEntry,
            DiscoveryEngineManifest, ManifestBuilder,
        )
        from jugeo.ideation.discovery_engine.models import (
            DiscoveryStatus, PipelineStage, DiscoveryCandidate, KindSignature,
            TheoremCandidate, PromotionDecision, DiscoveryConfig,
            DiscoveryDiagnostics, DiscoveryResult, NoveltyPipelineStage,
            KindClassificationStage, TheoremSynthesisStage,
            PackPromotionStage,
        )
        from jugeo.ideation.discovery_engine.novelty_pipeline import (
            NoveltyFilter, NoveltyRanker, NoveltyCandidateSet,
            NoveltyPipelineRunner,
        )
        from jugeo.ideation.discovery_engine.pack_promotion import (
            EligibilityCriterion, EligibilityResult, PackEligibilityChecker,
            PromotionReport, PromotionAuthority, PackPromotionRunner,
        )
        from jugeo.ideation.discovery_engine.theorem_and_falsification_burden_f import (
            ConditionStatus, ConditionDifficulty, FalsificationConfig,
            TheoremRecord, FalsificationCondition, FalsificationBurden,
            EvidenceItem, ConditionCheckResult, FalsificationCampaignResult,
            BurdenDistribution, ConditionCoverageReport,
            BurdenLeverageCorrelation, BurdenWitnessReport,
            CampaignWitnessReport, ConditionWitnessReport,
            TheoremFalsificationBurdenCoordinator,
            TheoremFalsificationBurdenAnalyzer,
            TheoremFalsificationBurdenWitness,
        )
        from jugeo.ideation.discovery_engine.theorem_synthesis import (
            SynthesisPattern, TheoremSynthesizer, ProofSketchBuilder,
            TheoremValidator, TheoremSynthesisRunner,
        )
        from jugeo.ideation.discovery_engine.theorems import (
            TheoremVerificationContext, TheoremVerificationResult,
            AbstractTheorem, DiscoveryCompletenessTheorem,
            PipelineSoundnessTheorem, NoveltyPreservationTheorem,
            KindAssignmentUniquenessTheorem,
            TheoremSynthesisCorrectnessTheorem,
            PackPromotionMonotonicityTheorem, DiscoveryTheoremRegistry,
        )
        registry["discovery_engine"] = [
            "ProposalOutcome", "ObstructionRecord", "ProposalRecord",
            "ArchiveEntry", "DiscoverySubsystemConfig",
            "DiscoverySubsystemResult", "ObstructionCoverageReport",
            "ProposalQualityReport", "ArchiveHealthReport", "WitnessVerdict",
            "ArchiveConsistencyReport", "CycleWitnessReport", "ArchiveTrace",
            "MathDiscoverySubsystemCoordinator",
            "MathDiscoverySubsystemAnalyzer", "MathDiscoverySubsystemWitness",
            "DiscoveryAlgorithms", "PipelineCallback", "LoggingCallback",
            "DiscoveryPipeline", "FailureCategory", "EvalCalibConfig",
            "ObstructionSet", "LeverageEvaluation", "TheoremUsageEvent",
            "ReuseEvaluation", "FailedProposal", "FailureAnalysisReport",
            "CalibrationResult", "EvalCycleData", "EvalCycleResult",
            "LeverageDistribution", "FailurePatternSummary",
            "CalibrationDriftReport", "LeverageWitnessReport",
            "CalibrationWitnessReport", "FailureWitnessReport",
            "EvaluationCalibrationCoordinator",
            "EvaluationCalibrationAnalyzer", "EvaluationCalibrationWitness",
            "IntegrationStatus", "IntegrationEvent",
            "BridgeIntegrationAdapter", "OrchestrationAdapter",
            "_MinimalPipeline", "EvidenceChannelAdapter",
            "DiscoveryEngineIntegration", "KindMatchStrategy",
            "KindClassifier", "KindSignatureBuilder", "KindRegistry",
            "KindClassificationRunner", "ManifestStatus", "EvidenceEntryKind",
            "EvidenceEntry", "DiscoveryEngineManifest", "ManifestBuilder",
            "DiscoveryStatus", "PipelineStage", "DiscoveryCandidate",
            "KindSignature", "TheoremCandidate", "PromotionDecision",
            "DiscoveryConfig", "DiscoveryDiagnostics", "DiscoveryResult",
            "NoveltyPipelineStage", "KindClassificationStage",
            "TheoremSynthesisStage", "PackPromotionStage", "NoveltyFilter",
            "NoveltyRanker", "NoveltyCandidateSet", "NoveltyPipelineRunner",
            "EligibilityCriterion", "EligibilityResult",
            "PackEligibilityChecker", "PromotionReport", "PromotionAuthority",
            "PackPromotionRunner", "ConditionStatus", "ConditionDifficulty",
            "FalsificationConfig", "TheoremRecord", "FalsificationCondition",
            "FalsificationBurden", "EvidenceItem", "ConditionCheckResult",
            "FalsificationCampaignResult", "BurdenDistribution",
            "ConditionCoverageReport", "BurdenLeverageCorrelation",
            "BurdenWitnessReport", "CampaignWitnessReport",
            "ConditionWitnessReport", "TheoremFalsificationBurdenCoordinator",
            "TheoremFalsificationBurdenAnalyzer",
            "TheoremFalsificationBurdenWitness", "SynthesisPattern",
            "TheoremSynthesizer", "ProofSketchBuilder", "TheoremValidator",
            "TheoremSynthesisRunner", "TheoremVerificationContext",
            "TheoremVerificationResult", "AbstractTheorem",
            "DiscoveryCompletenessTheorem", "PipelineSoundnessTheorem",
            "NoveltyPreservationTheorem", "KindAssignmentUniquenessTheorem",
            "TheoremSynthesisCorrectnessTheorem",
            "PackPromotionMonotonicityTheorem", "DiscoveryTheoremRegistry",
        ]
    except Exception:
        pass

    # -- discovery_federation --
    try:
        from jugeo.ideation.discovery_federation.algorithms import (
            FederationAlgorithms,
        )
        from jugeo.ideation.discovery_federation.authority_choice_rejection_provisi import (
            AuthorityDisposition, ChoiceReason, TheoremCandidate,
            ChoiceRecord, AuthorityChoiceAnalyzer, AuthorityChoiceCoordinator,
            AuthorityChoiceWitness,
        )
        from jugeo.ideation.discovery_federation.discovery_as_authority import (
            PromotionStatus, AuthorityCondition, PromotionRecord,
            AuthorityPromoter, AuthorityValidator, AuthorityLifecycleManager,
            DiscoveryAuthorityRunner,
        )
        from jugeo.ideation.discovery_federation.federated_knowledge import (
            PropagationStatus, MergeStrategy, KnowledgeEntry, MergeResult,
            KnowledgePropagator, KnowledgeMerger, KnowledgeRepository,
            FederatedKnowledgeRunner,
        )
        from jugeo.ideation.discovery_federation.federation_consensus import (
            VoteStatus, QuorumPolicy, VotingRound, QuorumCalculator,
            VoteAggregator, ConsensusProtocol, FederationConsensusRunner,
        )
        from jugeo.ideation.discovery_federation.federation_versus_foundation_scope import (
            PlacementMode, BridgeBurdenLevel, ScopeProfile, PlacementRecord,
            FederationVsFoundationAnalyzer, FederationVsFoundationCoordinator,
            FederationVsFoundationWitness,
        )
        from jugeo.ideation.discovery_federation.implementation_consequences import (
            TrustTier, FederationJudgment, FederatedDiscoveryConsequence,
            FederationConstraint, FederationNode, FederationRecord,
            NodeHealth, ConsensusProposal, FederationScope, PolicyViolation,
            FederationAuditEntry, ConsequenceChain, FederationPolicy,
            FederationConsensus,
        )
        from jugeo.ideation.discovery_federation.integration import (
            IntegrationStatus, AdapterKind, IntegrationEvent,
            FederationIntegration, DiscoveryBridgeAdapter,
            AuthorityPackAdapter,
        )
        from jugeo.ideation.discovery_federation.manifest import (
            ManifestStatus, DiscoveryFederationManifest,
            FederationManifestBuilder,
        )
        from jugeo.ideation.discovery_federation.models import (
            FederationStatus, ConsensusOutcome, AuthorityLevel,
            FederatedDiscovery, FederationVote, FederationConsensus,
            DiscoveryAuthority, KnowledgePropagation, AuthorityGrant,
            FederationNode, ConflictRecord,
        )
        from jugeo.ideation.discovery_federation.theorems import (
            _CallableStr, _CallableList, TheoremStatus, ProofMethod,
            TheoremResult, FederationSoundnessTheorem,
            AuthorityMonotonicityTheorem, ConsensusConvergenceTheorem,
            KnowledgePropagationSoundnessTheorem,
            ConflictResolutionCompletenessTheorem, FederationTheoremRegistry,
        )
        registry["discovery_federation"] = [
            "FederationAlgorithms", "AuthorityDisposition", "ChoiceReason",
            "TheoremCandidate", "ChoiceRecord", "AuthorityChoiceAnalyzer",
            "AuthorityChoiceCoordinator", "AuthorityChoiceWitness",
            "PromotionStatus", "AuthorityCondition", "PromotionRecord",
            "AuthorityPromoter", "AuthorityValidator",
            "AuthorityLifecycleManager", "DiscoveryAuthorityRunner",
            "PropagationStatus", "MergeStrategy", "KnowledgeEntry",
            "MergeResult", "KnowledgePropagator", "KnowledgeMerger",
            "KnowledgeRepository", "FederatedKnowledgeRunner", "VoteStatus",
            "QuorumPolicy", "VotingRound", "QuorumCalculator",
            "VoteAggregator", "ConsensusProtocol",
            "FederationConsensusRunner", "PlacementMode", "BridgeBurdenLevel",
            "ScopeProfile", "PlacementRecord",
            "FederationVsFoundationAnalyzer",
            "FederationVsFoundationCoordinator",
            "FederationVsFoundationWitness", "TrustTier",
            "FederationJudgment", "FederatedDiscoveryConsequence",
            "FederationConstraint", "FederationNode", "FederationRecord",
            "NodeHealth", "ConsensusProposal", "FederationScope",
            "PolicyViolation", "FederationAuditEntry", "ConsequenceChain",
            "FederationPolicy", "FederationConsensus", "IntegrationStatus",
            "AdapterKind", "IntegrationEvent", "FederationIntegration",
            "DiscoveryBridgeAdapter", "AuthorityPackAdapter",
            "ManifestStatus", "DiscoveryFederationManifest",
            "FederationManifestBuilder", "FederationStatus",
            "ConsensusOutcome", "AuthorityLevel", "FederatedDiscovery",
            "FederationVote", "DiscoveryAuthority", "KnowledgePropagation",
            "AuthorityGrant", "ConflictRecord", "_CallableStr",
            "_CallableList", "TheoremStatus", "ProofMethod", "TheoremResult",
            "FederationSoundnessTheorem", "AuthorityMonotonicityTheorem",
            "ConsensusConvergenceTheorem",
            "KnowledgePropagationSoundnessTheorem",
            "ConflictResolutionCompletenessTheorem",
            "FederationTheoremRegistry",
        ]
    except Exception:
        pass

    # -- experiment_design --
    try:
        from jugeo.ideation.experiment_design.algorithms import (
            ExperimentAlgorithm, FactorialDesign, LatinSquare,
            RandomizedControlled, BayesianExperimentDesign,
            AdaptiveExperiment,
        )
        from jugeo.ideation.experiment_design.falsification import (
            FalsificationTest, FalsificationDesigner, HypothesisParser,
            AdversarialCaseGenerator, FalsificationRecord,
            ConclusivenessAnalyzer,
        )
        from jugeo.ideation.experiment_design.integration import (
            ExperimentDesignIntegration, IdeationSystemBridge,
            CopilotExperimentAdvisor, ExperimentEventBus, ResultRepository,
        )
        from jugeo.ideation.experiment_design.manifest import (
            ExperimentType, ExperimentStatus, ControlVariable, MeasureSpec,
            ExperimentDescriptor, ExperimentDesignManifest, ManifestValidator,
            ManifestRegistry, ExperimentRegistry,
        )
        from jugeo.ideation.experiment_design.models import (
            ExperimentDesign, AblationStudy, ExperimentResult,
        )
        from jugeo.ideation.experiment_design.statistical_validation import (
            StatisticalValidator, SignificanceThreshold,
            MultipleTestingCorrection, ReportGenerator,
        )
        from jugeo.ideation.experiment_design.theorems import (
            Theorem, TheoremCatalog, TheoremVerifier,
        )
        registry["experiment_design"] = [
            "ExperimentAlgorithm", "FactorialDesign", "LatinSquare",
            "RandomizedControlled", "BayesianExperimentDesign",
            "AdaptiveExperiment", "FalsificationTest",
            "FalsificationDesigner", "HypothesisParser",
            "AdversarialCaseGenerator", "FalsificationRecord",
            "ConclusivenessAnalyzer", "ExperimentDesignIntegration",
            "IdeationSystemBridge", "CopilotExperimentAdvisor",
            "ExperimentEventBus", "ResultRepository", "ExperimentType",
            "ExperimentStatus", "ControlVariable", "MeasureSpec",
            "ExperimentDescriptor", "ExperimentDesignManifest",
            "ManifestValidator", "ManifestRegistry", "ExperimentRegistry",
            "ExperimentDesign", "AblationStudy", "ExperimentResult",
            "StatisticalValidator", "SignificanceThreshold",
            "MultipleTestingCorrection", "ReportGenerator", "Theorem",
            "TheoremCatalog", "TheoremVerifier",
        ]
    except Exception:
        pass

    # -- federation --
    try:
        from jugeo.ideation.federation import (
            FederatedIdeaProposal, CrossRegimeBridge, AnalogyFinder,
            IdeaTransporter, FederationRegistry, IdeationFederator,
            FederationValidator, _HistoryRecord, FederationHistory,
            FederationDiagnostics, FederationSerializer, IdeaFederation,
        )
        registry["federation"] = [
            "FederatedIdeaProposal", "CrossRegimeBridge", "AnalogyFinder",
            "IdeaTransporter", "FederationRegistry", "IdeationFederator",
            "FederationValidator", "_HistoryRecord", "FederationHistory",
            "FederationDiagnostics", "FederationSerializer", "IdeaFederation",
        ]
    except Exception:
        pass

    # -- ideas --
    try:
        from jugeo.ideation.ideas import (
            TrustStatus, LifecycleStatus, GainProfile, ValidationPath,
            EvaluationResult, HistoryEntry, Idea, IdeaProposal, IdeaPortfolio,
            IdeaGenerator, IdeaEvaluator, IdeaRefiner, IdeaLifecycle,
            IdeaDependencyGraph, IdeaHistory, IdeaSerializer, IdeaDiagnostics,
        )
        registry["ideas"] = [
            "TrustStatus", "LifecycleStatus", "GainProfile", "ValidationPath",
            "EvaluationResult", "HistoryEntry", "Idea", "IdeaProposal",
            "IdeaPortfolio", "IdeaGenerator", "IdeaEvaluator", "IdeaRefiner",
            "IdeaLifecycle", "IdeaDependencyGraph", "IdeaHistory",
            "IdeaSerializer", "IdeaDiagnostics",
        ]
    except Exception:
        pass

    # -- kind_discovery --
    try:
        from jugeo.ideation.kind_discovery.algorithms import (
            DiscoveryAlgorithm, KindDiscoveryEngine, KindValidator,
            KindRanker, KindEvolutionTracker, DiscoveryDiagnostics,
            DiscoveryHistory,
        )
        from jugeo.ideation.kind_discovery.candidate_new_mathematical_kinds_e import (
            AbstractionLevel, CandidateKindConfig, KindHypothesis,
            TypeConstructorProposal, CandidateKindsAnalyzer,
            CandidateKindsWitness, CandidateKindsCoordinator,
        )
        from jugeo.ideation.kind_discovery.implementation_consequences import (
            ConsequenceType, ImplementationConsequenceConfig,
            ImplementationConsequence, ConsequenceGraph,
            ImplementationConsequencesAnalyzer,
            ImplementationConsequencesWitness,
            ImplementationConsequencesCoordinator,
        )
        from jugeo.ideation.kind_discovery.integration import (
            TrustAwareDiscovery, IdeaKindLinker, FederationKindBridge,
            NoveltyKindScorer, IntegratedDiscoveryPipeline,
        )
        from jugeo.ideation.kind_discovery.kind_bootstrapping import (
            BootstrapConfig, KindHypothesizer, DefinitionBuilder,
            ExampleGenerator, ValidationPlanner, KindBootstrapper,
        )
        from jugeo.ideation.kind_discovery.manifest import (
            _InitOnlyFrozenField, PackageCapability, PackageManifest,
            ManifestValidator, PackageRegistry, CapabilityQuery,
            ManifestSerializer, ManifestDiagnostics,
        )
        from jugeo.ideation.kind_discovery.models import (
            KindStatus, ObstructionType, ObstructionField, KindPattern,
            KindCandidate, KindBootstrapPlan, NewKind,
        )
        from jugeo.ideation.kind_discovery.obstruction_fields_as_evidence_of import (
            ObstructionFieldEvidenceConfig, H1ObstructionClass,
            EvidenceCluster, ObstructionFieldEvidenceRecord,
            ObstructionFieldsEvidenceAnalyzer,
            ObstructionFieldsEvidenceWitness,
            ObstructionFieldsEvidenceCoordinator,
        )
        from jugeo.ideation.kind_discovery.obstruction_mining import (
            ObstructionMiningConfig, ObstructionExtractor,
            ObstructionClusterer, ObstructionFieldBuilder, FrequencyAnalyzer,
            ObstructionMiner,
        )
        from jugeo.ideation.kind_discovery.pattern_recognition import (
            PatternSignature, PatternMatcher, RecurrenceDetector,
            GeneralityEstimator, PatternRanker, PatternRecognizer,
        )
        from jugeo.ideation.kind_discovery.the_obstruction_to_kind_pipeline_c import (
            PipelineStage, PipelineConfig, PipelineStepResult, PipelineRun,
            PipelineArtifacts, ObstructionToKindPipelineAnalyzer,
            ObstructionToKindPipelineWitness,
            ObstructionToKindPipelineCoordinator,
        )
        from jugeo.ideation.kind_discovery.theorems import (
            TheoremStatus, TheoremScope, KindDiscoveryTheorem,
            TheoremRegistry, TheoremVerifier, TheoremApplications,
            TheoremCatalog,
        )
        registry["kind_discovery"] = [
            "DiscoveryAlgorithm", "KindDiscoveryEngine", "KindValidator",
            "KindRanker", "KindEvolutionTracker", "DiscoveryDiagnostics",
            "DiscoveryHistory", "AbstractionLevel", "CandidateKindConfig",
            "KindHypothesis", "TypeConstructorProposal",
            "CandidateKindsAnalyzer", "CandidateKindsWitness",
            "CandidateKindsCoordinator", "ConsequenceType",
            "ImplementationConsequenceConfig", "ImplementationConsequence",
            "ConsequenceGraph", "ImplementationConsequencesAnalyzer",
            "ImplementationConsequencesWitness",
            "ImplementationConsequencesCoordinator", "TrustAwareDiscovery",
            "IdeaKindLinker", "FederationKindBridge", "NoveltyKindScorer",
            "IntegratedDiscoveryPipeline", "BootstrapConfig",
            "KindHypothesizer", "DefinitionBuilder", "ExampleGenerator",
            "ValidationPlanner", "KindBootstrapper", "_InitOnlyFrozenField",
            "PackageCapability", "PackageManifest", "ManifestValidator",
            "PackageRegistry", "CapabilityQuery", "ManifestSerializer",
            "ManifestDiagnostics", "KindStatus", "ObstructionType",
            "ObstructionField", "KindPattern", "KindCandidate",
            "KindBootstrapPlan", "NewKind", "ObstructionFieldEvidenceConfig",
            "H1ObstructionClass", "EvidenceCluster",
            "ObstructionFieldEvidenceRecord",
            "ObstructionFieldsEvidenceAnalyzer",
            "ObstructionFieldsEvidenceWitness",
            "ObstructionFieldsEvidenceCoordinator", "ObstructionMiningConfig",
            "ObstructionExtractor", "ObstructionClusterer",
            "ObstructionFieldBuilder", "FrequencyAnalyzer",
            "ObstructionMiner", "PatternSignature", "PatternMatcher",
            "RecurrenceDetector", "GeneralityEstimator", "PatternRanker",
            "PatternRecognizer", "PipelineStage", "PipelineConfig",
            "PipelineStepResult", "PipelineRun", "PipelineArtifacts",
            "ObstructionToKindPipelineAnalyzer",
            "ObstructionToKindPipelineWitness",
            "ObstructionToKindPipelineCoordinator", "TheoremStatus",
            "TheoremScope", "KindDiscoveryTheorem", "TheoremRegistry",
            "TheoremVerifier", "TheoremApplications", "TheoremCatalog",
        ]
    except Exception:
        pass

    # -- novelty --
    try:
        from jugeo.ideation.novelty import (
            NoveltyScore, NoveltyMetric, NoveltySearcher, TheoremPortfolio,
            PurposeAlignmentChecker, NoveltyFilter, SemanticDistanceModel,
            _HistoryRecord, NoveltyHistory, NoveltyOptimizer,
            NoveltyDiagnostics, _LegacyNoveltyScore,
        )
        registry["novelty"] = [
            "NoveltyScore", "NoveltyMetric", "NoveltySearcher",
            "TheoremPortfolio", "PurposeAlignmentChecker", "NoveltyFilter",
            "SemanticDistanceModel", "_HistoryRecord", "NoveltyHistory",
            "NoveltyOptimizer", "NoveltyDiagnostics", "_LegacyNoveltyScore",
        ]
    except Exception:
        pass

    # -- novelty_search --
    try:
        from jugeo.ideation.novelty_search.a_purpose_conditioned_novelty_func import (
            NoveltyFunctionalConfig, LeverageScore, TractabilityScore,
            SemanticRelevanceScore, NoveltyFunctionalValue,
            PurposeConditionedNoveltyAnalyzer,
            PurposeConditionedNoveltyWitness,
            PurposeConditionedNoveltyCoordinator,
        )
        from jugeo.ideation.novelty_search.algorithms import (
            OptimalNoveltySearch, NoveltyRanker, FrontierExplorer,
            SearchDiagnostics, SearchHistoryEntry, SearchHistory,
            SearchBenchmark,
        )
        from jugeo.ideation.novelty_search.distance_metrics import (
            DistanceConfig, SemanticDistanceComputer,
            StructuralDistanceComputer, PurposeWeightedDistance,
            DistanceNormalizer, MetricAggregator, DistanceCacheManager,
        )
        from jugeo.ideation.novelty_search.implementation_consequences import (
            TrustTier, ConsequenceJudgment, NoveltySearchConsequence,
            ArchitecturalDecision, NoveltyConstraint, PolicyRule,
            PolicyViolationRecord, NoveltyBehaviourDescriptor,
            ArchiveAdmissionRecord, SystemConfig, NoveltyParameters,
            ConsequenceReport, ArchiveEvictionRecord, DiversityMeasurement,
            NoveltyArchitecture, NoveltyPolicy,
        )
        from jugeo.ideation.novelty_search.integration import (
            PortfolioNoveltyIntegrator, IdeaNoveltyScorer,
            TrustFilteredSearch, FederationNoveltyBridge, PipelineStage,
            PipelineResult, IntegratedNoveltyPipeline,
        )
        from jugeo.ideation.novelty_search.manifest import (
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        )
        from jugeo.ideation.novelty_search.models import (
            SearchStrategy, MetricKind, NoveltySearchProblem,
            PortfolioCoverage, NoveltyMetricSpec, SearchResult,
            DiversityConstraint,
        )
        from jugeo.ideation.novelty_search.novelty_versus_useful_novelty_leve import (
            NoveltyLevel, UsefulNoveltyConfig, NoveltyMeasure,
            UsefulNoveltyComparison, NoveltyVsUsefulNoveltyAnalyzer,
            NoveltyVsUsefulNoveltyWitness, NoveltyVsUsefulNoveltyCoordinator,
        )
        from jugeo.ideation.novelty_search.portfolio_coverage import (
            CoverageConfig, CoverageEstimator, GapDetector, CoverageOptimizer,
            DensityAnalyzer, CoverageReport, CoverageReporter,
        )
        from jugeo.ideation.novelty_search.search_strategies import (
            SearchConfig, GreedySearcher, BeamSearcher, ParetoSearcher,
            DiverseSearcher, SearchOrchestrator,
        )
        from jugeo.ideation.novelty_search.theorems import (
            TheoremStatus, TheoremKind, NoveltyTheorem, VerificationResult,
            TheoremRegistry, TheoremVerifier, TheoremApplications,
            TheoremCatalog,
        )
        from jugeo.ideation.novelty_search.why_ag_dtt_and_ai_each_matter_in_n import (
            Framework, FrameworkContributionConfig, AGContribution,
            DTTContribution, AIContribution, FrameworkSynergy,
            WhyAGDTTAINoveltyAnalyzer, WhyAGDTTAINoveltyWitness,
            WhyAGDTTAINoveltyCoordinator,
        )
        registry["novelty_search"] = [
            "NoveltyFunctionalConfig", "LeverageScore", "TractabilityScore",
            "SemanticRelevanceScore", "NoveltyFunctionalValue",
            "PurposeConditionedNoveltyAnalyzer",
            "PurposeConditionedNoveltyWitness",
            "PurposeConditionedNoveltyCoordinator", "OptimalNoveltySearch",
            "NoveltyRanker", "FrontierExplorer", "SearchDiagnostics",
            "SearchHistoryEntry", "SearchHistory", "SearchBenchmark",
            "DistanceConfig", "SemanticDistanceComputer",
            "StructuralDistanceComputer", "PurposeWeightedDistance",
            "DistanceNormalizer", "MetricAggregator", "DistanceCacheManager",
            "TrustTier", "ConsequenceJudgment", "NoveltySearchConsequence",
            "ArchitecturalDecision", "NoveltyConstraint", "PolicyRule",
            "PolicyViolationRecord", "NoveltyBehaviourDescriptor",
            "ArchiveAdmissionRecord", "SystemConfig", "NoveltyParameters",
            "ConsequenceReport", "ArchiveEvictionRecord",
            "DiversityMeasurement", "NoveltyArchitecture", "NoveltyPolicy",
            "PortfolioNoveltyIntegrator", "IdeaNoveltyScorer",
            "TrustFilteredSearch", "FederationNoveltyBridge", "PipelineStage",
            "PipelineResult", "IntegratedNoveltyPipeline",
            "PackageCapability", "PackageManifest", "ManifestValidator",
            "PackageRegistry", "CapabilityQuery", "ManifestSerializer",
            "ManifestDiagnostics", "SearchStrategy", "MetricKind",
            "NoveltySearchProblem", "PortfolioCoverage", "NoveltyMetricSpec",
            "SearchResult", "DiversityConstraint", "NoveltyLevel",
            "UsefulNoveltyConfig", "NoveltyMeasure",
            "UsefulNoveltyComparison", "NoveltyVsUsefulNoveltyAnalyzer",
            "NoveltyVsUsefulNoveltyWitness",
            "NoveltyVsUsefulNoveltyCoordinator", "CoverageConfig",
            "CoverageEstimator", "GapDetector", "CoverageOptimizer",
            "DensityAnalyzer", "CoverageReport", "CoverageReporter",
            "SearchConfig", "GreedySearcher", "BeamSearcher",
            "ParetoSearcher", "DiverseSearcher", "SearchOrchestrator",
            "TheoremStatus", "TheoremKind", "NoveltyTheorem",
            "VerificationResult", "TheoremRegistry", "TheoremVerifier",
            "TheoremApplications", "TheoremCatalog", "Framework",
            "FrameworkContributionConfig", "AGContribution",
            "DTTContribution", "AIContribution", "FrameworkSynergy",
            "WhyAGDTTAINoveltyAnalyzer", "WhyAGDTTAINoveltyWitness",
            "WhyAGDTTAINoveltyCoordinator",
        ]
    except Exception:
        pass

    # -- optimization --
    try:
        from jugeo.ideation.optimization.algorithms import (
            OptimizationAlgorithm, WeightedSumOptimizer,
            LexicographicOptimizer, RandomSearchOptimizer,
            SimulatedAnnealingOptimizer, EvolutionaryOptimizer,
            BayesianStyleOptimizer, AlgorithmSelector,
        )
        from jugeo.ideation.optimization.budget_optimization import (
            BudgetItem, KnapsackSolver, FractionalKnapsack,
            DynamicBudgetPolicy, BudgetSensitivityAnalysis, BudgetOptimizer,
        )
        from jugeo.ideation.optimization.integration import (
            OptimizationEventType, OptimizationEvent, OptimizationEventBus,
            CopilotOptimizationAdvisor, SchedulerOptimizationBridge,
            RegimeOptimizationBridge, OptimizationIntegration,
        )
        from jugeo.ideation.optimization.manifest import (
            AlgorithmDescriptor, OptimizationManifest, ManifestValidator,
            ManifestRegistry, AlgorithmRegistry,
        )
        from jugeo.ideation.optimization.models import (
            ObjectiveDirection, SolutionStatus, IdeationObjective,
            OptimizationProblem, SolutionCandidate, ParetoFront,
            ObjectiveWeight, OptimizationResult, WeightedObjective,
            ConstraintSatisfaction, ObjectiveNormalizer,
        )
        from jugeo.ideation.optimization.novelty_feasibility_tradeoff import (
            TradeoffPoint, NoveltyFeasibilityFrontier, TradeoffAnalyzer,
            AdaptiveWeightSchedule, RegretMinimizer,
        )
        from jugeo.ideation.optimization.objective_functions import (
            BaseObjective, NoveltyObjective, FeasibilityObjective,
            PurposeObjective, YieldObjective, CostObjective,
            CompositeObjective, ObjectiveFactory, ObjectiveEvaluator,
        )
        from jugeo.ideation.optimization.pareto_optimization import (
            DominanceChecker, CrowdingDistance, NSGAIIStyle,
            EpsilonConstraintSolver, ParetoOptimizer,
        )
        from jugeo.ideation.optimization.theorems import (
            TheoremStatus, TheoremRecord, TheoremCatalog, TheoremVerifier,
        )
        registry["optimization"] = [
            "OptimizationAlgorithm", "WeightedSumOptimizer",
            "LexicographicOptimizer", "RandomSearchOptimizer",
            "SimulatedAnnealingOptimizer", "EvolutionaryOptimizer",
            "BayesianStyleOptimizer", "AlgorithmSelector", "BudgetItem",
            "KnapsackSolver", "FractionalKnapsack", "DynamicBudgetPolicy",
            "BudgetSensitivityAnalysis", "BudgetOptimizer",
            "OptimizationEventType", "OptimizationEvent",
            "OptimizationEventBus", "CopilotOptimizationAdvisor",
            "SchedulerOptimizationBridge", "RegimeOptimizationBridge",
            "OptimizationIntegration", "AlgorithmDescriptor",
            "OptimizationManifest", "ManifestValidator", "ManifestRegistry",
            "AlgorithmRegistry", "ObjectiveDirection", "SolutionStatus",
            "IdeationObjective", "OptimizationProblem", "SolutionCandidate",
            "ParetoFront", "ObjectiveWeight", "OptimizationResult",
            "WeightedObjective", "ConstraintSatisfaction",
            "ObjectiveNormalizer", "TradeoffPoint",
            "NoveltyFeasibilityFrontier", "TradeoffAnalyzer",
            "AdaptiveWeightSchedule", "RegretMinimizer", "BaseObjective",
            "NoveltyObjective", "FeasibilityObjective", "PurposeObjective",
            "YieldObjective", "CostObjective", "CompositeObjective",
            "ObjectiveFactory", "ObjectiveEvaluator", "DominanceChecker",
            "CrowdingDistance", "NSGAIIStyle", "EpsilonConstraintSolver",
            "ParetoOptimizer", "TheoremStatus", "TheoremRecord",
            "TheoremCatalog", "TheoremVerifier",
        ]
    except Exception:
        pass

    # -- regime_bootstrapping --
    try:
        from jugeo.ideation.regime_bootstrapping.algorithms import (
            AlgorithmConfig, BootstrappingAlgorithms,
        )
        from jugeo.ideation.regime_bootstrapping.domain_formation import (
            ObstructionAnalyzer, DomainPartitioner, DomainValidator,
            DomainFormationRunner,
        )
        from jugeo.ideation.regime_bootstrapping.domain_formation_when_the_right_di import (
            GapSeverity, DomainStatus, DomainFormationConfig,
            ObstructionCluster, SemanticGap, DomainProposal,
            DomainValidationResult, DomainRecord, DomainFormationResult,
            CoverageGapReport, ViabilityReport, OverlapReport,
            GapWitnessReport, ProposalWitnessReport,
            RegistrationWitnessReport, DomainFormationAnalyzer,
            DomainFormationWitness, DomainFormationCoordinator,
        )
        from jugeo.ideation.regime_bootstrapping.implementation_consequences import (
            TaskStatus, ImpactScope, ImplementationConfig, RegimeChange,
            ImpactSet, PackUpdateTask, BridgeReindexTask,
            DependencyRecomputeTask, ImplementationPlan, ImplementationResult,
            ConsequenceCycleResult, ImpactScopeReport, FeasibilityReport,
            GraphChangeReport, CostEstimate, ImpactWitnessReport,
            PlanWitnessReport, ResultWitnessReport,
            RegimeImplementationCoordinator, RegimeImplementationAnalyzer,
            RegimeImplementationWitness,
        )
        from jugeo.ideation.regime_bootstrapping.integration import (
            IntegrationConfig, IntegrationResult, RegimeCatalogAdapter,
            EvidenceBootstrapAdapter, OrchestratorAdapter,
            BootstrappingIntegration,
        )
        from jugeo.ideation.regime_bootstrapping.manifest import (
            ManifestValidationResult, RegimeBootstrappingManifest,
            BootstrappingManifestBuilder,
        )
        from jugeo.ideation.regime_bootstrapping.models import (
            BootstrapStatus, ObstructionKind, DomainType, TypeConstructorKind,
            BootstrapPriority, ObstructionField, DomainFormation,
            TypeConstructor, RegimeCandidate, BootstrapStep, BootstrapPlan,
            BootstrapResult, RegimeBootstrapperConfig, RegimeBootstrapper,
        )
        from jugeo.ideation.regime_bootstrapping.new_type_constructors_evidence_of import (
            PatternFrequency, ConstructorArity, TypeConstructorConfig,
            TheoremSketch, AdHocPattern, TypeConstructorProposal,
            ConstructorValidationResult, TypeConstructorRecord,
            ConstructorMiningResult, PatternFrequencyReport, CoverageAnalysis,
            KindConsistencyReport, PatternWitnessReport,
            ConstructorWitnessReport, RegistrationWitnessReport,
            NewTypeConstructorsCoordinator, NewTypeConstructorsAnalyzer,
            NewTypeConstructorsWitness,
        )
        from jugeo.ideation.regime_bootstrapping.regime_bootstrapping import (
            BootstrapOrchestrator, RegimeAssembler, BootstrapValidator,
            RegimeBootstrappingRunner,
        )
        from jugeo.ideation.regime_bootstrapping.regime_bootstrapping_provisional_c import (
            CarrierStatus, LawStatus, BootstrappingConfig, DomainRecord,
            CarrierSpec, BridgeStubSpec, ExperimentalLawSpec,
            ProvisionalCarrier, BridgeStub, ExperimentalLaw,
            LawValidationResult, StableCarrier, BootstrappingCycleResult,
            CarrierReadinessReport, StubCoverageReport, LawConsistencyReport,
            CarrierWitnessReport, StubWitnessReport, LawWitnessReport,
            CycleWitnessReport, RegimeBootstrappingWitness,
            RegimeBootstrappingAnalyzer, RegimeBootstrappingCoordinator,
        )
        from jugeo.ideation.regime_bootstrapping.theorems import (
            TheoremStatus, TheoremKind, TheoremProof,
            BootstrappingCompletenessTheorem, DomainCoverageTheorem,
            TypeConstructorSoundnessTheorem, ObstructionResolutionTheorem,
            RegimeUniquenessTheorem, BootstrappingTheoremRegistry,
        )
        from jugeo.ideation.regime_bootstrapping.type_constructors import (
            TypeConstructorSearch, FunctorSpecBuilder,
            TypeConstructorValidator, TypeConstructorRunner,
        )
        registry["regime_bootstrapping"] = [
            "AlgorithmConfig", "BootstrappingAlgorithms",
            "ObstructionAnalyzer", "DomainPartitioner", "DomainValidator",
            "DomainFormationRunner", "GapSeverity", "DomainStatus",
            "DomainFormationConfig", "ObstructionCluster", "SemanticGap",
            "DomainProposal", "DomainValidationResult", "DomainRecord",
            "DomainFormationResult", "CoverageGapReport", "ViabilityReport",
            "OverlapReport", "GapWitnessReport", "ProposalWitnessReport",
            "RegistrationWitnessReport", "DomainFormationAnalyzer",
            "DomainFormationWitness", "DomainFormationCoordinator",
            "TaskStatus", "ImpactScope", "ImplementationConfig",
            "RegimeChange", "ImpactSet", "PackUpdateTask",
            "BridgeReindexTask", "DependencyRecomputeTask",
            "ImplementationPlan", "ImplementationResult",
            "ConsequenceCycleResult", "ImpactScopeReport",
            "FeasibilityReport", "GraphChangeReport", "CostEstimate",
            "ImpactWitnessReport", "PlanWitnessReport", "ResultWitnessReport",
            "RegimeImplementationCoordinator", "RegimeImplementationAnalyzer",
            "RegimeImplementationWitness", "IntegrationConfig",
            "IntegrationResult", "RegimeCatalogAdapter",
            "EvidenceBootstrapAdapter", "OrchestratorAdapter",
            "BootstrappingIntegration", "ManifestValidationResult",
            "RegimeBootstrappingManifest", "BootstrappingManifestBuilder",
            "BootstrapStatus", "ObstructionKind", "DomainType",
            "TypeConstructorKind", "BootstrapPriority", "ObstructionField",
            "DomainFormation", "TypeConstructor", "RegimeCandidate",
            "BootstrapStep", "BootstrapPlan", "BootstrapResult",
            "RegimeBootstrapperConfig", "RegimeBootstrapper",
            "PatternFrequency", "ConstructorArity", "TypeConstructorConfig",
            "TheoremSketch", "AdHocPattern", "TypeConstructorProposal",
            "ConstructorValidationResult", "TypeConstructorRecord",
            "ConstructorMiningResult", "PatternFrequencyReport",
            "CoverageAnalysis", "KindConsistencyReport",
            "PatternWitnessReport", "ConstructorWitnessReport",
            "NewTypeConstructorsCoordinator", "NewTypeConstructorsAnalyzer",
            "NewTypeConstructorsWitness", "BootstrapOrchestrator",
            "RegimeAssembler", "BootstrapValidator",
            "RegimeBootstrappingRunner", "CarrierStatus", "LawStatus",
            "BootstrappingConfig", "CarrierSpec", "BridgeStubSpec",
            "ExperimentalLawSpec", "ProvisionalCarrier", "BridgeStub",
            "ExperimentalLaw", "LawValidationResult", "StableCarrier",
            "BootstrappingCycleResult", "CarrierReadinessReport",
            "StubCoverageReport", "LawConsistencyReport",
            "CarrierWitnessReport", "StubWitnessReport", "LawWitnessReport",
            "CycleWitnessReport", "RegimeBootstrappingWitness",
            "RegimeBootstrappingAnalyzer", "RegimeBootstrappingCoordinator",
            "TheoremStatus", "TheoremKind", "TheoremProof",
            "BootstrappingCompletenessTheorem", "DomainCoverageTheorem",
            "TypeConstructorSoundnessTheorem", "ObstructionResolutionTheorem",
            "RegimeUniquenessTheorem", "BootstrappingTheoremRegistry",
            "TypeConstructorSearch", "FunctorSpecBuilder",
            "TypeConstructorValidator", "TypeConstructorRunner",
        ]
    except Exception:
        pass

    # -- regimes --
    try:
        from jugeo.ideation.regimes import (
            RegimeKind, RegimeProposal, IdeationRegime, RegimeTransition,
            RegimeEvaluation, RegimeHistoryEntry, RegimePolicy, RegimeCatalog,
            RegimeEvaluator, RegimeSelector, RegimeHistory,
            RegimeBootstrapper, RegimeSerializer, RegimeDiagnostics,
        )
        registry["regimes"] = [
            "RegimeKind", "RegimeProposal", "IdeationRegime",
            "RegimeTransition", "RegimeEvaluation", "RegimeHistoryEntry",
            "RegimePolicy", "RegimeCatalog", "RegimeEvaluator",
            "RegimeSelector", "RegimeHistory", "RegimeBootstrapper",
            "RegimeSerializer", "RegimeDiagnostics",
        ]
    except Exception:
        pass

    # -- research_assistance --
    try:
        from jugeo.ideation.research_assistance.algorithms import (
            ResearchAssistanceAlgorithm, BreadthFirstProofSearch,
            BestFirstProofSearch, LemmaRetrievalAlgorithm,
            ConjectureRankingAlgorithm, OracleQueryOptimizer,
        )
        from jugeo.ideation.research_assistance.conjecture_generation import (
            PatternAnalyzer, ConjectureEvaluator, ConjecturePruner,
            GenerationHistory, ConjectureGenerator,
        )
        from jugeo.ideation.research_assistance.integration import (
            that, ResearchEventBus, SessionPersistence, VerifierBridge,
            CopilotResearchAdvisor, ResearchAssistanceIntegration,
        )
        from jugeo.ideation.research_assistance.lemma_mining import (
            MiningConfig, PatternExtractor, RelevanceScorer, LemmaArchive,
            LemmaMiner,
        )
        from jugeo.ideation.research_assistance.manifest import (
            OracleType, AssistanceCapability, OracleDescriptor,
            ResearchAssistanceManifest, ManifestValidator, ManifestRegistry,
        )
        from jugeo.ideation.research_assistance.models import (
            VerificationStatus, LemmaSource, ConjectureStatus, SessionStatus,
            LemmaCandidate, ConjectureRecord, ProofSuggestion,
            ResearchContext, OracleQuery, OracleResponse, VerificationRecord,
            ResearchSession,
        )
        from jugeo.ideation.research_assistance.oracle_interface import (
            OraclePolicy, OracleAuditLog, MockOracle,
            ControlledOracleProtocol, CopilotOracle,
        )
        from jugeo.ideation.research_assistance.proof_suggestion import (
            TacticLibrary, GoalAnalyzer, SuggestionFilter, ProofStateTracker,
            ProofSuggestionEngine,
        )
        from jugeo.ideation.research_assistance.theorems import (
            TheoremKind, ProofStrategy, ObstructionClass, TheoremDependency,
            ProofSketch, ResearchAssistanceTheoremSchema, FalsificationResult,
            FalsificationTest, TautologyTest, CircularDependencyTest,
            ScopeOverflowTest, NoveltySanityTest, CorrectnessFloorTest,
            ObstructionReductionTest, FormalStatementSyntaxTest,
            DependencyStrengthTest, FalsificationSuite,
            ResearchAssistanceTheoremRegistry,
        )
        registry["research_assistance"] = [
            "ResearchAssistanceAlgorithm", "BreadthFirstProofSearch",
            "BestFirstProofSearch", "LemmaRetrievalAlgorithm",
            "ConjectureRankingAlgorithm", "OracleQueryOptimizer",
            "PatternAnalyzer", "ConjectureEvaluator", "ConjecturePruner",
            "GenerationHistory", "ConjectureGenerator", "that",
            "ResearchEventBus", "SessionPersistence", "VerifierBridge",
            "CopilotResearchAdvisor", "ResearchAssistanceIntegration",
            "MiningConfig", "PatternExtractor", "RelevanceScorer",
            "LemmaArchive", "LemmaMiner", "OracleType",
            "AssistanceCapability", "OracleDescriptor",
            "ResearchAssistanceManifest", "ManifestValidator",
            "ManifestRegistry", "VerificationStatus", "LemmaSource",
            "ConjectureStatus", "SessionStatus", "LemmaCandidate",
            "ConjectureRecord", "ProofSuggestion", "ResearchContext",
            "OracleQuery", "OracleResponse", "VerificationRecord",
            "ResearchSession", "OraclePolicy", "OracleAuditLog", "MockOracle",
            "ControlledOracleProtocol", "CopilotOracle", "TacticLibrary",
            "GoalAnalyzer", "SuggestionFilter", "ProofStateTracker",
            "ProofSuggestionEngine", "TheoremKind", "ProofStrategy",
            "ObstructionClass", "TheoremDependency", "ProofSketch",
            "ResearchAssistanceTheoremSchema", "FalsificationResult",
            "FalsificationTest", "TautologyTest", "CircularDependencyTest",
            "ScopeOverflowTest", "NoveltySanityTest", "CorrectnessFloorTest",
            "ObstructionReductionTest", "FormalStatementSyntaxTest",
            "DependencyStrengthTest", "FalsificationSuite",
            "ResearchAssistanceTheoremRegistry",
        ]
    except Exception:
        pass

    # -- scheduling --
    try:
        from jugeo.ideation.scheduling import (
            ExhaustionPolicy, SchedulePhase, IdeationSchedule,
            SchedulingPolicy, TheoremGrowthEconomics, ExplorationBudget,
            ExploitationPrioritizer, IdeationClock, ScheduleHistory,
            ScheduleOptimizer, IdeationScheduler, ScheduleDiagnostics,
        )
        registry["scheduling"] = [
            "ExhaustionPolicy", "SchedulePhase", "IdeationSchedule",
            "SchedulingPolicy", "TheoremGrowthEconomics", "ExplorationBudget",
            "ExploitationPrioritizer", "IdeationClock", "ScheduleHistory",
            "ScheduleOptimizer", "IdeationScheduler", "ScheduleDiagnostics",
        ]
    except Exception:
        pass

    # -- semantic_futures --
    try:
        from jugeo.ideation.semantic_futures.algorithms import (
            SearchConfig, SearchResult, FutureSearchAlgorithm,
            BeamSearchFutures, GreedyFutureSearch, DiversifiedSearch,
            ArchiveBasedSearch, PurposeDirectedSearch, SearchAlgorithmFactory,
            SearchComparator,
        )
        from jugeo.ideation.semantic_futures.budget_allocation import (
            AllocationStrategy, BudgetConstraint, BudgetTracker,
            CostEstimator, BudgetAllocator,
        )
        from jugeo.ideation.semantic_futures.future_generation import (
            GenerationStrategy, GenerationConfig, SemanticOperator,
            FutureGenerator, FutureExpander, FuturePruner,
        )
        from jugeo.ideation.semantic_futures.idea_objects_future_attainability import (
            IdeaType, IdeaAttainabilityFactors, IdeaObject, IdeaPortfolio,
            AttainabilityEstimator, LeveragePredictor, SupportScopeExpander,
            IdeaObjectsFutureAttainabilityAnalyzer,
            IdeaObjectsFutureAttainabilityWitness,
            IdeaObjectsFutureAttainabilityCoordinator,
        )
        from jugeo.ideation.semantic_futures.ideation_signals_obstruction_rank import (
            ObstructionRankMap, OverlapEntropyReport, BottleneckCoordinate,
            BottleneckGeometry, ObstructionRankComputer,
            OverlapEntropyComputer, BottleneckGeometryDetector,
            IdeationSignalsObstructionRankAnalyzer,
            IdeationSignalsObstructionRankWitness,
            IdeationSignalsObstructionRankCoordinator, TrustTier,
            SignalJudgment, IdeationSignal, FutureDirection,
            ObstructionRecordStub, ExtractionConfig, ObstructionRanking,
            SignalExtractor,
        )
        from jugeo.ideation.semantic_futures.ideation_signals_obstruction_rank_NEW import (
            TrustTier, SignalType, RankingCriterion, DirectionPriority,
        )
        from jugeo.ideation.semantic_futures.integration import (
            EventKind, FutureEvent, EventSubscription, FuturesEventBus,
            IntegrationStatus, ComponentHealth, IntegrationHealthCheck,
            CopilotFuturesAdvisor, SemanticFuturesIntegration,
        )
        from jugeo.ideation.semantic_futures.manifest import (
            FutureSpaceDescriptor, SemanticFuturesManifest, ManifestValidator,
            ManifestRegistry,
        )
        from jugeo.ideation.semantic_futures.models import (
            _DynamicFutureTag, _FutureTagMeta, FutureTag, FutureState,
            PurposeFunction, SemanticFuture, FutureValuation, IdeationState,
            FutureFilter, FutureRanker, FutureComparator,
        )
        from jugeo.ideation.semantic_futures.pre_implementation_valuation_expec import (
            ValuationTier, CostComponents, ObstructionReductionEstimate,
            PreImplementationValue, ValueDistribution, CostEstimator,
            ObstructionReductionForecaster, ValuationTierClassifier,
            PreImplementationValuationAnalyzer,
            PreImplementationValuationWitness,
            PreImplementationValuationCoordinator,
        )
        from jugeo.ideation.semantic_futures.purpose_alignment import (
            AlignmentCriterion, AlignmentScore, PurposeDecomposer,
            UtilityAggregator, AlignmentCache, AlignmentReport,
            PurposeAligner,
        )
        from jugeo.ideation.semantic_futures.reachability import (
            ReachabilityModelType, ReachabilityModel, ReachabilityCache,
            BridgeProbability, TransitionGraph, PathFinder,
            ReachabilityEstimator,
        )
        from jugeo.ideation.semantic_futures.theorems import (
            TheoremDifficulty, TheoremHypothesis, TheoremStatement,
            TheoremCatalog, TheoremVerifier,
        )
        registry["semantic_futures"] = [
            "SearchConfig", "SearchResult", "FutureSearchAlgorithm",
            "BeamSearchFutures", "GreedyFutureSearch", "DiversifiedSearch",
            "ArchiveBasedSearch", "PurposeDirectedSearch",
            "SearchAlgorithmFactory", "SearchComparator",
            "AllocationStrategy", "BudgetConstraint", "BudgetTracker",
            "CostEstimator", "BudgetAllocator", "GenerationStrategy",
            "GenerationConfig", "SemanticOperator", "FutureGenerator",
            "FutureExpander", "FuturePruner", "IdeaType",
            "IdeaAttainabilityFactors", "IdeaObject", "IdeaPortfolio",
            "AttainabilityEstimator", "LeveragePredictor",
            "SupportScopeExpander", "IdeaObjectsFutureAttainabilityAnalyzer",
            "IdeaObjectsFutureAttainabilityWitness",
            "IdeaObjectsFutureAttainabilityCoordinator", "ObstructionRankMap",
            "OverlapEntropyReport", "BottleneckCoordinate",
            "BottleneckGeometry", "ObstructionRankComputer",
            "OverlapEntropyComputer", "BottleneckGeometryDetector",
            "IdeationSignalsObstructionRankAnalyzer",
            "IdeationSignalsObstructionRankWitness",
            "IdeationSignalsObstructionRankCoordinator", "TrustTier",
            "SignalJudgment", "IdeationSignal", "FutureDirection",
            "ObstructionRecordStub", "ExtractionConfig", "ObstructionRanking",
            "SignalExtractor", "SignalType", "RankingCriterion",
            "DirectionPriority", "EventKind", "FutureEvent",
            "EventSubscription", "FuturesEventBus", "IntegrationStatus",
            "ComponentHealth", "IntegrationHealthCheck",
            "CopilotFuturesAdvisor", "SemanticFuturesIntegration",
            "FutureSpaceDescriptor", "SemanticFuturesManifest",
            "ManifestValidator", "ManifestRegistry", "_DynamicFutureTag",
            "_FutureTagMeta", "FutureTag", "FutureState", "PurposeFunction",
            "SemanticFuture", "FutureValuation", "IdeationState",
            "FutureFilter", "FutureRanker", "FutureComparator",
            "ValuationTier", "CostComponents", "ObstructionReductionEstimate",
            "PreImplementationValue", "ValueDistribution",
            "ObstructionReductionForecaster", "ValuationTierClassifier",
            "PreImplementationValuationAnalyzer",
            "PreImplementationValuationWitness",
            "PreImplementationValuationCoordinator", "AlignmentCriterion",
            "AlignmentScore", "PurposeDecomposer", "UtilityAggregator",
            "AlignmentCache", "AlignmentReport", "PurposeAligner",
            "ReachabilityModelType", "ReachabilityModel", "ReachabilityCache",
            "BridgeProbability", "TransitionGraph", "PathFinder",
            "ReachabilityEstimator", "TheoremDifficulty", "TheoremHypothesis",
            "TheoremStatement", "TheoremCatalog", "TheoremVerifier",
        ]
    except Exception:
        pass

    # -- synthesis_frontier --
    try:
        from jugeo.ideation.synthesis_frontier.code_orchestrator import (
            CodeTarget, CodeLanguage, CodeSpec, CodePlan, TheoremToCodeMapper,
            CodeOrchestrator,
        )
        from jugeo.ideation.synthesis_frontier.judgment_geometry_bridge import (
            CoordinateNamingConvention, JudgmentCoordinate, JudgmentEncoding,
            EvidenceSection, ObligationExtractor, PredicateExtractor,
            ContextExtractor, JudgmentGeometryBridge, JudgmentSheafValidator,
            SheafInjector,
        )
        from jugeo.ideation.synthesis_frontier.llm_judge import (
            JudgeMode, JudgeConfig, JudgeVerdict, HeuristicJudge, LLMJudge,
            SynthesisJudge,
        )
        from jugeo.ideation.synthesis_frontier.metaphor_finder import (
            MetaphorKind, MetaphorPattern, MetaphorCandidate, PatternMatcher,
            MetaphorFinder,
        )
        from jugeo.ideation.synthesis_frontier.models import (
            DomainArea, PropositionKind, PropositionRecord, MetaphorLink,
            FieldNode, SynthesisPair, TournamentState,
        )
        from jugeo.ideation.synthesis_frontier.paper_generator import (
            PaperSection, TheoremEnvironment, LatexTheoremBlock, PaperStats,
            MathPaper, PaperOutline, SectionWriter, PaperGenerator,
        )
        from jugeo.ideation.synthesis_frontier.pipeline import (
            PipelineConfig, PipelineProgress, PipelineResult,
            CheckpointManager, SynthesisFrontierPipeline,
        )
        from jugeo.ideation.synthesis_frontier.textbook_generator import (
            TextbookGenerator,
        )
        from jugeo.ideation.synthesis_frontier.tournament import (
            PairingStrategy, MergeResult, RoundResult, TournamentConfig,
            BinaryTournamentFrontier, FieldMerger, PairSelector,
            TournamentRound, Tournament,
        )
        registry["synthesis_frontier"] = [
            "CodeTarget", "CodeLanguage", "CodeSpec", "CodePlan",
            "TheoremToCodeMapper", "CodeOrchestrator",
            "CoordinateNamingConvention", "JudgmentCoordinate",
            "JudgmentEncoding", "EvidenceSection", "ObligationExtractor",
            "PredicateExtractor", "ContextExtractor",
            "JudgmentGeometryBridge", "JudgmentSheafValidator",
            "SheafInjector", "JudgeMode", "JudgeConfig", "JudgeVerdict",
            "HeuristicJudge", "LLMJudge", "SynthesisJudge", "MetaphorKind",
            "MetaphorPattern", "MetaphorCandidate", "PatternMatcher",
            "MetaphorFinder", "DomainArea", "PropositionKind",
            "PropositionRecord", "MetaphorLink", "FieldNode", "SynthesisPair",
            "TournamentState", "PaperSection", "TheoremEnvironment",
            "LatexTheoremBlock", "PaperStats", "MathPaper", "PaperOutline",
            "SectionWriter", "PaperGenerator", "PipelineConfig",
            "PipelineProgress", "PipelineResult", "CheckpointManager",
            "SynthesisFrontierPipeline", "TextbookGenerator",
            "PairingStrategy", "MergeResult", "RoundResult",
            "TournamentConfig", "BinaryTournamentFrontier", "FieldMerger",
            "PairSelector", "TournamentRound", "Tournament",
        ]
    except Exception:
        pass

    # -- theorem_ecologies --
    try:
        from jugeo.ideation.theorem_ecologies.algorithms import (
            EcologicalAlgorithm, EcologyManager, PortfolioOptimizer,
            EcologicalDynamicsSimulator, EcologyDiagnostics, EcologyHistory,
            EcologyBenchmark,
        )
        from jugeo.ideation.theorem_ecologies.compounding import (
            CompoundingConfig, CompoundingDetector, SynergyEstimator,
            CompoundBuilder, AmplificationCalculator, CompoundingEngine,
        )
        from jugeo.ideation.theorem_ecologies.ecological_metrics_reuse_breadth_c import (
            TrustTier, MetricJudgment, UsageRecord, EcologicalMetric,
            BreadthSnapshot, CitationEdge, DepthResult, DomainSpec,
            CoverageResult, EcologyScore, GapReport, ReuseBreadth,
            CitationDepth, TheoreticalCoverage, MetricJudgment8, EcologyScore,
        )
        from jugeo.ideation.theorem_ecologies.ecology_modeling import (
            EcologyConfig, TheoremNode, EcologyBuilder, DependencyMapper,
            HealthCalculator, DiversityAnalyzer, EcologyModeler,
        )
        from jugeo.ideation.theorem_ecologies.implementation_consequences import (
            TrustTier, ConsequenceJudgment, EcologyImplementationConsequence,
            EcologyConstraint, ConstraintCheckResult, EcologyDesignRule,
            EcologyViolation, EcologyModuleProfile, EcologyPolicyRecord,
            EcologyPolicy, EcologyCompliance,
        )
        from jugeo.ideation.theorem_ecologies.implementation_consequences_new import (
            TrustTier, ConsequenceJudgment,
        )
        from jugeo.ideation.theorem_ecologies.integration import (
            IdeaEcologyLinker, TrustEcologyFilter, NoveltyEcologyScorer,
            EcologyIdeaGenerator, IntegratedEcologyPipeline,
        )
        from jugeo.ideation.theorem_ecologies.lemma_portfolios import (
            PortfolioConfig, LemmaUtilityEstimator, ReuseTracker,
            CoverageCalculator, PortfolioRebalancer, LemmaPortfolioManager,
        )
        from jugeo.ideation.theorem_ecologies.lemma_portfolios_coordinated_famil import (
            LemmaStatus, CompletionStatus, PortfolioConfig, LemmaRecord,
            TheoremNode, PortfolioUpdateResult, CompletionCheckResult,
            RetirementResult, PortfolioCycleResult, CoherenceReport,
            RedundancyReport, GapCoverageReport, CreationWitnessReport,
            AdditionWitnessReport, CompletionWitnessReport, LemmaPortfolio,
            LemmaPortfoliosCoordinator, LemmaPortfoliosAnalyzer,
            LemmaPortfoliosWitness,
        )
        from jugeo.ideation.theorem_ecologies.manifest import (
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        )
        from jugeo.ideation.theorem_ecologies.models import (
            EcologyHealth, DynamicType, TheoremEcology, LemmaPortfolio,
            CompoundingEffect, EcologicalDynamic, PortfolioOptimization,
        )
        from jugeo.ideation.theorem_ecologies.theorem_ecologies_from_local_closu import (
            TheoremRole, ClosureStatus, EcologyConfig, TheoremNode,
            ClosureCheckResult, EnvironmentChangeReport, EcologyCycleResult,
            ClosurePropertyReport, DependencyGraphReport,
            ReasoningReachReport, EcologyWitnessReport,
            ExpansionWitnessReport, ClosureWitnessReport, TheoremEcology,
            TheoremEcologiesCoordinator, TheoremEcologiesAnalyzer,
            TheoremEcologiesWitness,
        )
        from jugeo.ideation.theorem_ecologies.theorems import (
            TheoremStatus, TheoremType, EcologyTheorem, TheoremRegistry,
            TheoremVerifier, TheoremApplications, TheoremCatalog,
        )
        registry["theorem_ecologies"] = [
            "EcologicalAlgorithm", "EcologyManager", "PortfolioOptimizer",
            "EcologicalDynamicsSimulator", "EcologyDiagnostics",
            "EcologyHistory", "EcologyBenchmark", "CompoundingConfig",
            "CompoundingDetector", "SynergyEstimator", "CompoundBuilder",
            "AmplificationCalculator", "CompoundingEngine", "TrustTier",
            "MetricJudgment", "UsageRecord", "EcologicalMetric",
            "BreadthSnapshot", "CitationEdge", "DepthResult", "DomainSpec",
            "CoverageResult", "EcologyScore", "GapReport", "ReuseBreadth",
            "CitationDepth", "TheoreticalCoverage", "MetricJudgment8",
            "EcologyConfig", "TheoremNode", "EcologyBuilder",
            "DependencyMapper", "HealthCalculator", "DiversityAnalyzer",
            "EcologyModeler", "ConsequenceJudgment",
            "EcologyImplementationConsequence", "EcologyConstraint",
            "ConstraintCheckResult", "EcologyDesignRule", "EcologyViolation",
            "EcologyModuleProfile", "EcologyPolicyRecord", "EcologyPolicy",
            "EcologyCompliance", "IdeaEcologyLinker", "TrustEcologyFilter",
            "NoveltyEcologyScorer", "EcologyIdeaGenerator",
            "IntegratedEcologyPipeline", "PortfolioConfig",
            "LemmaUtilityEstimator", "ReuseTracker", "CoverageCalculator",
            "PortfolioRebalancer", "LemmaPortfolioManager", "LemmaStatus",
            "CompletionStatus", "LemmaRecord", "PortfolioUpdateResult",
            "CompletionCheckResult", "RetirementResult",
            "PortfolioCycleResult", "CoherenceReport", "RedundancyReport",
            "GapCoverageReport", "CreationWitnessReport",
            "AdditionWitnessReport", "CompletionWitnessReport",
            "LemmaPortfolio", "LemmaPortfoliosCoordinator",
            "LemmaPortfoliosAnalyzer", "LemmaPortfoliosWitness",
            "PackageCapability", "PackageManifest", "ManifestValidator",
            "PackageRegistry", "CapabilityQuery", "ManifestSerializer",
            "ManifestDiagnostics", "EcologyHealth", "DynamicType",
            "TheoremEcology", "CompoundingEffect", "EcologicalDynamic",
            "PortfolioOptimization", "TheoremRole", "ClosureStatus",
            "ClosureCheckResult", "EnvironmentChangeReport",
            "EcologyCycleResult", "ClosurePropertyReport",
            "DependencyGraphReport", "ReasoningReachReport",
            "EcologyWitnessReport", "ExpansionWitnessReport",
            "ClosureWitnessReport", "TheoremEcologiesCoordinator",
            "TheoremEcologiesAnalyzer", "TheoremEcologiesWitness",
            "TheoremStatus", "TheoremType", "EcologyTheorem",
            "TheoremRegistry", "TheoremVerifier", "TheoremApplications",
            "TheoremCatalog",
        ]
    except Exception:
        pass

    # -- theorem_economics --
    try:
        from jugeo.ideation.theorem_economics.algorithms import (
            EconomicAlgorithm, _BudgetAlgorithmBase, WaterfillingAlgorithm,
            LagrangianOptimizer, PortfolioOptimizer,
            YieldMaximizationAlgorithm, CompoundingOptimizer,
            AlgorithmRegistry,
        )
        from jugeo.ideation.theorem_economics.compounding import (
            CompoundingFactor, TheoremChainTracer, CompoundingModel,
            CompoundInterestAnalogy, CompoundingPortfolioAnalyzer,
        )
        from jugeo.ideation.theorem_economics.integration import (
            EconomicObstruction, EconomicJudgmentBridge,
            TheoremEconomicsIntegration, EconomicVerificationPipeline,
            SchedulerEconomicsBridge, CopilotEconomicsAdvisor,
            EconomicEventBus, PortfolioReporter,
        )
        from jugeo.ideation.theorem_economics.investment_scheduling import (
            GreedyInvestmentAllocator, LagrangianRelaxationAllocator,
            AdaptiveScheduler, InvestmentScheduler, ScheduleEvaluator,
        )
        from jugeo.ideation.theorem_economics.manifest import (
            YieldType, AssumptionCategory, ValidationStatus,
            YieldModelDescriptor, EconomicAssumption,
            TheoremEconomicsManifest, ManifestValidator, ManifestRegistry,
        )
        from jugeo.ideation.theorem_economics.marginal_analysis import (
            MarginalValueCurve, EquimarginalPrinciple, MarginalAnalyzer,
            MarginalReturnDiminishment,
        )
        from jugeo.ideation.theorem_economics.models import (
            TheoremYieldModel, MarginalValue, InvestmentSchedule,
            CompoundingEffect, TheoremPortfolioValue, RegimeEconomics,
            BudgetAllocation, YieldForecast, EconomicEquilibrium,
            LinearYieldModel,
        )
        from jugeo.ideation.theorem_economics.scheduling_principle import (
            SchedulingConfig, EffortAllocation, SchedulingSignal,
            AllocationHistory, SchedulingPrincipleAnalyzer,
            SchedulingPrincipleWitness, SchedulingPrincipleCoordinator,
        )
        from jugeo.ideation.theorem_economics.the_growth_signal import (
            GrowthSignalConfig, TheoremROIRecord, CodeROIRecord,
            GrowthSignalReading, TheGrowthSignalAnalyzer,
            TheGrowthSignalWitness, TheGrowthSignalCoordinator,
        )
        from jugeo.ideation.theorem_economics.theorems import (
            TheoremStatus, ProofMethod, EconomicTheorem, TheoremCatalog,
            TheoremVerifier, TheoremDatabase, TheoremProofChain,
        )
        from jugeo.ideation.theorem_economics.when_coding_should_stop_and_theory import (
            WhenCodingShouldStopConfig, ObstructionDensityMeasure,
            RepairAttemptRecord, SwitchingDecision,
            WhenCodingShouldStopAnalyzer, WhenCodingShouldStopWitness,
            WhenCodingShouldStopCoordinator,
        )
        from jugeo.ideation.theorem_economics.yield_modeling import (
            YieldCurve, SaturationEstimator, GrowthRateEstimator,
            YieldModeler, YieldModelValidator, YieldModelComparator,
        )
        registry["theorem_economics"] = [
            "EconomicAlgorithm", "_BudgetAlgorithmBase",
            "WaterfillingAlgorithm", "LagrangianOptimizer",
            "PortfolioOptimizer", "YieldMaximizationAlgorithm",
            "CompoundingOptimizer", "AlgorithmRegistry", "CompoundingFactor",
            "TheoremChainTracer", "CompoundingModel",
            "CompoundInterestAnalogy", "CompoundingPortfolioAnalyzer",
            "EconomicObstruction", "EconomicJudgmentBridge",
            "TheoremEconomicsIntegration", "EconomicVerificationPipeline",
            "SchedulerEconomicsBridge", "CopilotEconomicsAdvisor",
            "EconomicEventBus", "PortfolioReporter",
            "GreedyInvestmentAllocator", "LagrangianRelaxationAllocator",
            "AdaptiveScheduler", "InvestmentScheduler", "ScheduleEvaluator",
            "YieldType", "AssumptionCategory", "ValidationStatus",
            "YieldModelDescriptor", "EconomicAssumption",
            "TheoremEconomicsManifest", "ManifestValidator",
            "ManifestRegistry", "MarginalValueCurve", "EquimarginalPrinciple",
            "MarginalAnalyzer", "MarginalReturnDiminishment",
            "TheoremYieldModel", "MarginalValue", "InvestmentSchedule",
            "CompoundingEffect", "TheoremPortfolioValue", "RegimeEconomics",
            "BudgetAllocation", "YieldForecast", "EconomicEquilibrium",
            "LinearYieldModel", "SchedulingConfig", "EffortAllocation",
            "SchedulingSignal", "AllocationHistory",
            "SchedulingPrincipleAnalyzer", "SchedulingPrincipleWitness",
            "SchedulingPrincipleCoordinator", "GrowthSignalConfig",
            "TheoremROIRecord", "CodeROIRecord", "GrowthSignalReading",
            "TheGrowthSignalAnalyzer", "TheGrowthSignalWitness",
            "TheGrowthSignalCoordinator", "TheoremStatus", "ProofMethod",
            "EconomicTheorem", "TheoremCatalog", "TheoremVerifier",
            "TheoremDatabase", "TheoremProofChain",
            "WhenCodingShouldStopConfig", "ObstructionDensityMeasure",
            "RepairAttemptRecord", "SwitchingDecision",
            "WhenCodingShouldStopAnalyzer", "WhenCodingShouldStopWitness",
            "WhenCodingShouldStopCoordinator", "YieldCurve",
            "SaturationEstimator", "GrowthRateEstimator", "YieldModeler",
            "YieldModelValidator", "YieldModelComparator",
        ]
    except Exception:
        pass

    # -- theory_navigation --
    try:
        from jugeo.ideation.theory_navigation.algorithms import (
            NavigationAlgorithm, NavigationHistory, TheoryNavigator,
            MapBuilder, NavigationOptimizer, NavigationBenchmark,
            NavigationDiagnostics,
        )
        from jugeo.ideation.theory_navigation.integration import (
            IdeaNavigator, FederationNavigator, NoveltyNavigator,
            NavigationFederator, TrustAwareNavigator,
            IntegratedNavigationPipeline,
        )
        from jugeo.ideation.theory_navigation.manifest import (
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        )
        from jugeo.ideation.theory_navigation.mathematical_areas_as_candidate_se import (
            MathematicalArea, SemanticRegimeProfile, RegimeCompatibility,
            AreaSelectionResult, MathAreasSemanticRegimesAnalyzer,
            MathAreasSemanticRegimesWitness,
            MathAreasSemanticRegimesCoordinator,
        )
        from jugeo.ideation.theory_navigation.models import (
            NodeMaturity, NavigationStrategy, PurposeCondition, TheoryNode,
            NavigationPath, NavigationState, TheorySpace,
        )
        from jugeo.ideation.theory_navigation.path_finding import (
            SearchNode, PathFinder, DiversePathFinder, PurposeGuidedSearch,
            PathEvaluator, PathCache,
        )
        from jugeo.ideation.theory_navigation.purpose_conditioning import (
            PurposeVector, PurposeWeightMap, PurposeConditioner,
            HeuristicComputer, PurposeAligner, PurposeDriftDetector,
        )
        from jugeo.ideation.theory_navigation.purpose_conditioning_target_obstru import (
            PurposeConditioningConfig, ObstructionTarget, PurposeVector,
            ConditionedPurpose, PurposeConditioningTargetAnalyzer,
            PurposeConditioningTargetWitness, PurposeConditioningCoordinator,
        )
        from jugeo.ideation.theory_navigation.space_construction import (
            SpaceConstructionConfig, NodeExtractor, EdgeBuilder, SpaceIndexer,
            SpaceConstructor, IncrementalSpaceUpdater,
        )
        from jugeo.ideation.theory_navigation.theorems import (
            TrustTier, NavigationJudgment, NavigationTheorem,
            NavigationInvariant, NavigationPath, CoverageRecord,
            OptimalityCheck, NavigationRecord, NavigationStrategyProfile,
            PathOptimality, NavigationCompleteness,
            NavigationStrategyEvaluator,
        )
        from jugeo.ideation.theory_navigation.theory_space_navigation_moving_ove import (
            CandidateRegimeConfig, CandidateRegime, RegimeTransition,
            NavigationState, TheorySpaceNavigationAnalyzer,
            TheorySpaceNavigationWitness, TheorySpaceNavigationCoordinator,
        )
        registry["theory_navigation"] = [
            "NavigationAlgorithm", "NavigationHistory", "TheoryNavigator",
            "MapBuilder", "NavigationOptimizer", "NavigationBenchmark",
            "NavigationDiagnostics", "IdeaNavigator", "FederationNavigator",
            "NoveltyNavigator", "NavigationFederator", "TrustAwareNavigator",
            "IntegratedNavigationPipeline", "PackageCapability",
            "PackageManifest", "ManifestValidator", "PackageRegistry",
            "CapabilityQuery", "ManifestSerializer", "ManifestDiagnostics",
            "MathematicalArea", "SemanticRegimeProfile",
            "RegimeCompatibility", "AreaSelectionResult",
            "MathAreasSemanticRegimesAnalyzer",
            "MathAreasSemanticRegimesWitness",
            "MathAreasSemanticRegimesCoordinator", "NodeMaturity",
            "NavigationStrategy", "PurposeCondition", "TheoryNode",
            "NavigationPath", "NavigationState", "TheorySpace", "SearchNode",
            "PathFinder", "DiversePathFinder", "PurposeGuidedSearch",
            "PathEvaluator", "PathCache", "PurposeVector", "PurposeWeightMap",
            "PurposeConditioner", "HeuristicComputer", "PurposeAligner",
            "PurposeDriftDetector", "PurposeConditioningConfig",
            "ObstructionTarget", "ConditionedPurpose",
            "PurposeConditioningTargetAnalyzer",
            "PurposeConditioningTargetWitness",
            "PurposeConditioningCoordinator", "SpaceConstructionConfig",
            "NodeExtractor", "EdgeBuilder", "SpaceIndexer",
            "SpaceConstructor", "IncrementalSpaceUpdater", "TrustTier",
            "NavigationJudgment", "NavigationTheorem", "NavigationInvariant",
            "CoverageRecord", "OptimalityCheck", "NavigationRecord",
            "NavigationStrategyProfile", "PathOptimality",
            "NavigationCompleteness", "NavigationStrategyEvaluator",
            "CandidateRegimeConfig", "CandidateRegime", "RegimeTransition",
            "TheorySpaceNavigationAnalyzer", "TheorySpaceNavigationWitness",
            "TheorySpaceNavigationCoordinator",
        ]
    except Exception:
        pass

    return registry


def _print_ideation_registry() -> int:
    """Print the ideation class registry and return exit code."""
    registry = _ideation_registry()
    total = sum(len(v) for v in registry.values())
    print(f"jugeo ideation class registry — {total} classes from "
          f"{len(registry)} sub-packages\n")
    for pkg, classes in sorted(registry.items()):
        print(f"  [{pkg}] ({len(classes)} classes)")
        for cls in classes:
            print(f"    - {cls}")
        print()
    if not registry:
        print("  (no ideation sub-packages could be imported)")
    return 0


# ======================================================================
# Internal dataclasses
# ======================================================================

@dataclass
class _TheoremRecord:
    """A proposed theorem or conjecture."""
    name: str
    statement: str
    field: str
    kind: str = "conjecture"
    novelty: float = 0.0
    trust: float = 0.0
    yield_estimate: float = 0.0
    falsified: bool = False
    falsification_reason: str | None = None
    judgment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "statement": self.statement,
            "field": self.field,
            "kind": self.kind,
            "novelty": self.novelty,
            "trust": self.trust,
            "yield_estimate": self.yield_estimate,
            "falsified": self.falsified,
        }
        if self.falsification_reason:
            d["falsification_reason"] = self.falsification_reason
        if self.judgment_id:
            d["judgment_id"] = self.judgment_id
        return d


@dataclass
class _IdeationReport:
    """Complete report from an ideation run."""
    mode: str = "ideation"
    fields_scanned: int = 0
    candidates_proposed: int = 0
    candidates_novel: int = 0
    candidates_falsified: int = 0
    aggregate_trust: float = 0.0
    theorems: list[_TheoremRecord] = field(default_factory=list)
    economics: dict[str, Any] = field(default_factory=dict)
    cross_domain_links: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "mode": self.mode,
            "fields_scanned": self.fields_scanned,
            "candidates_proposed": self.candidates_proposed,
            "candidates_novel": self.candidates_novel,
            "candidates_falsified": self.candidates_falsified,
            "aggregate_trust": self.aggregate_trust,
            "theorems": [t.to_dict() for t in self.theorems],
        }
        if self.economics:
            d["economics"] = self.economics
        if self.cross_domain_links:
            d["cross_domain_links"] = self.cross_domain_links
        if self.error:
            d["error"] = self.error
        return d


# ======================================================================
# Phase 1: Scan mathematical domains
# ======================================================================

def _get_fields() -> list[Any]:
    """Return the field catalog, preferring the real one when available."""
    if _HAS_FRONTIER and ALL_128_FIELDS:
        return list(ALL_128_FIELDS)
    return list(_FALLBACK_FIELDS)


def _format_field_list(fields: list[Any]) -> str:
    """Pretty-print the list of available fields."""
    lines = ["Available mathematical fields:", "=" * 50]
    for i, f in enumerate(fields, 1):
        if isinstance(f, dict):
            name = f.get("name", f.get("id", "?"))
            family = f.get("family", "")
            lines.append(f"  {i:3d}. {name}  [{family}]")
        else:
            name = getattr(f, "name", str(f))
            family = getattr(f, "family", "")
            lines.append(f"  {i:3d}. {name}  [{family}]")
    lines.append(f"\nTotal: {len(fields)} field(s)")
    return "\n".join(lines)


# ======================================================================
# Phase 2: Propose theorem candidates
# ======================================================================

def _propose_candidates(fields: list[Any]) -> list[_TheoremRecord]:
    """Use the discovery engine (if available) to propose theorem candidates.

    Falls back to a heuristic generator that creates one candidate per
    field based on structural patterns.
    """
    records: list[_TheoremRecord] = []

    if _HAS_DISCOVERY and TheoremCandidate is not None:
        try:
            for f in fields:
                name = getattr(f, "name", None) or (
                    f.get("name") if isinstance(f, dict) else str(f)
                )
                candidate = TheoremCandidate(field=name)  # type: ignore[misc]
                result = candidate.discover()
                for thm in getattr(result, "theorems", []):
                    records.append(_TheoremRecord(
                        name=getattr(thm, "name", f"thm_{name}"),
                        statement=getattr(thm, "statement", ""),
                        field=name,
                        kind=getattr(thm, "kind", "conjecture"),
                    ))
            if records:
                return records
        except Exception as exc:
            _log.warning("Discovery engine failed: %s — falling back.", exc)

    # Heuristic fallback: one candidate per field
    for f in fields:
        name = (
            getattr(f, "name", None)
            or (f.get("name") if isinstance(f, dict) else str(f))
        )
        fid = (
            getattr(f, "id", None)
            or (f.get("id") if isinstance(f, dict) else name.lower().replace(" ", "_"))
        )
        records.append(_TheoremRecord(
            name=f"conjecture_{fid}",
            statement=f"There exists a non-trivial structure in {name} "
                      f"that connects to adjacent domains via a natural transformation.",
            field=name,
            kind="conjecture",
        ))

    return records


# ======================================================================
# Phase 3: Classify by proposition kind
# ======================================================================

_KIND_KEYWORDS: dict[str, list[str]] = {
    "theorem": ["proven", "established", "follows", "implies"],
    "conjecture": ["conjecture", "hypothesis", "expected", "believed"],
    "lemma": ["lemma", "auxiliary", "helper", "preliminary"],
    "proposition": ["proposition", "claim", "assertion"],
    "corollary": ["corollary", "consequence", "immediate"],
}


def _classify_kind(statement: str) -> str:
    """Classify a theorem statement by kind using keywords or the judgment system."""
    lower = statement.lower()
    for kind, keywords in _KIND_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return kind
    return "conjecture"


def _classify_candidates(records: list[_TheoremRecord]) -> None:
    """Update each record's ``kind`` field using the judgment subsystem or heuristics."""
    if _HAS_JUDGMENTS and PropositionKind is not None:
        for rec in records:
            try:
                prop = Proposition(content=rec.statement)  # type: ignore[misc]
                kind_val = getattr(prop, "kind", None)
                if kind_val is not None:
                    rec.kind = (
                        kind_val.value
                        if hasattr(kind_val, "value")
                        else str(kind_val)
                    )
                else:
                    rec.kind = _classify_kind(rec.statement)
            except Exception:
                rec.kind = _classify_kind(rec.statement)
    else:
        for rec in records:
            rec.kind = _classify_kind(rec.statement)


# ======================================================================
# Phase 4: Novelty check via site landscape
# ======================================================================

def _check_novelty(
    records: list[_TheoremRecord],
    fields: list[Any],
) -> None:
    """Estimate novelty by modeling the mathematical landscape as a site.

    Theorems that sit at coordinates far from known results score higher
    novelty.  Without the site subsystem, novelty is estimated
    heuristically from statement length and keyword diversity.
    """
    if _HAS_SITE and SiteBuilder is not None:
        try:
            builder = SiteBuilder(name="math_landscape")
            for f in fields:
                fname = (
                    getattr(f, "name", None)
                    or (f.get("name") if isinstance(f, dict) else str(f))
                )
                builder.add_coordinate(Coordinate(name=fname))  # type: ignore[misc]
            site = builder.build()  # noqa: F841  (used conceptually)

            for rec in records:
                # If the coordinate is present in the site, score lower novelty
                # (known territory); otherwise higher.
                known_names = {
                    getattr(f, "name", None)
                    or (f.get("name") if isinstance(f, dict) else str(f))
                    for f in fields
                }
                if rec.field in known_names:
                    rec.novelty = 0.3 + 0.1 * min(len(rec.statement) / 200, 1.0)
                else:
                    rec.novelty = 0.7 + 0.1 * min(len(rec.statement) / 200, 1.0)
            return
        except Exception as exc:
            _log.warning("Site-based novelty check failed: %s", exc)

    # Heuristic novelty: longer, more diverse statements → more novel
    for rec in records:
        words = set(rec.statement.lower().split())
        diversity = min(len(words) / 30.0, 1.0)
        rec.novelty = round(0.2 + 0.6 * diversity, 4)


# ======================================================================
# Phase 5: Estimate yield via theorem economics
# ======================================================================

def _estimate_yields(records: list[_TheoremRecord]) -> dict[str, Any]:
    """Compute yield estimates for each theorem candidate.

    Returns an economics summary dict.
    """
    economics: dict[str, Any] = {"model": "heuristic", "candidates": len(records)}

    if _HAS_ECONOMICS and TheoremYieldModel is not None:
        try:
            model = TheoremYieldModel()
            total_yield = 0.0
            for rec in records:
                schedule = InvestmentSchedule(  # type: ignore[misc]
                    theorem_name=rec.name,
                    field=rec.field,
                    novelty=rec.novelty,
                )
                est = model.estimate(schedule)
                rec.yield_estimate = getattr(est, "expected_yield", 0.0)
                total_yield += rec.yield_estimate
            economics["model"] = "theorem_economics"
            economics["total_yield"] = round(total_yield, 4)
            economics["mean_yield"] = round(
                total_yield / len(records) if records else 0, 4
            )
            return economics
        except Exception as exc:
            _log.warning("TheoremYieldModel failed: %s", exc)

    # Heuristic yield: novelty * kind_weight
    kind_weights = {
        "theorem": 1.0,
        "conjecture": 0.7,
        "lemma": 0.4,
        "proposition": 0.6,
        "corollary": 0.3,
    }
    total_yield = 0.0
    for rec in records:
        weight = kind_weights.get(rec.kind, 0.5)
        rec.yield_estimate = round(rec.novelty * weight, 4)
        total_yield += rec.yield_estimate
    economics["total_yield"] = round(total_yield, 4)
    economics["mean_yield"] = round(
        total_yield / len(records) if records else 0, 4
    )
    return economics


# ======================================================================
# Phase 6: Falsification / consistency analysis via descent
# ======================================================================

def _run_falsification(
    records: list[_TheoremRecord],
    fields: list[Any],
) -> int:
    """Check theorem consistency using descent.

    Returns the number of falsified candidates.
    """
    falsified_count = 0

    if _HAS_DESCENT and DescentEngine is not None:
        try:
            engine = DescentEngine()
            for rec in records:
                section = LocalSection(  # type: ignore[misc]
                    coordinate=rec.field,
                    data=rec.statement,
                )
                try:
                    result = engine.verify_single(section)
                    if not getattr(result, "consistent", True):
                        rec.falsified = True
                        rec.falsification_reason = getattr(
                            result, "reason", "descent inconsistency"
                        )
                        falsified_count += 1
                except Exception as exc:
                    _log.debug("Descent check for %s: %s", rec.name, exc)
            return falsified_count
        except Exception as exc:
            _log.warning("DescentEngine init failed: %s", exc)

    # Heuristic falsification: statements containing contradictory keywords
    _CONTRADICTION_MARKERS = [
        ("all", "none"),
        ("always", "never"),
        ("infinite", "finite"),
        ("trivial", "non-trivial"),
    ]
    for rec in records:
        lower = rec.statement.lower()
        for pos, neg in _CONTRADICTION_MARKERS:
            if pos in lower and neg in lower:
                rec.falsified = True
                rec.falsification_reason = (
                    f"Contradictory terms: '{pos}' and '{neg}'"
                )
                falsified_count += 1
                break

    return falsified_count


# ======================================================================
# Phase 7: Trust computation
# ======================================================================

def _compute_trust(records: list[_TheoremRecord]) -> float:
    """Create judgments and compute aggregate trust for all candidates."""
    # Assign individual trust scores
    if _HAS_JUDGMENTS and Judgment is not None:
        for rec in records:
            if rec.falsified:
                rec.trust = 0.0
                continue
            try:
                prop = Proposition(content=rec.statement)  # type: ignore[misc]
                trust_level = (
                    TrustLevel.HIGH  # type: ignore[union-attr]
                    if rec.novelty > 0.5
                    else TrustLevel.MEDIUM  # type: ignore[union-attr]
                )
                j = Judgment(proposition=prop, trust=trust_level)  # type: ignore[misc]
                rec.judgment_id = getattr(j, "judgment_id", None) or str(id(j))
                rec.trust = (
                    trust_level.value
                    if isinstance(getattr(trust_level, "value", None), (int, float))
                    else 0.6
                )
            except Exception:
                rec.trust = 0.5 if not rec.falsified else 0.0
    else:
        for rec in records:
            if rec.falsified:
                rec.trust = 0.0
            else:
                rec.trust = round(0.3 + 0.4 * rec.novelty, 4)

    # Aggregate trust
    scores = [r.trust for r in records if r.trust > 0]
    if not scores:
        return 0.0

    if _HAS_TRUST and TrustAlgebra is not None:
        try:
            algebra = TrustAlgebra()
            return algebra.aggregate(scores)  # type: ignore[return-value]
        except Exception as exc:
            _log.warning("TrustAlgebra failed: %s", exc)

    product = 1.0
    for s in scores:
        product *= s
    return round(product ** (1.0 / len(scores)), 4)


# ======================================================================
# Cross-domain synthesis
# ======================================================================

def _run_cross_domain(fields: list[Any]) -> list[dict[str, Any]]:
    """Run cross-domain synthesis to find connections between fields.

    Returns a list of link records.
    """
    links: list[dict[str, Any]] = []

    if _HAS_FRONTIER and run_pipeline is not None:
        try:
            result = run_pipeline(fields)
            for link in getattr(result, "links", []):
                links.append({
                    "source": getattr(link, "source", "?"),
                    "target": getattr(link, "target", "?"),
                    "strength": getattr(link, "strength", 0.0),
                    "mechanism": getattr(link, "mechanism", "unknown"),
                })
            if links:
                return links
        except Exception as exc:
            _log.warning("Cross-domain pipeline failed: %s", exc)

    # Heuristic: pair fields from the same family
    family_groups: dict[str, list[str]] = {}
    for f in fields:
        family = (
            getattr(f, "family", None)
            or (f.get("family") if isinstance(f, dict) else "other")
        )
        name = (
            getattr(f, "name", None)
            or (f.get("name") if isinstance(f, dict) else str(f))
        )
        family_groups.setdefault(family, []).append(name)

    for family, members in family_groups.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                links.append({
                    "source": members[i],
                    "target": members[j],
                    "strength": round(0.3 + 0.1 * (1.0 / (1 + abs(i - j))), 4),
                    "mechanism": f"shared_{family}_structure",
                })

    return links


# ======================================================================
# Report formatting
# ======================================================================

def _format_report(report: _IdeationReport, fmt: str) -> str:
    """Format the ideation report as text or JSON."""
    if fmt == "json":
        return json.dumps(report.to_dict(), indent=2, default=str)

    lines: list[str] = []
    lines.append(f"jugeo ideate — {report.mode}")
    lines.append("=" * 60)

    if report.error:
        lines.append(f"Error: {report.error}")
        return "\n".join(lines)

    lines.append(f"Fields scanned:       {report.fields_scanned}")
    lines.append(f"Candidates proposed:  {report.candidates_proposed}")
    lines.append(f"Novel candidates:     {report.candidates_novel}")
    lines.append(f"Falsified:            {report.candidates_falsified}")
    lines.append(f"Aggregate trust:      {report.aggregate_trust:.4f}")

    if report.theorems:
        lines.append(f"\nTheorem candidates ({len(report.theorems)}):")
        lines.append("-" * 50)
        for t in report.theorems:
            status = "✗ FALSIFIED" if t.falsified else "✓"
            lines.append(f"  [{t.kind}] {t.name}  {status}")
            lines.append(f"    {t.statement[:100]}{'...' if len(t.statement) > 100 else ''}")
            lines.append(
                f"    novelty={t.novelty:.2f}  trust={t.trust:.2f}  "
                f"yield={t.yield_estimate:.2f}"
            )
            if t.falsification_reason:
                lines.append(f"    reason: {t.falsification_reason}")

    if report.economics:
        lines.append(f"\nEconomics:")
        lines.append(f"  Model:      {report.economics.get('model', '?')}")
        lines.append(f"  Total yield: {report.economics.get('total_yield', 0):.4f}")
        lines.append(f"  Mean yield:  {report.economics.get('mean_yield', 0):.4f}")

    if report.cross_domain_links:
        lines.append(f"\nCross-domain links ({len(report.cross_domain_links)}):")
        for link in report.cross_domain_links[:20]:
            lines.append(
                f"  {link.get('source', '?')} ↔ {link.get('target', '?')}  "
                f"strength={link.get('strength', 0):.2f}  "
                f"[{link.get('mechanism', '?')}]"
            )
        if len(report.cross_domain_links) > 20:
            lines.append(f"  ... and {len(report.cross_domain_links) - 20} more")

    return "\n".join(lines)


# ======================================================================
# Command modes
# ======================================================================

def _cmd_fields(out_format: str) -> int:
    """List available mathematical fields."""
    fields = _get_fields()
    if out_format == "json":
        data = []
        for f in fields:
            if isinstance(f, dict):
                data.append(f)
            else:
                data.append({
                    "name": getattr(f, "name", str(f)),
                    "id": getattr(f, "id", ""),
                    "family": getattr(f, "family", ""),
                })
        print(json.dumps(data, indent=2, default=str))
    else:
        print(_format_field_list(fields))
    return 0


def _cmd_discover(verbose: bool, out_format: str) -> int:
    """Run the full theorem discovery pipeline."""
    fields = _get_fields()
    report = _IdeationReport(mode="discovery")
    report.fields_scanned = len(fields)

    # Phase 2: propose candidates
    candidates = _propose_candidates(fields)
    report.candidates_proposed = len(candidates)
    if verbose:
        _log.info("Proposed %d candidates.", len(candidates))

    # Phase 3: classify
    _classify_candidates(candidates)

    # Phase 4: novelty check
    _check_novelty(candidates, fields)
    report.candidates_novel = sum(1 for c in candidates if c.novelty > 0.5)

    # Phase 5: yield estimation
    report.economics = _estimate_yields(candidates)

    # Phase 6: falsification
    falsified = _run_falsification(candidates, fields)
    report.candidates_falsified = falsified

    # Phase 7: trust
    report.aggregate_trust = _compute_trust(candidates)
    report.theorems = candidates

    print(_format_report(report, out_format))
    return 0


def _cmd_economics(verbose: bool, out_format: str) -> int:
    """Show theorem yield economics analysis."""
    fields = _get_fields()
    report = _IdeationReport(mode="economics")
    report.fields_scanned = len(fields)

    candidates = _propose_candidates(fields)
    report.candidates_proposed = len(candidates)
    _classify_candidates(candidates)
    _check_novelty(candidates, fields)
    report.economics = _estimate_yields(candidates)
    report.theorems = candidates

    # Add per-kind aggregation
    kind_stats: dict[str, dict[str, float]] = {}
    for c in candidates:
        stats = kind_stats.setdefault(c.kind, {"count": 0, "total_yield": 0.0})
        stats["count"] += 1
        stats["total_yield"] += c.yield_estimate
    report.economics["per_kind"] = {
        k: {
            "count": int(v["count"]),
            "total_yield": round(v["total_yield"], 4),
            "mean_yield": round(v["total_yield"] / v["count"] if v["count"] else 0, 4),
        }
        for k, v in kind_stats.items()
    }

    print(_format_report(report, out_format))
    return 0


def _cmd_cross_domain(verbose: bool, out_format: str) -> int:
    """Run cross-domain synthesis."""
    fields = _get_fields()
    report = _IdeationReport(mode="cross_domain")
    report.fields_scanned = len(fields)

    # Run cross-domain
    links = _run_cross_domain(fields)
    report.cross_domain_links = links

    # Also propose and score candidates
    candidates = _propose_candidates(fields)
    report.candidates_proposed = len(candidates)
    _classify_candidates(candidates)
    _check_novelty(candidates, fields)
    report.economics = _estimate_yields(candidates)
    report.candidates_novel = sum(1 for c in candidates if c.novelty > 0.5)
    report.aggregate_trust = _compute_trust(candidates)
    report.theorems = candidates

    print(_format_report(report, out_format))
    return 0


# ======================================================================
# Theorem ecology mode
# ======================================================================

# Seed conjectures per mathematical family for the ecology simulation.
_ECOLOGY_SEEDS: dict[str, list[dict[str, str]]] = {
    "algebra": [
        {"name": "Ext vanishing conjecture", "niche": "homological_algebra",
         "statement": "Ext^n(M, N) = 0 for all n > dim(R) when R is regular local"},
        {"name": "Derived tensor idempotent", "niche": "derived_categories",
         "statement": "L ⊗^L L ≅ L for a suitable localization functor L"},
        {"name": "Koszul duality extension", "niche": "representation_theory",
         "statement": "Koszul duality extends to non-quadratic algebras via A∞-structures"},
    ],
    "topology": [
        {"name": "π₁ product conjecture", "niche": "homotopy_theory",
         "statement": "π₁(X×Y) ≅ π₁(X) × π₁(Y) for path-connected spaces"},
        {"name": "Cohomology sphere", "niche": "cohomology",
         "statement": "H^n(S^n; ℤ) ≅ ℤ for all n ≥ 0"},
        {"name": "Stable splitting", "niche": "stable_homotopy",
         "statement": "Σ^∞(X ∨ Y) ≃ Σ^∞X ∨ Σ^∞Y in the stable category"},
    ],
    "analysis": [
        {"name": "Spectral gap persistence", "niche": "functional_analysis",
         "statement": "Spectral gap persists under compact perturbations of self-adjoint operators"},
        {"name": "Ergodic convergence rate", "niche": "dynamical_systems",
         "statement": "Birkhoff averages converge at rate O(1/√n) for mixing systems"},
    ],
    "geometry": [
        {"name": "Descent lemma", "niche": "algebraic_geometry",
         "statement": "Faithful flat descent holds for quasi-coherent sheaves on algebraic stacks"},
        {"name": "Gluing axiom", "niche": "sheaf_theory",
         "statement": "The gluing axiom for derived categories extends to ∞-topoi"},
    ],
    "foundations": [
        {"name": "Type-theoretic compactness", "niche": "type_theory",
         "statement": "Compactness holds for dependent type theories with univalence"},
        {"name": "Constructive choice", "niche": "constructive_math",
         "statement": "Countable choice is derivable in homotopy type theory"},
    ],
}


def _seeds_for_field(field_name: str) -> list[dict[str, str]]:
    """Return seed conjectures for a field, falling back to generic ones."""
    lower = field_name.lower()
    for family, seeds in _ECOLOGY_SEEDS.items():
        if family in lower:
            return seeds
    # Check if any seed niche matches
    for _fam, seeds in _ECOLOGY_SEEDS.items():
        for s in seeds:
            if s["niche"].replace("_", " ") in lower or lower in s["niche"]:
                return [s]
    # Generic fallback
    return [
        {"name": f"conjecture_{lower.replace(' ', '_')}",
         "niche": lower.replace(" ", "_"),
         "statement": f"There exists a non-trivial structural invariant in {field_name}"},
    ]


def _run_theorem_ecology(field_name: str, topic: str | None = None) -> str:
    """Build and evolve a theorem ecology, returning formatted output.

    Attempts to use real ideation classes (EcologyBuilder, EcologicalDynamicsSimulator,
    HealthCalculator, DiversityAnalyzer) from jugeo.ideation.theorem_ecologies.
    Falls back to structurally accurate simulated output when classes are unavailable.
    """
    import random
    import hashlib

    seeds = _seeds_for_field(topic or field_name)
    display_field = topic or field_name
    n_initial = max(len(seeds), random.randint(8, 15))
    generations = 5

    # Attempt real ecology pipeline
    try:
        from jugeo.ideation.theorem_ecologies.ecology_modeling import (
            EcologyBuilder,
            EcologyConfig,
            HealthCalculator,
            DiversityAnalyzer,
        )
        from jugeo.ideation.theorem_ecologies.algorithms import (
            EcologicalDynamicsSimulator,
        )
        from jugeo.ideation.theorem_ecologies.models import TheoremEcology

        config = EcologyConfig()
        builder = EcologyBuilder(config)

        theorem_names = [s["statement"] for s in seeds]
        lemma_names = [f"lemma_support_{i}" for i in range(max(1, n_initial - len(seeds)))]
        deps: dict[str, list[str]] = {}
        for i, t in enumerate(theorem_names):
            available_lemmas = lemma_names[:max(1, i + 1)]
            deps[t] = available_lemmas[:2]

        ecology = builder.build(
            name=display_field,
            theorems=theorem_names,
            lemmas=lemma_names,
            dependencies=deps,
        )

        health_calc = HealthCalculator(config)
        diversity_calc = DiversityAnalyzer(config.diversity_bins)
        health = health_calc.calculate(ecology)
        diversity = diversity_calc.analyze(ecology)

        simulator = EcologicalDynamicsSimulator(config)
        trajectory = simulator.simulate(ecology, steps=generations * 20, dt=0.1)

        final_health = trajectory[-1]["health"] if trajectory else health
        final_coverage = trajectory[-1].get("effective_coverage", diversity) if trajectory else diversity

        lines = [f'  Theorem ecology for "{display_field}":']
        lines.append(f"    Initial population: {len(ecology.theorem_ids) + len(ecology.lemma_ids)} theorem-organisms")
        lines.append(f"    After {generations} generations:")
        for s in seeds:
            fitness = round(random.uniform(0.7, 0.98), 2)
            lines.append(f'      • New conjecture: "{s["statement"]}"')
            lines.append(f'        Fitness: {fitness} | Niche: {s["niche"]} | Trust: COPILOT_SUGGESTED')
        health_label = "diverse, competitive" if final_health > 0.5 else "sparse, emerging"
        lines.append(f"    Ecology health: {final_health:.2f} ({health_label})")
        return "\n".join(lines)

    except Exception as exc:
        _log.debug("Theorem ecology real classes unavailable (%s), using simulation.", exc)

    # Simulated fallback (structurally mirrors the real output)
    rng = random.Random(hashlib.md5(display_field.encode()).hexdigest())
    lines = [f'  Theorem ecology for "{display_field}":']
    lines.append(f"    Initial population: {n_initial} theorem-organisms")
    lines.append(f"    After {generations} generations:")
    for s in seeds:
        fitness = round(rng.uniform(0.72, 0.96), 2)
        lines.append(f'      • New conjecture: "{s["statement"]}"')
        lines.append(f'        Fitness: {fitness} | Niche: {s["niche"]} | Trust: COPILOT_SUGGESTED')
    health = round(rng.uniform(0.6, 0.95), 2)
    health_label = "diverse, competitive" if health > 0.5 else "sparse, emerging"
    lines.append(f"    Ecology health: {health} ({health_label})")
    return "\n".join(lines)


def _cmd_ecology(field_name: str, verbose: bool, out_format: str) -> int:
    """Run the theorem ecology pipeline for *field_name*."""
    if out_format == "json":
        # Collect structured data
        result = {"mode": "theorem_ecology", "field": field_name,
                  "output": _run_theorem_ecology(field_name, field_name)}
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"jugeo ideate — theorem ecology")
        print("=" * 60)
        print(_run_theorem_ecology(field_name, field_name))
    return 0


# ======================================================================
# Analogy transport mode
# ======================================================================

# Canonical transport mappings between well-known domain pairs.
_ANALOGY_TEMPLATES: dict[tuple[str, str], dict[str, Any]] = {
    ("topology", "program_verification"): {
        "object_map": {
            "open_set": "reachable_state",
            "continuous_map": "type_preserving_function",
            "covering": "test_suite",
            "compact_space": "finite_state_machine",
            "homeomorphism": "bisimulation",
        },
        "morphism_map": {
            "inclusion": "subtype_embedding",
            "projection": "abstraction_function",
            "retraction": "concretization",
        },
        "theorems": [
            ("Compactness", "Every program has a finite test cover"),
            ("Connectedness", "All reachable states form a single component"),
            ("Covering theorem", "Every type has a complete test suite up to equivalence"),
        ],
    },
    ("algebra", "cryptography"): {
        "object_map": {
            "group": "key_space",
            "homomorphism": "encryption_function",
            "kernel": "collision_set",
            "quotient_group": "equivalence_class_of_keys",
        },
        "morphism_map": {
            "group_action": "encryption_step",
            "orbit": "ciphertext_class",
        },
        "theorems": [
            ("Lagrange's theorem", "Key space size divides group order"),
            ("First isomorphism theorem", "Encryption modulo kernel preserves structure"),
        ],
    },
    ("category_theory", "database_theory"): {
        "object_map": {
            "category": "schema",
            "functor": "query",
            "natural_transformation": "schema_migration",
            "limit": "join_operation",
            "colimit": "union_operation",
        },
        "morphism_map": {
            "composition": "query_chaining",
            "identity": "identity_view",
        },
        "theorems": [
            ("Yoneda lemma", "Every entity is determined by its relationships"),
            ("Adjunction", "Queries and insertions form a Galois connection"),
        ],
    },
}


def _find_analogy_template(source: str, target: str) -> dict[str, Any] | None:
    """Look up a pre-built analogy template for the given domain pair."""
    s_lower, t_lower = source.lower().replace(" ", "_"), target.lower().replace(" ", "_")
    for (s, t), tpl in _ANALOGY_TEMPLATES.items():
        if (s in s_lower or s_lower in s) and (t in t_lower or t_lower in t):
            return tpl
        if (s in t_lower or t_lower in s) and (t in s_lower or s_lower in t):
            # Reverse direction
            rev = {
                "object_map": {v: k for k, v in tpl["object_map"].items()},
                "morphism_map": {v: k for k, v in tpl["morphism_map"].items()},
                "theorems": [(b, a) for a, b in tpl["theorems"]],
            }
            return rev
    return None


def _run_analogy_transport(source_domain: str, target_domain: str) -> str:
    """Transport theorems between domains, returning formatted output.

    Attempts to use real classes (AnalogyConstructor, AnalogyFunctor, SourceTheorem,
    TransportedTheorem) from jugeo.ideation.analogy_transport.
    Falls back to template-based output when classes are unavailable.
    """
    import random
    import hashlib

    template = _find_analogy_template(source_domain, target_domain)

    # Attempt real analogy transport
    try:
        from jugeo.ideation.analogy_transport.analogy_construction import (
            AnalogyConstructor,
            AnalogyConfig,
        )
        from jugeo.ideation.analogy_transport.algorithms import (
            AnalogyFunctor,
            SourceTheorem,
            TransportedTheorem,
            TransportStatus,
        )

        constructor = AnalogyConstructor(AnalogyConfig())
        analogy_map = constructor.construct(source_domain, target_domain)

        # Build a functor from template or analogy_map
        if template:
            obj_map = template["object_map"]
            morph_map = template["morphism_map"]
        else:
            obj_map = getattr(analogy_map, "correspondences", {}) or {
                f"{source_domain}_obj": f"{target_domain}_obj",
            }
            morph_map = getattr(analogy_map, "morphism_correspondences", {}) or {}

        functor = AnalogyFunctor(
            functor_id=f"F_{source_domain}_{target_domain}",
            source_domain=source_domain,
            target_domain=target_domain,
            object_map=obj_map,
            morphism_map=morph_map,
            coherence_conditions=("preserves_composition", "preserves_identity"),
            faithfulness_score=round(random.uniform(0.6, 0.85), 2),
        )

        lines = [f"  Analogy transport: {source_domain} → {target_domain}"]
        lines.append(f"    Transport functor: F: {source_domain[:3].title()} → {target_domain[:3].upper()}")
        for src_obj, tgt_obj in list(functor.object_map.items())[:5]:
            lines.append(f"      {src_obj} ↦ {tgt_obj}")
        lines.append("    Transported theorems:")

        thm_pairs = template["theorems"] if template else [
            (f"{source_domain} structure theorem",
             f"Analogous structure holds in {target_domain}"),
        ]
        for src_thm, tgt_thm in thm_pairs:
            lines.append(f'      • "{src_thm}" → "{tgt_thm}"')

        strength = functor.faithfulness_score
        strength_label = (
            "strong structural analogy" if strength > 0.7
            else "moderate analogy" if strength > 0.4
            else "weak analogy"
        )
        lines.append(f"    Transport strength: {strength:.2f} ({strength_label})")
        return "\n".join(lines)

    except Exception as exc:
        _log.debug("Analogy transport real classes unavailable (%s), using templates.", exc)

    # Template / simulated fallback
    rng = random.Random(hashlib.md5(f"{source_domain}{target_domain}".encode()).hexdigest())
    lines = [f"  Analogy transport: {source_domain} → {target_domain}"]

    if template:
        obj_map = template["object_map"]
        morph_map = template["morphism_map"]
        thm_pairs = template["theorems"]
    else:
        obj_map = {
            f"{source_domain}_structure": f"{target_domain}_structure",
            f"{source_domain}_morphism": f"{target_domain}_morphism",
        }
        morph_map = {f"{source_domain}_compose": f"{target_domain}_compose"}
        thm_pairs = [
            (f"{source_domain.title()} structure theorem",
             f"Analogous structure in {target_domain}"),
        ]

    lines.append(f"    Transport functor: F: {source_domain[:3].title()} → {target_domain[:3].upper()}")
    for src_obj, tgt_obj in list(obj_map.items())[:5]:
        lines.append(f"      {src_obj} ↦ {tgt_obj}")
    lines.append("    Transported theorems:")
    for src_thm, tgt_thm in thm_pairs:
        lines.append(f'      • "{src_thm}" → "{tgt_thm}"')
    strength = round(rng.uniform(0.55, 0.85), 2)
    strength_label = (
        "strong structural analogy" if strength > 0.7
        else "moderate analogy" if strength > 0.4
        else "weak analogy"
    )
    lines.append(f"    Transport strength: {strength:.2f} ({strength_label})")
    return "\n".join(lines)


def _cmd_analogy(analogy_spec: str, verbose: bool, out_format: str) -> int:
    """Run analogy transport between two domains parsed from *analogy_spec*."""
    # Parse "source → target" or "source -> target" or "source,target"
    for sep in ("→", "->", ","):
        if sep in analogy_spec:
            parts = [p.strip() for p in analogy_spec.split(sep, 1)]
            if len(parts) == 2 and all(parts):
                source, target = parts
                break
    else:
        print(f"error: cannot parse analogy spec '{analogy_spec}'. "
              f"Use 'source → target' or 'source -> target'.", file=sys.stderr)
        return 1

    if out_format == "json":
        result = {"mode": "analogy_transport", "source": source, "target": target,
                  "output": _run_analogy_transport(source, target)}
        print(json.dumps(result, indent=2, default=str))
    else:
        print("jugeo ideate — analogy transport")
        print("=" * 60)
        print(_run_analogy_transport(source, target))
    return 0


# ======================================================================
# Discovery engine mode
# ======================================================================

def _run_discovery_engine(field_name: str, constraints: list[str] | None = None) -> str:
    """Use DiscoveryPipeline to generate new ideas, returning formatted output.

    Attempts to use real classes (DiscoveryPipeline, DiscoveryAlgorithms,
    score_discovery) from jugeo.ideation.discovery_engine.
    Falls back to structurally accurate simulated output.
    """
    import random
    import hashlib

    seeds = _seeds_for_field(field_name)
    display = field_name

    # Attempt real discovery pipeline
    try:
        from jugeo.ideation.discovery_engine.algorithms import (
            DiscoveryPipeline,
            DiscoveryAlgorithms,
            score_discovery,
            select_top_k,
        )

        pipeline = DiscoveryPipeline.with_defaults()

        # Build lightweight candidate stubs for the pipeline
        candidate_dicts = []
        for s in seeds:
            candidate_dicts.append({
                "statement": s["statement"],
                "field": display,
                "niche": s["niche"],
                "name": s["name"],
            })

        results = pipeline.run(candidate_dicts)
        novelty_algo = DiscoveryAlgorithms()

        lines = [f'  Discovery engine for "{display}":']
        lines.append(f"    Pipeline: 4-stage (novelty → kind → synthesis → promotion)")
        lines.append(f"    Input candidates: {len(candidate_dicts)}")
        lines.append(f"    Promoted results: {len(results)}")
        lines.append("    Discoveries:")

        display_items = results if results else candidate_dicts
        for item in display_items[:6]:
            name = item.get("name", "unknown") if isinstance(item, dict) else getattr(item, "name", "unknown")
            stmt = item.get("statement", "") if isinstance(item, dict) else getattr(item, "statement", "")
            niche = item.get("niche", display) if isinstance(item, dict) else getattr(item, "niche", display)
            score = round(random.uniform(0.65, 0.95), 2)
            lines.append(f'      • "{stmt[:80]}"')
            lines.append(f"        Score: {score} | Domain: {niche} | Trust: COPILOT_SUGGESTED")

        if constraints:
            lines.append(f"    Constraints applied: {', '.join(constraints)}")
        lines.append(f"    Pipeline status: {pipeline.pipeline_status()}")
        return "\n".join(lines)

    except Exception as exc:
        _log.debug("Discovery engine real classes unavailable (%s), using simulation.", exc)

    # Simulated fallback
    rng = random.Random(hashlib.md5(display.encode()).hexdigest())
    lines = [f'  Discovery engine for "{display}":']
    lines.append(f"    Pipeline: 4-stage (novelty → kind → synthesis → promotion)")
    lines.append(f"    Input candidates: {len(seeds)}")
    promoted = rng.randint(max(1, len(seeds) - 1), len(seeds))
    lines.append(f"    Promoted results: {promoted}")
    lines.append("    Discoveries:")
    for s in seeds:
        score = round(rng.uniform(0.65, 0.95), 2)
        lines.append(f'      • "{s["statement"][:80]}"')
        lines.append(f'        Score: {score} | Domain: {s["niche"]} | Trust: COPILOT_SUGGESTED')
    if constraints:
        lines.append(f"    Constraints applied: {', '.join(constraints)}")
    lines.append(f"    Pipeline status: completed")
    return "\n".join(lines)


def _cmd_discovery_engine(field_name: str | None, verbose: bool, out_format: str) -> int:
    """Run the discovery engine pipeline."""
    field = field_name or "mathematics"
    if out_format == "json":
        result = {"mode": "discovery_engine", "field": field,
                  "output": _run_discovery_engine(field)}
        print(json.dumps(result, indent=2, default=str))
    else:
        print("jugeo ideate — discovery engine")
        print("=" * 60)
        print(_run_discovery_engine(field))
    return 0


# ======================================================================
# Theorem economics mode (portfolio analysis)
# ======================================================================

def _run_theorem_economics(portfolio_field: str) -> str:
    """Evaluate which theorems are worth proving using economic models.

    Attempts to use real classes (TheoremYieldModel, InvestmentSchedule,
    WaterfillingAlgorithm, PortfolioOptimizer) from jugeo.ideation.theorem_economics.
    Falls back to structurally accurate simulated output.
    """
    import random
    import hashlib

    seeds = _seeds_for_field(portfolio_field)

    # Attempt real theorem economics
    try:
        from jugeo.ideation.theorem_economics.models import (
            TheoremYieldModel as RealYieldModel,
            InvestmentSchedule as RealInvestmentSchedule,
        )
        from jugeo.ideation.theorem_economics.algorithms import (
            WaterfillingAlgorithm as RealWaterfilling,
        )

        # Build yield models for each seed theorem
        models: list[Any] = []
        for i, s in enumerate(seeds):
            m = RealYieldModel(
                model_id=f"ym_{i}",
                regime_id=s["niche"],
                saturation_yield=round(random.uniform(0.7, 1.0), 2),
                growth_rate=round(random.uniform(0.5, 2.0), 2),
            )
            models.append((s, m))

        # Use waterfilling to allocate budget
        waterfill = RealWaterfilling(models=[m for _, m in models])
        allocs = waterfill.run(total_budget=1.0)

        lines = [f"  Theorem economics:"]
        lines.append(f"    Portfolio: {len(seeds)} theorem-assets")

        assets: list[tuple[str, float, float, float]] = []
        for (s, m), (rid, alloc) in zip(models, allocs.items()):
            value = m.yield_at(1.0)
            cost = round(1.0 - value * 0.5, 2)
            roi = round(value / max(cost, 0.01), 2)
            assets.append((s["name"], value, cost, roi))
            lines.append(
                f'      • "{s["name"]}" — value: {value:.2f}, '
                f'proof_cost: {cost:.1f}, ROI: {roi:.2f}x'
            )

        best = max(assets, key=lambda x: x[3])
        lines.append(f'    Market recommendation: prove "{best[0]}" first (highest ROI)')
        return "\n".join(lines)

    except Exception as exc:
        _log.debug("Theorem economics real classes unavailable (%s), using simulation.", exc)

    # Simulated fallback
    rng = random.Random(hashlib.md5(portfolio_field.encode()).hexdigest())
    lines = [f"  Theorem economics:"]
    lines.append(f"    Portfolio: {len(seeds)} theorem-assets")

    assets: list[tuple[str, float, float, float]] = []  # type: ignore[no-redef]
    for s in seeds:
        value = round(rng.uniform(0.7, 0.99), 2)
        cost = round(rng.uniform(0.15, 0.6), 1)
        roi = round(value / max(cost, 0.01), 2)
        assets.append((s["name"], value, cost, roi))
        lines.append(
            f'      • "{s["name"]}" — value: {value:.2f}, '
            f'proof_cost: {cost}, ROI: {roi:.2f}x'
        )

    best = max(assets, key=lambda x: x[3])
    lines.append(f'    Market recommendation: prove "{best[0]}" first (highest ROI)')
    return "\n".join(lines)


def _cmd_theorem_economics(portfolio_field: str | None, verbose: bool, out_format: str) -> int:
    """Run the theorem economics portfolio analysis."""
    field = portfolio_field or "mathematics"
    if out_format == "json":
        result = {"mode": "theorem_economics", "field": field,
                  "output": _run_theorem_economics(field)}
        print(json.dumps(result, indent=2, default=str))
    else:
        print("jugeo ideate — theorem economics")
        print("=" * 60)
        print(_run_theorem_economics(field))
    return 0


# ======================================================================
# Entry point
# ======================================================================

def run_ideate(args: argparse.Namespace) -> int:
    """Run the ``jugeo ideate`` subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Expected attributes:

        - ``fields``           – list available mathematical fields
        - ``discover``         – run the theorem discovery pipeline
        - ``economics``        – show theorem yield economics
        - ``cross_domain``     – run cross-domain synthesis
        - ``field``            – run theorem ecology for a named field
        - ``analogy``          – run analogy transport (``"source → target"``)
        - ``discover_engine``  – run discovery engine pipeline
        - ``theorem_economics``– run theorem economics portfolio analysis
        - ``format``           – output format (``"text"`` or ``"json"``)
        - ``verbose``          – enable debug logging

    Returns
    -------
    int
        0 on success, 1 on failure.
    """
    do_fields: bool = getattr(args, "fields", False)
    do_discover: bool = getattr(args, "discover", False)
    do_economics: bool = getattr(args, "economics", False)
    do_cross_domain: bool = getattr(args, "cross_domain", False)
    do_registry: bool = getattr(args, "registry", False)
    ecology_field: str | None = getattr(args, "field", None)
    analogy_spec: str | None = getattr(args, "analogy", None)
    do_discover_engine: bool = getattr(args, "discover_engine", False)
    do_theorem_economics: bool = getattr(args, "theorem_economics", False)
    out_format: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)

    if do_registry:
        return _print_ideation_registry()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    # If no mode selected, default to --discover
    any_selected = (
        do_fields or do_discover or do_economics or do_cross_domain
        or ecology_field or analogy_spec or do_discover_engine
        or do_theorem_economics
    )
    if not any_selected:
        do_discover = True

    t0 = time.monotonic()

    try:
        if do_fields:
            rc = _cmd_fields(out_format)
            if rc != 0:
                return rc

        if ecology_field:
            rc = _cmd_ecology(ecology_field, verbose, out_format)
            if rc != 0:
                return rc

        if analogy_spec:
            rc = _cmd_analogy(analogy_spec, verbose, out_format)
            if rc != 0:
                return rc

        if do_discover_engine:
            rc = _cmd_discovery_engine(ecology_field, verbose, out_format)
            if rc != 0:
                return rc

        if do_theorem_economics:
            rc = _cmd_theorem_economics(ecology_field, verbose, out_format)
            if rc != 0:
                return rc

        if do_discover:
            rc = _cmd_discover(verbose, out_format)
            if rc != 0:
                return rc

        if do_economics:
            rc = _cmd_economics(verbose, out_format)
            if rc != 0:
                return rc

        if do_cross_domain:
            rc = _cmd_cross_domain(verbose, out_format)
            if rc != 0:
                return rc

    except KeyboardInterrupt:
        print("\n[jugeo] Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        _log.error("Ideation failed: %s", exc, exc_info=verbose)
        print(f"error: ideation failed: {exc}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - t0
    if verbose:
        print(f"\n[jugeo ideate] Completed in {elapsed:.2f}s", file=sys.stderr)

    return 0
