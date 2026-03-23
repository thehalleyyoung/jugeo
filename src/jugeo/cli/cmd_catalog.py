"""jugeo catalog — comprehensive subsystem catalog and introspection."""
from __future__ import annotations

import argparse
import sys


# ---------------------------------------------------------------------------
# Registry: import all classes from every subsystem
# ---------------------------------------------------------------------------

def _collect_geometry_classes():
    """Collect all geometry classes."""
    registry = {}
    try:
        from jugeo.geometry.site import (
            CoordinateKind, MorphismKind, Coordinate, Morphism,
            OverlapData, CoveringFamily, GrothendieckTopology,
            CoordinateIndex, Site, SiteBuilder, SiteSerializer,
            SiteDiagnostics, CoordinateMorphism,
        )
        for cls in [
            CoordinateKind, MorphismKind, Coordinate, Morphism,
            OverlapData, CoveringFamily, GrothendieckTopology,
            CoordinateIndex, Site, SiteBuilder, SiteSerializer,
            SiteDiagnostics, CoordinateMorphism,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.geometry.descent import (
            OverlapStatus, DescentStrategy, TrustFloorPolicy, DescentPhase,
            LocalSection, OverlapCondition, CohomologyClass, RepairFrontier,
            DescentLog, DescentConfiguration, GluingData, GlobalSection,
            DescentObstruction, DescentResult, Obstruction, GluingReport,
            DescentEngine,
        )
        for cls in [
            OverlapStatus, DescentStrategy, TrustFloorPolicy, DescentPhase,
            LocalSection, OverlapCondition, CohomologyClass, RepairFrontier,
            DescentLog, DescentConfiguration, GluingData, GlobalSection,
            DescentObstruction, DescentResult, Obstruction, GluingReport,
            DescentEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.geometry.covers import (
            CoverMetric, CoverMember, OverlapDatum, Cover, Sieve,
            CoverRefinement, CoverBuilder, CoverCategory, CoverGenerator,
            CoverDiagnostics, CoverSerializer, MergeConflictPolicy,
            CoverMerger, CoverStatistics,
        )
        for cls in [
            CoverMetric, CoverMember, OverlapDatum, Cover, Sieve,
            CoverRefinement, CoverBuilder, CoverCategory, CoverGenerator,
            CoverDiagnostics, CoverSerializer, MergeConflictPolicy,
            CoverMerger, CoverStatistics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.geometry.hypercovers import (
            HypercoverKind, HypercoverLevel, Hypercover, HypercoverBuilder,
            SimplicialObject, CechNerve, HypercoverSynthesizer,
            MatchingObject, HypercoverMorphism, HypercoverDescent,
            HypercoverDiagnostics,
        )
        for cls in [
            HypercoverKind, HypercoverLevel, Hypercover, HypercoverBuilder,
            SimplicialObject, CechNerve, HypercoverSynthesizer,
            MatchingObject, HypercoverMorphism, HypercoverDescent,
            HypercoverDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.geometry.supports import (
            SupportRegion, StarNeighborhood, SupportSet, SupportedSection,
            SupportTracker, SupportStatus, SupportMap, SupportPropagation,
            VerificationResult, SupportVerifier, SupportMerger,
            SupportVisualization, SupportPolicy, SupportSerializer,
            SupportStatistics, EvidenceSupportScope,
        )
        for cls in [
            SupportRegion, StarNeighborhood, SupportSet, SupportedSection,
            SupportTracker, SupportStatus, SupportMap, SupportPropagation,
            VerificationResult, SupportVerifier, SupportMerger,
            SupportVisualization, SupportPolicy, SupportSerializer,
            SupportStatistics, EvidenceSupportScope,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_encodings_classes():
    """Collect all encoding classes from all sub-packages."""
    registry = {}
    # collection_heap_encodings
    try:
        from jugeo.encodings.collection_heap_encodings.algorithms import (
            AlgorithmStatus, AlgorithmResult, CollectionHeapAlgorithm, BottomUpHeapSummaryAlgorithm,
            FixedPointAliasAnalysis, CollectionInvariantInference, InterfaceAbstractionSynthesis, BoundaryConditionMinimization,
        )
        for cls in [
            AlgorithmStatus, AlgorithmResult, CollectionHeapAlgorithm, BottomUpHeapSummaryAlgorithm,
            FixedPointAliasAnalysis, CollectionInvariantInference, InterfaceAbstractionSynthesis, BoundaryConditionMinimization,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.aliasing_obligation import (
            AliasKind, PointerInfo, AliasingObligation, AliasPartitionBuilder,
            DisjointnessChecker,
        )
        for cls in [
            AliasKind, PointerInfo, AliasingObligation, AliasPartitionBuilder,
            DisjointnessChecker,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.aliasing_obligations import (
            TrustTier, Judgment, CechObstruction, AliasKind,
            DischargeResult, StalkerKind, AliasCechObstruction, AliasJudgment,
            StalkerEquivalence, _UnionFind, AliasingObligation, AliasProofBurden,
            MayAliasSet, AliasGlobalSection, AliasDescentObstruction,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, AliasKind,
            DischargeResult, StalkerKind, AliasCechObstruction, AliasJudgment,
            StalkerEquivalence, _UnionFind, AliasingObligation, AliasProofBurden,
            MayAliasSet, AliasGlobalSection, AliasDescentObstruction,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.collection_encoder import (
            CollectionKind, ElementTypeInfo, CollectionEncoder, CollectionInvariantChecker,
            CollectionFragmentClassifier,
        )
        for cls in [
            CollectionKind, ElementTypeInfo, CollectionEncoder, CollectionInvariantChecker,
            CollectionFragmentClassifier,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.collection_encodings_should_be_fam import (
            TrustTier, IndexKind, SectionStatus, CoverStrategyKind,
            CechObstruction, CollectionJudgment, GlobalSection, DescentObstruction,
            IndexObject, LocalSection, ElementSheaf, CollectionCoverStrategy,
            IndexedFamilyRepr, CollectionEncoding, EncodingStatistics,
        )
        for cls in [
            TrustTier, IndexKind, SectionStatus, CoverStrategyKind,
            CechObstruction, CollectionJudgment, GlobalSection, DescentObstruction,
            IndexObject, LocalSection, ElementSheaf, CollectionCoverStrategy,
            IndexedFamilyRepr, CollectionEncoding, EncodingStatistics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.exact_boundaries_and_explicit_non import (
            TrustTier, Judgment, CechObstruction, MembershipStatus,
            BoundaryKind, WitnessKind, CompletenessStatus, BoundaryCechObstruction,
            BoundaryJudgment, IndexBoundaryEntry, NonMembershipWitness, MembershipObligation,
            BoundaryProof, BoundaryGlobalSection, BoundaryDescentObstruction, ExactBoundaryEncoding,
            BoundaryStats, BoundaryChecker,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, MembershipStatus,
            BoundaryKind, WitnessKind, CompletenessStatus, BoundaryCechObstruction,
            BoundaryJudgment, IndexBoundaryEntry, NonMembershipWitness, MembershipObligation,
            BoundaryProof, BoundaryGlobalSection, BoundaryDescentObstruction, ExactBoundaryEncoding,
            BoundaryStats, BoundaryChecker,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.exact_boundary_encoder import (
            BoundaryKind, NonAliasingLawName, BoundaryCondition, _FormulaMinimizer,
            NonAliasingLawLibrary, ExactBoundaryEncoder, BoundaryVerifier,
        )
        for cls in [
            BoundaryKind, NonAliasingLawName, BoundaryCondition, _FormulaMinimizer,
            NonAliasingLawLibrary, ExactBoundaryEncoder, BoundaryVerifier,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.heap_summaries_and_object_identity import (
            TrustTier, HeapNodeKind, RegionKind, IdentityRelation,
            HeapSectionStatus, HeapCechObstruction, HeapJudgment, ObjectIdentityNode,
            AllocationRegion, HeapGlobalSection, HeapDescentObstruction, HeapGraphEncoding,
            HeapSummary, HeapSummaryStats,
        )
        for cls in [
            TrustTier, HeapNodeKind, RegionKind, IdentityRelation,
            HeapSectionStatus, HeapCechObstruction, HeapJudgment, ObjectIdentityNode,
            AllocationRegion, HeapGlobalSection, HeapDescentObstruction, HeapGraphEncoding,
            HeapSummary, HeapSummaryStats,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.heap_summary_encoder import (
            HeapKind, SeparationLogicFragment, HeapSummaryEncoder, HeapCompositionEngine,
            HeapInvariantChecker,
        )
        for cls in [
            HeapKind, SeparationLogicFragment, HeapSummaryEncoder, HeapCompositionEngine,
            HeapInvariantChecker,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.integration import (
            PipelineStage, PipelineResult, _StageTimer, _PipelineMonitor,
            CollectionHeapEncodingSession, EncoderRegistry, HeapCollectionPipeline,
        )
        for cls in [
            PipelineStage, PipelineResult, _StageTimer, _PipelineMonitor,
            CollectionHeapEncodingSession, EncoderRegistry, HeapCollectionPipeline,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.interface_summaries_as_uninterpret import (
            TrustTier, Judgment, CechObstruction, SortKind,
            AxiomKind, SummaryStatus, CallSiteStatus, SummaryCechObstruction,
            SummaryJudgment, SortSignature, SummaryAxiom, UninterpretedFunctionRepr,
            SummaryContract, InterfaceSummary, SummaryTableau, TableauGlobalSection,
            TableauDescentObstruction,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, SortKind,
            AxiomKind, SummaryStatus, CallSiteStatus, SummaryCechObstruction,
            SummaryJudgment, SortSignature, SummaryAxiom, UninterpretedFunctionRepr,
            SummaryContract, InterfaceSummary, SummaryTableau, TableauGlobalSection,
            TableauDescentObstruction,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.interface_summary_encoder import (
            RefinementStatus, MethodSignature, InterfaceSummaryEncoder, InterfaceRefinementChecker,
            AbstractSortBuilder,
        )
        for cls in [
            RefinementStatus, MethodSignature, InterfaceSummaryEncoder, InterfaceRefinementChecker,
            AbstractSortBuilder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.manifest import (
            ManifestValidationError, SectionKind, SectionEntry, CollectionHeapManifest,
            _ManifestSerializer,
        )
        for cls in [
            ManifestValidationError, SectionKind, SectionEntry, CollectionHeapManifest,
            _ManifestSerializer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.models import (
            ModelRegistry, CollectionEncoding, HeapSummary, AliasPartition,
            FiniteMapEncoding, InterfaceAbstraction,
        )
        for cls in [
            ModelRegistry, CollectionEncoding, HeapSummary, AliasPartition,
            FiniteMapEncoding, InterfaceAbstraction,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.collection_heap_encodings.theorems import (
            TheoremCategory, ProofStatus, ProofObligation, CollectionHeapTheorem,
            CollectionEncodingFaithfulnessTheorem, HeapSeparationSoundnessTheorem, AliasPartitionCompletenessTheorem, FrameRuleAdmissibilityTheorem,
            InterfaceAbstractionCorrectnessTheorem, NonAliasingLawConsistencyTheorem, FiniteMapTotalityTheorem, BoundaryConditionPrecisionTheorem,
            CollectionCardinalityConsistencyTheorem, HeapCompositionMonotonicityTheorem, TheoremSuite,
        )
        for cls in [
            TheoremCategory, ProofStatus, ProofObligation, CollectionHeapTheorem,
            CollectionEncodingFaithfulnessTheorem, HeapSeparationSoundnessTheorem, AliasPartitionCompletenessTheorem, FrameRuleAdmissibilityTheorem,
            InterfaceAbstractionCorrectnessTheorem, NonAliasingLawConsistencyTheorem, FiniteMapTotalityTheorem, BoundaryConditionPrecisionTheorem,
            CollectionCardinalityConsistencyTheorem, HeapCompositionMonotonicityTheorem, TheoremSuite,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # deduction_rules
    try:
        from jugeo.encodings.deduction_rules.inference_rules import (
            UnificationEngine, PremiseSet, ConclusionForm, SideConditionEvaluator,
            RuleSchema, CopilotRuleSuggester,
        )
        for cls in [
            UnificationEngine, PremiseSet, ConclusionForm, SideConditionEvaluator,
            RuleSchema, CopilotRuleSuggester,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.deduction_rules.integration import (
            DeductionSession, TransitionSystemRunner, RuleApplicationTracker, JudgmentDischarger,
            CopilotDeductionAssist,
        )
        for cls in [
            DeductionSession, TransitionSystemRunner, RuleApplicationTracker, JudgmentDischarger,
            CopilotDeductionAssist,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.deduction_rules.judgment_transitions import (
            SubstitutionAlgebra, TransitionSchema, TransitionComposer, TrustDeltaComputer,
            TransitionValidator, ProofTrace,
        )
        for cls in [
            SubstitutionAlgebra, TransitionSchema, TransitionComposer, TrustDeltaComputer,
            TransitionValidator, ProofTrace,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.deduction_rules.manifest import (
            SymbolKind, StabilityLevel, DependencyKind, SymbolEntry,
            DependencyEntry, TheoremEntry, CopilotCapability, DeductionRulesManifest,
        )
        for cls in [
            SymbolKind, StabilityLevel, DependencyKind, SymbolEntry,
            DependencyEntry, TheoremEntry, CopilotCapability, DeductionRulesManifest,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.deduction_rules.models import (
            RuleKind, TransitionKind, InferenceStatus, ApplicationResult,
            DeductionRule, JudgmentTransition, InferenceStep, RuleApplication,
            TransitionSystem, JudgmentTerm,
        )
        for cls in [
            RuleKind, TransitionKind, InferenceStatus, ApplicationResult,
            DeductionRule, JudgmentTransition, InferenceStep, RuleApplication,
            TransitionSystem, JudgmentTerm,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.deduction_rules import RuleSet, RuleMetadata
        for cls in [RuleSet, RuleMetadata]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.deduction_rules.semantic_rules import (
            RuleSchema, IntroductionRule, EliminationRule, ComputationRule,
            DefinitionalEqualityRule, SemanticRuleSystem, SoundnessChecker,
            InferenceRule as SemInferenceRule,
        )
        for cls in [
            RuleSchema, IntroductionRule, EliminationRule, ComputationRule,
            DefinitionalEqualityRule, SemanticRuleSystem, SoundnessChecker,
            SemInferenceRule,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.deduction_rules.structural_rules import (
            RuleSchema, WeakeningRule, ContractionRule, ExchangeRule,
            CutRule, StructuralRuleSystem, PermutationLemma,
            InferenceRule as StrInferenceRule,
        )
        for cls in [
            RuleSchema, WeakeningRule, ContractionRule, ExchangeRule,
            CutRule, StructuralRuleSystem, PermutationLemma,
            StrInferenceRule,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.deduction_rules.theorems import (
            VerificationStatus, TheoremKind, ProofMethod, Theorem,
            CutEliminationTheorem, StructuralAdmissibilityTheorem, SemanticSoundnessTheorem, ConfluenceTheorem,
            CompletenessTheorem, TheoremRegistry,
        )
        for cls in [
            VerificationStatus, TheoremKind, ProofMethod, Theorem,
            CutEliminationTheorem, StructuralAdmissibilityTheorem, SemanticSoundnessTheorem, ConfluenceTheorem,
            CompletenessTheorem, TheoremRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # doctrine_completion
    try:
        from jugeo.encodings.doctrine_completion.algorithms import (
            GroundingAlgorithm, GapFindingAlgorithm, CoverageComputationAlgorithm, EvidenceSynthesisAlgorithm,
            ClaimPropagationAlgorithm, DoctrineMinimizationAlgorithm, IncrementalCheckAlgorithm, RiskAssessmentAlgorithm,
        )
        for cls in [
            GroundingAlgorithm, GapFindingAlgorithm, CoverageComputationAlgorithm, EvidenceSynthesisAlgorithm,
            ClaimPropagationAlgorithm, DoctrineMinimizationAlgorithm, IncrementalCheckAlgorithm, RiskAssessmentAlgorithm,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.doctrine_completion.completeness import (
            CompletionStrategy, CompletenessMetrics, CompletenessAnalyzer, CriticalPathAnalyzer,
            DoctrineGraph, CompletionPlan, GapBridger,
        )
        for cls in [
            CompletionStrategy, CompletenessMetrics, CompletenessAnalyzer, CriticalPathAnalyzer,
            DoctrineGraph, CompletionPlan, GapBridger,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.doctrine_completion.doctrine_checker import (
            DoctrineChecker, GroundingVerifier, CoverageAnalyzer, GapPrioritizer,
            DoctrineAuditor,
        )
        for cls in [
            DoctrineChecker, GroundingVerifier, CoverageAnalyzer, GapPrioritizer,
            DoctrineAuditor,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.doctrine_completion.implementation_evidence import (
            EvidenceKind, EvidenceChain, EvidenceCollector, EvidenceValidator,
            EvidenceAggregator, ArtifactResolver, ConfidenceEstimator,
        )
        for cls in [
            EvidenceKind, EvidenceChain, EvidenceCollector, EvidenceValidator,
            EvidenceAggregator, ArtifactResolver, ConfidenceEstimator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.doctrine_completion.integration import (
            IntegrationHealth, DoctrineCompletionIntegration, ManifestDoctrineLinker, RuntimeDoctrineMonitor,
            EvidenceArchiveAdapter, DoctrineCompletionPipeline,
        )
        for cls in [
            IntegrationHealth, DoctrineCompletionIntegration, ManifestDoctrineLinker, RuntimeDoctrineMonitor,
            EvidenceArchiveAdapter, DoctrineCompletionPipeline,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.doctrine_completion.manifest import (
            DoctrineCompletionManifest, DoctrineDescriptor, DoctrineRegistry,
        )
        for cls in [
            DoctrineCompletionManifest, DoctrineDescriptor, DoctrineRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.doctrine_completion.models import (
            ClaimType, StatementStatus, EvidenceKind, GapSeverity,
            DoctrineStatement, ImplementationEvidence, CompletenessCheck, DoctrineGap,
            DoctrineCompletionReport, ClaimGroundingMap, EvidenceRequirement,
        )
        for cls in [
            ClaimType, StatementStatus, EvidenceKind, GapSeverity,
            DoctrineStatement, ImplementationEvidence, CompletenessCheck, DoctrineGap,
            DoctrineCompletionReport, ClaimGroundingMap, EvidenceRequirement,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.doctrine_completion.theorems import (
            DoctrineTheorem, TheoremStatement, DoctrineTheoremRegistry, ImplementationCompletenessProof,
            GroundingSoundnessProof, CoverageAdequacyProof,
        )
        for cls in [
            DoctrineTheorem, TheoremStatement, DoctrineTheoremRegistry, ImplementationCompletenessProof,
            GroundingSoundnessProof, CoverageAdequacyProof,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # incremental_memory
    try:
        from jugeo.encodings.incremental_memory.algorithms import (
            GlueAlgorithm, SectionDiffAlgorithm, OverlapResolutionAlgorithm, EpochAdvanceAlgorithm,
            MemoryCompactionAlgorithm, QuotaEnforcementAlgorithm, SupportMinimizationAlgorithm, BatchUpdateOptimizer,
        )
        for cls in [
            GlueAlgorithm, SectionDiffAlgorithm, OverlapResolutionAlgorithm, EpochAdvanceAlgorithm,
            MemoryCompactionAlgorithm, QuotaEnforcementAlgorithm, SupportMinimizationAlgorithm, BatchUpdateOptimizer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.incremental_memory.change_events import (
            ChangeEventStream, ChangeEventBatch, SupportTracker, EventAggregator,
            ChangeEventSerializer, ChangeEventFilter,
        )
        for cls in [
            ChangeEventStream, ChangeEventBatch, SupportTracker, EventAggregator,
            ChangeEventSerializer, ChangeEventFilter,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.incremental_memory.integration import (
            IntegrationHealth, RuntimeMemoryBridge, InvalidationEngineAdapter, MemoryStateExporter,
            IncrementalUpdatePipeline, IncrementalMemoryIntegration,
        )
        for cls in [
            IntegrationHealth, RuntimeMemoryBridge, InvalidationEngineAdapter, MemoryStateExporter,
            IncrementalUpdatePipeline, IncrementalMemoryIntegration,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.incremental_memory.invalidation import (
            CascadePolicy, RepairAction, RepairPlan, InvalidationWave,
            DependencyTracer, CascadeComputer, CascadeScheduler,
        )
        for cls in [
            CascadePolicy, RepairAction, RepairPlan, InvalidationWave,
            DependencyTracer, CascadeComputer, CascadeScheduler,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.incremental_memory.manifest import (
            EncodingStatus, SubsystemKind, EncodingDescriptor, IncrementalMemoryManifest,
            PackageRegistry, ManifestValidator,
        )
        for cls in [
            EncodingStatus, SubsystemKind, EncodingDescriptor, IncrementalMemoryManifest,
            PackageRegistry, ManifestValidator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.incremental_memory.models import (
            ChangeEventKind, RegionType, EncodingSupportSet, IncrementalUpdate,
            ChangeEvent, InvalidationWaveInfo, MemoryInvalidationCascade, PersistentMemoryState,
        )
        for cls in [
            ChangeEventKind, RegionType, EncodingSupportSet, IncrementalUpdate,
            ChangeEvent, InvalidationWaveInfo, MemoryInvalidationCascade, PersistentMemoryState,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.incremental_memory.theorems import (
            IncrementalMemoryTheorem, TheoremStatus, ProofStrategy, TheoremStatement,
            ProofWitness, SerializationDeterminismProof, GlueCompatibilityProof, CascadeTerminationProof,
            EpochMonotonicityProof, IncrementalMemoryTheoremRegistry,
        )
        for cls in [
            IncrementalMemoryTheorem, TheoremStatus, ProofStrategy, TheoremStatement,
            ProofWitness, SerializationDeterminismProof, GlueCompatibilityProof, CascadeTerminationProof,
            EpochMonotonicityProof, IncrementalMemoryTheoremRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.incremental_memory.update_law import (
            OverlapData, RestrictionResult, GlueComputation, RestrictionOperation,
            OverlapChecker, GlueOperation, UpdateLawProver,
        )
        for cls in [
            OverlapData, RestrictionResult, GlueComputation, RestrictionOperation,
            OverlapChecker, GlueOperation, UpdateLawProver,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # ir_stack
    try:
        from jugeo.encodings.ir_stack.algorithms import (
            AlgorithmConfig, AlgorithmResult,
        )
        for cls in [
            AlgorithmConfig, AlgorithmResult,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.an_implementation_ready_theory_nee import (
            TrustTierEnum, Judgment, CechObstruction, ConcretizationStep,
            ConcretizationTrace, AbstractionGap, GapBridgingStrategy, ConcreteObligation,
            ReadinessChecker, ImplementationReadySpec,
        )
        for cls in [
            TrustTierEnum, Judgment, CechObstruction, ConcretizationStep,
            ConcretizationTrace, AbstractionGap, GapBridgingStrategy, ConcreteObligation,
            ReadinessChecker, ImplementationReadySpec,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.integration import (
            IRStackSession, LoweringPipelineRunner, NormalFormService, AmbiguityResolver,
            CopilotIRAssist,
        )
        for cls in [
            IRStackSession, LoweringPipelineRunner, NormalFormService, AmbiguityResolver,
            CopilotIRAssist,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.ir_layers import (
            LayerScope, BindingEnvironment, ConstraintAccumulator, LayerDiffer,
            CrossLayerRef,
        )
        for cls in [
            LayerScope, BindingEnvironment, ConstraintAccumulator, LayerDiffer,
            CrossLayerRef,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.ir_nodes import (
            IRNodeKindRegistry, NodePayload, AmbiguityPropagator, NodeSubstituter,
            IRTreeWalker, CopilotNodeSuggestor,
        )
        for cls in [
            IRNodeKindRegistry, NodePayload, AmbiguityPropagator, NodeSubstituter,
            IRTreeWalker, CopilotNodeSuggestor,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.lowering import (
            LoweringPassRegistry, AmbiguityPreservationChecker, PassComposer, _StackCheckpoint,
            LoweringPipeline, CopilotLoweringHint, StandardLoweringPasses,
        )
        for cls in [
            LoweringPassRegistry, AmbiguityPreservationChecker, PassComposer, _StackCheckpoint,
            LoweringPipeline, CopilotLoweringHint, StandardLoweringPasses,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.lowering_should_preserve_ambiguity import (
            CollapseError, LoweringStep, AmbiguousIRNode, LoweringTrace,
            SemanticPreservation, AmbiguityWitness, AmbiguityPreservingLowering,
            CechCocycle,
        )
        for cls in [
            CollapseError, LoweringStep, AmbiguousIRNode, LoweringTrace,
            SemanticPreservation, AmbiguityWitness, AmbiguityPreservingLowering,
            CechCocycle,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.manifest import (
            PackageVersion, ComponentStatus, CapabilityFlag, ComponentDescriptor,
            IRStackManifest, APIExport, ManifestRegistry,
        )
        for cls in [
            PackageVersion, ComponentStatus, CapabilityFlag, ComponentDescriptor,
            IRStackManifest, APIExport, ManifestRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.models import (
            IRNodeKind, IRLayerKind, NormalFormKind, AmbiguityKind,
            LoweringPassKind, AmbiguityMark, IRNode, IRLayer,
            IRStack, NormalForm, LoweringPass,
        )
        for cls in [
            IRNodeKind, IRLayerKind, NormalFormKind, AmbiguityKind,
            LoweringPassKind, AmbiguityMark, IRNode, IRLayer,
            IRStack, NormalForm, LoweringPass,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.normal_forms import (
            ReductionStrategy, ReductionRule, ConfluenceChecker, NormalFormCache,
            CanonicalHasher,
        )
        for cls in [
            ReductionStrategy, ReductionRule, ConfluenceChecker, NormalFormCache,
            CanonicalHasher,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.normal_forms_where_comparison_cach import (
            records, identity, RewriteRule, NormalForm,
            CacheEntry, ComparisonCache, DeduplicationEntry, DeduplicationTable,
            NormalFormRewriter,
        )
        for cls in [
            records, identity, RewriteRule, NormalForm,
            CacheEntry, ComparisonCache, DeduplicationEntry, DeduplicationTable,
            NormalFormRewriter,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.the_theory_wants_a_small_number_of import (
            representatives, TrustTierEnum, IRKind, JudgmentTuple,
            CechObstructionClass, LoweringError, IRValidationError, IRNode,
            CanonicalForm, IRTransition, IRLevel, IRStack,
        )
        for cls in [
            representatives, TrustTierEnum, IRKind, JudgmentTuple,
            CechObstructionClass, LoweringError, IRValidationError, IRNode,
            CanonicalForm, IRTransition, IRLevel, IRStack,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.ir_stack.theorems import (
            captures, VerificationStatus, TheoremStatement, AmbiguityPreservationTheorem,
            NormalFormConfluenceTheorem, StackDepthMonotonicityTheorem, LoweringFaithfulnessTheorem, CacheCorrectnessTheorem,
            TheoremRegistry,
        )
        for cls in [
            captures, VerificationStatus, TheoremStatement, AmbiguityPreservationTheorem,
            NormalFormConfluenceTheorem, StackDepthMonotonicityTheorem, LoweringFaithfulnessTheorem, CacheCorrectnessTheorem,
            TheoremRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # pack_federation
    try:
        from jugeo.encodings.pack_federation.bridge_theorems_as_morphisms import (
            BridgeTheoremAsMorphism,
        )
        for cls in [
            BridgeTheoremAsMorphism,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.pack_federation.federation_protocol import (
            FederationProtocolEngine,
        )
        for cls in [
            FederationProtocolEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.pack_federation.integration import (
            PackFederationEncodingIntegration,
        )
        for cls in [
            PackFederationEncodingIntegration,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.pack_federation.manifest import (
            PackFederationCapability, PackFederationManifest,
        )
        for cls in [
            PackFederationCapability, PackFederationManifest,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.pack_federation.models import (
            BridgeTheoremEncoding, PackFederationEncoding, FederationProtocol, PackBoundary,
        )
        for cls in [
            BridgeTheoremEncoding, PackFederationEncoding, FederationProtocol, PackBoundary,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.pack_federation.pack_federation_as_sheaf import (
            PackFederationAsSheaf,
        )
        for cls in [
            PackFederationAsSheaf,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # partiality_model_reconstruction
    try:
        from jugeo.encodings.partiality_model_reconstruction.algebraic_data_surfaces_without_pr import (
            AlgebraicDataSurface, DeferredProof, RuntimeDischarge, SurfaceObligation,
            SurfaceBuilder,
        )
        for cls in [
            AlgebraicDataSurface, DeferredProof, RuntimeDischarge, SurfaceObligation,
            SurfaceBuilder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.algebraic_surfaces import (
            ConstructorArity, SurfaceKind, ProjectionMode, ConstructorSpec,
            RecognizerPredicate, AlgebraicFold, SurfaceProjection,
        )
        for cls in [
            ConstructorArity, SurfaceKind, ProjectionMode, ConstructorSpec,
            RecognizerPredicate, AlgebraicFold, SurfaceProjection,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.algorithms import (
            AlgorithmStatus, MergeStrategy, ValidationLevel, AlgorithmResult,
            AlgorithmRegistry,
        )
        for cls in [
            AlgorithmStatus, MergeStrategy, ValidationLevel, AlgorithmResult,
            AlgorithmRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.effect_summaries_and_branch_sensit import (
            TrustTier, Judgment, CechObstruction, EffectKind,
            EffectSummary, BranchSensitiveEffect, PartialBranchMap, EffectObligation,
            EffectAnalyzer,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, EffectKind,
            EffectSummary, BranchSensitiveEffect, PartialBranchMap, EffectObligation,
            EffectAnalyzer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.exception_semantics import (
            PropagationRule, SumTypeKind, ExceptionSort, MaybeEncoding,
            EitherEncoding, ExceptionPropagationGraph,
        )
        for cls in [
            PropagationRule, SumTypeKind, ExceptionSort, MaybeEncoding,
            EitherEncoding, ExceptionPropagationGraph,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.exception_valued_structural_semant import (
            TrustTier, ExceptionCategory, LiftingStrategy, EncodeMode,
            ExceptionPath, Judgment, CechObstruction, ExceptionValuedException,
            LiftedOperation, ExceptionSemanticsEncoding, ResultTypeDeclaration, ExceptionObligationBundle,
            ExceptionPatternLibrary, ResultTypeRegistry, ExceptionSemanticsEncoder, ExceptionValuedStructuralSemanticsAnalyzer,
            ExceptionValuedStructuralSemanticsWitness, ExceptionValuedStructuralSemanticsCoordinator, ExceptionValueEncoding, ThrowSection,
            CatchHandler, ExceptionSheafMap, ExceptionEncoder, CechH1Obstruction,
            ExceptionValueEncoding, ThrowSection, CatchHandler, ExceptionSheafMap,
            _ExceptionBodyVisitor, ExceptionEncoder,
        )
        for cls in [
            TrustTier, ExceptionCategory, LiftingStrategy, EncodeMode,
            ExceptionPath, Judgment, CechObstruction, ExceptionValuedException,
            LiftedOperation, ExceptionSemanticsEncoding, ResultTypeDeclaration, ExceptionObligationBundle,
            ExceptionPatternLibrary, ResultTypeRegistry, ExceptionSemanticsEncoder, ExceptionValuedStructuralSemanticsAnalyzer,
            ExceptionValuedStructuralSemanticsWitness, ExceptionValuedStructuralSemanticsCoordinator, ExceptionValueEncoding, ThrowSection,
            CatchHandler, ExceptionSheafMap, ExceptionEncoder, CechH1Obstruction,
            ExceptionValueEncoding, ThrowSection, CatchHandler, ExceptionSheafMap,
            _ExceptionBodyVisitor, ExceptionEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.integration import (
            SessionState, BridgeStatus, PartialityEncodingSession, ModelReconstructionPipeline,
            ExceptionSemanticsBridge, CopilotReconstructionAssist,
        )
        for cls in [
            SessionState, BridgeStatus, PartialityEncodingSession, ModelReconstructionPipeline,
            ExceptionSemanticsBridge, CopilotReconstructionAssist,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.manifest import (
            ManifestStatus, ComponentKind, ComponentRecord, PackageManifest,
            ManifestValidator,
        )
        for cls in [
            ManifestStatus, ComponentKind, ComponentRecord, PackageManifest,
            ManifestValidator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.model_reconstruction import (
            AssemblyPhase, CompletionStrategy, ReconstructionPipeline, PartialModelAssembler,
            TrustAnnotator, EvidencePackager,
        )
        for cls in [
            AssemblyPhase, CompletionStrategy, ReconstructionPipeline, PartialModelAssembler,
            TrustAnnotator, EvidencePackager,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.model_reconstruction_as_a_first_cl import (
            PartialEvidence, ReconstructionPlan, ModelReconstructor, TotalModelWitness,
            ReconstructionEngine, EvidenceGap, PartialEvidence, ReconstructionStep,
            ReconstructionPlan, TotalModelWitness, ReconstructionGlobalSection, ReconstructionDescentObstruction,
            ModelReconstructor, ReconstructionStats,
        )
        for cls in [
            PartialEvidence, ReconstructionPlan, ModelReconstructor, TotalModelWitness,
            ReconstructionEngine, EvidenceGap, PartialEvidence, ReconstructionStep,
            ReconstructionPlan, TotalModelWitness, ReconstructionGlobalSection, ReconstructionDescentObstruction,
            ModelReconstructor, ReconstructionStats,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.models import (
            PartialityKind, ExceptionKind, ReconstructionStatus, TrustAnnotationKind,
            PartialFunctionEncoding, ExceptionValuedSemantics, AlgebraicSurface, ModelReconstruction,
            BranchSensitivity, SolverModelReconstruction,
        )
        for cls in [
            PartialityKind, ExceptionKind, ReconstructionStatus, TrustAnnotationKind,
            PartialFunctionEncoding, ExceptionValuedSemantics, AlgebraicSurface, ModelReconstruction,
            BranchSensitivity, SolverModelReconstruction,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.partial_functions import (
            DomainPredicateKind, TotalizationKind, CompositionMode, DomainPredicate,
            PartialFunctionLattice, GuardedEncoding, TotalizationStrategy,
        )
        for cls in [
            DomainPredicateKind, TotalizationKind, CompositionMode, DomainPredicate,
            PartialFunctionLattice, GuardedEncoding, TotalizationStrategy,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.reconstruction_witnesses import (
            WitnessKind, ObstructionClass, ConsistencyStatus, VariableBinding,
            ConstraintDischarge, CechPatch, ReconstructionWitness, _CechObstructionChecker,
            ReconstructionWitnessAnalyzer, ReconstructionWitnessCoordinator, WitnessResult,
        )
        for cls in [
            WitnessKind, ObstructionClass, ConsistencyStatus, VariableBinding,
            ConstraintDischarge, CechPatch, ReconstructionWitness, _CechObstructionChecker,
            ReconstructionWitnessAnalyzer, ReconstructionWitnessCoordinator, WitnessResult,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.theorems import (
            VerificationStatus, TheoremKind, Theorem, TheoremRegistry,
            CopilotTheoremAssist,
        )
        for cls in [
            VerificationStatus, TheoremKind, Theorem, TheoremRegistry,
            CopilotTheoremAssist,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.partiality_model_reconstruction.why_python_obligations_are_full_of import (
            PartialitySource, PartialDomain, PartialnessObligation, TotalExtension,
            PartialityAnalyzer,
        )
        for cls in [
            PartialitySource, PartialDomain, PartialnessObligation, TotalExtension,
            PartialityAnalyzer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # scalar_encodings
    try:
        from jugeo.encodings.scalar_encodings.algorithms import (
            IncrementalRefinementSolver, GuardSimplificationEngine, PathConditionPropagator, FailureRegressionTracker,
        )
        for cls in [
            IncrementalRefinementSolver, GuardSimplificationEngine, PathConditionPropagator, FailureRegressionTracker,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.branching_joins_and_path_sensitive import (
            PathNodeKind, BranchingJoinsPathSensitiveWitness, BranchingJoinsPathSensitiveAnalyzer, BranchingJoinsPathSensitiveCoordinator,
        )
        for cls in [
            PathNodeKind, BranchingJoinsPathSensitiveWitness, BranchingJoinsPathSensitiveAnalyzer, BranchingJoinsPathSensitiveCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.exact_failure_artifacts import (
            ExactFailureKind, ExactFailureArtifactsWitness, ExactFailureArtifactsAnalyzer, ExactFailureArtifactsCoordinator,
            TrustTier, FailureMode, FailureWitness, FailureArtifact,
            ExactFailureEncoding, ArtifactCatalog, FailurePattern, FailureRepairRecord,
        )
        for cls in [
            ExactFailureKind, ExactFailureArtifactsWitness, ExactFailureArtifactsAnalyzer, ExactFailureArtifactsCoordinator,
            TrustTier, FailureMode, FailureWitness, FailureArtifact,
            ExactFailureEncoding, ArtifactCatalog, FailurePattern, FailureRepairRecord,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.failure_artifact_encoder import (
            FailureKind, FailureArtifact, FailurePreconditionExtractor, FailureArtifactEncoder,
        )
        for cls in [
            FailureKind, FailureArtifact, FailurePreconditionExtractor, FailureArtifactEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.integration import (
            ScalarEncodingPipeline, Z3SessionBridge, SupportRegionLinker, CountermodelInterpreter,
            FragmentRouter,
        )
        for cls in [
            ScalarEncodingPipeline, Z3SessionBridge, SupportRegionLinker, CountermodelInterpreter,
            FragmentRouter,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.manifest import (
            CoverageStatus, ManifestRecord, SymbolGroup, ClaimSummary,
            PackageManifest,
        )
        for cls in [
            CoverageStatus, ManifestRecord, SymbolGroup, ClaimSummary,
            PackageManifest,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.models import (
            SortKind, FragmentHint, EncodeStatus, RefinementEncoding,
            PathCondition, GuardFormula, ArithmeticObligation, EncodingContext,
            EncodingResult,
        )
        for cls in [
            SortKind, FragmentHint, EncodeStatus, RefinementEncoding,
            PathCondition, GuardFormula, ArithmeticObligation, EncodingContext,
            EncodingResult,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.path_condition_encoder import (
            BranchNode, PathTree, JoinConditionSynthesizer, PathConditionEncoder,
        )
        for cls in [
            BranchNode, PathTree, JoinConditionSynthesizer, PathConditionEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.refinement_type_encoder import (
            RefinementSortBuilder, PredicateNormalizer, ConstraintLifter, RefinementTypeEncoder,
        )
        for cls in [
            RefinementSortBuilder, PredicateNormalizer, ConstraintLifter, RefinementTypeEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.the_encoding_layer_should_begin_fr import (
            ScalarSort, TheEncodingLayerBeginWitness, TheEncodingLayerBeginAnalyzer, TheEncodingLayerBeginCoordinator,
        )
        for cls in [
            ScalarSort, TheEncodingLayerBeginWitness, TheEncodingLayerBeginAnalyzer, TheEncodingLayerBeginCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.scalar_encodings.theorems import (
            TheoremStatus, TheoremRecord, TheoremRegistry,
        )
        for cls in [
            TheoremStatus, TheoremRecord, TheoremRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # sequence_mutation_encodings
    try:
        from jugeo.encodings.sequence_mutation_encodings.algorithms import (
            AbstractDomain, AbstractState, FramePreservationResult,
            RepairResult, WindowResult,
        )
        for cls in [
            AbstractDomain, AbstractState, FramePreservationResult,
            RepairResult, WindowResult,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.finite_map_encoder import (
            EncodedMap, FiniteMapEncoder,
        )
        for cls in [
            EncodedMap, FiniteMapEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.finite_maps_and_interface_dictiona import (
            TrustTier, Judgment, CechObstruction, FiniteMapEncoding,
            KeyValueSheaf, DictInterfaceSummary, MapUpdateObligation, MapMorphism,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, FiniteMapEncoding,
            KeyValueSheaf, DictInterfaceSummary, MapUpdateObligation, MapMorphism,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.heap_slice_encoder import (
            EncodedHeapSlice, HeapSliceEncoder,
        )
        for cls in [
            EncodedHeapSlice, HeapSliceEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.heap_slices_and_mutation_support import (
            TrustTier, Judgment, CechObstruction, HeapSliceCechObstruction,
            HeapSliceJudgment, HeapSliceGlobalSection, HeapSliceDescentObstruction, WriteBarrier,
            SliceConsistencyObligation, MutationTransition, HeapSlice, HeapSliceStats,
            CechObstruction, HeapSlice, MutationTransition, WriteBarrier,
            SliceConsistencyObligation, HeapSliceEncoder,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, HeapSliceCechObstruction,
            HeapSliceJudgment, HeapSliceGlobalSection, HeapSliceDescentObstruction, WriteBarrier,
            SliceConsistencyObligation, MutationTransition, HeapSlice, HeapSliceStats,
            CechObstruction, HeapSlice, MutationTransition, WriteBarrier,
            SliceConsistencyObligation, HeapSliceEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.integration import (
            _StubZ3Result, SequenceMutationSolverIntegration,
        )
        for cls in [
            _StubZ3Result, SequenceMutationSolverIntegration,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.manifest import (
            SubsystemManifest, ManifestValidator,
        )
        for cls in [
            SubsystemManifest, ManifestValidator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.models import (
            MutationKind, SequenceInvariantKind, SequenceEncoding, MutationSlice,
            HeapSlice, SupportAwareMutation, SequenceInvariant,
        )
        for cls in [
            MutationKind, SequenceInvariantKind, SequenceEncoding, MutationSlice,
            HeapSlice, SupportAwareMutation, SequenceInvariant,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.mutation_countermodel_encoder import (
            RepairKind, RepairSuggestion, ViolationContext, MutationCountermodelEncoder,
        )
        for cls in [
            RepairKind, RepairSuggestion, ViolationContext, MutationCountermodelEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.mutation_countermodels_as_repair_g import (
            TrustTier, AnomalyKind, MutationKind, RepairStepKind,
            CountermodelCechObstruction, CountermodelJudgment, CountermodelGlobalSection, CountermodelDescentObstruction,
            RepairDescentObstruction, MutationAnomaly, RepairGuide, SequenceRepairPlan,
            MutationCountermodel, CountermodelExtractor,
        )
        for cls in [
            TrustTier, AnomalyKind, MutationKind, RepairStepKind,
            CountermodelCechObstruction, CountermodelJudgment, CountermodelGlobalSection, CountermodelDescentObstruction,
            RepairDescentObstruction, MutationAnomaly, RepairGuide, SequenceRepairPlan,
            MutationCountermodel, CountermodelExtractor,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.mutation_countermodels_as_repair_new import (
            TrustTier, RepairKind, GuideConfidence, CountermodelInterpretation,
            RepairPriority, Judgment, CechObstruction, MutationCountermodel,
            RepairGuide, CountermodelAsGuide, RepairBundle, CountermodelDatabase,
            RepairHistory, RepairGuideRanker, MutationCountermodelsRepairGuidesCoordinator, MutationCountermodelsRepairGuidesAnalyzer,
            MutationCountermodelsRepairGuidesWitness,
        )
        for cls in [
            TrustTier, RepairKind, GuideConfidence, CountermodelInterpretation,
            RepairPriority, Judgment, CechObstruction, MutationCountermodel,
            RepairGuide, CountermodelAsGuide, RepairBundle, CountermodelDatabase,
            RepairHistory, RepairGuideRanker, MutationCountermodelsRepairGuidesCoordinator, MutationCountermodelsRepairGuidesAnalyzer,
            MutationCountermodelsRepairGuidesWitness,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.sequence_window_encoder import (
            WindowPredicate, SequenceWindowEncoder,
        )
        for cls in [
            WindowPredicate, SequenceWindowEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.sequence_windows import (
            TrustTier, WindowStatus, OverlapConditionStatus, CoverStatus,
            WindowKind, WindowCechObstruction, WindowJudgment, WindowSection,
            SequenceWindow, WindowOverlapCondition, WindowGlobalSection, WindowDescentObstruction,
            SlidingCover, WindowCoverStats, WindowGluing,
        )
        for cls in [
            TrustTier, WindowStatus, OverlapConditionStatus, CoverStatus,
            WindowKind, WindowCechObstruction, WindowJudgment, WindowSection,
            SequenceWindow, WindowOverlapCondition, WindowGlobalSection, WindowDescentObstruction,
            SlidingCover, WindowCoverStats, WindowGluing,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.structured_data_encoder import (
            EncodedList, EncodedTuple, StructuredDataEncoder,
        )
        for cls in [
            EncodedList, EncodedTuple, StructuredDataEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.structured_data_should_not_be_flat import (
            TrustTier, SequenceKind, SliceStatus, RestrictionKind,
            PresheafStatus, SequenceCechObstruction, SequenceJudgment, IndexedSlice,
            SequenceSection, SequenceGlobalSection, SequenceDescentObstruction, SequenceSheaf,
            StructuredSequenceEncoding, SequenceCover,
        )
        for cls in [
            TrustTier, SequenceKind, SliceStatus, RestrictionKind,
            PresheafStatus, SequenceCechObstruction, SequenceJudgment, IndexedSlice,
            SequenceSection, SequenceGlobalSection, SequenceDescentObstruction, SequenceSheaf,
            StructuredSequenceEncoding, SequenceCover,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.sequence_mutation_encodings.theorems import (
            _StubValidationStatus, VerifyResult, SequenceMutationTheorem, FramePreservationTheorem,
            SupportClosureTheorem, MutationCompositionTheorem, HeapSliceConsistencyTheorem, InvariantRepairTheorem,
        )
        for cls in [
            _StubValidationStatus, VerifyResult, SequenceMutationTheorem, FramePreservationTheorem,
            SupportClosureTheorem, MutationCompositionTheorem, HeapSliceConsistencyTheorem, InvariantRepairTheorem,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # structural_frontier
    try:
        from jugeo.encodings.structural_frontier.algorithms import (
            FrontierExplorer, DecidabilityBisector, CountermodelAggregator, RepairPriorityScheduler,
        )
        for cls in [
            FrontierExplorer, DecidabilityBisector, CountermodelAggregator, RepairPriorityScheduler,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.countermodel_to_repair import (
            ObstructionClassifier, RepairCandidateGenerator, RepairFrontierNavigator, CountermodelToRepair,
        )
        for cls in [
            ObstructionClassifier, RepairCandidateGenerator, RepairFrontierNavigator, CountermodelToRepair,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.countermodels_should_become_first import (
            CountermodelRole, CountermodelsBecomeFirstClassWitness, CountermodelsBecomeFirstClassAnalyzer, CountermodelsBecomeFirstClassCoordinator,
        )
        for cls in [
            CountermodelRole, CountermodelsBecomeFirstClassWitness, CountermodelsBecomeFirstClassAnalyzer, CountermodelsBecomeFirstClassCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.integration import (
            PipelinePhase, _StubDefiner, _StubTypeSystem, _StubRepairPipeline,
            StructuralFrontierPipeline, Z3FrontierBridge, FrontierSupportLinker, TypeSystemIntegrator,
            CountermodelRepairDispatcher,
        )
        for cls in [
            PipelinePhase, _StubDefiner, _StubTypeSystem, _StubRepairPipeline,
            StructuralFrontierPipeline, Z3FrontierBridge, FrontierSupportLinker, TypeSystemIntegrator,
            CountermodelRepairDispatcher,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.manifest import (
            CoverageStatus, ManifestRecord, SymbolGroup, ClaimSummary,
            PackageManifest,
        )
        for cls in [
            CoverageStatus, ManifestRecord, SymbolGroup, ClaimSummary,
            PackageManifest,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.models import (
            DecidabilityClass, FrontierSide, RepairAction, StructuralFrontier,
            SolverLiftedType, FrontierBoundary, DecidabilityMap, CountermodelObstruction,
        )
        for cls in [
            DecidabilityClass, FrontierSide, RepairAction, StructuralFrontier,
            SolverLiftedType, FrontierBoundary, DecidabilityMap, CountermodelObstruction,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.solver_lifted_type_system import (
            TypeLiftingStrategy, InvariantChecker, TypeLiftingTranslator, SolverLiftedTypeSystem,
        )
        for cls in [
            TypeLiftingStrategy, InvariantChecker, TypeLiftingTranslator, SolverLiftedTypeSystem,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.structural_frontier_definer import (
            DecidabilityOracle, FrontierBoundaryLocator, UndecidabilityWitness, StructuralFrontierDefiner,
        )
        for cls in [
            DecidabilityOracle, FrontierBoundaryLocator, UndecidabilityWitness, StructuralFrontierDefiner,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.the_code_should_make_solver_lifted import (
            LiftingStage, TheCodeMakeSolverWitness, TheCodeMakeSolverAnalyzer, TheCodeMakeSolverCoordinator,
        )
        for cls in [
            LiftingStage, TheCodeMakeSolverWitness, TheCodeMakeSolverAnalyzer, TheCodeMakeSolverCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.theorems import (
            TheoremStatus, TheoremRecord, TheoremRegistry,
        )
        for cls in [
            TheoremStatus, TheoremRecord, TheoremRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.structural_frontier.z3_should_own_the_structural_front import (
            StructuralOwnershipKind, Z3OwnStructuralFrontierWitness, Z3OwnStructuralFrontierAnalyzer, Z3OwnStructuralFrontierCoordinator,
        )
        for cls in [
            StructuralOwnershipKind, Z3OwnStructuralFrontierWitness, Z3OwnStructuralFrontierAnalyzer, Z3OwnStructuralFrontierCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # tensor_quantifier_encodings
    try:
        from jugeo.encodings.tensor_quantifier_encodings.affine_and_quasi_affine_normal_for import (
            ModularConstraint, AffineNormalForm, QuasiAffineEncoding, LinearConstraintEncoding,
            AffineObligation, AffineReduction, AffineSystem,
        )
        for cls in [
            ModularConstraint, AffineNormalForm, QuasiAffineEncoding, LinearConstraintEncoding,
            AffineObligation, AffineReduction, AffineSystem,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.affine_normal_form_encoder import (
            AffineNormalFormEncoder,
        )
        for cls in [
            AffineNormalFormEncoder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.integration import (
            TensorEncodingContext, TensorQuantifierSolverIntegration,
        )
        for cls in [
            TensorEncodingContext, TensorQuantifierSolverIntegration,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.manifest import (
            CapabilityKind, DependencyKind, SubsystemManifest, TheoryProvenance,
            CapabilityDeclaration, DependencySpec, ManifestValidationError, ManifestValidator,
        )
        for cls in [
            CapabilityKind, DependencyKind, SubsystemManifest, TheoryProvenance,
            CapabilityDeclaration, DependencySpec, ManifestValidationError, ManifestValidator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.models import (
            TensorLayout, DisciplineKind, ExtractionStrategy, ConstraintKind,
            TensorExtent, AffineLegality, QuantifierDiscipline, WitnessExtractor,
            TensorConstraint,
        )
        for cls in [
            TensorLayout, DisciplineKind, ExtractionStrategy, ConstraintKind,
            TensorExtent, AffineLegality, QuantifierDiscipline, WitnessExtractor,
            TensorConstraint,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.quantifier_discipline import (
            DisciplineReport, QuantifierInfo, QuantifierDisciplineChecker, QuantifierInstantiator,
        )
        for cls in [
            DisciplineReport, QuantifierInfo, QuantifierDisciplineChecker, QuantifierInstantiator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.theorems import (
            TensorQuantifierTheorem, AffineTransformLegalityTheorem, FarkasInfeasibilityTheorem, QuantifierEliminationTheorem,
            WitnessCompletenessTheorem, BroadcastCompatibilityTheorem,
        )
        for cls in [
            TensorQuantifierTheorem, AffineTransformLegalityTheorem, FarkasInfeasibilityTheorem, QuantifierEliminationTheorem,
            WitnessCompletenessTheorem, BroadcastCompatibilityTheorem,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.why_tensors_matter import (
            TensorMotivationExamples, TensorEncodingPrimer,
        )
        for cls in [
            TensorMotivationExamples, TensorEncodingPrimer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.why_this_family_matters_disproport import (
            Binding, BindingStructure, QuantifierMatrix, QuantifierScope,
            TensorProduct, ScopeNesting, TensorQuantifierEncoding,
        )
        for cls in [
            Binding, BindingStructure, QuantifierMatrix, QuantifierScope,
            TensorProduct, ScopeNesting, TensorQuantifierEncoding,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.witness_extraction_and_proof_burde import (
            TrustTier, Judgment, CechObstruction, ExtractionStep,
            ExtractionTrace, QuantifierWitness, SingleBurden, ProofBurden,
            WitnessValidity, BurdenDistribution, WitnessExtractor,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, ExtractionStep,
            ExtractionTrace, QuantifierWitness, SingleBurden, ProofBurden,
            WitnessValidity, BurdenDistribution, WitnessExtractor,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.tensor_quantifier_encodings.witness_extractor import (
            TensorWitness, DependenceWitness, FarkasCoefficients, TensorWitnessExtractor,
            AffineLegalityWitnessExtractor,
        )
        for cls in [
            TensorWitness, DependenceWitness, FarkasCoefficients, TensorWitnessExtractor,
            AffineLegalityWitnessExtractor,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # text_encodings
    try:
        from jugeo.encodings.text_encodings.algorithms import (
            AlgorithmStatus, AlgorithmResult, TextEncodingAlgorithm, NamingLawInference,
            DocumentationShadowExtraction, StringConstraintPropagation, TextCountermodelMinimization, NamingLawCompliance,
        )
        for cls in [
            AlgorithmStatus, AlgorithmResult, TextEncodingAlgorithm, NamingLawInference,
            DocumentationShadowExtraction, StringConstraintPropagation, TextCountermodelMinimization, NamingLawCompliance,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.countermodels_and_clausewise_expla import (
            TextCountermodel, ClauseExplanation, SemanticDivergenceWitness, TextRepairHint,
            CountermodelSearch, ClauseDecomposition,
        )
        for cls in [
            TextCountermodel, ClauseExplanation, SemanticDivergenceWitness, TextRepairHint,
            CountermodelSearch, ClauseDecomposition,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.encoding_families import (
            EncodingMember, TextEncodingFamily, SubwordEncoding, CharEncoding,
            EmbeddingEncoding, SelectionCriterion, EncodingSelector, CrossEncodingAlignment,
            BaseEncoding,
        )
        for cls in [
            EncodingMember, TextEncodingFamily, SubwordEncoding, CharEncoding,
            EmbeddingEncoding, SelectionCriterion, EncodingSelector, CrossEncodingAlignment,
            BaseEncoding,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.integration import (
            TextEncodingSession, TextEncoderRegistry, PipelineResult, TextEncodingPipeline,
        )
        for cls in [
            TextEncodingSession, TextEncoderRegistry, PipelineResult, TextEncodingPipeline,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.manifest import (
            ManifestValidator, ManifestSerializer, TextEncodingManifest,
        )
        for cls in [
            ManifestValidator, ManifestSerializer, TextEncodingManifest,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.models import (
            ConstraintKind, ConstraintStrength, StringEncoding, SymbolicText,
            NamingLaw, DocumentationShadow, TextConstraint,
        )
        for cls in [
            ConstraintKind, ConstraintStrength, StringEncoding, SymbolicText,
            NamingLaw, DocumentationShadow, TextConstraint,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.normalized_text_environment import (
            TextNormalizationStrategy, NormalizedTextEnvironment, EncodingEnvironmentBuilder,
        )
        for cls in [
            TextNormalizationStrategy, NormalizedTextEnvironment, EncodingEnvironmentBuilder,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.text_countermodels import (
            ViolationType, ConstraintViolation, TextCountermodels, StringRepairEngine,
            CountermodelInterpreter,
        )
        for cls in [
            ViolationType, ConstraintViolation, TextCountermodels, StringRepairEngine,
            CountermodelInterpreter,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.text_encoding_families import (
            StringOperationKind, TextEncodingFamilies, StringFragmentClassifier, NamingLawFamily,
            DocumentationConstraintFamily,
        )
        for cls in [
            StringOperationKind, TextEncodingFamilies, StringFragmentClassifier, NamingLawFamily,
            DocumentationConstraintFamily,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.the_normalized_text_environment import (
            NormalizationError, ObstructionError, TrustViolationError, NormStep,
            NormalizationTrace, TextNormalization, TextCanonicalForm, NormalizedTextEnv,
            NormalizationObligation, TextEquivalenceClass, JudgmentComponent,
        )
        for cls in [
            NormalizationError, ObstructionError, TrustViolationError, NormStep,
            NormalizationTrace, TextNormalization, TextCanonicalForm, NormalizedTextEnv,
            NormalizationObligation, TextEquivalenceClass, JudgmentComponent,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.theorems import (
            TheoremStatus, ProofObligation, TextEncodingTheorem, StringEncodingFaithfulness,
            NamingLawConsistency, DocumentationShadowSoundness, TextConstraintPropagationCompleteness, NormalizationInvariance,
            CountermodelMinimality, StringFragmentDecidability, TheoremSuite,
        )
        for cls in [
            TheoremStatus, ProofObligation, TextEncodingTheorem, StringEncodingFaithfulness,
            NamingLawConsistency, DocumentationShadowSoundness, TextConstraintPropagationCompleteness, NormalizationInvariance,
            CountermodelMinimality, StringFragmentDecidability, TheoremSuite,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.why_text_deserves_its_own_structur import (
            TokenizedText, TextSection, TokenObservation, TextRestriction,
            TextCovering, TextSheaf, TextEncoding,
        )
        for cls in [
            TokenizedText, TextSection, TokenObservation, TextRestriction,
            TextCovering, TextSheaf, TextEncoding,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.text_encodings.why_text_deserves_structure import (
            WhyTextDeservesStructure, StringSolverSurvey, SymbolicTextMotivation,
        )
        for cls in [
            WhyTextDeservesStructure, StringSolverSurvey, SymbolicTextMotivation,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # theorem_schemas
    try:
        from jugeo.encodings.theorem_schemas.algorithms import (
            MatchScore, SchemaMatchingAlgorithm, BindingInferenceAlgorithm, SchemaCompositionAlgorithm,
            ObligationPrioritizationAlgorithm, SchemaConsistencyChecker, TemplateExpansionAlgorithm, ProofSearchAlgorithm,
            SchemaMinimizationAlgorithm,
        )
        for cls in [
            MatchScore, SchemaMatchingAlgorithm, BindingInferenceAlgorithm, SchemaCompositionAlgorithm,
            ObligationPrioritizationAlgorithm, SchemaConsistencyChecker, TemplateExpansionAlgorithm, ProofSearchAlgorithm,
            SchemaMinimizationAlgorithm,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.theorem_schemas.integration import (
            IntegrationHealth, JudgmentSchemaAdapter, ManifestSchemaLinker, RuntimeSchemaMonitor,
            SchemaViolationReporter, TheoremSchemaIntegration, _StubTracker, _StubDispatcher,
        )
        for cls in [
            IntegrationHealth, JudgmentSchemaAdapter, ManifestSchemaLinker, RuntimeSchemaMonitor,
            SchemaViolationReporter, TheoremSchemaIntegration, _StubTracker, _StubDispatcher,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.theorem_schemas.manifest import (
            TheoremSchemasManifest, SchemaDescriptor, SchemaRegistry,
        )
        for cls in [
            TheoremSchemasManifest, SchemaDescriptor, SchemaRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.theorem_schemas.models import (
            ProofStyle, InstanceStatus, SubsystemKind, ProofAgent,
            TheoremSchema, SubsystemSchema, SchemaInstance, ProofObligation,
            SchemaValidator,
        )
        for cls in [
            ProofStyle, InstanceStatus, SubsystemKind, ProofAgent,
            TheoremSchema, SubsystemSchema, SchemaInstance, ProofObligation,
            SchemaValidator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.theorem_schemas.obligation_discharge import (
            DischargeStatus, DischargeAttempt, DischargeRecord, DischargeResult,
            DischargeError, ObligationDischarger,
        )
        for cls in [
            DischargeStatus, DischargeAttempt, DischargeRecord, DischargeResult,
            DischargeError, ObligationDischarger,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.theorem_schemas.proof_obligations import (
            ObligationStatus, DischargeRecord, ObligationTracker, ObligationQueue,
            ObligationDispatcher, ObligationAuditor,
        )
        for cls in [
            ObligationStatus, DischargeRecord, ObligationTracker, ObligationQueue,
            ObligationDispatcher, ObligationAuditor,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.theorem_schemas.schema_templates import (
            provides, DescentSchemaTemplate, TrustSchemaTemplate, EvidenceSchemaTemplate,
            FederationSchemaTemplate, InvalidationSchemaTemplate,
        )
        for cls in [
            provides, DescentSchemaTemplate, TrustSchemaTemplate, EvidenceSchemaTemplate,
            FederationSchemaTemplate, InvalidationSchemaTemplate,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.encodings.theorem_schemas.theorems import (
            SchemaSystemTheorem, ProofStatus, TheoremStatement, SchemaSystemTheoremRegistry,
            SchemaSoundnessProof, SchemaCompletenessProof, InstantiationCorrectnessProof,
        )
        for cls in [
            SchemaSystemTheorem, ProofStatus, TheoremStatement, SchemaSystemTheoremRegistry,
            SchemaSoundnessProof, SchemaCompletenessProof, InstantiationCorrectnessProof,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_generation_classes():
    """Collect all generation classes from all sub-packages."""
    registry = {}
    # __root__
    try:
        from jugeo.generation.backpressure import (
            BackpressureKind, BackpressureLevel, PressureResponseKind, BackpressureSignal,
            PressureResponse, BackpressurePolicy, ProductionRateTracker, IntegrationRateTracker,
            BackpressureDamper, _PressureEpisode, BackpressureHistory, BackpressureMonitor,
            LoadShedder, BackpressureController, BackpressureDiagnostics,
        )
        for cls in [
            BackpressureKind, BackpressureLevel, PressureResponseKind, BackpressureSignal,
            PressureResponse, BackpressurePolicy, ProductionRateTracker, IntegrationRateTracker,
            BackpressureDamper, _PressureEpisode, BackpressureHistory, BackpressureMonitor,
            LoadShedder, BackpressureController, BackpressureDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.construction import (
            ConstructionStatus, SourceChannel, ConstructionGoal, Candidate,
            ConstructionContext, ConstructionResult, CandidateNormalizer, CandidateComparator,
            CandidateSelector, ConstructionLoop, ConstructionHistory, ConstructionDiagnostics,
            ConstructionStep, ConstructionPlan,
        )
        for cls in [
            ConstructionStatus, SourceChannel, ConstructionGoal, Candidate,
            ConstructionContext, ConstructionResult, CandidateNormalizer, CandidateComparator,
            CandidateSelector, ConstructionLoop, ConstructionHistory, ConstructionDiagnostics,
            ConstructionStep, ConstructionPlan,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.goals import (
            GoalPriority, GoalStatus, GoalEventKind, GenerationGoal,
            OverlapGoal, ConstructionGoal, GoalDecomposer, GoalTree,
            GoalDependencyGraph, GoalScheduler, GoalTracker, GoalPrioritizer,
            GoalEvent, GoalHistory, GoalSerializer, GoalDiagnostics,
        )
        for cls in [
            GoalPriority, GoalStatus, GoalEventKind, GenerationGoal,
            OverlapGoal, ConstructionGoal, GoalDecomposer, GoalTree,
            GoalDependencyGraph, GoalScheduler, GoalTracker, GoalPrioritizer,
            GoalEvent, GoalHistory, GoalSerializer, GoalDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.integration import (
            IntegrationStatus, IntegrationStrategy, IntegrationPlan, IntegrationResult,
            RegressionChecker, SemanticClosureChecker, ReplayEngine, IntegrationScheduler,
            GluingOrchestrator, IntegrationEngine, IntegrationHistory, IntegrationDiagnostics,
        )
        for cls in [
            IntegrationStatus, IntegrationStrategy, IntegrationPlan, IntegrationResult,
            RegressionChecker, SemanticClosureChecker, ReplayEngine, IntegrationScheduler,
            GluingOrchestrator, IntegrationEngine, IntegrationHistory, IntegrationDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.treaties import (
            TreatyStatus, QuantifierKind, ChallengeVerdict, PatternKind,
            InvalidationSeverity, TreatyLaw, TreatyGuard, Treaty,
            _MinedPattern, TreatySynthesizer, TreatyManager, TreatyValidator,
            TreatyChallenger, TreatyPatternMiner, TreatyInvalidationMonitor, TreatyHistory,
            TreatySerializer, TreatyClause, OverlapTreaty,
        )
        for cls in [
            TreatyStatus, QuantifierKind, ChallengeVerdict, PatternKind,
            InvalidationSeverity, TreatyLaw, TreatyGuard, Treaty,
            _MinedPattern, TreatySynthesizer, TreatyManager, TreatyValidator,
            TreatyChallenger, TreatyPatternMiner, TreatyInvalidationMonitor, TreatyHistory,
            TreatySerializer, TreatyClause, OverlapTreaty,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # cover_design
    try:
        from jugeo.generation.cover_design.algorithms import (
            DependencyGraph, OverlapGraph, ScheduleResult,
        )
        for cls in [
            DependencyGraph, OverlapGraph, ScheduleResult,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.budget_allocation import (
            AllocationPolicy, AllocationRecord, BudgetFlowEdge, BudgetFlowGraph,
            BudgetAllocationAnalyzer, BudgetAllocationWitness, BudgetAllocationCoordinator,
        )
        for cls in [
            AllocationPolicy, AllocationRecord, BudgetFlowEdge, BudgetFlowGraph,
            BudgetAllocationAnalyzer, BudgetAllocationWitness, BudgetAllocationCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.completion_criteria import (
            CompletionCondition, CompletionRecord, CompletionDecision, CriticalPatchSet,
            CompletionCriteriaAnalyzer, CompletionCriteriaCoordinator, CompletionCriteriaWitness,
        )
        for cls in [
            CompletionCondition, CompletionRecord, CompletionDecision, CriticalPatchSet,
            CompletionCriteriaAnalyzer, CompletionCriteriaCoordinator, CompletionCriteriaWitness,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.cover_design_principles import (
            _PrincipleKind, CoverageGap, CoverPrinciple, PrincipleViolation,
            _FallbackCoverDesignPlan, CoverDesignPrinciplesAnalyzer, CoverDesignPrinciplesWitness, CoverDesignPrinciplesCoordinator,
        )
        for cls in [
            _PrincipleKind, CoverageGap, CoverPrinciple, PrincipleViolation,
            _FallbackCoverDesignPlan, CoverDesignPrinciplesAnalyzer, CoverDesignPrinciplesWitness, CoverDesignPrinciplesCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.dependency_ordering import (
            CyclicDependencyError, DependencyEdge, TopologicalOrder, CriticalPath,
            DependencyDAG, DependencyOrderingCoordinator, DependencyOrderingAnalyzer, DependencyOrderingWitness,
            InterfaceDependency,
        )
        for cls in [
            CyclicDependencyError, DependencyEdge, TopologicalOrder, CriticalPath,
            DependencyDAG, DependencyOrderingCoordinator, DependencyOrderingAnalyzer, DependencyOrderingWitness,
            InterfaceDependency,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.initial_cover_synthesis_obligation import (
            TrustTier, Judgment, CechObstruction, CoverCandidate,
            CoverSynthesisObligation, CoverProposal, SynthesisStrategy, InitialCoverSynthesis,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, CoverCandidate,
            CoverSynthesisObligation, CoverProposal, SynthesisStrategy, InitialCoverSynthesis,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.integration import (
            DesignIntegrationConfig, DesignPipelineState, DesignRecord, CopilotCoverDesignAdapter,
            CoverDesignPipelineAdapter, CoverDesignIntegration, _StubEngine,
        )
        for cls in [
            DesignIntegrationConfig, DesignPipelineState, DesignRecord, CopilotCoverDesignAdapter,
            CoverDesignPipelineAdapter, CoverDesignIntegration, _StubEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.manifest import (
            ManifestError, MissingSymbolError, InvalidVersionError, MissingFileError,
            PackageManifest, FileManifest, ManifestRegistry, ManifestDiagnostics,
        )
        for cls in [
            ManifestError, MissingSymbolError, InvalidVersionError, MissingFileError,
            PackageManifest, FileManifest, ManifestRegistry, ManifestDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.models import (
            TrustTier, PatchStatus, CoverDesignPhase, OverlapCompatibility,
            CoverDesignError, CechConditionViolation, BudgetExhaustedError, PatchSelectionError,
            Budget, PatchDescriptor, OverlapRecord, CoverDesignPlan,
            QualityMetric, CoverDesignResult,
        )
        for cls in [
            TrustTier, PatchStatus, CoverDesignPhase, OverlapCompatibility,
            CoverDesignError, CechConditionViolation, BudgetExhaustedError, PatchSelectionError,
            Budget, PatchDescriptor, OverlapRecord, CoverDesignPlan,
            QualityMetric, CoverDesignResult,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.module_boundaries_overlap_quality import (
            TrustTier, Judgment, CechObstruction, BoundaryType,
            ModuleBoundary, OverlapQuality, BoundaryAnalysis, CoverOverlapMetric,
            BoundaryOptimizer,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, BoundaryType,
            ModuleBoundary, OverlapQuality, BoundaryAnalysis, CoverOverlapMetric,
            BoundaryOptimizer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.parallelism_strategy import (
            ParallelismPolicy, DependencyEdge, ParallelismConstraint, ParallelismGroup,
            GenerationWave, ParallelismStrategyAnalyzer, ParallelismStrategyWitness, ParallelismStrategyCoordinator,
        )
        for cls in [
            ParallelismPolicy, DependencyEdge, ParallelismConstraint, ParallelismGroup,
            GenerationWave, ParallelismStrategyAnalyzer, ParallelismStrategyWitness, ParallelismStrategyCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.patch_selection import (
            SelectionPolicy, PatchCandidate, SelectionRanking, _SelectionResult,
            PatchSelectionAnalyzer, PatchSelectionWitness, PatchSelectionCoordinator,
        )
        for cls in [
            SelectionPolicy, PatchCandidate, SelectionRanking, _SelectionResult,
            PatchSelectionAnalyzer, PatchSelectionWitness, PatchSelectionCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.quality_metrics import (
            QualityLevel, MetricDefinition, MetricThreshold, MetricResult,
            QualityReport, QualityMetricsCoordinator, QualityMetricsAnalyzer, QualityMetricsWitness,
            CechCondition,
        )
        for cls in [
            QualityLevel, MetricDefinition, MetricThreshold, MetricResult,
            QualityReport, QualityMetricsCoordinator, QualityMetricsAnalyzer, QualityMetricsWitness,
            CechCondition,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.s08_integration import (
            _FallbackCriticalPatchSet, PipelineStage, IntegrationConfig, IntegrationResult,
            CopilotCoverDesignParticipant, CoverDesignIntegrationAnalyzer, CoverDesignIntegrationWitness, CoverDesignIntegrationCoordinator,
        )
        for cls in [
            _FallbackCriticalPatchSet, PipelineStage, IntegrationConfig, IntegrationResult,
            CopilotCoverDesignParticipant, CoverDesignIntegrationAnalyzer, CoverDesignIntegrationWitness, CoverDesignIntegrationCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.semantic_decomposition_criteria_co import (
            TrustTier, Judgment, CechObstruction, SemanticDecompositionCriteria,
            CoverQualityScore, SemanticBoundary, DecompositionPolicy, CriteriaEvaluator,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, SemanticDecompositionCriteria,
            CoverQualityScore, SemanticBoundary, DecompositionPolicy, CriteriaEvaluator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.cover_design.theorems import (
            TheoremResult, TheoremSuite,
        )
        for cls in [
            TheoremResult, TheoremSuite,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # hypercover_treaties
    try:
        from jugeo.generation.hypercover_treaties.algorithms import (
            TrustTier, Judgment, CechObstruction, ResolutionStrategy,
            TreatySynthesizer, ConflictDetector, TreatyNegotiator, TreatyAlgorithms,
            TreatySynthesizer, ConflictDetector, ResolutionStrategy, TreatyNegotiator,
            TreatyGraph, SynthesisEngine, CechConflictClass, NegotiationProtocol,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, ResolutionStrategy,
            TreatySynthesizer, ConflictDetector, TreatyNegotiator, TreatyAlgorithms,
            TreatySynthesizer, ConflictDetector, ResolutionStrategy, TreatyNegotiator,
            TreatyGraph, SynthesisEngine, CechConflictClass, NegotiationProtocol,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.algorithms_new import (
            TrustTier, Judgment, CechObstruction, ResolutionStrategy,
            TreatySynthesizer, ConflictDetector, TreatyNegotiator, TreatyAlgorithms,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, ResolutionStrategy,
            TreatySynthesizer, ConflictDetector, TreatyNegotiator, TreatyAlgorithms,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.hypercover_synthesis import (
            HypercoverConditionChecker, GoalStructureParser, HypercoverSynthesizer, SynthesisDriver,
        )
        for cls in [
            HypercoverConditionChecker, GoalStructureParser, HypercoverSynthesizer, SynthesisDriver,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.implementation_consequences import (
            TrustTier, Judgment, CechObstruction, BoundaryGuarantee,
            TreatyImplementationConsequence, TreatyViolation, ConsequenceChecker, GuaranteeMatrix,
            BoundaryInspector, ViolationAggregator, ConsequencePropagator, TreatyAudit,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, BoundaryGuarantee,
            TreatyImplementationConsequence, TreatyViolation, ConsequenceChecker, GuaranteeMatrix,
            BoundaryInspector, ViolationAggregator, ConsequencePropagator, TreatyAudit,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.integration import (
            TrustTier, Judgment, CechObstruction, TreatyRegistry,
            CoverDesignBridge, OrchestratorTreatyBridge, TreatyIntegration, IntegrationLayer,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, TreatyRegistry,
            CoverDesignBridge, OrchestratorTreatyBridge, TreatyIntegration, IntegrationLayer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.manifest import (
            ExportKind, DependencyKind, ModuleDescriptor, ExportRegistry,
            DependencyTracker, HypercoverTreatiesManifest,
        )
        for cls in [
            ExportKind, DependencyKind, ModuleDescriptor, ExportRegistry,
            DependencyTracker, HypercoverTreatiesManifest,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.models import (
            SynthesisPhase, LawStability, CandidateSource, TreatyRole,
            OutcomeKind, HypercoverSynthesisRecord, TreatyCandidate, OverlapLaw,
            DependentTreaty, SynthesisOutcome, SynthesisConfig, OverlapLawIndex,
        )
        for cls in [
            SynthesisPhase, LawStability, CandidateSource, TreatyRole,
            OutcomeKind, HypercoverSynthesisRecord, TreatyCandidate, OverlapLaw,
            DependentTreaty, SynthesisOutcome, SynthesisConfig, OverlapLawIndex,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.overlap_law_discovery_friction_min import (
            TrustTier, Judgment, _LegacyJudgment, CechObstruction,
            CechH1Cochain, LawDatabase, OverlapLawDiscovery, TreatyFrictionMetric,
            HypercoverTreaty, LawDiscoveryEngine, FrictionMinimizer,
        )
        for cls in [
            TrustTier, Judgment, _LegacyJudgment, CechObstruction,
            CechH1Cochain, LawDatabase, OverlapLawDiscovery, TreatyFrictionMetric,
            HypercoverTreaty, LawDiscoveryEngine, FrictionMinimizer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.overlap_laws import (
            LawCandidate, LawVerifier, OverlapLawLibrary, OverlapLawDiscovery,
        )
        for cls in [
            LawCandidate, LawVerifier, OverlapLawLibrary, OverlapLawDiscovery,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.theorems import (
            TheoremCondition, TheoremResult, DescentSuccessTheorem, TreatyConsistencyTheorem,
            HypercoverExistenceTheorem, OverlapLawCompletenessTheorem, TheoremProver, ProofCertificate,
        )
        for cls in [
            TheoremCondition, TheoremResult, DescentSuccessTheorem, TreatyConsistencyTheorem,
            HypercoverExistenceTheorem, OverlapLawCompletenessTheorem, TheoremProver, ProofCertificate,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.treaty_formation import (
            FormationReport, FormationValidator, DependencyResolver, TreatyNegotiator,
            TreatyFormationProcess,
        )
        for cls in [
            FormationReport, FormationValidator, DependencyResolver, TreatyNegotiator,
            TreatyFormationProcess,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.hypercover_treaties.treaty_merging import (
            MergeStrategy, MergePhase, TreatyMergeWitness, TreatyMergeConflict,
            TreatyMergeRecord, TreatyMergeAnalyzer, TreatyMergeCoordinator,
        )
        for cls in [
            MergeStrategy, MergePhase, TreatyMergeWitness, TreatyMergeConflict,
            TreatyMergeRecord, TreatyMergeAnalyzer, TreatyMergeCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # inhabitant_fleets
    try:
        from jugeo.generation.inhabitant_fleets.ai_fleets import (
            FleetMember, FleetCoordinator, InhabitantFleet, FleetRegistry,
            BidAggregator,
        )
        for cls in [
            FleetMember, FleetCoordinator, InhabitantFleet, FleetRegistry,
            BidAggregator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.algorithms import (
            FleetAllocationAlgorithm, GreedyFleetAllocation, OptimalFleetAllocation, HeuristicFleetAllocation,
            BackpressurePropagation, InhabitantRanking, SemanticDistanceComputer, FleetConvergenceChecker,
        )
        for cls in [
            FleetAllocationAlgorithm, GreedyFleetAllocation, OptimalFleetAllocation, HeuristicFleetAllocation,
            BackpressurePropagation, InhabitantRanking, SemanticDistanceComputer, FleetConvergenceChecker,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.fleet_merging import (
            MergePolicy, FleetPhase, FleetMergeWitness, MergeConflict,
            FleetMergeRecord, FleetMergeAnalyzer, FleetMergeCoordinator,
        )
        for cls in [
            MergePolicy, FleetPhase, FleetMergeWitness, MergeConflict,
            FleetMergeRecord, FleetMergeAnalyzer, FleetMergeCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.fleet_search_over_admissible_inhab import (
            TrustTier, Judgment, CechObstruction, SearchStrategy,
            AdmissibilityChecker, FleetMemory, ObstructionMonitor, ResultAggregator,
            ParallelSearchSimulator, AdmissibleInhabitant, FleetMember, SearchFleet,
            FleetSearch, FleetCoordinator,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, SearchStrategy,
            AdmissibilityChecker, FleetMemory, ObstructionMonitor, ResultAggregator,
            ParallelSearchSimulator, AdmissibleInhabitant, FleetMember, SearchFleet,
            FleetSearch, FleetCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.implementation_consequences import (
            TrustTier, Judgment, CechObstruction, FleetImplementationConsequence,
            FleetPolicy, FleetConstraint, FleetAudit, ConsequenceManager,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, FleetImplementationConsequence,
            FleetPolicy, FleetConstraint, FleetAudit, ConsequenceManager,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.integration import (
            DescentAdaptor, GoalAdaptor, FrontierIntegrator, ConstructionAdaptor,
            InhabitantFleetPipeline,
        )
        for cls in [
            DescentAdaptor, GoalAdaptor, FrontierIntegrator, ConstructionAdaptor,
            InhabitantFleetPipeline,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis import (
            InhabitantSpace, SynthesisContext, InhabitantValidator, LocalInhabitantSynthesizer,
        )
        for cls in [
            InhabitantSpace, SynthesisContext, InhabitantValidator, LocalInhabitantSynthesizer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.local_inhabitant_synthesis_goal_re import (
            TypeEnvironment, CoverElementContext, SynthesisTree, ObstructionTracker,
            InhabitantEvaluator, GoalDecomposer, SynthesisGoal, InhabitantCandidate,
            SynthesisPolicy, LocalInhabitantSynthesis,
        )
        for cls in [
            TypeEnvironment, CoverElementContext, SynthesisTree, ObstructionTracker,
            InhabitantEvaluator, GoalDecomposer, SynthesisGoal, InhabitantCandidate,
            SynthesisPolicy, LocalInhabitantSynthesis,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.manifest import (
            ModuleDescriptor, ExportRegistry, DependencyTracker, InhabitantFleetsManifest,
        )
        for cls in [
            ModuleDescriptor, ExportRegistry, DependencyTracker, InhabitantFleetsManifest,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.models import (
            ProposalStatus, SeverityLevel, MoveType, InhabitantProposal,
            FleetBid, BackpressureSignal, SemanticMove, NormalizedProposal,
        )
        for cls in [
            ProposalStatus, SeverityLevel, MoveType, InhabitantProposal,
            FleetBid, BackpressureSignal, SemanticMove, NormalizedProposal,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.semantic_backpressure import (
            InstabilityMetric, BackpressureMonitor, BackpressureController, BackpressureResolver,
            CascadeDetector,
        )
        for cls in [
            InstabilityMetric, BackpressureMonitor, BackpressureController, BackpressureResolver,
            CascadeDetector,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.semantic_backpressure_congestion_s import (
            TrustTier, Judgment, CechObstruction, SemanticObligation,
            SemanticBackpressure, CongestionSignal, BackpressurePolicy, FleetThrottler,
            CongestionAnalyzer, ObligationQueue, CongestionDetector, ThrottleController,
            BackpressureGraph, FlowController,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, SemanticObligation,
            SemanticBackpressure, CongestionSignal, BackpressurePolicy, FleetThrottler,
            CongestionAnalyzer, ObligationQueue, CongestionDetector, ThrottleController,
            BackpressureGraph, FlowController,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.inhabitant_fleets.theorems import (
            TheoremVerifier, FleetConvergenceTheorem, BackpressureBoundednessTheorem, SemanticMoveCompletenessTheorem,
            InhabitantExistenceTheorem,
        )
        for cls in [
            TheoremVerifier, FleetConvergenceTheorem, BackpressureBoundednessTheorem, SemanticMoveCompletenessTheorem,
            InhabitantExistenceTheorem,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # local_construction
    try:
        from jugeo.generation.local_construction.coordinated_elaboration import (
            LocalConstructionError, InterfaceBreachError, BudgetExhaustedError, ConvergenceFailureError,
            ElaborationSchedule, CoordinationConflict, CoordinatedElaborationEngine,
        )
        for cls in [
            LocalConstructionError, InterfaceBreachError, BudgetExhaustedError, ConvergenceFailureError,
            ElaborationSchedule, CoordinationConflict, CoordinatedElaborationEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.coordination_with_semantic_account import (
            TrustTier, Judgment, CechObstruction, SemanticAccounting,
            ResourceTracker, ObligationLedger, CompletionRecord, AccountingEngine,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, SemanticAccounting,
            ResourceTracker, ObligationLedger, CompletionRecord, AccountingEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.copilot_in_construction import (
            CopilotProposal, CopilotNegotiationRecord, CopilotStrategyState, StrategyAdaptation,
            CopilotConstructionParticipant,
        )
        for cls in [
            CopilotProposal, CopilotNegotiationRecord, CopilotStrategyState, StrategyAdaptation,
            CopilotConstructionParticipant,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.integration import (
            IntegrationConfig, PipelineState, ConstructionRecord, LocalConstructionIntegration,
        )
        for cls in [
            IntegrationConfig, PipelineState, ConstructionRecord, LocalConstructionIntegration,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.interface_discipline import (
            InterfaceBreach, NegotiationRecord, InterfaceDisciplineEnforcer,
        )
        for cls in [
            InterfaceBreach, NegotiationRecord, InterfaceDisciplineEnforcer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.interface_discipline_overlap_objec import (
            TrustTier, Judgment, CechObstruction, InterfaceDiscipline,
            OverlapObjective, InterfaceObligation, GluingCondition, DisciplineChecker,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, InterfaceDiscipline,
            OverlapObjective, InterfaceObligation, GluingCondition, DisciplineChecker,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.local_construction_loop import (
            LocalConstructionLoopEngine,
        )
        for cls in [
            LocalConstructionLoopEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.local_construction_loops_proposal import (
            TrustTier, Judgment, CechObstruction, LocalConstructionLoop,
            ConstructionProposal, LocalVerification, RefinementStep, LoopController,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, LocalConstructionLoop,
            ConstructionProposal, LocalVerification, RefinementStep, LoopController,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.manifest import (
            ManifestError, MissingSymbolError, InvalidVersionError, MissingFileError,
            PackageManifest, FileManifest, ManifestRegistry, ManifestDiagnostics,
        )
        for cls in [
            ManifestError, MissingSymbolError, InvalidVersionError, MissingFileError,
            PackageManifest, FileManifest, ManifestRegistry, ManifestDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.models import (
            LoopStatus, StrictnessLevel, GenerationMethod, LocalConstructionError,
            InterfaceBreachError, BudgetExhaustedError, ConvergenceFailureError, LocalConstructionLoop,
            InterfaceDiscipline, CoordinatedElaboration, CandidateSet,
        )
        for cls in [
            LoopStatus, StrictnessLevel, GenerationMethod, LocalConstructionError,
            InterfaceBreachError, BudgetExhaustedError, ConvergenceFailureError, LocalConstructionLoop,
            InterfaceDiscipline, CoordinatedElaboration, CandidateSet,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.local_construction.theorems import (
            TheoremResult, TheoremSuite,
        )
        for cls in [
            TheoremResult, TheoremSuite,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # replay_gluing
    try:
        from jugeo.generation.replay_gluing.algorithms import (
            ReplayAlgorithm, FullReplayAlgorithm, IncrementalReplayAlgorithm, LazyReplayAlgorithm,
            ChangeImpactAnalyzer, GluingMerger, ReplayTask, ReplayScheduler,
            AlgorithmRegistry,
        )
        for cls in [
            ReplayAlgorithm, FullReplayAlgorithm, IncrementalReplayAlgorithm, LazyReplayAlgorithm,
            ChangeImpactAnalyzer, GluingMerger, ReplayTask, ReplayScheduler,
            AlgorithmRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.convergence_verification import (
            ConvergenceStatus, ConvergenceMetric, FixedPointChecker, ConvergenceCertificate,
            ConvergenceReport, ConvergenceVerifier,
        )
        for cls in [
            ConvergenceStatus, ConvergenceMetric, FixedPointChecker, ConvergenceCertificate,
            ConvergenceReport, ConvergenceVerifier,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.cumulative_generation_memory_assem import (
            TrustTier, TrustAlgebra, CechCohomology, GenerationEpisode,
            CumulativeGenerationMemory, MemoryAssembly, MemoryCatalog, EpisodeStore,
            MemoryCompressor, EpisodeRetriever, CumulativeIndex, MemoryConsolidator,
            GenerationStatistics, MemoryGarbageCollector,
        )
        for cls in [
            TrustTier, TrustAlgebra, CechCohomology, GenerationEpisode,
            CumulativeGenerationMemory, MemoryAssembly, MemoryCatalog, EpisodeStore,
            MemoryCompressor, EpisodeRetriever, CumulativeIndex, MemoryConsolidator,
            GenerationStatistics, MemoryGarbageCollector,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.final_assembly import (
            AssemblyStrategy, AssemblyPhase, AssemblyWitness, AssemblyConflict,
            FinalAssemblyRecord, FinalAssemblyAnalyzer, FinalAssemblyCoordinator,
        )
        for cls in [
            AssemblyStrategy, AssemblyPhase, AssemblyWitness, AssemblyConflict,
            FinalAssemblyRecord, FinalAssemblyAnalyzer, FinalAssemblyCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.global_gluing_under_replay_integra import (
            TrustTier, Judgment, CechObstruction, ReplayGluing,
            GluingRecord, GlobalGluingUnderReplay, ReplayIntegration, GluingEngine,
            ReplayMove, ReplayIntegrationRecord, CompatibilityStatus, OverlapCompatibility,
            CechCocycleFragment, GlobalGluingResult, GlobalGluingUnderReplay, ReplayGluing,
            GluingRecord, ReplayIntegration, SheafGluingEngine, ReplayBuffer,
            GluingConsistencyChecker, CocycleConditionVerifier, LocalSectionRegistry, OverlapCompatibilityMatrix,
            ReplayFidelityMeasure,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, ReplayGluing,
            GluingRecord, GlobalGluingUnderReplay, ReplayIntegration, GluingEngine,
            ReplayMove, ReplayIntegrationRecord, CompatibilityStatus, OverlapCompatibility,
            CechCocycleFragment, GlobalGluingResult, GlobalGluingUnderReplay, ReplayGluing,
            GluingRecord, ReplayIntegration, SheafGluingEngine, ReplayBuffer,
            GluingConsistencyChecker, CocycleConditionVerifier, LocalSectionRegistry, OverlapCompatibilityMatrix,
            ReplayFidelityMeasure,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.implementation_path_for_cumulative import (
            TrustTier, CumulativeMemoryImplementation, ImplementationStep, MemoryBackend,
            MemoryIndexer, ImplementationRoadmap, BackendFactory, IndexBuilder,
            QueryPlan, QueryPlanner, MigrationCheckpoint, MemoryMigrationTool,
            CapacityPlanner, ValidationReport, ImplementationValidator,
        )
        for cls in [
            TrustTier, CumulativeMemoryImplementation, ImplementationStep, MemoryBackend,
            MemoryIndexer, ImplementationRoadmap, BackendFactory, IndexBuilder,
            QueryPlan, QueryPlanner, MigrationCheckpoint, MemoryMigrationTool,
            CapacityPlanner, ValidationReport, ImplementationValidator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.incremental_replay import (
            ReplayError, OverlapIncompatibilityError, ReplayStep, ReconciliationResult,
            GluingSnapshot, ReplayCache, OverlapReconciler, IncrementalReplayer,
        )
        for cls in [
            ReplayError, OverlapIncompatibilityError, ReplayStep, ReconciliationResult,
            GluingSnapshot, ReplayCache, OverlapReconciler, IncrementalReplayer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.integration import (
            PipelineResult, ReplayGluingPipeline, DescentAdaptor, GoalAdaptor,
            FrontierIntegrator,
        )
        for cls in [
            PipelineResult, ReplayGluingPipeline, DescentAdaptor, GoalAdaptor,
            FrontierIntegrator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.manifest import (
            DependencyKind, ExportKind, ModuleDescriptor, ExportDescriptor,
            DependencyRecord, ExportRegistry, DependencyTracker, ReplayGluingManifest,
        )
        for cls in [
            DependencyKind, ExportKind, ModuleDescriptor, ExportDescriptor,
            DependencyRecord, ExportRegistry, DependencyTracker, ReplayGluingManifest,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.models import (
            PatchStatus, ReplayStrategy, ReplayPhase, ReplayGluingPlan,
            GluingUnderReplay, IncrementalGluing, ConvergenceRecord, ReplayMetrics,
            GluingDiff,
        )
        for cls in [
            PatchStatus, ReplayStrategy, ReplayPhase, ReplayGluingPlan,
            GluingUnderReplay, IncrementalGluing, ConvergenceRecord, ReplayMetrics,
            GluingDiff,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.replay_planning import (
            ChangeSet, DependencyAnalyzer, ReplayPlanner, ReplayCostEstimator,
            ReplayPlanWitness, ReplayPlanAnalyzer, ReplayPlanCoordinator,
        )
        for cls in [
            ChangeSet, DependencyAnalyzer, ReplayPlanner, ReplayCostEstimator,
            ReplayPlanWitness, ReplayPlanAnalyzer, ReplayPlanCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.theorem_and_falsification_burden_f import (
            TrustTier, Judgment, CechObstruction, ReplayGluingTheorem,
            GluingCorrectnessProof, FalsificationBurden, GluingInvariant, TheoremChecker,
            CohomologyObstruction, TheoremDatabase, ProofChecker, CounterexampleGenerator,
            InvariantMonitor, FalsificationOracle, ProofObligationTracker,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, ReplayGluingTheorem,
            GluingCorrectnessProof, FalsificationBurden, GluingInvariant, TheoremChecker,
            CohomologyObstruction, TheoremDatabase, ProofChecker, CounterexampleGenerator,
            InvariantMonitor, FalsificationOracle, ProofObligationTracker,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.replay_gluing.theorems import (
            TheoremStatus, TheoremResult, IncrementalCorrectnessTheorem, ConvergenceGuaranteeTheorem,
            ReplaySoundnessTheorem, MonotonicityClaim, TheoremSuite,
        )
        for cls in [
            TheoremStatus, TheoremResult, IncrementalCorrectnessTheorem, ConvergenceGuaranteeTheorem,
            ReplaySoundnessTheorem, MonotonicityClaim, TheoremSuite,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # semantic_closure
    try:
        from jugeo.generation.semantic_closure.algorithms import (
            AlgorithmType, ClosureAlgorithm, ClosureIteration, TransitiveClosure,
            WarshallResult, FixedPointIterator, KleeneClosure, JudgmentSheafClosure,
            ClosureAlgorithmRegistry,
        )
        for cls in [
            AlgorithmType, ClosureAlgorithm, ClosureIteration, TransitiveClosure,
            WarshallResult, FixedPointIterator, KleeneClosure, JudgmentSheafClosure,
            ClosureAlgorithmRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.closure_checking import (
            ClosureChecker, ObligationRegistry, EvidenceAggregator, ClosureReport,
        )
        for cls in [
            ClosureChecker, ObligationRegistry, EvidenceAggregator, ClosureReport,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.global_section_assembly import (
            AssemblyStatus, CompatibilityReport, ObstructionRecord, GlobalSection,
            AssemblyResult, GlobalSectionWitness, GlobalSectionAnalyzer, GlobalSectionCoordinator,
        )
        for cls in [
            AssemblyStatus, CompatibilityReport, ObstructionRecord, GlobalSection,
            AssemblyResult, GlobalSectionWitness, GlobalSectionAnalyzer, GlobalSectionCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.integration import (
            DescentAdaptor, GoalAdaptor, FrontierIntegrator, ConstructionAdaptor,
            IntegrationState, SemanticClosurePipeline,
        )
        for cls in [
            DescentAdaptor, GoalAdaptor, FrontierIntegrator, ConstructionAdaptor,
            IntegrationState, SemanticClosurePipeline,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.integration_closure import (
            IntegrationState, ClosureStrategy, GreedyClosureStrategy, PriorityClosureStrategy,
            ConservativeClosureStrategy, ClosureCertificate, IntegrationClosureEngine,
        )
        for cls in [
            IntegrationState, ClosureStrategy, GreedyClosureStrategy, PriorityClosureStrategy,
            ConservativeClosureStrategy, ClosureCertificate, IntegrationClosureEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.manifest import (
            SemanticClosureManifest, ClosureCapability, ExportedSymbol, ManifestEntry,
        )
        for cls in [
            SemanticClosureManifest, ClosureCapability, ExportedSymbol, ManifestEntry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.models import (
            ClosureResult, CheckType, GapSeverity, RegressionStatus,
            RegressionKind, ClosureCheck, ClosureGap, RegressionTest,
            RegressionRecord, SemanticClosure, SemanticClosure,
        )
        for cls in [
            ClosureResult, CheckType, GapSeverity, RegressionStatus,
            RegressionKind, ClosureCheck, ClosureGap, RegressionTest,
            RegressionRecord, SemanticClosure, SemanticClosure,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.regression_as_semantic_memory_reta import (
            TrustTier, Judgment, CechObstruction, SemanticRegression,
            MemoryRetentionPolicy, ClosureRegression, RegressionOracle, RegressionEngine,
        )
        for cls in [
            TrustTier, Judgment, CechObstruction, SemanticRegression,
            MemoryRetentionPolicy, ClosureRegression, RegressionOracle, RegressionEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.regression_testing import (
            RegressionTestSuite, BaselineManager, RegressionDetector, RegressionRepairer,
        )
        for cls in [
            RegressionTestSuite, BaselineManager, RegressionDetector, RegressionRepairer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.residual_gap_analysis import (
            GapClassification, ResidualGapReport, ResidualGapWitness, ResidualGapAnalyzer,
            ResidualGapCoordinator,
        )
        for cls in [
            GapClassification, ResidualGapReport, ResidualGapWitness, ResidualGapAnalyzer,
            ResidualGapCoordinator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.semantic_closure_completion_criter import (
            CompletionStatus, WitnessType, CriterionKind, ClosureCompletionCriterion,
            ClosureMetric, CriteriaEvaluation, CompletionCheck, ClosureWitness,
            CompletionReport, CriteriaRegistry, WitnessValidator, CompletionEngine,
        )
        for cls in [
            CompletionStatus, WitnessType, CriterionKind, ClosureCompletionCriterion,
            ClosureMetric, CriteriaEvaluation, CompletionCheck, ClosureWitness,
            CompletionReport, CriteriaRegistry, WitnessValidator, CompletionEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.semantic_closure.theorems import (
            TheoremStatus, ComplexityClass, ClosureTheorem, ExistenceProof,
            UniquenessArgument, ComplexityBound, TheoremResult, TheoremSuiteResult,
            BipartiteGraph, TheoremSuite,
        )
        for cls in [
            TheoremStatus, ComplexityClass, ClosureTheorem, ExistenceProof,
            UniquenessArgument, ComplexityBound, TheoremResult, TheoremSuiteResult,
            BipartiteGraph, TheoremSuite,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # state_space
    try:
        from jugeo.generation.state_space.algorithms import (
            TrustTier, SearchNode, SearchResult, PriorityQueue,
            SemanticHeuristic, StateSpaceSearch,
        )
        for cls in [
            TrustTier, SearchNode, SearchResult, PriorityQueue,
            SemanticHeuristic, StateSpaceSearch,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.backtracking import (
            BacktrackingStrategy, ChoicePoint, BacktrackResult, BacktrackStats,
            ConflictCause, LearningClause, BacktrackingCoordinator, BacktrackingAnalyzer,
            BacktrackingWitness,
        )
        for cls in [
            BacktrackingStrategy, ChoicePoint, BacktrackResult, BacktrackStats,
            ConflictCause, LearningClause, BacktrackingCoordinator, BacktrackingAnalyzer,
            BacktrackingWitness,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.convergence_detection import (
            ConvergenceStatus, ConvergenceReport, ConvergenceHistory, ConvergenceCriterion,
            ThresholdCriterion, FixedPointCriterion, GoalStateCriterion, MaxRoundsCriterion,
            ConvergenceCoordinator, ConvergenceAnalyzer, ConvergenceWitness,
        )
        for cls in [
            ConvergenceStatus, ConvergenceReport, ConvergenceHistory, ConvergenceCriterion,
            ThresholdCriterion, FixedPointCriterion, GoalStateCriterion, MaxRoundsCriterion,
            ConvergenceCoordinator, ConvergenceAnalyzer, ConvergenceWitness,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.frontier_management import (
            FrontierType, FrontierStats, FrontierWitness, BoundedPriorityFrontier,
            BeamFrontier, FrontierCoordinator, FrontierAnalyzer,
        )
        for cls in [
            FrontierType, FrontierStats, FrontierWitness, BoundedPriorityFrontier,
            BeamFrontier, FrontierCoordinator, FrontierAnalyzer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.generation_as_section_construction import (
            SectionTarget, GenerationGoal, CoverDesign, SectionConstructionPlan,
            SectionConstructionWitness, GenerationAsSectionConstruction,
        )
        for cls in [
            SectionTarget, GenerationGoal, CoverDesign, SectionConstructionPlan,
            SectionConstructionWitness, GenerationAsSectionConstruction,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.generation_moves_as_dependent_tran import (
            GenerationMove, DependentTransition, MoveObligation, TransitionGuard,
            MoveResult, MoveRegistry,
        )
        for cls in [
            GenerationMove, DependentTransition, MoveObligation, TransitionGuard,
            MoveResult, MoveRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.implementation_consequences import (
            ImplementationConsequence, StateSpaceConstraint, GenerationPolicy, PolicyViolation,
            ConsequenceDeriver,
        )
        for cls in [
            ImplementationConsequence, StateSpaceConstraint, GenerationPolicy, PolicyViolation,
            ConsequenceDeriver,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.integration import (
            GenerationJudgment, OrchestratorBridge, SolverBridge, EvidenceBridge,
            StateSpaceIntegration, IntegrationManager,
        )
        for cls in [
            GenerationJudgment, OrchestratorBridge, SolverBridge, EvidenceBridge,
            StateSpaceIntegration, IntegrationManager,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.manifest import (
            StateSpaceCapability, ExportedSymbol, ModuleDescriptor, StateSpaceManifest,
            CapabilityProbe,
        )
        for cls in [
            StateSpaceCapability, ExportedSymbol, ModuleDescriptor, StateSpaceManifest,
            CapabilityProbe,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.models import (
            TransitionType, StateStatus, StateSpaceError, InvalidTransitionError,
            StateNotFoundError, ConvergenceError, ObligationConflictError, SemanticState,
            StateTransition, GenerationStateSpace, ConvergenceMetric,
        )
        for cls in [
            TransitionType, StateStatus, StateSpaceError, InvalidTransitionError,
            StateNotFoundError, ConvergenceError, ObligationConflictError, SemanticState,
            StateTransition, GenerationStateSpace, ConvergenceMetric,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.pruning import (
            PruneDecision, DominanceResult, PruningStats, PruningAnalysis,
            PruningRule, DominancePruningRule, ObstructionPruningRule, BoundPruningRule,
            PruningCoordinator, PruningAnalyzer, PruningWitness,
        )
        for cls in [
            PruneDecision, DominanceResult, PruningStats, PruningAnalysis,
            PruningRule, DominancePruningRule, ObstructionPruningRule, BoundPruningRule,
            PruningCoordinator, PruningAnalyzer, PruningWitness,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.search_strategies import (
            SearchResult, SearchTree, SearchStepResult, SearchStrategy,
            BreadthFirstStrategy, DepthFirstStrategy, BestFirstStrategy, BeamSearchStrategy,
            SearchStrategyCoordinator, SearchStrategyAnalyzer, SearchStrategyWitness,
        )
        for cls in [
            SearchResult, SearchTree, SearchStepResult, SearchStrategy,
            BreadthFirstStrategy, DepthFirstStrategy, BestFirstStrategy, BeamSearchStrategy,
            SearchStrategyCoordinator, SearchStrategyAnalyzer, SearchStrategyWitness,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.state_merging import (
            MergeStatus, MergeConflict, MergeResult, CompatibilityScore,
            ConflictResolution, StateMergingCoordinator, StateMergingAnalyzer, StateMergingWitness,
            _FallbackState,
        )
        for cls in [
            MergeStatus, MergeConflict, MergeResult, CompatibilityScore,
            ConflictResolution, StateMergingCoordinator, StateMergingAnalyzer, StateMergingWitness,
            _FallbackState,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.state_representation import (
            StateComparisonResult, StateDiff, StateRepresentationCoordinator, StateRepresentationAnalyzer,
            StateRepresentationWitness,
        )
        for cls in [
            StateComparisonResult, StateDiff, StateRepresentationCoordinator, StateRepresentationAnalyzer,
            StateRepresentationWitness,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.state_serialization import (
            SerializationFormat, SerializationError, SerializationResult, CheckpointRecord,
            StateSerializationCoordinator, StateSerializationAnalyzer, StateSerializationWitness,
        )
        for cls in [
            SerializationFormat, SerializationError, SerializationResult, CheckpointRecord,
            StateSerializationCoordinator, StateSerializationAnalyzer, StateSerializationWitness,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.the_core_state_space_for_generatio import (
            GenStateKind, GenerationState, StateTransition, StateSpace,
            GenerationContext, StateSpaceExplorer,
        )
        for cls in [
            GenStateKind, GenerationState, StateTransition, StateSpace,
            GenerationContext, StateSpaceExplorer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.generation.state_space.theorems import (
            CorrectnessObligation, TerminationArgument, CompletenessProof, GenerationTheorem,
            TheoremRegistry, CompletenessVerifier, TerminationChecker, CorrectnessValidator,
        )
        for cls in [
            CorrectnessObligation, TerminationArgument, CompletenessProof, GenerationTheorem,
            TheoremRegistry, CompletenessVerifier, TerminationChecker, CorrectnessValidator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_orchestration_classes():
    """Collect orchestration classes."""
    registry = {}
    try:
        from jugeo.orchestration.controller import (
            MoveKind, SemanticMove as OrcSemanticMove, OrchestratorState,
            ResourceBudget, OrchestratorConfiguration, OrchestratorEventKind,
            OrchestratorEvent, OrchestratorEventBus, MoveRecord, MoveHistory,
            ConvergenceMonitor, ControlLaw, GreedyControl, LookaheadControl,
            BalancedControl, AdaptiveControl, MoveGenerator,
            OrchestratorDiagnostics, Orchestrator, ControlDecision,
            OrchestrationController,
        )
        for cls in [
            MoveKind, OrcSemanticMove, OrchestratorState, ResourceBudget,
            OrchestratorConfiguration, OrchestratorEventKind,
            OrchestratorEvent, OrchestratorEventBus, MoveRecord, MoveHistory,
            ConvergenceMonitor, ControlLaw, GreedyControl, LookaheadControl,
            BalancedControl, AdaptiveControl, MoveGenerator,
            OrchestratorDiagnostics, Orchestrator, ControlDecision,
            OrchestrationController,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.fleet import (
            BidOutcome, ChallengeOutcome, FleetMember, FleetBid, Fleet,
            BidEvaluator, FleetScheduler, CompetitiveSearch,
            FleetCalibration, ChallengeRecord, FleetHistory,
            FleetDiagnostics, FleetState,
        )
        for cls in [
            BidOutcome, ChallengeOutcome, FleetMember, FleetBid, Fleet,
            BidEvaluator, FleetScheduler, CompetitiveSearch,
            FleetCalibration, ChallengeRecord, FleetHistory,
            FleetDiagnostics, FleetState,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier import (
            FrontierItem, FrontierState, PhaseKind, TransitionTrigger,
            FrontierNode, Frontier, FrontierSearch, FrontierScorer,
            PhaseTransition, FrontierDiversity, FrontierBudget,
            FrontierHistory, FrontierDiagnostics,
        )
        for cls in [
            FrontierItem, FrontierState, PhaseKind, TransitionTrigger,
            FrontierNode, Frontier, FrontierSearch, FrontierScorer,
            PhaseTransition, FrontierDiversity, FrontierBudget,
            FrontierHistory, FrontierDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.budgets import (
            BudgetDimension, BudgetAllocation, Budget as OrcBudget,
            BudgetPolicy, BudgetAllocator, BudgetTracker, BudgetEnforcer,
            BudgetOptimizer, BudgetHistory, BudgetAlert, BudgetSerializer,
            BudgetDiagnostics, BudgetLedger,
            AlertRecord,
        )
        for cls in [
            BudgetDimension, BudgetAllocation, OrcBudget, BudgetPolicy,
            BudgetAllocator, BudgetTracker, BudgetEnforcer, BudgetOptimizer,
            BudgetHistory, BudgetAlert, BudgetSerializer,
            BudgetDiagnostics, BudgetLedger,
            AlertRecord,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.negotiation import (
            SessionState, DeadlockKind, NegotiationEventKind,
            TreatyProposal, NegotiationSession, FrictionPattern,
            CompromiseStrategy, NegotiationMemory, DeadlockDetector,
            Negotiator, NegotiationHistory, TreatyArchive,
            NegotiationEventBus, NegotiationDiagnostics,
            NegotiationPosition, NegotiationRound,
        )
        for cls in [
            SessionState, DeadlockKind, NegotiationEventKind,
            TreatyProposal, NegotiationSession, FrictionPattern,
            CompromiseStrategy, NegotiationMemory, DeadlockDetector,
            Negotiator, NegotiationHistory, TreatyArchive,
            NegotiationEventBus, NegotiationDiagnostics,
            NegotiationPosition, NegotiationRound,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.synthesis_orchestrator import (
            ObstructionPattern, SynthesisOrchestratorConfig,
            TheoryDeficitSignal, SynthesisCampaign, TheoryDeficitDetector,
            EvidenceBridge as SynthEvidenceBridge, CampaignScheduler,
            SynthesisOrchestrator, SynthesisOrchestratorDiagnostics,
        )
        for cls in [
            ObstructionPattern, SynthesisOrchestratorConfig,
            TheoryDeficitSignal, SynthesisCampaign, TheoryDeficitDetector,
            SynthEvidenceBridge, CampaignScheduler, SynthesisOrchestrator,
            SynthesisOrchestratorDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # mixed_evidence_routing
    try:
        from jugeo.orchestration.mixed_evidence_routing.models import (
            EvidenceChannel, RoutingStrategy, EscalationUrgency,
            RoutingDecision, JurisdictionMap, EvidenceChannelSelector,
            CopilotQueryRecord, HumanEscalation, RoutingHistory,
            ChannelStats,
        )
        for cls in [
            EvidenceChannel, RoutingStrategy, EscalationUrgency,
            RoutingDecision, JurisdictionMap, EvidenceChannelSelector,
            CopilotQueryRecord, HumanEscalation, RoutingHistory,
            ChannelStats,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # fleet_competition
    try:
        from jugeo.orchestration.fleet_competition.models import (
            BidStatus, RoundPhase, CalibrationStatus, BidDelta,
            CompetitiveBid, FleetRound, CalibrationTrace,
        )
        for cls in [
            BidStatus, RoundPhase, CalibrationStatus, BidDelta,
            CompetitiveBid, FleetRound, CalibrationTrace,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # frontier_objectives
    try:
        from jugeo.orchestration.frontier_objectives.models import (
            ObjectiveKind, FrontierObjective, PhaseTransitionModel,
            ClosureGainEstimate, DiversityMetric, ObjectiveResult,
            FrontierBudgetModel, ObjectiveSet, ScoringState,
        )
        for cls in [
            ObjectiveKind, FrontierObjective, PhaseTransitionModel,
            ClosureGainEstimate, DiversityMetric, ObjectiveResult,
            FrontierBudgetModel, ObjectiveSet, ScoringState,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # frontier_phases
    try:
        from jugeo.orchestration.frontier_phases.models import (
            PhaseHealthStatus, PhaseDescriptor, PhaseTransitionRecord,
            PhaseHistory, StallDetector, ConvergenceCertificate,
        )
        for cls in [
            PhaseHealthStatus, PhaseDescriptor, PhaseTransitionRecord,
            PhaseHistory, StallDetector, ConvergenceCertificate,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # semantic_control
    try:
        from jugeo.orchestration.semantic_control.models import (
            ControlLawKind, StateHealthStatus, ConvergenceMode,
            SemanticControlState, StateDelta, AdmissibleMove,
            SemanticTrajectory,
        )
        for cls in [
            ControlLawKind, StateHealthStatus, ConvergenceMode,
            SemanticControlState, StateDelta, AdmissibleMove,
            SemanticTrajectory,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # treaty_memory
    try:
        from jugeo.orchestration.treaty_memory.models import (
            NegotiationOutcome, MemoryIndexKind, ArchivePolicy,
            TreatyClause, TreatyMemoryRecord, TreatyArchiveEntry,
            NegotiationResult, MemoryQuery, MemoryStatistics,
        )
        for cls in [
            NegotiationOutcome, MemoryIndexKind, ArchivePolicy,
            TreatyClause, TreatyMemoryRecord, TreatyArchiveEntry,
            NegotiationResult, MemoryQuery, MemoryStatistics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # mixed_evidence_routing additional modules
    try:
        from jugeo.orchestration.mixed_evidence_routing.algorithms import (
            RoutingTable, PriorityRouter, FallbackChain,
            SemanticLoadBalancer, RouterMetrics, RouterRegistry,
            RoutingAlgorithmSelector,
            BalanceStrategy, RoutingTableEntry, TieBreakPolicy,
        )
        for cls in [RoutingTable, PriorityRouter, FallbackChain,
                    SemanticLoadBalancer, RouterMetrics, RouterRegistry,
                    RoutingAlgorithmSelector,
                    BalanceStrategy, RoutingTableEntry, TieBreakPolicy]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.channel_selection import (
            Z3ChannelAdapter, CopilotChannelAdapter,
            RuntimeWitnessAdapter, HumanEscalationAdapter,
            CompositeChannelOrchestrator, ChannelLoadBalancer,
            ChannelSelector,
            ChannelAdapterProtocol,
        )
        for cls in [Z3ChannelAdapter, CopilotChannelAdapter,
                    RuntimeWitnessAdapter, HumanEscalationAdapter,
                    CompositeChannelOrchestrator, ChannelLoadBalancer,
                    ChannelSelector,
                    ChannelAdapterProtocol]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.trust_aware_routing import (
            TrustRequirement, TrustCeilingMap,
            TrustAwareRoutingDecision, TrustRoutingAnalyzer,
            TrustAwareRouter, TrustRoutingCoordinator,
        )
        for cls in [TrustRequirement, TrustCeilingMap,
                    TrustAwareRoutingDecision, TrustRoutingAnalyzer,
                    TrustAwareRouter, TrustRoutingCoordinator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.channel_conflict_resolution import (
            ChannelVerdict, ChannelConflict, ConflictResolutionResult,
            TrustConservativeResolver, MajorityVoteResolver,
            ChannelConflictDetector, ChannelConflictResolver,
            ConflictResolutionCoordinator,
            ConflictType,
        )
        for cls in [ChannelVerdict, ChannelConflict, ConflictResolutionResult,
                    TrustConservativeResolver, MajorityVoteResolver,
                    ChannelConflictDetector, ChannelConflictResolver,
                    ConflictResolutionCoordinator,
                    ConflictType]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.routing_policies import (
            PolicyCondition, PolicyAction, RoutingPolicy,
            PolicyConflictDetector, PolicyEngine, PolicyCoordinator,
            PolicyConflict,
        )
        for cls in [PolicyCondition, PolicyAction, RoutingPolicy,
                    PolicyConflictDetector, PolicyEngine, PolicyCoordinator,
                    PolicyConflict]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.evidence_aggregation import (
            TrustLattice, EvidencePiece, AggregatedEvidence,
            TrustAlgebraAggregator, EvidenceBuffer,
            EvidenceAggregator, AggregationCoordinator,
        )
        for cls in [TrustLattice, EvidencePiece, AggregatedEvidence,
                    TrustAlgebraAggregator, EvidenceBuffer,
                    EvidenceAggregator, AggregationCoordinator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.manifest import (
            MixedEvidenceRoutingManifest, ChannelRegistry,
            JurisdictionCatalog, RoutingConfiguration,
        )
        for cls in [MixedEvidenceRoutingManifest, ChannelRegistry,
                    JurisdictionCatalog, RoutingConfiguration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.integration import (
            RoutingTrustIntegrator, RoutingDescentConnector,
            CopilotTrustGateway, RoutingFleetBridge,
            MixedEvidenceOrchestrator,
            AdaptiveRoutingPolicy, JurisdictionAuditLog, RoutingContext,
            RoutingOutcome as MERRoutingOutcome, RoutingPolicyRegistry,
            StrictJurisdictionPolicy,
        )
        for cls in [RoutingTrustIntegrator, RoutingDescentConnector,
                    CopilotTrustGateway, RoutingFleetBridge,
                    MixedEvidenceOrchestrator,
                    AdaptiveRoutingPolicy, JurisdictionAuditLog,
                    RoutingContext, MERRoutingOutcome,
                    RoutingPolicyRegistry, StrictJurisdictionPolicy]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.theorems import (
            Theorem45_1_JurisdictionCompleteness,
            Theorem45_2_TrustCeilingEnforcement,
            Theorem45_3_RoutingConsistency,
            Theorem45_4_HumanEscalationTermination,
            Lemma45_A_ChannelComposability,
            RoutingOutcome as MERTRoutingOutcome,
        )
        for cls in [Theorem45_1_JurisdictionCompleteness,
                    Theorem45_2_TrustCeilingEnforcement,
                    Theorem45_3_RoutingConsistency,
                    Theorem45_4_HumanEscalationTermination,
                    Lemma45_A_ChannelComposability,
                    MERTRoutingOutcome]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.canonicalized_fragments_for_z3 import (
            NormalizationLevel, Z3Sort, FragmentType,
            VariableBinding, NormalizationRule, SortSignature,
            CanonicalizedFragment, Z3Preparation, FragmentNormalizer,
            SolverInputBuilder, Z3SolverSession, CanonicalHashRegistry,
            TrustProof, EvaluationContext, FragmentBase,
            EvidenceSetBase, ProofObject,
        )
        for cls in [NormalizationLevel, Z3Sort, FragmentType,
                    VariableBinding, NormalizationRule, SortSignature,
                    CanonicalizedFragment, Z3Preparation, FragmentNormalizer,
                    SolverInputBuilder, Z3SolverSession, CanonicalHashRegistry,
                    TrustProof, EvaluationContext, FragmentBase,
                    EvidenceSetBase, ProofObject]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.mixed_evidence_routing.mixed_obligations_should_be_split import (
            ChannelType, HomogeneousFragment, MixedObligation,
            SplitResult, SplitFailure, ObligationSplitter,
            ObligationComplexity, TractabilityClass, MixedObligationSplitter,
            Z3Part, OraclePart, SplitObligation, SplitStrategy,
            TractabilityProof, ObligationFragment, SplitProofChain,
            ObligationClassifier, SplitResultMerger,
        )
        for cls in [ChannelType, HomogeneousFragment, MixedObligation,
                    SplitResult, SplitFailure, ObligationSplitter,
                    ObligationComplexity, TractabilityClass, MixedObligationSplitter,
                    Z3Part, OraclePart, SplitObligation, SplitStrategy,
                    TractabilityProof, ObligationFragment, SplitProofChain,
                    ObligationClassifier, SplitResultMerger]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # fleet_competition additional modules
    try:
        from jugeo.orchestration.fleet_competition.bid_evaluation import (
            BidEvaluation, BidEvaluationCriterion, MultiCriterionEvaluator,
            ParetoFilter, BidRanker, BidAuction, EvaluationHistory,
        )
        for cls in [BidEvaluation, BidEvaluationCriterion, MultiCriterionEvaluator,
                    ParetoFilter, BidRanker, BidAuction, EvaluationHistory]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.fleet_competition.challenge_protocol import (
            AdjudicationPolicy, ChallengeEvent, EvidenceGatherer,
            ChallengeInitiator, ChallengeAdjudicator, ChallengeLedger,
            ChallengeEventBus, ChallengeStatistics,
        )
        for cls in [AdjudicationPolicy, ChallengeEvent, EvidenceGatherer,
                    ChallengeInitiator, ChallengeAdjudicator, ChallengeLedger,
                    ChallengeEventBus, ChallengeStatistics]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.fleet_competition.calibration import (
            CalibrationSample, CalibrationReport, AccuracyEstimator,
            LatencyTracker, TrustDecay, CalibrationScheduler,
            CalibrationEngine, CrossMemberCalibrator,
        )
        for cls in [CalibrationSample, CalibrationReport, AccuracyEstimator,
                    LatencyTracker, TrustDecay, CalibrationScheduler,
                    CalibrationEngine, CrossMemberCalibrator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.fleet_competition.integration import (
            FleetTrustIntegrator, FleetDescentConnector,
            FleetFrontierBridge, FleetCompetitionOrchestrator,
            CompetitionSession,
        )
        for cls in [FleetTrustIntegrator, FleetDescentConnector,
                    FleetFrontierBridge, FleetCompetitionOrchestrator,
                    CompetitionSession]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.fleet_competition.manifest import (
            CompetitionConfig, BidSchemaEntry, BidSchemaRegistry,
            FleetCompetitionDescriptor, FleetCompetitionManifest,
        )
        for cls in [CompetitionConfig, BidSchemaEntry, BidSchemaRegistry,
                    FleetCompetitionDescriptor, FleetCompetitionManifest]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.fleet_competition.theorems import (
            Theorem46_1_MonotonicBidRefinement,
            Theorem46_2_ChallengeConservativity,
            Theorem46_3_CalibrationConvergence,
            Theorem46_4_ParetoStability,
            Lemma46_A_BidDeltaAntiSymmetry,
            CompetitionState,
        )
        for cls in [Theorem46_1_MonotonicBidRefinement,
                    Theorem46_2_ChallengeConservativity,
                    Theorem46_3_CalibrationConvergence,
                    Theorem46_4_ParetoStability,
                    Lemma46_A_BidDeltaAntiSymmetry,
                    CompetitionState]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.fleet_competition.loser_handling import (
            LoserRecord, LoserPenalty, PenaltyLedger, LoserArchive,
            LoserHandler, LoserHandlingAnalyzer, LoserHandlingCoordinator,
            LoserHandlingWitness,
            CompetitionResult as LHCompetitionResult,
        )
        for cls in [LoserRecord, LoserPenalty, PenaltyLedger, LoserArchive,
                    LoserHandler, LoserHandlingAnalyzer, LoserHandlingCoordinator,
                    LoserHandlingWitness,
                    LHCompetitionResult]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # frontier_objectives additional modules
    try:
        from jugeo.orchestration.frontier_objectives.budget_allocation import (
            BudgetChannel, AllocationDecision, BudgetAllocator as FO_BudgetAllocator,
            AdaptiveBudgetPolicy, BudgetLedger as FO_BudgetLedger,
            ChannelPriorityQueue, BudgetRebalancer, BudgetAuditLog,
            BudgetReport,
        )
        for cls in [BudgetChannel, AllocationDecision, FO_BudgetAllocator,
                    AdaptiveBudgetPolicy, FO_BudgetLedger,
                    ChannelPriorityQueue, BudgetRebalancer, BudgetAuditLog,
                    BudgetReport]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_objectives.objective_scoring import (
            ScoringContext, ObjectiveScorer, ClosureGainPredictor,
            StabilityAnalyzer, DiversityEnforcer, CostEstimator,
            CompositeObjectiveFunction, ScoringHistory,
        )
        for cls in [ScoringContext, ObjectiveScorer, ClosureGainPredictor,
                    StabilityAnalyzer, DiversityEnforcer, CostEstimator,
                    CompositeObjectiveFunction, ScoringHistory]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_objectives.manifest import (
            FrontierObjectivesManifest, ObjectiveEntry,
            ObjectiveRegistry, ManifestValidator as FO_ManifestValidator,
        )
        for cls in [FrontierObjectivesManifest, ObjectiveEntry,
                    ObjectiveRegistry, FO_ManifestValidator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_objectives.integration import (
            FrontierObjectivesOrchestrator, ObjectiveFrontierBridge,
            PhaseTransitionHandler, ObjectiveTrustAdapter,
            FrontierDescentIntegrator, IntegrationPipeline,
        )
        for cls in [FrontierObjectivesOrchestrator, ObjectiveFrontierBridge,
                    PhaseTransitionHandler, ObjectiveTrustAdapter,
                    FrontierDescentIntegrator, IntegrationPipeline]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_objectives.theorems import (
            Theorem47_1_ClosureGainMonotonicity,
            Theorem47_2_PhaseTransitionDetectability,
            Theorem47_3_DiversityMaintenability,
            Theorem47_4_BudgetFeasibility,
            Lemma47_A_ObjectiveComposability,
            InvariantKind, InvariantViolation, TheoremBase,
            TheoremProofAttempt,
        )
        for cls in [Theorem47_1_ClosureGainMonotonicity,
                    Theorem47_2_PhaseTransitionDetectability,
                    Theorem47_3_DiversityMaintenability,
                    Theorem47_4_BudgetFeasibility,
                    Lemma47_A_ObjectiveComposability,
                    InvariantKind, InvariantViolation, TheoremBase,
                    TheoremProofAttempt]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_objectives.exploitation_pressure import (
            ExploitationMode, ExploitationPressureVector,
            ExploitationPressureHistory, HighValueConcentrationDetector,
            ConvergenceProximityEstimator, BudgetDeficitEstimator,
            RewardGradientTracker, ExploitationPressureAnalyzer,
            ExploitationPressureWitness, ExploitationPressureCoordinator,
        )
        for cls in [ExploitationMode, ExploitationPressureVector,
                    ExploitationPressureHistory, HighValueConcentrationDetector,
                    ConvergenceProximityEstimator, BudgetDeficitEstimator,
                    RewardGradientTracker, ExploitationPressureAnalyzer,
                    ExploitationPressureWitness, ExploitationPressureCoordinator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_objectives.exploration_pressure import (
            PressureSource, ExplorationPressureVector,
            ExplorationPressureHistory, EntropyDeficitDetector,
            StagnationDetector, BudgetSurplusEstimator,
            CoverageGapAnalyzer, ExplorationPressureAnalyzer,
            ExplorationPressureWitness, ExplorationPressureCoordinator,
        )
        for cls in [PressureSource, ExplorationPressureVector,
                    ExplorationPressureHistory, EntropyDeficitDetector,
                    StagnationDetector, BudgetSurplusEstimator,
                    CoverageGapAnalyzer, ExplorationPressureAnalyzer,
                    ExplorationPressureWitness, ExplorationPressureCoordinator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # frontier_phases additional modules
    try:
        from jugeo.orchestration.frontier_phases.phase_management import (
            PhaseEventBus, ExplorationPolicy, ExploitationPolicy,
            RecoveryScheduler, StallRecoveryProtocol,
            PhaseTransitionEngine, PhaseManager,
        )
        for cls in [PhaseEventBus, ExplorationPolicy, ExploitationPolicy,
                    RecoveryScheduler, StallRecoveryProtocol,
                    PhaseTransitionEngine, PhaseManager]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_phases.phase_detection import (
            PhaseSignalExtractor, PhaseHeuristics,
            PhaseConfidenceEstimator, PhaseWindowAnalyzer,
            PhaseClassifier, TransitionDetector, PhaseChangeNotifier,
        )
        for cls in [PhaseSignalExtractor, PhaseHeuristics,
                    PhaseConfidenceEstimator, PhaseWindowAnalyzer,
                    PhaseClassifier, TransitionDetector, PhaseChangeNotifier]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_phases.manifest import (
            FrontierPhasesManifest, PhaseRegistry,
            TransitionTriggerCatalog,
        )
        for cls in [FrontierPhasesManifest, PhaseRegistry,
                    TransitionTriggerCatalog]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_phases.integration import (
            FrontierPhasesBridge, PhaseMonitorAdapter,
            FrontierPhasesIntegrator, PhaseExportSnapshot,
        )
        for cls in [FrontierPhasesBridge, PhaseMonitorAdapter,
                    FrontierPhasesIntegrator, PhaseExportSnapshot]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_phases.algorithms import (
            FrontierPhasesConfig, PlanStep, ExecutionResult,
            FrontierPhasesPlanner, FrontierPhasesExecutor,
            SignalNormalizationSpec, FrontierPhasesNormalizer,
            PhaseAlgorithmRegistry,
        )
        for cls in [FrontierPhasesConfig, PlanStep, ExecutionResult,
                    FrontierPhasesPlanner, FrontierPhasesExecutor,
                    SignalNormalizationSpec, FrontierPhasesNormalizer,
                    PhaseAlgorithmRegistry]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.frontier_phases.bandit_style_allocation_across_het import (
            BanditArm, ArmStats, BanditPolicy, AllocationRecord,
            BanditAllocator, BanditAllocationCoordinator,
            BanditAllocationAnalyzer, BanditAllocationWitness,
            HeterogeneousPhase, PhaseReward, AllocationPolicy,
            BanditAllocation,
        )
        for cls in [BanditArm, ArmStats, BanditPolicy, AllocationRecord,
                    BanditAllocator, BanditAllocationCoordinator,
                    BanditAllocationAnalyzer, BanditAllocationWitness,
                    HeterogeneousPhase, PhaseReward, AllocationPolicy,
                    BanditAllocation]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # semantic_control additional modules
    try:
        from jugeo.orchestration.semantic_control.convergence import (
            ConvergenceMetrics, ObligationTracker, CoverageAnalyzer,
            ConvergenceRateEstimator, DivergenceDetector,
            CertificationAuthority, ConvergenceMonitor,
        )
        for cls in [ConvergenceMetrics, ObligationTracker, CoverageAnalyzer,
                    ConvergenceRateEstimator, DivergenceDetector,
                    CertificationAuthority, ConvergenceMonitor]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.semantic_control.manifest import (
            SemanticControlManifest, MoveRegistry, ControlLawCatalog,
        )
        for cls in [SemanticControlManifest, MoveRegistry, ControlLawCatalog]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.semantic_control.move_selection import (
            PreconditionChecker, PostconditionVerifier, MoveEnumerator,
            MovePrioritizer, MoveConflictResolver,
            MoveApplicationEngine, MoveSelector,
        )
        for cls in [PreconditionChecker, PostconditionVerifier, MoveEnumerator,
                    MovePrioritizer, MoveConflictResolver,
                    MoveApplicationEngine, MoveSelector]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.semantic_control.state_management import (
            StateSnapshot, StateEventBus, StateValidator,
            StateProjector, StateAggregator, StateDeltaComputer,
            StateManager,
        )
        for cls in [StateSnapshot, StateEventBus, StateValidator,
                    StateProjector, StateAggregator, StateDeltaComputer,
                    StateManager]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.semantic_control.semantic_transitions import (
            TransitionRecord, TransitionGuard, TransitionGuardRegistry,
            SemanticTransitionEngine, TransitionAnalyzer,
            TransitionCoordinator,
        )
        for cls in [TransitionRecord, TransitionGuard, TransitionGuardRegistry,
                    SemanticTransitionEngine, TransitionAnalyzer,
                    TransitionCoordinator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.semantic_control.integration import (
            ControlTrustIntegrator, ControlDescentConnector,
            ControlFleetBridge, ControlFrontierAdapter,
            SemanticControlOrchestrator,
        )
        for cls in [ControlTrustIntegrator, ControlDescentConnector,
                    ControlFleetBridge, ControlFrontierAdapter,
                    SemanticControlOrchestrator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.semantic_control.theorems import (
            Theorem44_1_ControlLawMonotonicity,
            Theorem44_2_AdmissibilityConservation,
            Theorem44_3_ConvergenceLaw,
            Theorem44_4_ObligationFiniteness,
            Lemma44_A_StateTransitionClosure,
            InvariantKind as SCInvariantKind,
        )
        for cls in [Theorem44_1_ControlLawMonotonicity,
                    Theorem44_2_AdmissibilityConservation,
                    Theorem44_3_ConvergenceLaw,
                    Theorem44_4_ObligationFiniteness,
                    Lemma44_A_StateTransitionClosure,
                    SCInvariantKind]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # treaty_memory additional modules
    try:
        from jugeo.orchestration.treaty_memory.algorithms import (
            TreatyMemoryPlanner, TreatyMemoryExecutor,
            TreatyMemoryNormalizer,
        )
        for cls in [TreatyMemoryPlanner, TreatyMemoryExecutor,
                    TreatyMemoryNormalizer]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.treaty_memory.negotiation_memory import (
            NegotiationEpisode, EpisodeIndex,
            NegotiationMemoryAnalyzer, NegotiationMemoryCoordinator,
        )
        for cls in [NegotiationEpisode, EpisodeIndex,
                    NegotiationMemoryAnalyzer, NegotiationMemoryCoordinator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.treaty_memory.manifest import (
            TreatyMemoryManifest, MemorySchemaRegistry,
            ArchiveCatalog, MemoryModuleDescriptor, PackageHealthCheck,
        )
        for cls in [TreatyMemoryManifest, MemorySchemaRegistry,
                    ArchiveCatalog, MemoryModuleDescriptor, PackageHealthCheck]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.treaty_memory.integration import (
            TreatyMemoryBridge, TreatyMemoryImporter,
            TreatyMemoryExporter, TreatyMemoryHealthMonitor,
        )
        for cls in [TreatyMemoryBridge, TreatyMemoryImporter,
                    TreatyMemoryExporter, TreatyMemoryHealthMonitor]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.treaty_memory.theorems import (
            Theorem48_1_MemoryMonotonicity,
            Theorem48_2_LawStability,
            Theorem48_3_ArchiveCompression,
            Theorem48_4_CapitalNonNegativity,
            Theorem48_5_InterfaceDiscoveryCompleteness,
            FalsificationReport,
        )
        for cls in [Theorem48_1_MemoryMonotonicity,
                    Theorem48_2_LawStability,
                    Theorem48_3_ArchiveCompression,
                    Theorem48_4_CapitalNonNegativity,
                    Theorem48_5_InterfaceDiscoveryCompleteness,
                    FalsificationReport]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.treaty_memory.archival_value_semantic_capital_an import (
            CapitalUnit, CompressionStrategy, ValueAnalysisReport,
            SemanticCapitalAccount, ArchivalValueAnalyzer,
            ArchivalValueCoordinator,
        )
        for cls in [CapitalUnit, CompressionStrategy, ValueAnalysisReport,
                    SemanticCapitalAccount, ArchivalValueAnalyzer,
                    ArchivalValueCoordinator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.treaty_memory.semantic_archives_versus_raw_histo import (
            SemanticTag, ArchiveEntry, ArchivalIndex,
            SemanticArchivesAnalyzer, SemanticArchivesCoordinator,
        )
        for cls in [SemanticTag, ArchiveEntry, ArchivalIndex,
                    SemanticArchivesAnalyzer, SemanticArchivesCoordinator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.orchestration.fleet_competition.accepted_competition_should_improv import (
            CompetitionResult as ACCompetitionResult,
        )
        registry["ACCompetitionResult"] = ACCompetitionResult
    except Exception:
        pass
    return registry


def _collect_ideation_classes():
    """Collect ideation classes."""
    registry = {}
    # analogy_transport
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
        for _cls in [
            AnalogyQuality, TransportStatus, AnalogyFunctor, SourceTheorem,
            TransportedTheorem, TransportResult, TransportPlan,
            PlanValidationResult, PlanningCycleResult, TransportVerification,
            NormalizedTheorem, TransportPlannerConfig,
            TransportExecutorConfig, NormalizerConfig, DomainRegistry,
            AnalogyTransportPlanner, AnalogyTransportExecutor,
            AnalogyTransportNormalizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.analogy_transport.analogy_construction import (
            AnalogyConfig, AnalogyConstructor,
        )
        for _cls in [
            AnalogyConfig, AnalogyConstructor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
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
        for _cls in [
            BundleStatus, SyncStatus, RegistrationRecord, DomainUpdate,
            TransportOpportunity, PackSyncResult, SubscriptionHandle,
            BridgeIntegrationResult, BundleValidationResult, BundleSummary,
            BridgeConfig, TheoremRegistry, AnalogyTransportBridge,
            ExportBundle, TrustTier, IntegrationJudgment,
            TransportRegistration, IntegrationStatus, BridgeSignal,
            SynchronisationRecord, ExportManifest,
            AnalogyTransportIntegration, TheoremTransportBridge,
            OrchestratorAnalogyBridge,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.analogy_transport.manifest import (
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        )
        for _cls in [
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.analogy_transport.models import (
            AnalogyQuality, TransportFidelity, AnalogyMap,
            StructurePreservation, PurposePreservation, TransportedIdea,
            AnalogyVerification,
        )
        for _cls in [
            AnalogyQuality, TransportFidelity, AnalogyMap,
            StructurePreservation, PurposePreservation, TransportedIdea,
            AnalogyVerification,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.analogy_transport.structure_preservation import (
            StructurePreservationAuditor,
        )
        for _cls in [
            StructurePreservationAuditor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.analogy_transport.theorems import (
            TestType, SuiteOutcome, FalsificationTest,
            FalsificationTestResult, SchemaValidationResult,
            CoherenceCheckResult, StructurePreservationResult,
            CanonicalTheorem, FalsificationSuiteResult, FalsificationAnalysis,
            SchemaConfig, FalsificationSuiteConfig,
            AnalogyTransportTheoremSchema, FalsificationSuite,
        )
        for _cls in [
            TestType, SuiteOutcome, FalsificationTest,
            FalsificationTestResult, SchemaValidationResult,
            CoherenceCheckResult, StructurePreservationResult,
            CanonicalTheorem, FalsificationSuiteResult, FalsificationAnalysis,
            SchemaConfig, FalsificationSuiteConfig,
            AnalogyTransportTheoremSchema, FalsificationSuite,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine import DiscoveryEngineAPI
        registry["DiscoveryEngineAPI"] = DiscoveryEngineAPI
    except Exception:
        pass
    # discovery_engine
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
        for _cls in [
            ProposalOutcome, ObstructionRecord, ProposalRecord, ArchiveEntry,
            DiscoverySubsystemConfig, DiscoverySubsystemResult,
            ObstructionCoverageReport, ProposalQualityReport,
            ArchiveHealthReport, WitnessVerdict, ArchiveConsistencyReport,
            CycleWitnessReport, ArchiveTrace,
            MathDiscoverySubsystemCoordinator, MathDiscoverySubsystemAnalyzer,
            MathDiscoverySubsystemWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.algorithms import (
            DiscoveryAlgorithms, PipelineCallback, LoggingCallback,
            DiscoveryPipeline,
        )
        for _cls in [
            DiscoveryAlgorithms, PipelineCallback, LoggingCallback,
            DiscoveryPipeline,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
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
        for _cls in [
            FailureCategory, EvalCalibConfig, ObstructionSet,
            LeverageEvaluation, TheoremUsageEvent, ReuseEvaluation,
            FailedProposal, FailureAnalysisReport, CalibrationResult,
            EvalCycleData, EvalCycleResult, LeverageDistribution,
            FailurePatternSummary, CalibrationDriftReport,
            LeverageWitnessReport, CalibrationWitnessReport,
            FailureWitnessReport, EvaluationCalibrationCoordinator,
            EvaluationCalibrationAnalyzer, EvaluationCalibrationWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.integration import (
            IntegrationStatus, IntegrationEvent, BridgeIntegrationAdapter,
            OrchestrationAdapter, _MinimalPipeline, EvidenceChannelAdapter,
            DiscoveryEngineIntegration,
        )
        for _cls in [
            IntegrationStatus, IntegrationEvent, BridgeIntegrationAdapter,
            OrchestrationAdapter, _MinimalPipeline, EvidenceChannelAdapter,
            DiscoveryEngineIntegration,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.kind_classification import (
            KindMatchStrategy, KindClassifier, KindSignatureBuilder,
            KindRegistry, KindClassificationRunner,
        )
        for _cls in [
            KindMatchStrategy, KindClassifier, KindSignatureBuilder,
            KindRegistry, KindClassificationRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.manifest import (
            ManifestStatus, EvidenceEntryKind, EvidenceEntry,
            DiscoveryEngineManifest, ManifestBuilder,
        )
        for _cls in [
            ManifestStatus, EvidenceEntryKind, EvidenceEntry,
            DiscoveryEngineManifest, ManifestBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.models import (
            DiscoveryStatus, PipelineStage, DiscoveryCandidate, KindSignature,
            TheoremCandidate, PromotionDecision, DiscoveryConfig,
            DiscoveryDiagnostics, DiscoveryResult, NoveltyPipelineStage,
            KindClassificationStage, TheoremSynthesisStage,
            PackPromotionStage,
        )
        for _cls in [
            DiscoveryStatus, PipelineStage, DiscoveryCandidate, KindSignature,
            TheoremCandidate, PromotionDecision, DiscoveryConfig,
            DiscoveryDiagnostics, DiscoveryResult, NoveltyPipelineStage,
            KindClassificationStage, TheoremSynthesisStage,
            PackPromotionStage,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.novelty_pipeline import (
            NoveltyFilter, NoveltyRanker, NoveltyCandidateSet,
            NoveltyPipelineRunner,
        )
        for _cls in [
            NoveltyFilter, NoveltyRanker, NoveltyCandidateSet,
            NoveltyPipelineRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.pack_promotion import (
            EligibilityCriterion, EligibilityResult, PackEligibilityChecker,
            PromotionReport, PromotionAuthority, PackPromotionRunner,
        )
        for _cls in [
            EligibilityCriterion, EligibilityResult, PackEligibilityChecker,
            PromotionReport, PromotionAuthority, PackPromotionRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
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
        for _cls in [
            ConditionStatus, ConditionDifficulty, FalsificationConfig,
            TheoremRecord, FalsificationCondition, FalsificationBurden,
            EvidenceItem, ConditionCheckResult, FalsificationCampaignResult,
            BurdenDistribution, ConditionCoverageReport,
            BurdenLeverageCorrelation, BurdenWitnessReport,
            CampaignWitnessReport, ConditionWitnessReport,
            TheoremFalsificationBurdenCoordinator,
            TheoremFalsificationBurdenAnalyzer,
            TheoremFalsificationBurdenWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.theorem_synthesis import (
            SynthesisPattern, TheoremSynthesizer, ProofSketchBuilder,
            TheoremValidator, TheoremSynthesisRunner,
        )
        for _cls in [
            SynthesisPattern, TheoremSynthesizer, ProofSketchBuilder,
            TheoremValidator, TheoremSynthesisRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_engine.theorems import (
            TheoremVerificationContext, TheoremVerificationResult,
            AbstractTheorem, DiscoveryCompletenessTheorem,
            PipelineSoundnessTheorem, NoveltyPreservationTheorem,
            KindAssignmentUniquenessTheorem,
            TheoremSynthesisCorrectnessTheorem,
            PackPromotionMonotonicityTheorem, DiscoveryTheoremRegistry,
        )
        for _cls in [
            TheoremVerificationContext, TheoremVerificationResult,
            AbstractTheorem, DiscoveryCompletenessTheorem,
            PipelineSoundnessTheorem, NoveltyPreservationTheorem,
            KindAssignmentUniquenessTheorem,
            TheoremSynthesisCorrectnessTheorem,
            PackPromotionMonotonicityTheorem, DiscoveryTheoremRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # discovery_federation
    try:
        from jugeo.ideation.discovery_federation.algorithms import (
            FederationAlgorithms,
        )
        for _cls in [
            FederationAlgorithms,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.authority_choice_rejection_provisi import (
            AuthorityDisposition, ChoiceReason, TheoremCandidate,
            ChoiceRecord, AuthorityChoiceAnalyzer, AuthorityChoiceCoordinator,
            AuthorityChoiceWitness,
        )
        for _cls in [
            AuthorityDisposition, ChoiceReason, TheoremCandidate,
            ChoiceRecord, AuthorityChoiceAnalyzer, AuthorityChoiceCoordinator,
            AuthorityChoiceWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.discovery_as_authority import (
            PromotionStatus, AuthorityCondition, PromotionRecord,
            AuthorityPromoter, AuthorityValidator, AuthorityLifecycleManager,
            DiscoveryAuthorityRunner,
        )
        for _cls in [
            PromotionStatus, AuthorityCondition, PromotionRecord,
            AuthorityPromoter, AuthorityValidator, AuthorityLifecycleManager,
            DiscoveryAuthorityRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.federated_knowledge import (
            PropagationStatus, MergeStrategy, KnowledgeEntry, MergeResult,
            KnowledgePropagator, KnowledgeMerger, KnowledgeRepository,
            FederatedKnowledgeRunner,
        )
        for _cls in [
            PropagationStatus, MergeStrategy, KnowledgeEntry, MergeResult,
            KnowledgePropagator, KnowledgeMerger, KnowledgeRepository,
            FederatedKnowledgeRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.federation_consensus import (
            VoteStatus, QuorumPolicy, VotingRound, QuorumCalculator,
            VoteAggregator, ConsensusProtocol, FederationConsensusRunner,
        )
        for _cls in [
            VoteStatus, QuorumPolicy, VotingRound, QuorumCalculator,
            VoteAggregator, ConsensusProtocol, FederationConsensusRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.federation_versus_foundation_scope import (
            PlacementMode, BridgeBurdenLevel, ScopeProfile, PlacementRecord,
            FederationVsFoundationAnalyzer, FederationVsFoundationCoordinator,
            FederationVsFoundationWitness,
        )
        for _cls in [
            PlacementMode, BridgeBurdenLevel, ScopeProfile, PlacementRecord,
            FederationVsFoundationAnalyzer, FederationVsFoundationCoordinator,
            FederationVsFoundationWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.implementation_consequences import (
            TrustTier, FederationJudgment, FederatedDiscoveryConsequence,
            FederationConstraint, FederationNode, FederationRecord,
            NodeHealth, ConsensusProposal, FederationScope, PolicyViolation,
            FederationAuditEntry, ConsequenceChain, FederationPolicy,
            FederationConsensus,
        )
        for _cls in [
            TrustTier, FederationJudgment, FederatedDiscoveryConsequence,
            FederationConstraint, FederationNode, FederationRecord,
            NodeHealth, ConsensusProposal, FederationScope, PolicyViolation,
            FederationAuditEntry, ConsequenceChain, FederationPolicy,
            FederationConsensus,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.integration import (
            IntegrationStatus, AdapterKind, IntegrationEvent,
            FederationIntegration, DiscoveryBridgeAdapter,
            AuthorityPackAdapter,
        )
        for _cls in [
            IntegrationStatus, AdapterKind, IntegrationEvent,
            FederationIntegration, DiscoveryBridgeAdapter,
            AuthorityPackAdapter,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.manifest import (
            ManifestStatus, DiscoveryFederationManifest,
            FederationManifestBuilder,
        )
        for _cls in [
            ManifestStatus, DiscoveryFederationManifest,
            FederationManifestBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.models import (
            FederationStatus, ConsensusOutcome, AuthorityLevel,
            FederatedDiscovery, FederationVote, FederationConsensus,
            DiscoveryAuthority, KnowledgePropagation, AuthorityGrant,
            FederationNode, ConflictRecord,
        )
        for _cls in [
            FederationStatus, ConsensusOutcome, AuthorityLevel,
            FederatedDiscovery, FederationVote, FederationConsensus,
            DiscoveryAuthority, KnowledgePropagation, AuthorityGrant,
            FederationNode, ConflictRecord,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.discovery_federation.theorems import (
            _CallableStr, _CallableList, TheoremStatus, ProofMethod,
            TheoremResult, FederationSoundnessTheorem,
            AuthorityMonotonicityTheorem, ConsensusConvergenceTheorem,
            KnowledgePropagationSoundnessTheorem,
            ConflictResolutionCompletenessTheorem, FederationTheoremRegistry,
        )
        for _cls in [
            _CallableStr, _CallableList, TheoremStatus, ProofMethod,
            TheoremResult, FederationSoundnessTheorem,
            AuthorityMonotonicityTheorem, ConsensusConvergenceTheorem,
            KnowledgePropagationSoundnessTheorem,
            ConflictResolutionCompletenessTheorem, FederationTheoremRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # experiment_design
    try:
        from jugeo.ideation.experiment_design.algorithms import (
            ExperimentAlgorithm, FactorialDesign, LatinSquare,
            RandomizedControlled, BayesianExperimentDesign,
            AdaptiveExperiment,
        )
        for _cls in [
            ExperimentAlgorithm, FactorialDesign, LatinSquare,
            RandomizedControlled, BayesianExperimentDesign,
            AdaptiveExperiment,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.experiment_design.falsification import (
            FalsificationTest, FalsificationDesigner, HypothesisParser,
            AdversarialCaseGenerator, FalsificationRecord,
            ConclusivenessAnalyzer,
        )
        for _cls in [
            FalsificationTest, FalsificationDesigner, HypothesisParser,
            AdversarialCaseGenerator, FalsificationRecord,
            ConclusivenessAnalyzer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.experiment_design.integration import (
            ExperimentDesignIntegration, IdeationSystemBridge,
            CopilotExperimentAdvisor, ExperimentEventBus, ResultRepository,
        )
        for _cls in [
            ExperimentDesignIntegration, IdeationSystemBridge,
            CopilotExperimentAdvisor, ExperimentEventBus, ResultRepository,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.experiment_design.manifest import (
            ExperimentType, ExperimentStatus, ControlVariable, MeasureSpec,
            ExperimentDescriptor, ExperimentDesignManifest, ManifestValidator,
            ManifestRegistry, ExperimentRegistry,
        )
        for _cls in [
            ExperimentType, ExperimentStatus, ControlVariable, MeasureSpec,
            ExperimentDescriptor, ExperimentDesignManifest, ManifestValidator,
            ManifestRegistry, ExperimentRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.experiment_design.models import (
            ExperimentDesign, AblationStudy, ExperimentResult,
        )
        for _cls in [
            ExperimentDesign, AblationStudy, ExperimentResult,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.experiment_design.statistical_validation import (
            StatisticalValidator, SignificanceThreshold,
            MultipleTestingCorrection, ReportGenerator,
        )
        for _cls in [
            StatisticalValidator, SignificanceThreshold,
            MultipleTestingCorrection, ReportGenerator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.experiment_design.theorems import (
            Theorem, TheoremCatalog, TheoremVerifier,
        )
        for _cls in [
            Theorem, TheoremCatalog, TheoremVerifier,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # federation
    try:
        from jugeo.ideation.federation import (
            FederatedIdeaProposal, CrossRegimeBridge, AnalogyFinder,
            IdeaTransporter, FederationRegistry, IdeationFederator,
            FederationValidator, _HistoryRecord, FederationHistory,
            FederationDiagnostics, FederationSerializer, IdeaFederation,
        )
        for _cls in [
            FederatedIdeaProposal, CrossRegimeBridge, AnalogyFinder,
            IdeaTransporter, FederationRegistry, IdeationFederator,
            FederationValidator, _HistoryRecord, FederationHistory,
            FederationDiagnostics, FederationSerializer, IdeaFederation,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # ideas
    try:
        from jugeo.ideation.ideas import (
            TrustStatus, LifecycleStatus, GainProfile, ValidationPath,
            EvaluationResult, HistoryEntry, Idea, IdeaProposal, IdeaPortfolio,
            IdeaGenerator, IdeaEvaluator, IdeaRefiner, IdeaLifecycle,
            IdeaDependencyGraph, IdeaHistory, IdeaSerializer, IdeaDiagnostics,
        )
        for _cls in [
            TrustStatus, LifecycleStatus, GainProfile, ValidationPath,
            EvaluationResult, HistoryEntry, Idea, IdeaProposal, IdeaPortfolio,
            IdeaGenerator, IdeaEvaluator, IdeaRefiner, IdeaLifecycle,
            IdeaDependencyGraph, IdeaHistory, IdeaSerializer, IdeaDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # kind_discovery
    try:
        from jugeo.ideation.kind_discovery.algorithms import (
            DiscoveryAlgorithm, KindDiscoveryEngine, KindValidator,
            KindRanker, KindEvolutionTracker, DiscoveryDiagnostics,
            DiscoveryHistory,
        )
        for _cls in [
            DiscoveryAlgorithm, KindDiscoveryEngine, KindValidator,
            KindRanker, KindEvolutionTracker, DiscoveryDiagnostics,
            DiscoveryHistory,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.candidate_new_mathematical_kinds_e import (
            AbstractionLevel, CandidateKindConfig, KindHypothesis,
            TypeConstructorProposal, CandidateKindsAnalyzer,
            CandidateKindsWitness, CandidateKindsCoordinator,
        )
        for _cls in [
            AbstractionLevel, CandidateKindConfig, KindHypothesis,
            TypeConstructorProposal, CandidateKindsAnalyzer,
            CandidateKindsWitness, CandidateKindsCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.implementation_consequences import (
            ConsequenceType, ImplementationConsequenceConfig,
            ImplementationConsequence, ConsequenceGraph,
            ImplementationConsequencesAnalyzer,
            ImplementationConsequencesWitness,
            ImplementationConsequencesCoordinator,
        )
        for _cls in [
            ConsequenceType, ImplementationConsequenceConfig,
            ImplementationConsequence, ConsequenceGraph,
            ImplementationConsequencesAnalyzer,
            ImplementationConsequencesWitness,
            ImplementationConsequencesCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.integration import (
            TrustAwareDiscovery, IdeaKindLinker, FederationKindBridge,
            NoveltyKindScorer, IntegratedDiscoveryPipeline,
        )
        for _cls in [
            TrustAwareDiscovery, IdeaKindLinker, FederationKindBridge,
            NoveltyKindScorer, IntegratedDiscoveryPipeline,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.kind_bootstrapping import (
            BootstrapConfig, KindHypothesizer, DefinitionBuilder,
            ExampleGenerator, ValidationPlanner, KindBootstrapper,
        )
        for _cls in [
            BootstrapConfig, KindHypothesizer, DefinitionBuilder,
            ExampleGenerator, ValidationPlanner, KindBootstrapper,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.manifest import (
            _InitOnlyFrozenField, PackageCapability, PackageManifest,
            ManifestValidator, PackageRegistry, CapabilityQuery,
            ManifestSerializer, ManifestDiagnostics,
        )
        for _cls in [
            _InitOnlyFrozenField, PackageCapability, PackageManifest,
            ManifestValidator, PackageRegistry, CapabilityQuery,
            ManifestSerializer, ManifestDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.models import (
            KindStatus, ObstructionType, ObstructionField, KindPattern,
            KindCandidate, KindBootstrapPlan, NewKind,
        )
        for _cls in [
            KindStatus, ObstructionType, ObstructionField, KindPattern,
            KindCandidate, KindBootstrapPlan, NewKind,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.obstruction_fields_as_evidence_of import (
            ObstructionFieldEvidenceConfig, H1ObstructionClass,
            EvidenceCluster, ObstructionFieldEvidenceRecord,
            ObstructionFieldsEvidenceAnalyzer,
            ObstructionFieldsEvidenceWitness,
            ObstructionFieldsEvidenceCoordinator,
        )
        for _cls in [
            ObstructionFieldEvidenceConfig, H1ObstructionClass,
            EvidenceCluster, ObstructionFieldEvidenceRecord,
            ObstructionFieldsEvidenceAnalyzer,
            ObstructionFieldsEvidenceWitness,
            ObstructionFieldsEvidenceCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.obstruction_mining import (
            ObstructionMiningConfig, ObstructionExtractor,
            ObstructionClusterer, ObstructionFieldBuilder, FrequencyAnalyzer,
            ObstructionMiner,
        )
        for _cls in [
            ObstructionMiningConfig, ObstructionExtractor,
            ObstructionClusterer, ObstructionFieldBuilder, FrequencyAnalyzer,
            ObstructionMiner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.pattern_recognition import (
            PatternSignature, PatternMatcher, RecurrenceDetector,
            GeneralityEstimator, PatternRanker, PatternRecognizer,
        )
        for _cls in [
            PatternSignature, PatternMatcher, RecurrenceDetector,
            GeneralityEstimator, PatternRanker, PatternRecognizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.the_obstruction_to_kind_pipeline_c import (
            PipelineStage, PipelineConfig, PipelineStepResult, PipelineRun,
            PipelineArtifacts, ObstructionToKindPipelineAnalyzer,
            ObstructionToKindPipelineWitness,
            ObstructionToKindPipelineCoordinator,
        )
        for _cls in [
            PipelineStage, PipelineConfig, PipelineStepResult, PipelineRun,
            PipelineArtifacts, ObstructionToKindPipelineAnalyzer,
            ObstructionToKindPipelineWitness,
            ObstructionToKindPipelineCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.kind_discovery.theorems import (
            TheoremStatus, TheoremScope, KindDiscoveryTheorem,
            TheoremRegistry, TheoremVerifier, TheoremApplications,
            TheoremCatalog,
        )
        for _cls in [
            TheoremStatus, TheoremScope, KindDiscoveryTheorem,
            TheoremRegistry, TheoremVerifier, TheoremApplications,
            TheoremCatalog,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # novelty
    try:
        from jugeo.ideation.novelty import (
            NoveltyScore, NoveltyMetric, NoveltySearcher, TheoremPortfolio,
            PurposeAlignmentChecker, NoveltyFilter, SemanticDistanceModel,
            _HistoryRecord, NoveltyHistory, NoveltyOptimizer,
            NoveltyDiagnostics, _LegacyNoveltyScore,
        )
        for _cls in [
            NoveltyScore, NoveltyMetric, NoveltySearcher, TheoremPortfolio,
            PurposeAlignmentChecker, NoveltyFilter, SemanticDistanceModel,
            _HistoryRecord, NoveltyHistory, NoveltyOptimizer,
            NoveltyDiagnostics, _LegacyNoveltyScore,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # novelty_search
    try:
        from jugeo.ideation.novelty_search.a_purpose_conditioned_novelty_func import (
            NoveltyFunctionalConfig, LeverageScore, TractabilityScore,
            SemanticRelevanceScore, NoveltyFunctionalValue,
            PurposeConditionedNoveltyAnalyzer,
            PurposeConditionedNoveltyWitness,
            PurposeConditionedNoveltyCoordinator,
        )
        for _cls in [
            NoveltyFunctionalConfig, LeverageScore, TractabilityScore,
            SemanticRelevanceScore, NoveltyFunctionalValue,
            PurposeConditionedNoveltyAnalyzer,
            PurposeConditionedNoveltyWitness,
            PurposeConditionedNoveltyCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.algorithms import (
            OptimalNoveltySearch, NoveltyRanker, FrontierExplorer,
            SearchDiagnostics, SearchHistoryEntry, SearchHistory,
            SearchBenchmark,
        )
        for _cls in [
            OptimalNoveltySearch, NoveltyRanker, FrontierExplorer,
            SearchDiagnostics, SearchHistoryEntry, SearchHistory,
            SearchBenchmark,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.distance_metrics import (
            DistanceConfig, SemanticDistanceComputer,
            StructuralDistanceComputer, PurposeWeightedDistance,
            DistanceNormalizer, MetricAggregator, DistanceCacheManager,
        )
        for _cls in [
            DistanceConfig, SemanticDistanceComputer,
            StructuralDistanceComputer, PurposeWeightedDistance,
            DistanceNormalizer, MetricAggregator, DistanceCacheManager,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.implementation_consequences import (
            TrustTier, ConsequenceJudgment, NoveltySearchConsequence,
            ArchitecturalDecision, NoveltyConstraint, PolicyRule,
            PolicyViolationRecord, NoveltyBehaviourDescriptor,
            ArchiveAdmissionRecord, SystemConfig, NoveltyParameters,
            ConsequenceReport, ArchiveEvictionRecord, DiversityMeasurement,
            NoveltyArchitecture, NoveltyPolicy,
        )
        for _cls in [
            TrustTier, ConsequenceJudgment, NoveltySearchConsequence,
            ArchitecturalDecision, NoveltyConstraint, PolicyRule,
            PolicyViolationRecord, NoveltyBehaviourDescriptor,
            ArchiveAdmissionRecord, SystemConfig, NoveltyParameters,
            ConsequenceReport, ArchiveEvictionRecord, DiversityMeasurement,
            NoveltyArchitecture, NoveltyPolicy,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.integration import (
            PortfolioNoveltyIntegrator, IdeaNoveltyScorer,
            TrustFilteredSearch, FederationNoveltyBridge, PipelineStage,
            PipelineResult, IntegratedNoveltyPipeline,
        )
        for _cls in [
            PortfolioNoveltyIntegrator, IdeaNoveltyScorer,
            TrustFilteredSearch, FederationNoveltyBridge, PipelineStage,
            PipelineResult, IntegratedNoveltyPipeline,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.manifest import (
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        )
        for _cls in [
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.models import (
            SearchStrategy, MetricKind, NoveltySearchProblem,
            PortfolioCoverage, NoveltyMetricSpec, SearchResult,
            DiversityConstraint,
        )
        for _cls in [
            SearchStrategy, MetricKind, NoveltySearchProblem,
            PortfolioCoverage, NoveltyMetricSpec, SearchResult,
            DiversityConstraint,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.novelty_versus_useful_novelty_leve import (
            NoveltyLevel, UsefulNoveltyConfig, NoveltyMeasure,
            UsefulNoveltyComparison, NoveltyVsUsefulNoveltyAnalyzer,
            NoveltyVsUsefulNoveltyWitness, NoveltyVsUsefulNoveltyCoordinator,
        )
        for _cls in [
            NoveltyLevel, UsefulNoveltyConfig, NoveltyMeasure,
            UsefulNoveltyComparison, NoveltyVsUsefulNoveltyAnalyzer,
            NoveltyVsUsefulNoveltyWitness, NoveltyVsUsefulNoveltyCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.portfolio_coverage import (
            CoverageConfig, CoverageEstimator, GapDetector, CoverageOptimizer,
            DensityAnalyzer, CoverageReport, CoverageReporter,
        )
        for _cls in [
            CoverageConfig, CoverageEstimator, GapDetector, CoverageOptimizer,
            DensityAnalyzer, CoverageReport, CoverageReporter,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.search_strategies import (
            SearchConfig, GreedySearcher, BeamSearcher, ParetoSearcher,
            DiverseSearcher, SearchOrchestrator,
        )
        for _cls in [
            SearchConfig, GreedySearcher, BeamSearcher, ParetoSearcher,
            DiverseSearcher, SearchOrchestrator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.theorems import (
            TheoremStatus, TheoremKind, NoveltyTheorem, VerificationResult,
            TheoremRegistry, TheoremVerifier, TheoremApplications,
            TheoremCatalog,
        )
        for _cls in [
            TheoremStatus, TheoremKind, NoveltyTheorem, VerificationResult,
            TheoremRegistry, TheoremVerifier, TheoremApplications,
            TheoremCatalog,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.novelty_search.why_ag_dtt_and_ai_each_matter_in_n import (
            Framework, FrameworkContributionConfig, AGContribution,
            DTTContribution, AIContribution, FrameworkSynergy,
            WhyAGDTTAINoveltyAnalyzer, WhyAGDTTAINoveltyWitness,
            WhyAGDTTAINoveltyCoordinator,
        )
        for _cls in [
            Framework, FrameworkContributionConfig, AGContribution,
            DTTContribution, AIContribution, FrameworkSynergy,
            WhyAGDTTAINoveltyAnalyzer, WhyAGDTTAINoveltyWitness,
            WhyAGDTTAINoveltyCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # optimization
    try:
        from jugeo.ideation.optimization.algorithms import (
            OptimizationAlgorithm, WeightedSumOptimizer,
            LexicographicOptimizer, RandomSearchOptimizer,
            SimulatedAnnealingOptimizer, EvolutionaryOptimizer,
            BayesianStyleOptimizer, AlgorithmSelector,
        )
        for _cls in [
            OptimizationAlgorithm, WeightedSumOptimizer,
            LexicographicOptimizer, RandomSearchOptimizer,
            SimulatedAnnealingOptimizer, EvolutionaryOptimizer,
            BayesianStyleOptimizer, AlgorithmSelector,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.optimization.budget_optimization import (
            BudgetItem, KnapsackSolver, FractionalKnapsack,
            DynamicBudgetPolicy, BudgetSensitivityAnalysis, BudgetOptimizer,
        )
        for _cls in [
            BudgetItem, KnapsackSolver, FractionalKnapsack,
            DynamicBudgetPolicy, BudgetSensitivityAnalysis, BudgetOptimizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.optimization.integration import (
            OptimizationEventType, OptimizationEvent, OptimizationEventBus,
            CopilotOptimizationAdvisor, SchedulerOptimizationBridge,
            RegimeOptimizationBridge, OptimizationIntegration,
        )
        for _cls in [
            OptimizationEventType, OptimizationEvent, OptimizationEventBus,
            CopilotOptimizationAdvisor, SchedulerOptimizationBridge,
            RegimeOptimizationBridge, OptimizationIntegration,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.optimization.manifest import (
            AlgorithmDescriptor, OptimizationManifest, ManifestValidator,
            ManifestRegistry, AlgorithmRegistry,
        )
        for _cls in [
            AlgorithmDescriptor, OptimizationManifest, ManifestValidator,
            ManifestRegistry, AlgorithmRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.optimization.models import (
            ObjectiveDirection, SolutionStatus, IdeationObjective,
            OptimizationProblem, SolutionCandidate, ParetoFront,
            ObjectiveWeight, OptimizationResult, WeightedObjective,
            ConstraintSatisfaction, ObjectiveNormalizer,
        )
        for _cls in [
            ObjectiveDirection, SolutionStatus, IdeationObjective,
            OptimizationProblem, SolutionCandidate, ParetoFront,
            ObjectiveWeight, OptimizationResult, WeightedObjective,
            ConstraintSatisfaction, ObjectiveNormalizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.optimization.novelty_feasibility_tradeoff import (
            TradeoffPoint, NoveltyFeasibilityFrontier, TradeoffAnalyzer,
            AdaptiveWeightSchedule, RegretMinimizer,
        )
        for _cls in [
            TradeoffPoint, NoveltyFeasibilityFrontier, TradeoffAnalyzer,
            AdaptiveWeightSchedule, RegretMinimizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.optimization.objective_functions import (
            BaseObjective, NoveltyObjective, FeasibilityObjective,
            PurposeObjective, YieldObjective, CostObjective,
            CompositeObjective, ObjectiveFactory, ObjectiveEvaluator,
        )
        for _cls in [
            BaseObjective, NoveltyObjective, FeasibilityObjective,
            PurposeObjective, YieldObjective, CostObjective,
            CompositeObjective, ObjectiveFactory, ObjectiveEvaluator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.optimization.pareto_optimization import (
            DominanceChecker, CrowdingDistance, NSGAIIStyle,
            EpsilonConstraintSolver, ParetoOptimizer,
        )
        for _cls in [
            DominanceChecker, CrowdingDistance, NSGAIIStyle,
            EpsilonConstraintSolver, ParetoOptimizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.optimization.theorems import (
            TheoremStatus, TheoremRecord, TheoremCatalog, TheoremVerifier,
        )
        for _cls in [
            TheoremStatus, TheoremRecord, TheoremCatalog, TheoremVerifier,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # regime_bootstrapping
    try:
        from jugeo.ideation.regime_bootstrapping.algorithms import (
            AlgorithmConfig, BootstrappingAlgorithms,
        )
        for _cls in [
            AlgorithmConfig, BootstrappingAlgorithms,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.regime_bootstrapping.domain_formation import (
            ObstructionAnalyzer, DomainPartitioner, DomainValidator,
            DomainFormationRunner,
        )
        for _cls in [
            ObstructionAnalyzer, DomainPartitioner, DomainValidator,
            DomainFormationRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.regime_bootstrapping.domain_formation_when_the_right_di import (
            GapSeverity, DomainStatus, DomainFormationConfig,
            ObstructionCluster, SemanticGap, DomainProposal,
            DomainValidationResult, DomainRecord, DomainFormationResult,
            CoverageGapReport, ViabilityReport, OverlapReport,
            GapWitnessReport, ProposalWitnessReport,
            RegistrationWitnessReport, DomainFormationAnalyzer,
            DomainFormationWitness, DomainFormationCoordinator,
        )
        for _cls in [
            GapSeverity, DomainStatus, DomainFormationConfig,
            ObstructionCluster, SemanticGap, DomainProposal,
            DomainValidationResult, DomainRecord, DomainFormationResult,
            CoverageGapReport, ViabilityReport, OverlapReport,
            GapWitnessReport, ProposalWitnessReport,
            RegistrationWitnessReport, DomainFormationAnalyzer,
            DomainFormationWitness, DomainFormationCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
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
        for _cls in [
            TaskStatus, ImpactScope, ImplementationConfig, RegimeChange,
            ImpactSet, PackUpdateTask, BridgeReindexTask,
            DependencyRecomputeTask, ImplementationPlan, ImplementationResult,
            ConsequenceCycleResult, ImpactScopeReport, FeasibilityReport,
            GraphChangeReport, CostEstimate, ImpactWitnessReport,
            PlanWitnessReport, ResultWitnessReport,
            RegimeImplementationCoordinator, RegimeImplementationAnalyzer,
            RegimeImplementationWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.regime_bootstrapping.integration import (
            IntegrationConfig, IntegrationResult, RegimeCatalogAdapter,
            EvidenceBootstrapAdapter, OrchestratorAdapter,
            BootstrappingIntegration,
        )
        for _cls in [
            IntegrationConfig, IntegrationResult, RegimeCatalogAdapter,
            EvidenceBootstrapAdapter, OrchestratorAdapter,
            BootstrappingIntegration,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.regime_bootstrapping.manifest import (
            ManifestValidationResult, RegimeBootstrappingManifest,
            BootstrappingManifestBuilder,
        )
        for _cls in [
            ManifestValidationResult, RegimeBootstrappingManifest,
            BootstrappingManifestBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.regime_bootstrapping.models import (
            BootstrapStatus, ObstructionKind, DomainType, TypeConstructorKind,
            BootstrapPriority, ObstructionField, DomainFormation,
            TypeConstructor, RegimeCandidate, BootstrapStep, BootstrapPlan,
            BootstrapResult, RegimeBootstrapperConfig, RegimeBootstrapper,
        )
        for _cls in [
            BootstrapStatus, ObstructionKind, DomainType, TypeConstructorKind,
            BootstrapPriority, ObstructionField, DomainFormation,
            TypeConstructor, RegimeCandidate, BootstrapStep, BootstrapPlan,
            BootstrapResult, RegimeBootstrapperConfig, RegimeBootstrapper,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
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
        for _cls in [
            PatternFrequency, ConstructorArity, TypeConstructorConfig,
            TheoremSketch, AdHocPattern, TypeConstructorProposal,
            ConstructorValidationResult, TypeConstructorRecord,
            ConstructorMiningResult, PatternFrequencyReport, CoverageAnalysis,
            KindConsistencyReport, PatternWitnessReport,
            ConstructorWitnessReport, RegistrationWitnessReport,
            NewTypeConstructorsCoordinator, NewTypeConstructorsAnalyzer,
            NewTypeConstructorsWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.regime_bootstrapping.regime_bootstrapping import (
            BootstrapOrchestrator, RegimeAssembler, BootstrapValidator,
            RegimeBootstrappingRunner,
        )
        for _cls in [
            BootstrapOrchestrator, RegimeAssembler, BootstrapValidator,
            RegimeBootstrappingRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
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
        for _cls in [
            CarrierStatus, LawStatus, BootstrappingConfig, DomainRecord,
            CarrierSpec, BridgeStubSpec, ExperimentalLawSpec,
            ProvisionalCarrier, BridgeStub, ExperimentalLaw,
            LawValidationResult, StableCarrier, BootstrappingCycleResult,
            CarrierReadinessReport, StubCoverageReport, LawConsistencyReport,
            CarrierWitnessReport, StubWitnessReport, LawWitnessReport,
            CycleWitnessReport, RegimeBootstrappingWitness,
            RegimeBootstrappingAnalyzer, RegimeBootstrappingCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.regime_bootstrapping.theorems import (
            TheoremStatus, TheoremKind, TheoremProof,
            BootstrappingCompletenessTheorem, DomainCoverageTheorem,
            TypeConstructorSoundnessTheorem, ObstructionResolutionTheorem,
            RegimeUniquenessTheorem, BootstrappingTheoremRegistry,
        )
        for _cls in [
            TheoremStatus, TheoremKind, TheoremProof,
            BootstrappingCompletenessTheorem, DomainCoverageTheorem,
            TypeConstructorSoundnessTheorem, ObstructionResolutionTheorem,
            RegimeUniquenessTheorem, BootstrappingTheoremRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.regime_bootstrapping.type_constructors import (
            TypeConstructorSearch, FunctorSpecBuilder,
            TypeConstructorValidator, TypeConstructorRunner,
        )
        for _cls in [
            TypeConstructorSearch, FunctorSpecBuilder,
            TypeConstructorValidator, TypeConstructorRunner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # regimes
    try:
        from jugeo.ideation.regimes import (
            RegimeKind, RegimeProposal, IdeationRegime, RegimeTransition,
            RegimeEvaluation, RegimeHistoryEntry, RegimePolicy, RegimeCatalog,
            RegimeEvaluator, RegimeSelector, RegimeHistory,
            RegimeBootstrapper, RegimeSerializer, RegimeDiagnostics,
        )
        for _cls in [
            RegimeKind, RegimeProposal, IdeationRegime, RegimeTransition,
            RegimeEvaluation, RegimeHistoryEntry, RegimePolicy, RegimeCatalog,
            RegimeEvaluator, RegimeSelector, RegimeHistory,
            RegimeBootstrapper, RegimeSerializer, RegimeDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # research_assistance
    try:
        from jugeo.ideation.research_assistance.algorithms import (
            ResearchAssistanceAlgorithm, BreadthFirstProofSearch,
            BestFirstProofSearch, LemmaRetrievalAlgorithm,
            ConjectureRankingAlgorithm, OracleQueryOptimizer,
        )
        for _cls in [
            ResearchAssistanceAlgorithm, BreadthFirstProofSearch,
            BestFirstProofSearch, LemmaRetrievalAlgorithm,
            ConjectureRankingAlgorithm, OracleQueryOptimizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.research_assistance.conjecture_generation import (
            PatternAnalyzer, ConjectureEvaluator, ConjecturePruner,
            GenerationHistory, ConjectureGenerator,
        )
        for _cls in [
            PatternAnalyzer, ConjectureEvaluator, ConjecturePruner,
            GenerationHistory, ConjectureGenerator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.research_assistance.integration import (
            that, ResearchEventBus, SessionPersistence, VerifierBridge,
            CopilotResearchAdvisor, ResearchAssistanceIntegration,
        )
        for _cls in [
            that, ResearchEventBus, SessionPersistence, VerifierBridge,
            CopilotResearchAdvisor, ResearchAssistanceIntegration,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.research_assistance.lemma_mining import (
            MiningConfig, PatternExtractor, RelevanceScorer, LemmaArchive,
            LemmaMiner,
        )
        for _cls in [
            MiningConfig, PatternExtractor, RelevanceScorer, LemmaArchive,
            LemmaMiner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.research_assistance.manifest import (
            OracleType, AssistanceCapability, OracleDescriptor,
            ResearchAssistanceManifest, ManifestValidator, ManifestRegistry,
        )
        for _cls in [
            OracleType, AssistanceCapability, OracleDescriptor,
            ResearchAssistanceManifest, ManifestValidator, ManifestRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.research_assistance.models import (
            VerificationStatus, LemmaSource, ConjectureStatus, SessionStatus,
            LemmaCandidate, ConjectureRecord, ProofSuggestion,
            ResearchContext, OracleQuery, OracleResponse, VerificationRecord,
            ResearchSession,
        )
        for _cls in [
            VerificationStatus, LemmaSource, ConjectureStatus, SessionStatus,
            LemmaCandidate, ConjectureRecord, ProofSuggestion,
            ResearchContext, OracleQuery, OracleResponse, VerificationRecord,
            ResearchSession,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.research_assistance.oracle_interface import (
            OraclePolicy, OracleAuditLog, MockOracle,
            ControlledOracleProtocol, CopilotOracle,
        )
        for _cls in [
            OraclePolicy, OracleAuditLog, MockOracle,
            ControlledOracleProtocol, CopilotOracle,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.research_assistance.proof_suggestion import (
            TacticLibrary, GoalAnalyzer, SuggestionFilter, ProofStateTracker,
            ProofSuggestionEngine,
        )
        for _cls in [
            TacticLibrary, GoalAnalyzer, SuggestionFilter, ProofStateTracker,
            ProofSuggestionEngine,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.research_assistance.theorems import (
            TheoremKind, ProofStrategy, ObstructionClass, TheoremDependency,
            ProofSketch, ResearchAssistanceTheoremSchema, FalsificationResult,
            FalsificationTest, TautologyTest, CircularDependencyTest,
            ScopeOverflowTest, NoveltySanityTest, CorrectnessFloorTest,
            ObstructionReductionTest, FormalStatementSyntaxTest,
            DependencyStrengthTest, FalsificationSuite,
            ResearchAssistanceTheoremRegistry,
        )
        for _cls in [
            TheoremKind, ProofStrategy, ObstructionClass, TheoremDependency,
            ProofSketch, ResearchAssistanceTheoremSchema, FalsificationResult,
            FalsificationTest, TautologyTest, CircularDependencyTest,
            ScopeOverflowTest, NoveltySanityTest, CorrectnessFloorTest,
            ObstructionReductionTest, FormalStatementSyntaxTest,
            DependencyStrengthTest, FalsificationSuite,
            ResearchAssistanceTheoremRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # scheduling
    try:
        from jugeo.ideation.scheduling import (
            ExhaustionPolicy, SchedulePhase, IdeationSchedule,
            SchedulingPolicy, TheoremGrowthEconomics, ExplorationBudget,
            ExploitationPrioritizer, IdeationClock, ScheduleHistory,
            ScheduleOptimizer, IdeationScheduler, ScheduleDiagnostics,
        )
        for _cls in [
            ExhaustionPolicy, SchedulePhase, IdeationSchedule,
            SchedulingPolicy, TheoremGrowthEconomics, ExplorationBudget,
            ExploitationPrioritizer, IdeationClock, ScheduleHistory,
            ScheduleOptimizer, IdeationScheduler, ScheduleDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # semantic_futures
    try:
        from jugeo.ideation.semantic_futures.algorithms import (
            SearchConfig, SearchResult, FutureSearchAlgorithm,
            BeamSearchFutures, GreedyFutureSearch, DiversifiedSearch,
            ArchiveBasedSearch, PurposeDirectedSearch, SearchAlgorithmFactory,
            SearchComparator,
        )
        for _cls in [
            SearchConfig, SearchResult, FutureSearchAlgorithm,
            BeamSearchFutures, GreedyFutureSearch, DiversifiedSearch,
            ArchiveBasedSearch, PurposeDirectedSearch, SearchAlgorithmFactory,
            SearchComparator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.budget_allocation import (
            AllocationStrategy, BudgetConstraint, BudgetTracker,
            CostEstimator, BudgetAllocator,
        )
        for _cls in [
            AllocationStrategy, BudgetConstraint, BudgetTracker,
            CostEstimator, BudgetAllocator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.future_generation import (
            GenerationStrategy, GenerationConfig, SemanticOperator,
            FutureGenerator, FutureExpander, FuturePruner,
        )
        for _cls in [
            GenerationStrategy, GenerationConfig, SemanticOperator,
            FutureGenerator, FutureExpander, FuturePruner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.idea_objects_future_attainability import (
            IdeaType, IdeaAttainabilityFactors, IdeaObject, IdeaPortfolio,
            AttainabilityEstimator, LeveragePredictor, SupportScopeExpander,
            IdeaObjectsFutureAttainabilityAnalyzer,
            IdeaObjectsFutureAttainabilityWitness,
            IdeaObjectsFutureAttainabilityCoordinator,
        )
        for _cls in [
            IdeaType, IdeaAttainabilityFactors, IdeaObject, IdeaPortfolio,
            AttainabilityEstimator, LeveragePredictor, SupportScopeExpander,
            IdeaObjectsFutureAttainabilityAnalyzer,
            IdeaObjectsFutureAttainabilityWitness,
            IdeaObjectsFutureAttainabilityCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
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
        for _cls in [
            ObstructionRankMap, OverlapEntropyReport, BottleneckCoordinate,
            BottleneckGeometry, ObstructionRankComputer,
            OverlapEntropyComputer, BottleneckGeometryDetector,
            IdeationSignalsObstructionRankAnalyzer,
            IdeationSignalsObstructionRankWitness,
            IdeationSignalsObstructionRankCoordinator, TrustTier,
            SignalJudgment, IdeationSignal, FutureDirection,
            ObstructionRecordStub, ExtractionConfig, ObstructionRanking,
            SignalExtractor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.ideation_signals_obstruction_rank_NEW import (
            TrustTier, SignalType, RankingCriterion, DirectionPriority,
        )
        for _cls in [
            TrustTier, SignalType, RankingCriterion, DirectionPriority,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.integration import (
            EventKind, FutureEvent, EventSubscription, FuturesEventBus,
            IntegrationStatus, ComponentHealth, IntegrationHealthCheck,
            CopilotFuturesAdvisor, SemanticFuturesIntegration,
        )
        for _cls in [
            EventKind, FutureEvent, EventSubscription, FuturesEventBus,
            IntegrationStatus, ComponentHealth, IntegrationHealthCheck,
            CopilotFuturesAdvisor, SemanticFuturesIntegration,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.manifest import (
            FutureSpaceDescriptor, SemanticFuturesManifest, ManifestValidator,
            ManifestRegistry,
        )
        for _cls in [
            FutureSpaceDescriptor, SemanticFuturesManifest, ManifestValidator,
            ManifestRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.models import (
            _DynamicFutureTag, _FutureTagMeta, FutureTag, FutureState,
            PurposeFunction, SemanticFuture, FutureValuation, IdeationState,
            FutureFilter, FutureRanker, FutureComparator,
        )
        for _cls in [
            _DynamicFutureTag, _FutureTagMeta, FutureTag, FutureState,
            PurposeFunction, SemanticFuture, FutureValuation, IdeationState,
            FutureFilter, FutureRanker, FutureComparator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.pre_implementation_valuation_expec import (
            ValuationTier, CostComponents, ObstructionReductionEstimate,
            PreImplementationValue, ValueDistribution, CostEstimator,
            ObstructionReductionForecaster, ValuationTierClassifier,
            PreImplementationValuationAnalyzer,
            PreImplementationValuationWitness,
            PreImplementationValuationCoordinator,
        )
        for _cls in [
            ValuationTier, CostComponents, ObstructionReductionEstimate,
            PreImplementationValue, ValueDistribution, CostEstimator,
            ObstructionReductionForecaster, ValuationTierClassifier,
            PreImplementationValuationAnalyzer,
            PreImplementationValuationWitness,
            PreImplementationValuationCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.purpose_alignment import (
            AlignmentCriterion, AlignmentScore, PurposeDecomposer,
            UtilityAggregator, AlignmentCache, AlignmentReport,
            PurposeAligner,
        )
        for _cls in [
            AlignmentCriterion, AlignmentScore, PurposeDecomposer,
            UtilityAggregator, AlignmentCache, AlignmentReport,
            PurposeAligner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.reachability import (
            ReachabilityModelType, ReachabilityModel, ReachabilityCache,
            BridgeProbability, TransitionGraph, PathFinder,
            ReachabilityEstimator,
        )
        for _cls in [
            ReachabilityModelType, ReachabilityModel, ReachabilityCache,
            BridgeProbability, TransitionGraph, PathFinder,
            ReachabilityEstimator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.semantic_futures.theorems import (
            TheoremDifficulty, TheoremHypothesis, TheoremStatement,
            TheoremCatalog, TheoremVerifier,
        )
        for _cls in [
            TheoremDifficulty, TheoremHypothesis, TheoremStatement,
            TheoremCatalog, TheoremVerifier,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # synthesis_frontier
    try:
        from jugeo.ideation.synthesis_frontier.code_orchestrator import (
            CodeTarget, CodeLanguage, CodeSpec, CodePlan, TheoremToCodeMapper,
            CodeOrchestrator,
        )
        for _cls in [
            CodeTarget, CodeLanguage, CodeSpec, CodePlan, TheoremToCodeMapper,
            CodeOrchestrator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.synthesis_frontier.judgment_geometry_bridge import (
            CoordinateNamingConvention, JudgmentCoordinate, JudgmentEncoding,
            EvidenceSection, ObligationExtractor, PredicateExtractor,
            ContextExtractor, JudgmentGeometryBridge, JudgmentSheafValidator,
            SheafInjector,
        )
        for _cls in [
            CoordinateNamingConvention, JudgmentCoordinate, JudgmentEncoding,
            EvidenceSection, ObligationExtractor, PredicateExtractor,
            ContextExtractor, JudgmentGeometryBridge, JudgmentSheafValidator,
            SheafInjector,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.synthesis_frontier.llm_judge import (
            JudgeMode, JudgeConfig, JudgeVerdict, HeuristicJudge, LLMJudge,
            SynthesisJudge,
        )
        for _cls in [
            JudgeMode, JudgeConfig, JudgeVerdict, HeuristicJudge, LLMJudge,
            SynthesisJudge,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.synthesis_frontier.metaphor_finder import (
            MetaphorKind, MetaphorPattern, MetaphorCandidate, PatternMatcher,
            MetaphorFinder,
        )
        for _cls in [
            MetaphorKind, MetaphorPattern, MetaphorCandidate, PatternMatcher,
            MetaphorFinder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.synthesis_frontier.models import (
            DomainArea, PropositionKind, PropositionRecord, MetaphorLink,
            FieldNode, SynthesisPair, TournamentState,
        )
        for _cls in [
            DomainArea, PropositionKind, PropositionRecord, MetaphorLink,
            FieldNode, SynthesisPair, TournamentState,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.synthesis_frontier.paper_generator import (
            PaperSection, TheoremEnvironment, LatexTheoremBlock, PaperStats,
            MathPaper, PaperOutline, SectionWriter, PaperGenerator,
        )
        for _cls in [
            PaperSection, TheoremEnvironment, LatexTheoremBlock, PaperStats,
            MathPaper, PaperOutline, SectionWriter, PaperGenerator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.synthesis_frontier.pipeline import (
            PipelineConfig, PipelineProgress, PipelineResult,
            CheckpointManager, SynthesisFrontierPipeline,
        )
        for _cls in [
            PipelineConfig, PipelineProgress, PipelineResult,
            CheckpointManager, SynthesisFrontierPipeline,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.synthesis_frontier.textbook_generator import (
            TextbookGenerator,
        )
        for _cls in [
            TextbookGenerator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.synthesis_frontier.tournament import (
            PairingStrategy, MergeResult, RoundResult, TournamentConfig,
            BinaryTournamentFrontier, FieldMerger, PairSelector,
            TournamentRound, Tournament, JudgmentCriteria,
        )
        for _cls in [
            PairingStrategy, MergeResult, RoundResult, TournamentConfig,
            BinaryTournamentFrontier, FieldMerger, PairSelector,
            TournamentRound, Tournament, JudgmentCriteria,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # theorem_ecologies
    try:
        from jugeo.ideation.theorem_ecologies.algorithms import (
            EcologicalAlgorithm, EcologyManager, PortfolioOptimizer,
            EcologicalDynamicsSimulator, EcologyDiagnostics, EcologyHistory,
            EcologyBenchmark,
        )
        for _cls in [
            EcologicalAlgorithm, EcologyManager, PortfolioOptimizer,
            EcologicalDynamicsSimulator, EcologyDiagnostics, EcologyHistory,
            EcologyBenchmark,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.compounding import (
            CompoundingConfig, CompoundingDetector, SynergyEstimator,
            CompoundBuilder, AmplificationCalculator, CompoundingEngine,
        )
        for _cls in [
            CompoundingConfig, CompoundingDetector, SynergyEstimator,
            CompoundBuilder, AmplificationCalculator, CompoundingEngine,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.ecological_metrics_reuse_breadth_c import (
            TrustTier, MetricJudgment, UsageRecord, EcologicalMetric,
            BreadthSnapshot, CitationEdge, DepthResult, DomainSpec,
            CoverageResult, EcologyScore, GapReport, ReuseBreadth,
            CitationDepth, TheoreticalCoverage, MetricJudgment8, EcologyScore,
        )
        for _cls in [
            TrustTier, MetricJudgment, UsageRecord, EcologicalMetric,
            BreadthSnapshot, CitationEdge, DepthResult, DomainSpec,
            CoverageResult, EcologyScore, GapReport, ReuseBreadth,
            CitationDepth, TheoreticalCoverage, MetricJudgment8, EcologyScore,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.ecology_modeling import (
            EcologyConfig, TheoremNode, EcologyBuilder, DependencyMapper,
            HealthCalculator, DiversityAnalyzer, EcologyModeler,
        )
        for _cls in [
            EcologyConfig, TheoremNode, EcologyBuilder, DependencyMapper,
            HealthCalculator, DiversityAnalyzer, EcologyModeler,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.implementation_consequences import (
            TrustTier, ConsequenceJudgment, EcologyImplementationConsequence,
            EcologyConstraint, ConstraintCheckResult, EcologyDesignRule,
            EcologyViolation, EcologyModuleProfile, EcologyPolicyRecord,
            EcologyPolicy, EcologyCompliance,
        )
        for _cls in [
            TrustTier, ConsequenceJudgment, EcologyImplementationConsequence,
            EcologyConstraint, ConstraintCheckResult, EcologyDesignRule,
            EcologyViolation, EcologyModuleProfile, EcologyPolicyRecord,
            EcologyPolicy, EcologyCompliance,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.implementation_consequences_new import (
            TrustTier, ConsequenceJudgment,
        )
        for _cls in [
            TrustTier, ConsequenceJudgment,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.integration import (
            IdeaEcologyLinker, TrustEcologyFilter, NoveltyEcologyScorer,
            EcologyIdeaGenerator, IntegratedEcologyPipeline,
        )
        for _cls in [
            IdeaEcologyLinker, TrustEcologyFilter, NoveltyEcologyScorer,
            EcologyIdeaGenerator, IntegratedEcologyPipeline,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.lemma_portfolios import (
            PortfolioConfig, LemmaUtilityEstimator, ReuseTracker,
            CoverageCalculator, PortfolioRebalancer, LemmaPortfolioManager,
        )
        for _cls in [
            PortfolioConfig, LemmaUtilityEstimator, ReuseTracker,
            CoverageCalculator, PortfolioRebalancer, LemmaPortfolioManager,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.lemma_portfolios_coordinated_famil import (
            LemmaStatus, CompletionStatus, PortfolioConfig, LemmaRecord,
            TheoremNode, PortfolioUpdateResult, CompletionCheckResult,
            RetirementResult, PortfolioCycleResult, CoherenceReport,
            RedundancyReport, GapCoverageReport, CreationWitnessReport,
            AdditionWitnessReport, CompletionWitnessReport, LemmaPortfolio,
            LemmaPortfoliosCoordinator, LemmaPortfoliosAnalyzer,
            LemmaPortfoliosWitness,
        )
        for _cls in [
            LemmaStatus, CompletionStatus, PortfolioConfig, LemmaRecord,
            TheoremNode, PortfolioUpdateResult, CompletionCheckResult,
            RetirementResult, PortfolioCycleResult, CoherenceReport,
            RedundancyReport, GapCoverageReport, CreationWitnessReport,
            AdditionWitnessReport, CompletionWitnessReport, LemmaPortfolio,
            LemmaPortfoliosCoordinator, LemmaPortfoliosAnalyzer,
            LemmaPortfoliosWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.manifest import (
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        )
        for _cls in [
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.models import (
            EcologyHealth, DynamicType, TheoremEcology, LemmaPortfolio,
            CompoundingEffect, EcologicalDynamic, PortfolioOptimization,
        )
        for _cls in [
            EcologyHealth, DynamicType, TheoremEcology, LemmaPortfolio,
            CompoundingEffect, EcologicalDynamic, PortfolioOptimization,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.theorem_ecologies_from_local_closu import (
            TheoremRole, ClosureStatus, EcologyConfig, TheoremNode,
            ClosureCheckResult, EnvironmentChangeReport, EcologyCycleResult,
            ClosurePropertyReport, DependencyGraphReport,
            ReasoningReachReport, EcologyWitnessReport,
            ExpansionWitnessReport, ClosureWitnessReport, TheoremEcology,
            TheoremEcologiesCoordinator, TheoremEcologiesAnalyzer,
            TheoremEcologiesWitness,
        )
        for _cls in [
            TheoremRole, ClosureStatus, EcologyConfig, TheoremNode,
            ClosureCheckResult, EnvironmentChangeReport, EcologyCycleResult,
            ClosurePropertyReport, DependencyGraphReport,
            ReasoningReachReport, EcologyWitnessReport,
            ExpansionWitnessReport, ClosureWitnessReport, TheoremEcology,
            TheoremEcologiesCoordinator, TheoremEcologiesAnalyzer,
            TheoremEcologiesWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_ecologies.theorems import (
            TheoremStatus, TheoremType, EcologyTheorem, TheoremRegistry,
            TheoremVerifier, TheoremApplications, TheoremCatalog,
        )
        for _cls in [
            TheoremStatus, TheoremType, EcologyTheorem, TheoremRegistry,
            TheoremVerifier, TheoremApplications, TheoremCatalog,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # theorem_economics
    try:
        from jugeo.ideation.theorem_economics.algorithms import (
            EconomicAlgorithm, _BudgetAlgorithmBase, WaterfillingAlgorithm,
            LagrangianOptimizer, PortfolioOptimizer,
            YieldMaximizationAlgorithm, CompoundingOptimizer,
            AlgorithmRegistry,
        )
        for _cls in [
            EconomicAlgorithm, _BudgetAlgorithmBase, WaterfillingAlgorithm,
            LagrangianOptimizer, PortfolioOptimizer,
            YieldMaximizationAlgorithm, CompoundingOptimizer,
            AlgorithmRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.compounding import (
            CompoundingFactor, TheoremChainTracer, CompoundingModel,
            CompoundInterestAnalogy, CompoundingPortfolioAnalyzer,
        )
        for _cls in [
            CompoundingFactor, TheoremChainTracer, CompoundingModel,
            CompoundInterestAnalogy, CompoundingPortfolioAnalyzer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.integration import (
            EconomicObstruction, EconomicJudgmentBridge,
            TheoremEconomicsIntegration, EconomicVerificationPipeline,
            SchedulerEconomicsBridge, CopilotEconomicsAdvisor,
            EconomicEventBus, PortfolioReporter,
        )
        for _cls in [
            EconomicObstruction, EconomicJudgmentBridge,
            TheoremEconomicsIntegration, EconomicVerificationPipeline,
            SchedulerEconomicsBridge, CopilotEconomicsAdvisor,
            EconomicEventBus, PortfolioReporter,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.investment_scheduling import (
            GreedyInvestmentAllocator, LagrangianRelaxationAllocator,
            AdaptiveScheduler, InvestmentScheduler, ScheduleEvaluator,
        )
        for _cls in [
            GreedyInvestmentAllocator, LagrangianRelaxationAllocator,
            AdaptiveScheduler, InvestmentScheduler, ScheduleEvaluator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.manifest import (
            YieldType, AssumptionCategory, ValidationStatus,
            YieldModelDescriptor, EconomicAssumption,
            TheoremEconomicsManifest, ManifestValidator, ManifestRegistry,
        )
        for _cls in [
            YieldType, AssumptionCategory, ValidationStatus,
            YieldModelDescriptor, EconomicAssumption,
            TheoremEconomicsManifest, ManifestValidator, ManifestRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.marginal_analysis import (
            MarginalValueCurve, EquimarginalPrinciple, MarginalAnalyzer,
            MarginalReturnDiminishment,
        )
        for _cls in [
            MarginalValueCurve, EquimarginalPrinciple, MarginalAnalyzer,
            MarginalReturnDiminishment,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.models import (
            TheoremYieldModel, MarginalValue, InvestmentSchedule,
            CompoundingEffect, TheoremPortfolioValue, RegimeEconomics,
            BudgetAllocation, YieldForecast, EconomicEquilibrium,
            LinearYieldModel,
        )
        for _cls in [
            TheoremYieldModel, MarginalValue, InvestmentSchedule,
            CompoundingEffect, TheoremPortfolioValue, RegimeEconomics,
            BudgetAllocation, YieldForecast, EconomicEquilibrium,
            LinearYieldModel,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.scheduling_principle import (
            SchedulingConfig, EffortAllocation, SchedulingSignal,
            AllocationHistory, SchedulingPrincipleAnalyzer,
            SchedulingPrincipleWitness, SchedulingPrincipleCoordinator,
        )
        for _cls in [
            SchedulingConfig, EffortAllocation, SchedulingSignal,
            AllocationHistory, SchedulingPrincipleAnalyzer,
            SchedulingPrincipleWitness, SchedulingPrincipleCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.the_growth_signal import (
            GrowthSignalConfig, TheoremROIRecord, CodeROIRecord,
            GrowthSignalReading, TheGrowthSignalAnalyzer,
            TheGrowthSignalWitness, TheGrowthSignalCoordinator,
        )
        for _cls in [
            GrowthSignalConfig, TheoremROIRecord, CodeROIRecord,
            GrowthSignalReading, TheGrowthSignalAnalyzer,
            TheGrowthSignalWitness, TheGrowthSignalCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.theorems import (
            TheoremStatus, ProofMethod, EconomicTheorem, TheoremCatalog,
            TheoremVerifier, TheoremDatabase, TheoremProofChain,
        )
        for _cls in [
            TheoremStatus, ProofMethod, EconomicTheorem, TheoremCatalog,
            TheoremVerifier, TheoremDatabase, TheoremProofChain,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.when_coding_should_stop_and_theory import (
            WhenCodingShouldStopConfig, ObstructionDensityMeasure,
            RepairAttemptRecord, SwitchingDecision,
            WhenCodingShouldStopAnalyzer, WhenCodingShouldStopWitness,
            WhenCodingShouldStopCoordinator,
        )
        for _cls in [
            WhenCodingShouldStopConfig, ObstructionDensityMeasure,
            RepairAttemptRecord, SwitchingDecision,
            WhenCodingShouldStopAnalyzer, WhenCodingShouldStopWitness,
            WhenCodingShouldStopCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theorem_economics.yield_modeling import (
            YieldCurve, SaturationEstimator, GrowthRateEstimator,
            YieldModeler, YieldModelValidator, YieldModelComparator,
        )
        for _cls in [
            YieldCurve, SaturationEstimator, GrowthRateEstimator,
            YieldModeler, YieldModelValidator, YieldModelComparator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # theory_navigation
    try:
        from jugeo.ideation.theory_navigation.algorithms import (
            NavigationAlgorithm, NavigationHistory, TheoryNavigator,
            MapBuilder, NavigationOptimizer, NavigationBenchmark,
            NavigationDiagnostics,
        )
        for _cls in [
            NavigationAlgorithm, NavigationHistory, TheoryNavigator,
            MapBuilder, NavigationOptimizer, NavigationBenchmark,
            NavigationDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.integration import (
            IdeaNavigator, FederationNavigator, NoveltyNavigator,
            NavigationFederator, TrustAwareNavigator,
            IntegratedNavigationPipeline,
        )
        for _cls in [
            IdeaNavigator, FederationNavigator, NoveltyNavigator,
            NavigationFederator, TrustAwareNavigator,
            IntegratedNavigationPipeline,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.manifest import (
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        )
        for _cls in [
            PackageCapability, PackageManifest, ManifestValidator,
            PackageRegistry, CapabilityQuery, ManifestSerializer,
            ManifestDiagnostics,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.mathematical_areas_as_candidate_se import (
            MathematicalArea, SemanticRegimeProfile, RegimeCompatibility,
            AreaSelectionResult, MathAreasSemanticRegimesAnalyzer,
            MathAreasSemanticRegimesWitness,
            MathAreasSemanticRegimesCoordinator,
        )
        for _cls in [
            MathematicalArea, SemanticRegimeProfile, RegimeCompatibility,
            AreaSelectionResult, MathAreasSemanticRegimesAnalyzer,
            MathAreasSemanticRegimesWitness,
            MathAreasSemanticRegimesCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.models import (
            NodeMaturity, NavigationStrategy, PurposeCondition, TheoryNode,
            NavigationPath, NavigationState, TheorySpace,
        )
        for _cls in [
            NodeMaturity, NavigationStrategy, PurposeCondition, TheoryNode,
            NavigationPath, NavigationState, TheorySpace,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.path_finding import (
            SearchNode, PathFinder, DiversePathFinder, PurposeGuidedSearch,
            PathEvaluator, PathCache,
        )
        for _cls in [
            SearchNode, PathFinder, DiversePathFinder, PurposeGuidedSearch,
            PathEvaluator, PathCache,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.purpose_conditioning import (
            PurposeVector, PurposeWeightMap, PurposeConditioner,
            HeuristicComputer, PurposeAligner, PurposeDriftDetector,
        )
        for _cls in [
            PurposeVector, PurposeWeightMap, PurposeConditioner,
            HeuristicComputer, PurposeAligner, PurposeDriftDetector,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.purpose_conditioning_target_obstru import (
            PurposeConditioningConfig, ObstructionTarget, PurposeVector,
            ConditionedPurpose, PurposeConditioningTargetAnalyzer,
            PurposeConditioningTargetWitness, PurposeConditioningCoordinator,
        )
        for _cls in [
            PurposeConditioningConfig, ObstructionTarget, PurposeVector,
            ConditionedPurpose, PurposeConditioningTargetAnalyzer,
            PurposeConditioningTargetWitness, PurposeConditioningCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.space_construction import (
            SpaceConstructionConfig, NodeExtractor, EdgeBuilder, SpaceIndexer,
            SpaceConstructor, IncrementalSpaceUpdater,
        )
        for _cls in [
            SpaceConstructionConfig, NodeExtractor, EdgeBuilder, SpaceIndexer,
            SpaceConstructor, IncrementalSpaceUpdater,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.theorems import (
            TrustTier, NavigationJudgment, NavigationTheorem,
            NavigationInvariant, NavigationPath, CoverageRecord,
            OptimalityCheck, NavigationRecord, NavigationStrategyProfile,
            PathOptimality, NavigationCompleteness,
            NavigationStrategyEvaluator,
        )
        for _cls in [
            TrustTier, NavigationJudgment, NavigationTheorem,
            NavigationInvariant, NavigationPath, CoverageRecord,
            OptimalityCheck, NavigationRecord, NavigationStrategyProfile,
            PathOptimality, NavigationCompleteness,
            NavigationStrategyEvaluator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.ideation.theory_navigation.theory_space_navigation_moving_ove import (
            CandidateRegimeConfig, CandidateRegime, RegimeTransition,
            NavigationState, TheorySpaceNavigationAnalyzer,
            TheorySpaceNavigationWitness, TheorySpaceNavigationCoordinator,
        )
        for _cls in [
            CandidateRegimeConfig, CandidateRegime, RegimeTransition,
            NavigationState, TheorySpaceNavigationAnalyzer,
            TheorySpaceNavigationWitness, TheorySpaceNavigationCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    return registry


def _collect_foundations_classes():
    """Collect foundations classes."""
    registry = {}
    try:
        from jugeo.foundations.formal_core.models import (
            ObjectData, MorphismData, CategoryStructure, FormalSite,
            TrustAlgebraAxioms, ObstructionTheory, DescentData,
        )
        for cls in [
            ObjectData, MorphismData, CategoryStructure, FormalSite,
            TrustAlgebraAxioms, ObstructionTheory, DescentData,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.formal_core.algorithms import (
            TrustAlgebraVerifier, SiteCompletionAlgorithm,
            ObstructionVanishingAlgorithm,
        )
        for cls in [TrustAlgebraVerifier, SiteCompletionAlgorithm,
                    ObstructionVanishingAlgorithm]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.formal_core.site_definition import (
            FormalJudgmentObject, Sieve, GrothendieckTopology as FGT,
            SheafOnSite, SiteCoherenceChecker, ProgrammaticJudgmentSite,
        )
        for cls in [FormalJudgmentObject, Sieve, FGT, SheafOnSite,
                    SiteCoherenceChecker, ProgrammaticJudgmentSite]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.formal_core.a_site_for_programmatic_judgment import (
            ContextKind, MorphismKind as FMK, CoverAxiomStatus,
            SiteCoordinate, ContextMorphism, CoveringFamily as FCF,
            CoveringRelation, GrothendieckSite, JudgmentTuple,
            JudgmentSection, JudgmentSheaf, JudgmentSite,
            ASiteProgrammaticJudgmentWitness,
            ASiteProgrammaticJudgmentCoordinator,
            ASiteProgrammaticJudgmentAnalyzer,
        )
        for cls in [
            ContextKind, FMK, CoverAxiomStatus, SiteCoordinate,
            ContextMorphism, FCF, CoveringRelation, GrothendieckSite,
            JudgmentTuple, JudgmentSection, JudgmentSheaf, JudgmentSite,
            ASiteProgrammaticJudgmentWitness,
            ASiteProgrammaticJudgmentCoordinator,
            ASiteProgrammaticJudgmentAnalyzer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # descent_locality
    try:
        from jugeo.foundations.descent_locality import DescentLocality
        registry["DescentLocality"] = DescentLocality
    except Exception:
        pass
    try:
        from jugeo.foundations.descent_locality.models import (
            CompatibilityStatus, TransportCoherence, SectionKind,
            ObstructionDegree, LocalityPrinciple, TransportData,
            GluingData as DLGluingData, ObstructionClass, DescentDatum,
        )
        for cls in [
            CompatibilityStatus, TransportCoherence, SectionKind,
            ObstructionDegree, LocalityPrinciple, TransportData,
            DLGluingData, ObstructionClass, DescentDatum,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # trust_certificates
    try:
        from jugeo.foundations.trust_certificates.models import (
            TrustAlgebraModel, ProvenanceModel, EvidenceModel,
            CertificateModel,
        )
        for cls in [TrustAlgebraModel, ProvenanceModel, EvidenceModel,
                    CertificateModel]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.trust_certificates import TrustCertificate
        registry["TrustCertificate"] = TrustCertificate
    except Exception:
        pass
    # type_objects
    try:
        from jugeo.foundations.type_objects.models import (
            CarrierKind, TypeTrustAnnotation, TypeCarrier,
            TransportMap, GluingLaw, JuGeoType,
        )
        for cls in [CarrierKind, TypeTrustAnnotation, TypeCarrier,
                    TransportMap, GluingLaw, JuGeoType]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # judgment_products
    try:
        from jugeo.foundations.judgment_products.models import (
            ProductStatus, ProductKind, ProjectionMode, JudgmentProduct,
            SemanticProduct, LocalJudgmentSection, ComparisonMap,
            ExplanationProjection,
        )
        for cls in [
            ProductStatus, ProductKind, ProjectionMode, JudgmentProduct,
            SemanticProduct, LocalJudgmentSection, ComparisonMap,
            ExplanationProjection,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # oracle_federation
    try:
        from jugeo.foundations.oracle_federation.models import (
            WitnessKind, MergeStrategy, OracleModel,
            SolverFederationModel, RuntimeWitnessModel,
            JurisdictionModel, OracleChannelConfig, FederationConfig,
            WitnessCollectionConfig, ModelRegistry,
        )
        for cls in [
            WitnessKind, MergeStrategy, OracleModel,
            SolverFederationModel, RuntimeWitnessModel,
            JurisdictionModel, OracleChannelConfig, FederationConfig,
            WitnessCollectionConfig, ModelRegistry,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # project_hypercovers
    try:
        from jugeo.foundations.project_hypercovers.models import (
            ProjectKind, CoverStrategy, FleetStatus,
            DecompositionStatus, PatchRole, CoordinateMorphism as PHCoordMorph,
            OverlapCell, CohomologyClass as PHCohomClass, ProjectSite,
            ModuleCover, FleetMember as PHFleetMember,
            HypercoverDecomposition,
        )
        for cls in [
            ProjectKind, CoverStrategy, FleetStatus,
            DecompositionStatus, PatchRole, PHCoordMorph,
            OverlapCell, PHCohomClass, ProjectSite, ModuleCover,
            PHFleetMember, HypercoverDecomposition,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.project_hypercovers.from_single_artifact_reasoning_to import (
            CocycleStatus,
        )
        registry["CocycleStatus"] = CocycleStatus
    except Exception:
        pass
    # formal_core additional modules
    try:
        from jugeo.foundations.formal_core.integration import (
            TrustAlgebraToChannelBridge, SiteToGeometryBridge,
            ObstructionToEvidenceBridge, FormalCoreIntegration,
        )
        for cls in [TrustAlgebraToChannelBridge, SiteToGeometryBridge,
                    ObstructionToEvidenceBridge, FormalCoreIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.formal_core.manifest import (
            SectionManifest, SymbolRegistry,
        )
        for cls in [SectionManifest, SymbolRegistry]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.formal_core.theorems import (
            TheoremStatement as FC_TheoremStatement, Lemma, Corollary,
        )
        for cls in [FC_TheoremStatement, Lemma, Corollary]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.formal_core.trust_algebra import (
            AlgebraAxiom, AlgebraAxiomSet, PromotionPolicy,
            TrustCompositionLaw, TrustOrderedAlgebra,
        )
        for cls in [AlgebraAxiom, AlgebraAxiomSet, PromotionPolicy,
                    TrustCompositionLaw, TrustOrderedAlgebra]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.formal_core.obstruction_theory import (
            Cochain, CoboundaryCondition,
            CohomologicalObstructionComputer, TrustObstructionMap,
            DescentObstructionChecker,
        )
        for cls in [Cochain, CoboundaryCondition,
                    CohomologicalObstructionComputer, TrustObstructionMap,
                    DescentObstructionChecker]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # descent_locality additional
    try:
        from jugeo.foundations.descent_locality.algorithms import (
            CompatibilityChecker, ObstructionComputer, RepairFinder,
            DescentAlgorithms,
        )
        for cls in [CompatibilityChecker, ObstructionComputer, RepairFinder,
                    DescentAlgorithms]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.descent_locality.covers_and_hypercovers import (
            CoverFamily, HypercoverStructure, CoverRefinementMap,
            CanonicalCoverFactory,
        )
        for cls in [CoverFamily, HypercoverStructure, CoverRefinementMap,
                    CanonicalCoverFactory]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.descent_locality.integration import (
            DescentBridge, CoverBridge, SiteBridge,
            IntegratedResult, DescentIntegration,
        )
        for cls in [DescentBridge, CoverBridge, SiteBridge,
                    IntegratedResult, DescentIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.descent_locality.theorems import (
            HypothesisCheck, SheafAxioms, DescentTheorems,
            ObstructionTheorems,
        )
        for cls in [HypothesisCheck, SheafAxioms, DescentTheorems,
                    ObstructionTheorems]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # oracle_federation additional
    try:
        from jugeo.foundations.oracle_federation.controlled_oracles import (
            OracleProposalRecord, OracleJurisdiction,
            TrustCeilingEnforcer, OracleChannel, CopilotOracleChannel,
        )
        for cls in [OracleProposalRecord, OracleJurisdiction,
                    TrustCeilingEnforcer, OracleChannel, CopilotOracleChannel]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.oracle_federation.algorithms import (
            TrustCeilingPropagator, FederationLoadBalancer,
            WitnessCorrelator,
        )
        for cls in [TrustCeilingPropagator, FederationLoadBalancer,
                    WitnessCorrelator]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.oracle_federation.solver_federation import (
            FragmentClassification, Z3Routing, SolverFederation,
            FederationRouter,
        )
        for cls in [FragmentClassification, Z3Routing, SolverFederation,
                    FederationRouter]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.oracle_federation.runtime_witnesses import (
            HeapWitness, IdentityWitness, StackWitness,
            WitnessValidator, RuntimeWitnessCollector,
        )
        for cls in [HeapWitness, IdentityWitness, StackWitness,
                    WitnessValidator, RuntimeWitnessCollector]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.oracle_federation.integration import (
            WitnessToEvidenceAdapter, FederationPipelineAdapter,
            SiteOracleBridge, OracleFederationIntegration,
        )
        for cls in [WitnessToEvidenceAdapter, FederationPipelineAdapter,
                    SiteOracleBridge, OracleFederationIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.oracle_federation.semantic_jurisdiction import (
            CoordinateRange, JurisdictionClaim, AuthorityMapping,
            JurisdictionConflict, SemanticDomain,
            SemanticJurisdictionCoordinator, SemanticJurisdictionAnalyzer,
        )
        for cls in [CoordinateRange, JurisdictionClaim, AuthorityMapping,
                    JurisdictionConflict, SemanticDomain,
                    SemanticJurisdictionCoordinator, SemanticJurisdictionAnalyzer]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # trust_certificates additional
    try:
        from jugeo.foundations.trust_certificates.algorithms import (
            TrustResolutionAlgorithm, ProvenanceChainBuilder,
            CertificateIssuanceAlgorithm, EvidenceAggregationAlgorithm,
            TrustPathFinder, BatchCertificationPipeline,
        )
        for cls in [TrustResolutionAlgorithm, ProvenanceChainBuilder,
                    CertificateIssuanceAlgorithm, EvidenceAggregationAlgorithm,
                    TrustPathFinder, BatchCertificationPipeline]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.trust_certificates.certificates_as_faithful_projectio import (
            ManifestProjection, FaithfulnessChecker,
            CertificateProjector, ProjectionRecord, ResidualPreserver,
        )
        for cls in [ManifestProjection, FaithfulnessChecker,
                    CertificateProjector, ProjectionRecord, ResidualPreserver]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.trust_certificates.integration import (
            JudgmentBridge, GeometryBridge,
            TrustCertificatesIntegration,
        )
        for cls in [JudgmentBridge, GeometryBridge,
                    TrustCertificatesIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # type_objects additional
    try:
        from jugeo.foundations.type_objects.algorithms import (
            TypeInferenceResult, TypeCheckResult,
            GluingResult, ComparisonResult, TypeAlgorithms,
        )
        for cls in [TypeInferenceResult, TypeCheckResult,
                    GluingResult, ComparisonResult, TypeAlgorithms]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.type_objects.integration import (
            JudgmentTypeExtractor, TypeJudgmentEmbedder,
            TypeSolverBridge, TypeIntegration,
        )
        for cls in [JudgmentTypeExtractor, TypeJudgmentEmbedder,
                    TypeSolverBridge, TypeIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.type_objects.coordinates_where_context_support import (
            TypeContext, ContextualType, SupportAwareType,
            ScopeIndexedType, TypeLocalization, CoordinateTypeSystem,
        )
        for cls in [TypeContext, ContextualType, SupportAwareType,
                    ScopeIndexedType, TypeLocalization, CoordinateTypeSystem]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.type_objects.carrier_laws_transport_gluing_and import (
            CarrierLaw, TransportCoherence as TO_TransportCoherence,
            GluingCoherence, CarrierValidator, CarrierLawSystem,
        )
        for cls in [CarrierLaw, TO_TransportCoherence, GluingCoherence,
                    CarrierValidator, CarrierLawSystem]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.type_objects.from_ordinary_annotations_to_coord import (
            AnnotationInterpreter, CoordinateIndexer,
            SemanticTypeDecorator, TypeAnnotationLifter,
        )
        for cls in [AnnotationInterpreter, CoordinateIndexer,
                    SemanticTypeDecorator, TypeAnnotationLifter]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # judgment_products additional
    try:
        from jugeo.foundations.judgment_products.algorithms import (
            ProductComputationResult, DischargeAttemptResult,
            JudgmentAlgorithms,
        )
        for cls in [ProductComputationResult, DischargeAttemptResult,
                    JudgmentAlgorithms]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.judgment_products.sections_are_the_real_products_of import (
            SectionProduct, SectionFunctor, SectionComparison,
            SectionProducts,
        )
        for cls in [SectionProduct, SectionFunctor, SectionComparison,
                    SectionProducts]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.judgment_products.judgments_are_not_boolean_facts import (
            NonBooleanJudgment, StructuredJudgment,
            JudgmentComparison, JudgmentProductAlgebra,
        )
        for cls in [NonBooleanJudgment, StructuredJudgment,
                    JudgmentComparison, JudgmentProductAlgebra]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.judgment_products.comparison_maps_and_explanation_pr import (
            RefinementWitness as JP_RefinementWitness,
            EquivalenceCertificate, ComparisonMaps,
        )
        for cls in [JP_RefinementWitness, EquivalenceCertificate, ComparisonMaps]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.judgment_products.integration import (
            SectionBridge, ComparisonBridge, LocalJudgmentAdapter,
            JudgmentIntegration,
        )
        for cls in [SectionBridge, ComparisonBridge, LocalJudgmentAdapter,
                    JudgmentIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # project_hypercovers additional
    try:
        from jugeo.foundations.project_hypercovers.project_sites import (
            CoordinateRegistry, TopologyGenerator, SemanticSiteBuilder,
            ProjectSiteInspector,
        )
        for cls in [CoordinateRegistry, TopologyGenerator, SemanticSiteBuilder,
                    ProjectSiteInspector]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.project_hypercovers.module_covers import (
            CoverBuilder, OverlapComputer, CoverRefiner,
        )
        for cls in [CoverBuilder, OverlapComputer, CoverRefiner]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.foundations.project_hypercovers.integration import (
            ProjectHypercoverIntegration, ProjectHypercoverExporter,
            ProjectHypercoverImporter,
        )
        for cls in [ProjectHypercoverIntegration, ProjectHypercoverExporter,
                    ProjectHypercoverImporter]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_problem_modes_classes():
    """Collect problem_modes classes."""
    registry = {}
    # bug_detection
    try:
        from jugeo.problem_modes.bug_detection.detector import BugDetector
        from jugeo.problem_modes.bug_detection.models import (
            BugKind, BugReport, BugDetectionResult, DetectionSession,
        )
        for cls in [BugDetector, BugKind, BugReport, BugDetectionResult,
                    DetectionSession]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # specification_satisfaction
    try:
        from jugeo.problem_modes.specification_satisfaction.models import (
            SpecificationKind, WitnessStatus, GapSeverity as SSGapSev,
            SatisfactionStatus, DescentCondition, Specification,
            SatisfactionWitness, CertificateOfSatisfaction, ResidualGap,
        )
        for cls in [
            SpecificationKind, WitnessStatus, SSGapSev,
            SatisfactionStatus, DescentCondition, Specification,
            SatisfactionWitness, CertificateOfSatisfaction, ResidualGap,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # relational_refinement
    try:
        from jugeo.problem_modes.relational_refinement.models import (
            RefinementRelation, EquivalenceClass, RefinementWitness,
            RefinementOrder,
        )
        for cls in [RefinementRelation, EquivalenceClass,
                    RefinementWitness, RefinementOrder]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # repair_semantics
    try:
        from jugeo.problem_modes.repair_semantics.models import (
            CounterexampleRecord, RepairPlan, RepairFrontier as RSRepairFrontier,
            DebugStatus, DebugSession, RepairValidator, RepairStep,
        )
        for cls in [CounterexampleRecord, RepairPlan, RSRepairFrontier,
                    DebugStatus, DebugSession, RepairValidator, RepairStep]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # public_alignment
    try:
        from jugeo.problem_modes.public_alignment.models import (
            PublicClaim, HonestProjection, DocumentationSection,
            MigrationPlan,
        )
        for cls in [PublicClaim, HonestProjection, DocumentationSection,
                    MigrationPlan]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # problem_atlas
    try:
        from jugeo.problem_modes.problem_atlas.models import (
            ProblemCategory, DifficultyLevel, DecidabilityKind,
            ConjunctionMode, ProblemClass, SemanticSignature,
            EvidenceRequirement as PAEvidReq, AtlasCatalog,
        )
        for cls in [
            ProblemCategory, DifficultyLevel, DecidabilityKind,
            ConjunctionMode, ProblemClass, SemanticSignature,
            PAEvidReq, AtlasCatalog,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_python_runtime_classes():
    """Collect python_runtime classes."""
    registry = {}
    # callable_surfaces
    try:
        from jugeo.python_runtime.callable_surfaces.algorithms import (
            CallableSurfaceAnalyzer, MethodResolutionAlgorithm,
            CallCompatibilityChecker, InheritanceGraphAlgorithm,
            DecoratorAnalyzer,
        )
        for _cls in [
            CallableSurfaceAnalyzer, MethodResolutionAlgorithm,
            CallCompatibilityChecker, InheritanceGraphAlgorithm,
            DecoratorAnalyzer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.class_construction import (
            ClassBuilder, MetaclassAnalyzer, InitAnalyzer,
            ClassHierarchyTracker,
        )
        for _cls in [
            ClassBuilder, MetaclassAnalyzer, InitAnalyzer,
            ClassHierarchyTracker,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.class_objects_construction_pipelin import (
            ClassObjectsConstructionPipelineCoordinator,
            ClassObjectsConstructionPipelineAnalyzer,
            ClassObjectsConstructionPipelineWitness,
        )
        for _cls in [
            ClassObjectsConstructionPipelineCoordinator,
            ClassObjectsConstructionPipelineAnalyzer,
            ClassObjectsConstructionPipelineWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.descriptor_lookup_route_tagged_att import (
            DescriptorLookupRouteTaggedCoordinator,
            DescriptorLookupRouteTaggedAnalyzer,
            DescriptorLookupRouteTaggedWitness,
        )
        for _cls in [
            DescriptorLookupRouteTaggedCoordinator,
            DescriptorLookupRouteTaggedAnalyzer,
            DescriptorLookupRouteTaggedWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.descriptors import (
            DescriptorProtocol, DescriptorInspector, PropertyAnalyzer,
            SlotDescriptorAnalyzer, DescriptorJudgmentBuilder,
        )
        for _cls in [
            DescriptorProtocol, DescriptorInspector, PropertyAnalyzer,
            SlotDescriptorAnalyzer, DescriptorJudgmentBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.function_values_and_method_values import (
            FunctionValuesMethodValuesCoordinator,
            FunctionValuesMethodValuesAnalyzer,
            FunctionValuesMethodValuesWitness,
        )
        for _cls in [
            FunctionValuesMethodValuesCoordinator,
            FunctionValuesMethodValuesAnalyzer,
            FunctionValuesMethodValuesWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.functions import (
            AnnotationResolver, SignatureExtractor, CallableSurfaceCache,
            FunctionMorphismAnalyzer,
        )
        for _cls in [
            AnnotationResolver, SignatureExtractor, CallableSurfaceCache,
            FunctionMorphismAnalyzer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.integration import (
            CallableJudgmentEmitter, Z3CallableEncoder,
            CallableCoordinateMapper, SupportRegionBuilder,
            CopilotCallableAdvisor,
        )
        for _cls in [
            CallableJudgmentEmitter, Z3CallableEncoder,
            CallableCoordinateMapper, SupportRegionBuilder,
            CopilotCallableAdvisor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.manifest import (
            Capability, ComponentRegistration, PackageManifest,
        )
        for _cls in [
            Capability, ComponentRegistration, PackageManifest,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.method_binding import (
            MROComputer, MethodResolver, BindingConstraintChecker,
            MethodBinder,
        )
        for _cls in [
            MROComputer, MethodResolver, BindingConstraintChecker,
            MethodBinder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.models import (
            ParameterKind, ParameterSpec, DescriptorKind, CallableSurface,
            MethodBinding, DescriptorRecord, BoundMethod, ClassConstruction,
            SignatureRecord,
        )
        for _cls in [
            ParameterKind, ParameterSpec, DescriptorKind, CallableSurface,
            MethodBinding, DescriptorRecord, BoundMethod, ClassConstruction,
            SignatureRecord,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.callable_surfaces.theorems import (
            enters, TheoremKind, CallableTheorem, ArityConsistencyTheorem,
            DescriptorPriorityTheorem, MROValidityTheorem,
            BindingValidityTheorem, SurfaceCompatibilityTheorem,
            TheoremRegistry,
        )
        for _cls in [
            enters, TheoremKind, CallableTheorem, ArityConsistencyTheorem,
            DescriptorPriorityTheorem, MROValidityTheorem,
            BindingValidityTheorem, SurfaceCompatibilityTheorem,
            TheoremRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # concurrency_boundaries
    try:
        from jugeo.python_runtime.concurrency_boundaries.algorithms import (
            ConcurrencyAnalyzer, CancellationHandler, ExceptionGroupProcessor,
            BoundaryEnforcer,
        )
        for _cls in [
            ConcurrencyAnalyzer, CancellationHandler, ExceptionGroupProcessor,
            BoundaryEnforcer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.cancellation_and_exception_group_s import (
            CancellationStatus, CancellationNode, ExceptionGroupNode,
            CancellationTreeAnalyzer, ExceptionGroupAnalyzer,
            CancellationExceptionGroupSemanticsCoordinator,
        )
        for _cls in [
            CancellationStatus, CancellationNode, ExceptionGroupNode,
            CancellationTreeAnalyzer, ExceptionGroupAnalyzer,
            CancellationExceptionGroupSemanticsCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.cancellation_obstructions import (
            CancellationObstructionInjector, ObstructionPropagator,
            CancellationShield, CancellationDischarger,
        )
        for _cls in [
            CancellationObstructionInjector, ObstructionPropagator,
            CancellationShield, CancellationDischarger,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.concurrency_in_python_is_not_one_p import (
            ConcurrencyLayer, CoverageLevel, ConcurrencyRecord,
            LayerCoverageAnalyzer, PhenomenonWitness,
            ConcurrencyPythonOnePhenomenonCoordinator,
        )
        for _cls in [
            ConcurrencyLayer, CoverageLevel, ConcurrencyRecord,
            LayerCoverageAnalyzer, PhenomenonWitness,
            ConcurrencyPythonOnePhenomenonCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.exception_groups_process_boundaries import (
            ExceptionGroupProcessor, MultiObstructionRecord,
            ProcessBoundaryEnforcer, IPCMorphismBuilder,
        )
        for _cls in [
            ExceptionGroupProcessor, MultiObstructionRecord,
            ProcessBoundaryEnforcer, IPCMorphismBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.integration import (
            SupportBridge, JudgmentBridge, FleetBridge,
            ConcurrencyBoundariesIntegration,
        )
        for _cls in [
            SupportBridge, JudgmentBridge, FleetBridge,
            ConcurrencyBoundariesIntegration,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.manifest import (
            SymbolRecord, ConcurrencyBoundariesManifest, ManifestValidator,
            ManifestRegistry, TheoryAlignment,
        )
        for _cls in [
            SymbolRecord, ConcurrencyBoundariesManifest, ManifestValidator,
            ManifestRegistry, TheoryAlignment,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.models import (
            ConcurrencyRole, CancellationReason, BoundaryKind, ScopeStatus,
            TaskLocalSection, CancellationRecord, ExceptionGroupRecord,
            ProcessBoundary, ConcurrencyScope,
        )
        for _cls in [
            ConcurrencyRole, CancellationReason, BoundaryKind, ScopeStatus,
            TaskLocalSection, CancellationRecord, ExceptionGroupRecord,
            ProcessBoundary, ConcurrencyScope,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.process_boundaries_and_replicated import (
            BoundaryKind, ProcessBoundaryRecord, ReplicatedStateRecord,
            FederationBoundaryAnalyzer, ReplicatedStateAnalyzer,
            ProcessBoundariesReplicatedStateCoordinator,
        )
        for _cls in [
            BoundaryKind, ProcessBoundaryRecord, ReplicatedStateRecord,
            FederationBoundaryAnalyzer, ReplicatedStateAnalyzer,
            ProcessBoundariesReplicatedStateCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.replicated_state_obstructions import (
            ObstructionKind, ObstructionRecord, StateVector,
            ObstructionDetector, ObstructionWitness,
            ReplicatedStateObstructionsCoordinator,
        )
        for _cls in [
            ObstructionKind, ObstructionRecord, StateVector,
            ObstructionDetector, ObstructionWitness,
            ReplicatedStateObstructionsCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.task_local_context import (
            TaskSectionManager, ContextVarBridge, SectionInheritanceEngine,
            TaskSectionCleanup,
        )
        for _cls in [
            TaskSectionManager, ContextVarBridge, SectionInheritanceEngine,
            TaskSectionCleanup,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.task_local_context_as_hidden_but_s import (
            ContextVisibility, ContextBinding, HiddenInputRecord,
            ContextBindingAnalyzer, HiddenContextWitness,
            TaskLocalContextHiddenCoordinator,
        )
        for _cls in [
            ContextVisibility, ContextBinding, HiddenInputRecord,
            ContextBindingAnalyzer, HiddenContextWitness,
            TaskLocalContextHiddenCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.concurrency_boundaries.theorems import (
            TheoremRecord, TheoremProver, TheoremLibrary,
        )
        for _cls in [
            TheoremRecord, TheoremProver, TheoremLibrary,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # effects_async
    try:
        from jugeo.python_runtime.effects_async.algorithms import (
            AlgorithmSuite,
        )
        for _cls in [
            AlgorithmSuite,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.async_and_task_semantics_suspended import (
            SuspendedSection, TaskCoordinate, AwaitEdge, SuspensionPoint,
            AwaitGraph, AsyncTaskSemanticsSuspendedAnalyzer,
            AsyncTaskSemanticsSuspendedWitness,
            AsyncTaskSemanticsSuspendedCoordinator,
        )
        for _cls in [
            SuspendedSection, TaskCoordinate, AwaitEdge, SuspensionPoint,
            AwaitGraph, AsyncTaskSemanticsSuspendedAnalyzer,
            AsyncTaskSemanticsSuspendedWitness,
            AsyncTaskSemanticsSuspendedCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.async_primitives import (
            CoroutineSection, EventLoopTopology, AsyncSiteBuilder,
            TaskRegistry,
        )
        for _cls in [
            CoroutineSection, EventLoopTopology, AsyncSiteBuilder,
            TaskRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.context_managers import (
            ContextScopeManager, SectionScopeStack, AsyncContextScope,
            ContextCoveringBuilder,
        )
        for _cls in [
            ContextScopeManager, SectionScopeStack, AsyncContextScope,
            ContextCoveringBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.context_managers_temporal_obligati import (
            TemporalObligation, ObligationEdge, ObligationViolation,
            ObligationGraph, ContextManagersTemporalObligationsCoordinator,
            ContextManagersTemporalObligationsAnalyzer,
            ContextManagersTemporalObligationsWitness,
        )
        for _cls in [
            TemporalObligation, ObligationEdge, ObligationViolation,
            ObligationGraph, ContextManagersTemporalObligationsCoordinator,
            ContextManagersTemporalObligationsAnalyzer,
            ContextManagersTemporalObligationsWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.exceptions import (
            ExceptionSheaf, ExceptionChain, FailurePropagator,
            StructuredFailureEncoder,
        )
        for _cls in [
            ExceptionSheaf, ExceptionChain, FailurePropagator,
            StructuredFailureEncoder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.exceptions_as_alternate_semantic_p import (
            ExceptionPath, ExceptionKindRecord, ExceptionWitnessRecord,
            ExceptionsAlternateSemanticPathsCoordinator,
            ExceptionsAlternateSemanticPathsAnalyzer,
            ExceptionsAlternateSemanticPathsWitness,
        )
        for _cls in [
            ExceptionPath, ExceptionKindRecord, ExceptionWitnessRecord,
            ExceptionsAlternateSemanticPathsCoordinator,
            ExceptionsAlternateSemanticPathsAnalyzer,
            ExceptionsAlternateSemanticPathsWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.generators import (
            GeneratorSheaf, LazyFiberBuilder, IteratorSection,
            GeneratorCombinator,
        )
        for _cls in [
            GeneratorSheaf, LazyFiberBuilder, IteratorSection,
            GeneratorCombinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.integration import (
            ExceptionJudgmentIntegrator, AsyncSiteIntegrator,
            ContextScopeIntegrator, GeneratorChannelBridge,
        )
        for _cls in [
            ExceptionJudgmentIntegrator, AsyncSiteIntegrator,
            ContextScopeIntegrator, GeneratorChannelBridge,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.manifest import (
            CoverageStatus, SymbolRole, ClaimStatus, ManifestRecord,
            SymbolGroup, ClaimSummary, PackageManifest,
        )
        for _cls in [
            CoverageStatus, SymbolRole, ClaimStatus, ManifestRecord,
            SymbolGroup, ClaimSummary, PackageManifest,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.models import (
            ExceptionSection, CancellationRecord, AsyncSection,
            GeneratorSection, ContextScope,
        )
        for _cls in [
            ExceptionSection, CancellationRecord, AsyncSection,
            GeneratorSection, ContextScope,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.effects_async.theorems import (
            Theorem_ExceptionSectionality, Theorem_ContextScopeCovers,
            Theorem_AsyncTopologicalOrder, Theorem_GeneratorFiberSequence,
            Theorem_CancellationPropagation, TheoremSuite,
        )
        for _cls in [
            Theorem_ExceptionSectionality, Theorem_ContextScopeCovers,
            Theorem_AsyncTopologicalOrder, Theorem_GeneratorFiberSequence,
            Theorem_CancellationPropagation, TheoremSuite,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # generated_contracts
    try:
        from jugeo.python_runtime.generated_contracts.algorithms import (
            AnalysisPlan, AnalysisResult, AnnotationGraphNode,
            AnnotationGraph, AnnotationGraphBuilder, DecoratorStackAnalyzer,
            AnnotationsDecoratorsRegistriesGeneratedPlanner,
            AnnotationsDecoratorsRegistriesGeneratedExecutor,
            AnnotationsDecoratorsRegistriesGeneratedNormalizer,
        )
        for _cls in [
            AnalysisPlan, AnalysisResult, AnnotationGraphNode,
            AnnotationGraph, AnnotationGraphBuilder, DecoratorStackAnalyzer,
            AnnotationsDecoratorsRegistriesGeneratedPlanner,
            AnnotationsDecoratorsRegistriesGeneratedExecutor,
            AnnotationsDecoratorsRegistriesGeneratedNormalizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.annotations_as_latent_behavior import (
            AnnotationRecord, WitnessRecord, AnnotationInspector,
            LatencyPromotionEngine, AnnotationsLatentBehaviorAnalyzer,
            AnnotationsLatentBehaviorWitness,
            AnnotationsLatentBehaviorCoordinator,
        )
        for _cls in [
            AnnotationRecord, WitnessRecord, AnnotationInspector,
            LatencyPromotionEngine, AnnotationsLatentBehaviorAnalyzer,
            AnnotationsLatentBehaviorWitness,
            AnnotationsLatentBehaviorCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.generated_contracts import (
            ContractSource, GeneratedContractRecord, WitnessRecord,
            DataclassContractExtractor, ProtocolContractExtractor,
            TypedDictContractExtractor, NamedTupleContractExtractor,
            ContractCompletionChecker, GeneratedContractsAnalyzer,
            GeneratedContractsWitness, GeneratedContractsCoordinator,
        )
        for _cls in [
            ContractSource, GeneratedContractRecord, WitnessRecord,
            DataclassContractExtractor, ProtocolContractExtractor,
            TypedDictContractExtractor, NamedTupleContractExtractor,
            ContractCompletionChecker, GeneratedContractsAnalyzer,
            GeneratedContractsWitness, GeneratedContractsCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.integration import (
            TrustTier, IntegrationKind, BridgeStatus, SolverFormula,
            SolverResult, EvidenceRecord, CoordinateMapper, SolverInterface,
            CopilotAdvisor, AnnotationsDecoratorsRegistriesGeneratedBridge,
        )
        for _cls in [
            TrustTier, IntegrationKind, BridgeStatus, SolverFormula,
            SolverResult, EvidenceRecord, CoordinateMapper, SolverInterface,
            CopilotAdvisor, AnnotationsDecoratorsRegistriesGeneratedBridge,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.manifest import (
            CoverageStatus, SymbolRole, ClaimStatus, ManifestRecord,
            SymbolGroup, ClaimSummary, PackageManifest,
        )
        for _cls in [
            CoverageStatus, SymbolRole, ClaimStatus, ManifestRecord,
            SymbolGroup, ClaimSummary, PackageManifest,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.models import (
            DecoratorTransformer, AnnotationContract, ContractRecord,
            RegistrySection,
        )
        for _cls in [
            DecoratorTransformer, AnnotationContract, ContractRecord,
            RegistrySection,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.registries import (
            SingleDispatchRegistry, ABCAbstractRegistry,
            DataclassFieldRegistry, PluginRegistryBuilder,
        )
        for _cls in [
            SingleDispatchRegistry, ABCAbstractRegistry,
            DataclassFieldRegistry, PluginRegistryBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.registry_surfaces import (
            TrustTier, JudgmentTuple, ContractEntry, RegistryQuery,
            ContractRegistry, RegistrySurface, SurfaceAPI,
            ContractRegistryCoordinator, ContractRegistryAnalyzer,
            ContractRegistryWitness, RegistryKind, RegistryEntry,
            RegistrySurfaceRecord, WitnessRecord,
            SingleDispatchSurfaceAnalyzer, ABCSurfaceAnalyzer,
            DataclassFieldSurfaceAnalyzer, RegistrySurfacesAnalyzer,
            RegistrySurfacesWitness, RegistrySurfacesCoordinator,
        )
        for _cls in [
            TrustTier, JudgmentTuple, ContractEntry, RegistryQuery,
            ContractRegistry, RegistrySurface, SurfaceAPI,
            ContractRegistryCoordinator, ContractRegistryAnalyzer,
            ContractRegistryWitness, RegistryKind, RegistryEntry,
            RegistrySurfaceRecord, WitnessRecord,
            SingleDispatchSurfaceAnalyzer, ABCSurfaceAnalyzer,
            DataclassFieldSurfaceAnalyzer, RegistrySurfacesAnalyzer,
            RegistrySurfacesWitness, RegistrySurfacesCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.theorem_burden import (
            BurdenKind, ProofObligation, BurdenReport, BurdenAccumulator,
            BurdenDischargeEngine, TheoremBurdenAnalyzer,
            TheoremBurdenWitness, TheoremBurdenCoordinator,
        )
        for _cls in [
            BurdenKind, ProofObligation, BurdenReport, BurdenAccumulator,
            BurdenDischargeEngine, TheoremBurdenAnalyzer,
            TheoremBurdenWitness, TheoremBurdenCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.generated_contracts.theorems import (
            TrustTier, JudgmentTuple, TheoremKind, ProofStatus,
            ContractTheorem, SoundnessProof, CompletenessArgument,
            PrecisionMetric, ContractTheoremRegistry, TheoremVerifier,
            CompletenessChecker, PrecisionBoundComputer, TheoremSuite,
            TheoremVerificationStatus, TheoremRecord, BaseTheorem,
            AnnotationLatencyTheorem, DecoratorMorphismTheorem,
            RegistryCoverageTheorem, ContractCompletenessTheorem,
            TheoremBurdenTheorem, TheoremRegistry, FalsificationCase,
            FalsificationSuite,
            AnnotationsDecoratorsRegistriesGeneratedTheoremSchema,
        )
        for _cls in [
            TrustTier, JudgmentTuple, TheoremKind, ProofStatus,
            ContractTheorem, SoundnessProof, CompletenessArgument,
            PrecisionMetric, ContractTheoremRegistry, TheoremVerifier,
            CompletenessChecker, PrecisionBoundComputer, TheoremSuite,
            TheoremVerificationStatus, TheoremRecord, BaseTheorem,
            AnnotationLatencyTheorem, DecoratorMorphismTheorem,
            RegistryCoverageTheorem, ContractCompletenessTheorem,
            TheoremBurdenTheorem, TheoremRegistry, FalsificationCase,
            FalsificationSuite,
            AnnotationsDecoratorsRegistriesGeneratedTheoremSchema,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # heap_aliasing
    try:
        from jugeo.python_runtime.heap_aliasing.algorithms import (
            HeapAnalyzer, UnionFindAlgorithm, AliasAnalysisAlgorithm,
            MutationFlowAlgorithm, HeapDiffAlgorithm,
        )
        for _cls in [
            HeapAnalyzer, UnionFindAlgorithm, AliasAnalysisAlgorithm,
            MutationFlowAlgorithm, HeapDiffAlgorithm,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.aliasing import (
            AliasPartitioner, AliasDetector, AliasGraph,
            SupportOverlapChecker, AliasSetTracker,
        )
        for _cls in [
            AliasPartitioner, AliasDetector, AliasGraph,
            SupportOverlapChecker, AliasSetTracker,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.aliasing_as_shared_geometry_suppor import (
            UnionFind, AliasGeometryRecord, MutationPropagationRecord,
            AliasAssignmentVisitor, AliasingSharedGeometrySupportCoordinator,
            AliasingSharedGeometrySupportAnalyzer,
            AliasingSharedGeometrySupportWitness,
        )
        for _cls in [
            UnionFind, AliasGeometryRecord, MutationPropagationRecord,
            AliasAssignmentVisitor, AliasingSharedGeometrySupportCoordinator,
            AliasingSharedGeometrySupportAnalyzer,
            AliasingSharedGeometrySupportWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.descent import (
            DescentConditionResult, DescentConditionChecker,
            HeapConsistencyVerifier, CocycleConditionChecker,
            LocalToGlobalMapper, HeapCoherenceTracker,
        )
        for _cls in [
            DescentConditionResult, DescentConditionChecker,
            HeapConsistencyVerifier, CocycleConditionChecker,
            LocalToGlobalMapper, HeapCoherenceTracker,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.heap_objects import (
            HeapObjectFactory, HeapObjectRegistry, IdentityTracker,
            HeapSectionBuilder,
        )
        for _cls in [
            HeapObjectFactory, HeapObjectRegistry, IdentityTracker,
            HeapSectionBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.identity_and_equality_observationa import (
            ComparisonKind, ComparisonRecord, ObservationalEquivalenceRecord,
            ComparisonASTVisitor,
            IdentityEqualityObservationalCriteriaCoordinator,
            IdentityEqualityObservationalCriteriaAnalyzer,
            IdentityEqualityObservationalCriteriaWitness,
        )
        for _cls in [
            ComparisonKind, ComparisonRecord, ObservationalEquivalenceRecord,
            ComparisonASTVisitor,
            IdentityEqualityObservationalCriteriaCoordinator,
            IdentityEqualityObservationalCriteriaAnalyzer,
            IdentityEqualityObservationalCriteriaWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.integration import (
            HeapJudgmentEmitter, Z3HeapEncoder, HeapCoordinateMapper,
            SupportRegionBuilder, CopilotHeapAdvisor,
        )
        for _cls in [
            HeapJudgmentEmitter, Z3HeapEncoder, HeapCoordinateMapper,
            SupportRegionBuilder, CopilotHeapAdvisor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.manifest import (
            Capability, ComponentRegistration, PackageManifest,
        )
        for _cls in [
            Capability, ComponentRegistration, PackageManifest,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.models import (
            ObjectKind, IdentityCoordinate, HeapObject, AliasPartition,
            MutationEvent, HeapSection, AliasEdge, HeapSnapshot,
            MutationPatch,
        )
        for _cls in [
            ObjectKind, IdentityCoordinate, HeapObject, AliasPartition,
            MutationEvent, HeapSection, AliasEdge, HeapSnapshot,
            MutationPatch,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.mutation import (
            MutationValidationResult, MutationValidator, MutationRecorder,
            DescentChecker, MutationImpactAnalyzer, FrozenObjectChecker,
        )
        for _cls in [
            MutationValidationResult, MutationValidator, MutationRecorder,
            DescentChecker, MutationImpactAnalyzer, FrozenObjectChecker,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.primitive_and_heap_mediated_values import (
            ValueKind, ValueRecord, ASTValueVisitor,
            PrimitiveHeapMediatedValuesCoordinator,
            PrimitiveHeapMediatedValuesAnalyzer,
            PrimitiveHeapMediatedValuesWitness,
        )
        for _cls in [
            ValueKind, ValueRecord, ASTValueVisitor,
            PrimitiveHeapMediatedValuesCoordinator,
            PrimitiveHeapMediatedValuesAnalyzer,
            PrimitiveHeapMediatedValuesWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.heap_aliasing.theorems import (
            TheoremKind, TheoremStatus, HeapTheorem, TheoremViolation,
            IdentityUniquenessTheorem, AliasTransitivityTheorem,
            MutationConsistencyTheorem, DescentConditionTheorem,
            ImmutabilityPreservedTheorem, TheoremRegistry,
        )
        for _cls in [
            TheoremKind, TheoremStatus, HeapTheorem, TheoremViolation,
            IdentityUniquenessTheorem, AliasTransitivityTheorem,
            MutationConsistencyTheorem, DescentConditionTheorem,
            ImmutabilityPreservedTheorem, TheoremRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # import_graph
    try:
        from jugeo.python_runtime.import_graph.algorithms import (
            ImportRecord, AnalysisPlan, ComplexityEstimate, IncrementalPlan,
            AnalysisResult, IncrementalResult,
            ImportsPackageFixedPointsNormalizer,
            ImportsPackageFixedPointsExecutor,
            ImportsPackageFixedPointsPlanner,
        )
        for _cls in [
            ImportRecord, AnalysisPlan, ComplexityEstimate, IncrementalPlan,
            AnalysisResult, IncrementalResult,
            ImportsPackageFixedPointsNormalizer,
            ImportsPackageFixedPointsExecutor,
            ImportsPackageFixedPointsPlanner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.dynamic_import_and_reflection import (
            DynamicImportKind, DynamicImportRecord, ImportlibUsageRecord,
            ModuleSpecRecord, ReflectionRecord, DynamicImportWitnessRecord,
            ModuleAttributeMap, ImportHookRecord, LazyImportRecord,
            PluginPatternRecord, DynamicImportReflectionAnalyzer,
            DynamicImportReflectionWitness,
            DynamicImportReflectionCoordinator,
        )
        for _cls in [
            DynamicImportKind, DynamicImportRecord, ImportlibUsageRecord,
            ModuleSpecRecord, ReflectionRecord, DynamicImportWitnessRecord,
            ModuleAttributeMap, ImportHookRecord, LazyImportRecord,
            PluginPatternRecord, DynamicImportReflectionAnalyzer,
            DynamicImportReflectionWitness,
            DynamicImportReflectionCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.import_cycles_and_package_fixed_po import (
            CycleKind, CycleRecord, FixedPointRecord,
            PartialModuleWitnessRecord, ImportCyclesPackageFixedAnalyzer,
            ImportCyclesPackageFixedCoordinator,
            ImportCyclesPackageFixedWitness,
        )
        for _cls in [
            CycleKind, CycleRecord, FixedPointRecord,
            PartialModuleWitnessRecord, ImportCyclesPackageFixedAnalyzer,
            ImportCyclesPackageFixedCoordinator,
            ImportCyclesPackageFixedWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.import_graph import (
            ImportGraphBuilder, CircularImportDetector, SysModulesSection,
            ImportGraphSerializer,
        )
        for _cls in [
            ImportGraphBuilder, CircularImportDetector, SysModulesSection,
            ImportGraphSerializer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.import_is_execution_plus_namespace import (
            ImportKind, ImportExecutionRecord, NamespaceTransportResult,
            ShadowedNameRecord, ExecutionWitnessRecord, NamespaceDeltaRecord,
            ImportExecutionNamespaceTransportCoordinator,
            ImportExecutionNamespaceTransportAnalyzer,
            ImportExecutionNamespaceTransportWitness,
        )
        for _cls in [
            ImportKind, ImportExecutionRecord, NamespaceTransportResult,
            ShadowedNameRecord, ExecutionWitnessRecord, NamespaceDeltaRecord,
            ImportExecutionNamespaceTransportCoordinator,
            ImportExecutionNamespaceTransportAnalyzer,
            ImportExecutionNamespaceTransportWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.integration import (
            ImportsPackageFixedPointsBridge,
            ImportsPackageFixedPointsExportBundle, CopilotImportAdvisor,
        )
        for _cls in [
            ImportsPackageFixedPointsBridge,
            ImportsPackageFixedPointsExportBundle, CopilotImportAdvisor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.manifest import (
            CoverageStatus, SymbolRole, ClaimStatus, ManifestRecord,
            SymbolGroup, ClaimSummary, PackageManifest,
        )
        for _cls in [
            CoverageStatus, SymbolRole, ClaimStatus, ManifestRecord,
            SymbolGroup, ClaimSummary, PackageManifest,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.models import (
            ImportNode, ImportEdge, PackageFixedPoint, DynamicLoadRecord,
            ReExportMap,
        )
        for _cls in [
            ImportNode, ImportEdge, PackageFixedPoint, DynamicLoadRecord,
            ReExportMap,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.package_fixpoints import (
            FixedPointComputer, NamespacePackageHandler, StabilityVerifier,
            FixedPointRegistry,
        )
        for _cls in [
            FixedPointComputer, NamespacePackageHandler, StabilityVerifier,
            FixedPointRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.proof_targets_for_import_semantics import (
            ProofTargetKind, TargetDifficulty, ProofTarget,
            ProofAttemptResult, ImportInvariant, InvariantWitnessRecord,
            DischargeRecord, ProofTargetsImportSemanticsAnalyzer,
            ProofTargetsImportSemanticsWitness,
            ProofTargetsImportSemanticsCoordinator,
        )
        for _cls in [
            ProofTargetKind, TargetDifficulty, ProofTarget,
            ProofAttemptResult, ImportInvariant, InvariantWitnessRecord,
            DischargeRecord, ProofTargetsImportSemanticsAnalyzer,
            ProofTargetsImportSemanticsWitness,
            ProofTargetsImportSemanticsCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.re_exports_star_imports_and_packag import (
            StarImportRiskLevel, ReExportRecord, ReExportHop,
            PackageSurfaceRecord, StarImportWitnessRecord, NameOriginRecord,
            ReExportsStarImportsCoordinator, ReExportsStarImportsAnalyzer,
            ReExportsStarImportsWitness,
        )
        for _cls in [
            StarImportRiskLevel, ReExportRecord, ReExportHop,
            PackageSurfaceRecord, StarImportWitnessRecord, NameOriginRecord,
            ReExportsStarImportsCoordinator, ReExportsStarImportsAnalyzer,
            ReExportsStarImportsWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.reexports import (
            ReExportAnalyzer, StarImportResolver, TrustTransporter,
            PrivateLeakDetector,
        )
        for _cls in [
            ReExportAnalyzer, StarImportResolver, TrustTransporter,
            PrivateLeakDetector,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.import_graph.theorems import (
            TheoremId, TheoremCheckResult, FalsificationResult,
            FalsificationSummary, _T191_ImportGraphAcyclicity,
            _T192_FixedPointUniqueness, _T193_ReexportConsistency,
            _T194_StarImportDeterminism, _T195_NamespaceDisjointness,
            _T196_DynamicImportReachability,
            ImportsPackageFixedPointsTheoremSchema,
            ImportsPackageFixedPointsFalsificationSuite,
        )
        for _cls in [
            TheoremId, TheoremCheckResult, FalsificationResult,
            FalsificationSummary, _T191_ImportGraphAcyclicity,
            _T192_FixedPointUniqueness, _T193_ReexportConsistency,
            _T194_StarImportDeterminism, _T195_NamespaceDisjointness,
            _T196_DynamicImportReachability,
            ImportsPackageFixedPointsTheoremSchema,
            ImportsPackageFixedPointsFalsificationSuite,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # live_mutation
    try:
        from jugeo.python_runtime.live_mutation.algorithms import (
            LiveMutationTracker, InvalidationEngine, HotReloadPlanner,
            DynamicSectionValidator,
        )
        for _cls in [
            LiveMutationTracker, InvalidationEngine, HotReloadPlanner,
            DynamicSectionValidator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.epoch_indexed_module_and_object_su import (
            EpochKind, EpochRecord, ObjectSummary, EpochDelta, EpochStore,
            ObjectSummaryAnalyzer, EpochIndexedModuleObjectCoordinator,
        )
        for _cls in [
            EpochKind, EpochRecord, ObjectSummary, EpochDelta, EpochStore,
            ObjectSummaryAnalyzer, EpochIndexedModuleObjectCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.exec_and_eval_as_bounded_or_residu import (
            EventBoundedness, ExecEvent, EvalEvent, BoundednessClassification,
            ResidualObservation, ExecBoundednessAnalyzer,
            ResidualEventWitness, ExecEvalBoundedResidualCoordinator,
        )
        for _cls in [
            EventBoundedness, ExecEvent, EvalEvent, BoundednessClassification,
            ResidualObservation, ExecBoundednessAnalyzer,
            ResidualEventWitness, ExecEvalBoundedResidualCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.exec_eval_injection import (
            ExecInjector, EvalQuerier, NamespaceTracker, DynamicTrustAssigner,
        )
        for _cls in [
            ExecInjector, EvalQuerier, NamespaceTracker, DynamicTrustAssigner,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.hot_reload import (
            HotReloadEngine, DescentPlanner, ReloadRollback,
            ConsistencyChecker,
        )
        for _cls in [
            HotReloadEngine, DescentPlanner, ReloadRollback,
            ConsistencyChecker,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.hot_reload_and_development_mode_se import (
            ReloadKind, DevSessionPhase, ReloadEvent, DevModeState,
            ReloadDiff, DevModeObservation, HotReloadEngine, DevModeWitness,
            HotReloadDevelopmentModeCoordinator,
        )
        for _cls in [
            ReloadKind, DevSessionPhase, ReloadEvent, DevModeState,
            ReloadDiff, DevModeObservation, HotReloadEngine, DevModeWitness,
            HotReloadDevelopmentModeCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.integration import (
            SupportBridge, JudgmentBridge, ChannelBridge, FleetBridge,
            LiveMutationIntegration,
        )
        for _cls in [
            SupportBridge, JudgmentBridge, ChannelBridge, FleetBridge,
            LiveMutationIntegration,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.manifest import (
            MutationRiskLevel, MutationCategory, SymbolRecord,
            LiveMutationManifest, ManifestValidator, ManifestRegistry,
            TheoryAlignment,
        )
        for _cls in [
            MutationRiskLevel, MutationCategory, SymbolRecord,
            LiveMutationManifest, ManifestValidator, ManifestRegistry,
            TheoryAlignment,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.models import (
            MutationKind, InvalidationScope, ReloadStatus, TrustTier,
            ExecContext, DynamicSection, EvalResult, MonkeyPatchRecord,
            HotReloadEvent,
        )
        for _cls in [
            MutationKind, InvalidationScope, ReloadStatus, TrustTier,
            ExecContext, DynamicSection, EvalResult, MonkeyPatchRecord,
            HotReloadEvent,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.monkey_patching import (
            MonkeyPatcher, InvalidationTrigger, PatchStack, PatchAuditor,
        )
        for _cls in [
            MonkeyPatcher, InvalidationTrigger, PatchStack, PatchAuditor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.monkey_patching_and_late_rebinding import (
            RebindingKind, ObstructionKind, RebindingRecord,
            ObstructionRecord, RebindingChain, LateRebindingAnalyzer,
            PatchEvidence, PatchObstructionWitness,
            MonkeyPatchingLateRebindingCoordinator,
        )
        for _cls in [
            RebindingKind, ObstructionKind, RebindingRecord,
            ObstructionRecord, RebindingChain, LateRebindingAnalyzer,
            PatchEvidence, PatchObstructionWitness,
            MonkeyPatchingLateRebindingCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.semantic_apertures_in_the_python_w import (
            ApertureKind, ApertureState, SemanticApertureRecord,
            ApertureIndexEntry, ApertureObservation, SemanticApertureAnalyzer,
            ApertureWitness, SemanticAperturesPythonWorldCoordinator,
        )
        for _cls in [
            ApertureKind, ApertureState, SemanticApertureRecord,
            ApertureIndexEntry, ApertureObservation, SemanticApertureAnalyzer,
            ApertureWitness, SemanticAperturesPythonWorldCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.live_mutation.theorems import (
            TheoremStatus, ProofMethod, TheoremRecord, TheoremProver,
            TheoremLibrary,
        )
        for _cls in [
            TheoremStatus, ProofMethod, TheoremRecord, TheoremProver,
            TheoremLibrary,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # metaobject_surfaces
    try:
        from jugeo.python_runtime.metaobject_surfaces.algorithms import (
            MROAlgorithmTracer,
        )
        for _cls in [
            MROAlgorithmTracer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.behavioral_surfaces import (
            ProtocolSurfaceAnalyzer, StructuralSubtypeChecker,
            BehavioralSurfaceBuilder, JudgmentIndexedProtocol,
        )
        for _cls in [
            ProtocolSurfaceAnalyzer, StructuralSubtypeChecker,
            BehavioralSurfaceBuilder, JudgmentIndexedProtocol,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.class_creation import (
            ClassCreationOrchestrator, _TracedDefinition, BodyExecutionTracer,
            InitSubclassProbe, SetNameHookApplicator,
        )
        for _cls in [
            ClassCreationOrchestrator, _TracedDefinition, BodyExecutionTracer,
            InitSubclassProbe, SetNameHookApplicator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.class_creation_as_staged_semantics import (
            ClassKind, MetaclassUsageKind, ClassCreationRecord,
            ThreePhaseTrace, MetaclassRef, DescriptorRef,
            ClassCreationWitnessRecord, SetNameCallRecord,
            ClassCreationStagedSemanticsAnalyzer,
            ClassCreationStagedSemanticsWitness,
            ClassCreationStagedSemanticsCoordinator, Plain, WithMeta, MyABC,
            DC,
        )
        for _cls in [
            ClassKind, MetaclassUsageKind, ClassCreationRecord,
            ThreePhaseTrace, MetaclassRef, DescriptorRef,
            ClassCreationWitnessRecord, SetNameCallRecord,
            ClassCreationStagedSemanticsAnalyzer,
            ClassCreationStagedSemanticsWitness,
            ClassCreationStagedSemanticsCoordinator, Plain, WithMeta, MyABC,
            DC,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.descriptor_resolution_routes import (
            DescriptorKind, ResolutionRoute, DescriptorResolutionResult,
            MROLookupTrace, DescriptorAnalysisRecord, PropertyUsageRecord,
            DescriptorConflict, SlotsAnalysisRecord, GetAttrWitnessRecord,
            SetAttrWitnessRecord, DescriptorProtocolProbe,
            DescriptorResolutionRoutesAnalyzer,
            DescriptorResolutionRoutesWitness,
            DescriptorResolutionRoutesCoordinator, MyClass,
        )
        for _cls in [
            DescriptorKind, ResolutionRoute, DescriptorResolutionResult,
            MROLookupTrace, DescriptorAnalysisRecord, PropertyUsageRecord,
            DescriptorConflict, SlotsAnalysisRecord, GetAttrWitnessRecord,
            SetAttrWitnessRecord, DescriptorProtocolProbe,
            DescriptorResolutionRoutesAnalyzer,
            DescriptorResolutionRoutesWitness,
            DescriptorResolutionRoutesCoordinator, MyClass,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.descriptors import (
            DescriptorResolver, SlotCoordinateBuilder,
            PropertyDescriptorAnalyzer, DescriptorTrustTracker,
        )
        for _cls in [
            DescriptorResolver, SlotCoordinateBuilder,
            PropertyDescriptorAnalyzer, DescriptorTrustTracker,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.generated_behavioral_surfaces import (
            SurfaceKind, OverridePattern, BehavioralSurfaceRecord,
            DataclassSurfaceRecord, ProtocolSurfaceRecord, ABCSurfaceRecord,
            MergedSurfaceRecord, BehavioralContract, ProtocolComplianceRecord,
            AbstractMethodRecord, DunderSurface, FieldSurfaceRecord,
            SurfaceComplianceWitnessRecord,
            AbstractInstantiationWitnessRecord, RuntimeProtocolCheckRecord,
            GeneratedBehavioralSurfacesAnalyzer,
            GeneratedBehavioralSurfacesWitness,
            GeneratedBehavioralSurfacesCoordinator, Foo, Baz,
        )
        for _cls in [
            SurfaceKind, OverridePattern, BehavioralSurfaceRecord,
            DataclassSurfaceRecord, ProtocolSurfaceRecord, ABCSurfaceRecord,
            MergedSurfaceRecord, BehavioralContract, ProtocolComplianceRecord,
            AbstractMethodRecord, DunderSurface, FieldSurfaceRecord,
            SurfaceComplianceWitnessRecord,
            AbstractInstantiationWitnessRecord, RuntimeProtocolCheckRecord,
            GeneratedBehavioralSurfacesAnalyzer,
            GeneratedBehavioralSurfacesWitness,
            GeneratedBehavioralSurfacesCoordinator, Foo, Baz,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.integration import (
            MetaclassJudgmentIntegrator, BehavioralSurfaceSiteBuilder,
            DescriptorChainChannelBridge, ClassCreationJudgmentEmitter,
        )
        for _cls in [
            MetaclassJudgmentIntegrator, BehavioralSurfaceSiteBuilder,
            DescriptorChainChannelBridge, ClassCreationJudgmentEmitter,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.manifest import (
            CoverageStatus, SymbolRole, ClaimStatus, ManifestRecord,
            SymbolGroup, ClaimSummary, PackageManifest,
        )
        for _cls in [
            CoverageStatus, SymbolRole, ClaimStatus, ManifestRecord,
            SymbolGroup, ClaimSummary, PackageManifest,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.metaclasses import (
            MetaclassMROResolver, MetaclassConflictChecker,
            TypeConstructorSite, ABCMetaAnalyzer,
        )
        for _cls in [
            MetaclassMROResolver, MetaclassConflictChecker,
            TypeConstructorSite, ABCMetaAnalyzer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.metaclasses_as_contract_transforme import (
            MetaclassPattern, MetaclassContractRecord,
            ContractTransformationTrace, MetaclassConflict,
            MetaclassInheritanceRecord, NewOverrideRecord, InitOverrideRecord,
            TransformationStep, InjectedDescriptorRecord,
            MetaclassCallWitnessRecord, NamespaceMutationRecord,
            MetaclassesContractTransformersAnalyzer,
            MetaclassesContractTransformersWitness,
            MetaclassesContractTransformersCoordinator, SingletonMeta,
        )
        for _cls in [
            MetaclassPattern, MetaclassContractRecord,
            ContractTransformationTrace, MetaclassConflict,
            MetaclassInheritanceRecord, NewOverrideRecord, InitOverrideRecord,
            TransformationStep, InjectedDescriptorRecord,
            MetaclassCallWitnessRecord, NamespaceMutationRecord,
            MetaclassesContractTransformersAnalyzer,
            MetaclassesContractTransformersWitness,
            MetaclassesContractTransformersCoordinator, SingletonMeta,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.models import (
            MetaclassRecord, BehavioralSurface, DescriptorChain,
            ClassCreationTrace,
        )
        for _cls in [
            MetaclassRecord, BehavioralSurface, DescriptorChain,
            ClassCreationTrace,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.metaobject_surfaces.theorems import (
            Theorem_MetaclassMROWellFounded, Theorem_DescriptorDataPrecedence,
            Theorem_BehavioralSurfaceFunctor,
            Theorem_ClassCreationMonotonicity,
            Theorem_MetaclassConflictObstruction,
        )
        for _cls in [
            Theorem_MetaclassMROWellFounded, Theorem_DescriptorDataPrecedence,
            Theorem_BehavioralSurfaceFunctor,
            Theorem_ClassCreationMonotonicity,
            Theorem_MetaclassConflictObstruction,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # program_loader
    try:
        from jugeo.python_runtime.program_loader import (
            ProgramLoaderError, ProgramSource, SymbolicProgram, ProgramLoader,
        )
        for _cls in [
            ProgramLoaderError, ProgramSource, SymbolicProgram, ProgramLoader,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # scope_and_state
    try:
        from jugeo.python_runtime.scope_and_state.algorithms import (
            NameResolutionEngine, ScopeTreeAlgorithm,
            ClosureAnalysisAlgorithm, ModuleStateDiffAlgorithm,
            ReachabilityAnalyzer,
        )
        for _cls in [
            NameResolutionEngine, ScopeTreeAlgorithm,
            ClosureAnalysisAlgorithm, ModuleStateDiffAlgorithm,
            ReachabilityAnalyzer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.closure_capture_cell_transport_lat import (
            ClosureCaptureCellTransportCoordinator,
            ClosureCaptureCellTransportAnalyzer,
            ClosureCaptureCellTransportWitness,
        )
        for _cls in [
            ClosureCaptureCellTransportCoordinator,
            ClosureCaptureCellTransportAnalyzer,
            ClosureCaptureCellTransportWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.closures import (
            ClosureDetector, ClosureLifter, CellVariableTracker,
            ClosureJudgmentBuilder,
        )
        for _cls in [
            ClosureDetector, ClosureLifter, CellVariableTracker,
            ClosureJudgmentBuilder,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.global_and_local_bindings_obligati import (
            GlobalLocalBindingsObligationCoordinator,
            GlobalLocalBindingsObligationAnalyzer,
            GlobalLocalBindingsObligationWitness,
        )
        for _cls in [
            GlobalLocalBindingsObligationCoordinator,
            GlobalLocalBindingsObligationAnalyzer,
            GlobalLocalBindingsObligationWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.integration import (
            ScopeJudgmentEmitter, Z3ScopeEncoder, ScopeCoordinateMapper,
            SupportRegionBuilder, CopilotScopeAdvisor,
        )
        for _cls in [
            ScopeJudgmentEmitter, Z3ScopeEncoder, ScopeCoordinateMapper,
            SupportRegionBuilder, CopilotScopeAdvisor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.manifest import (
            Capability, ComponentRegistration, PackageManifest,
        )
        for _cls in [
            Capability, ComponentRegistration, PackageManifest,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.models import (
            NameKind, ScopeKind, NameCoordinate, ScopeSection, ClosureRecord,
            ModuleStateManifest, NameResolutionResult, ScopeChain,
        )
        for _cls in [
            NameKind, ScopeKind, NameCoordinate, ScopeSection, ClosureRecord,
            ModuleStateManifest, NameResolutionResult, ScopeChain,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.module_state import (
            ModuleStateSnapshot, ModuleStateTracker, GlobalNameTracker,
            ImportTracker, ModuleStateValidator,
        )
        for _cls in [
            ModuleStateSnapshot, ModuleStateTracker, GlobalNameTracker,
            ImportTracker, ModuleStateValidator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.names import (
            NameClassifier, NameRegistry, NameNormalizer, BindingSiteResolver,
        )
        for _cls in [
            NameClassifier, NameRegistry, NameNormalizer, BindingSiteResolver,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.scope_semantics_coordinate_formati import (
            ScopeSemanticsCoordinateFormationCoordinator,
            ScopeSemanticsCoordinateFormationAnalyzer,
            ScopeSemanticsCoordinateFormationWitness,
        )
        for _cls in [
            ScopeSemanticsCoordinateFormationCoordinator,
            ScopeSemanticsCoordinateFormationAnalyzer,
            ScopeSemanticsCoordinateFormationWitness,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.scopes import (
            ScopeBuilder, ScopeAnalyzer, ScopeValidator, ScopeVisualizer,
        )
        for _cls in [
            ScopeBuilder, ScopeAnalyzer, ScopeValidator, ScopeVisualizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.scope_and_state.theorems import (
            TheoremKind, ScopeTheorem, NameUniquenessTheorem,
            ScopeCoveringTheorem, ClosureWellFormednessTheorem,
            ModuleStateConsistencyTheorem, ResolutionDeterminismTheorem,
            TheoremRegistry,
        )
        for _cls in [
            TheoremKind, ScopeTheorem, NameUniquenessTheorem,
            ScopeCoveringTheorem, ClosureWellFormednessTheorem,
            ModuleStateConsistencyTheorem, ResolutionDeterminismTheorem,
            TheoremRegistry,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    # unstable_protocols
    try:
        from jugeo.python_runtime.unstable_protocols.algorithms import (
            ProtocolAnalyzer, StabilityChecker, DelegationTracker,
            ProxyValidator,
        )
        for _cls in [
            ProtocolAnalyzer, StabilityChecker, DelegationTracker,
            ProxyValidator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.delegation_chains import (
            DelegationLink, ChainAnalysisResult, WitnessRecord,
            DelegationDetector, ChainTracer, RepairTargetLocator,
            DelegationChainsAnalyzer, DelegationChainsWitness,
            DelegationChainsCoordinator,
        )
        for _cls in [
            DelegationLink, ChainAnalysisResult, WitnessRecord,
            DelegationDetector, ChainTracer, RepairTargetLocator,
            DelegationChainsAnalyzer, DelegationChainsWitness,
            DelegationChainsCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.integration import (
            UnstableProtocolIntegration, SupportBridge, JudgmentBridge,
            FleetBridge,
        )
        for _cls in [
            UnstableProtocolIntegration, SupportBridge, JudgmentBridge,
            FleetBridge,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.manifest import (
            SymbolRecord, UnstableProtocolsManifest, ManifestValidator,
            ManifestRegistry, TheoryAlignment,
        )
        for _cls in [
            SymbolRecord, UnstableProtocolsManifest, ManifestValidator,
            ManifestRegistry, TheoryAlignment,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.models import (
            StabilityLevel, ProxyRestriction, DelegationKind, ProtocolSection,
            ProxyRecord, DelegationChain, UnstableInterface, StabilityMonitor,
        )
        for _cls in [
            StabilityLevel, ProxyRestriction, DelegationKind, ProtocolSection,
            ProxyRecord, DelegationChain, UnstableInterface, StabilityMonitor,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.protocol_obligations import (
            ProtocolObligation, ProtocolSatisfactionRecord,
            ProtocolAuditReport, WitnessRecord, ProtocolExtractor,
            SatisfactionChecker, ProtocolInheritanceResolver,
            ProtocolObligationsAnalyzer, ProtocolObligationsWitness,
            ProtocolObligationsCoordinator,
        )
        for _cls in [
            ProtocolObligation, ProtocolSatisfactionRecord,
            ProtocolAuditReport, WitnessRecord, ProtocolExtractor,
            SatisfactionChecker, ProtocolInheritanceResolver,
            ProtocolObligationsAnalyzer, ProtocolObligationsWitness,
            ProtocolObligationsCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.protocol_sections import (
            ProtocolSectionManager, ProtocolDescentEngine, ProtocolGluer,
            StalenessDetector,
        )
        for _cls in [
            ProtocolSectionManager, ProtocolDescentEngine, ProtocolGluer,
            StalenessDetector,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.proxy_delegation import (
            ProxyManager, DelegationMorphism, DelegationChainBuilder,
            ProxyValidator,
        )
        for _cls in [
            ProxyManager, DelegationMorphism, DelegationChainBuilder,
            ProxyValidator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.stable_versus_unstable_surface_are import (
            StabilityHeuristic, SurfaceStabilityRecord, WitnessRecord,
            SurfaceAuditReport, SurfaceClassifier, StabilityHistoryTracker,
            SurfaceComparisonEngine, StableUnstableSurfaceAreaAnalyzer,
            StableUnstableSurfaceAreaWitness,
            StableUnstableSurfaceAreaCoordinator,
        )
        for _cls in [
            StabilityHeuristic, SurfaceStabilityRecord, WitnessRecord,
            SurfaceAuditReport, SurfaceClassifier, StabilityHistoryTracker,
            SurfaceComparisonEngine, StableUnstableSurfaceAreaAnalyzer,
            StableUnstableSurfaceAreaWitness,
            StableUnstableSurfaceAreaCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.theorems import (
            TheoremRecord, TheoremProver, TheoremLibrary,
        )
        for _cls in [
            TheoremRecord, TheoremProver, TheoremLibrary,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.unstable_surfaces import (
            SurfaceTracker, RetractionEventLog, ObstructionInjector,
            SurfaceStabilizer,
        )
        for _cls in [
            SurfaceTracker, RetractionEventLog, ObstructionInjector,
            SurfaceStabilizer,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    try:
        from jugeo.python_runtime.unstable_protocols.why_this_matters_for_repair import (
            RepairRisk, RepairFeasibility, RepairConstraint,
            RepairFeasibilityRecord, WitnessRecord, RepairReport,
            StabilityRepairAnalyzer, DelegationRepairAnalyzer,
            ProtocolRepairAnalyzer, RepairFeasibilityOracle,
            WhyThisMattersRepairAnalyzer, WhyThisMattersRepairWitness,
            WhyThisMattersRepairCoordinator,
        )
        for _cls in [
            RepairRisk, RepairFeasibility, RepairConstraint,
            RepairFeasibilityRecord, WitnessRecord, RepairReport,
            StabilityRepairAnalyzer, DelegationRepairAnalyzer,
            ProtocolRepairAnalyzer, RepairFeasibilityOracle,
            WhyThisMattersRepairAnalyzer, WhyThisMattersRepairWitness,
            WhyThisMattersRepairCoordinator,
        ]:
            registry[_cls.__name__] = _cls
    except Exception:
        pass
    return registry


def _collect_evaluation_classes():
    """Collect evaluation classes."""
    registry = {}
    try:
        from jugeo.evaluation.evaluation_design.models import (
            EvaluationStatus, ClauseType, AblationKind, CalibrationMethod,
            EvaluationDesign, ClauseResult, AblationResult,
            CalibrationReport, EvaluationResult as EDEvalResult,
            ClausewiseEvaluator, AblationDesign,
        )
        for cls in [
            EvaluationStatus, ClauseType, AblationKind, CalibrationMethod,
            EvaluationDesign, ClauseResult, AblationResult,
            CalibrationReport, EDEvalResult, ClausewiseEvaluator,
            AblationDesign,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # methodology_loops
    try:
        from jugeo.evaluation.methodology_loops.models import (
            LoopPhase, LoopStatus, TransitionKind, LoopDiagnostics,
            MethodologyConfig, LoopState, LoopTransition,
            MethodologyLoop, FormalizationLoop, ImplementationLoop,
            FalsificationLoop,
        )
        for cls in [
            LoopPhase, LoopStatus, TransitionKind, LoopDiagnostics,
            MethodologyConfig, LoopState, LoopTransition,
            MethodologyLoop, FormalizationLoop, ImplementationLoop,
            FalsificationLoop,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # scaling_limits
    try:
        from jugeo.evaluation.scaling_limits.models import (
            ComplexityClass, ScalingRegime, LimitKind, ComplexityBound,
            PhaseChange, ScalingLaw, LimitCertificate,
            ComplexityAnalyzer, PhaseChangeDetector, ScalingLawFitter,
            FundamentalLimits,
        )
        for cls in [
            ComplexityClass, ScalingRegime, LimitKind, ComplexityBound,
            PhaseChange, ScalingLaw, LimitCertificate,
            ComplexityAnalyzer, PhaseChangeDetector, ScalingLawFitter,
            FundamentalLimits,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # evaluation_design additional
    try:
        from jugeo.evaluation.evaluation_design.algorithms import (
            EvaluationAlgorithms,
        )
        registry[EvaluationAlgorithms.__name__] = EvaluationAlgorithms
    except Exception:
        pass
    try:
        from jugeo.evaluation.evaluation_design.manifest import (
            EvaluationDesignManifest, EvaluationManifestBuilder,
            EvaluationManifestRegistry,
        )
        for cls in [EvaluationDesignManifest, EvaluationManifestBuilder,
                    EvaluationManifestRegistry]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evaluation.evaluation_design.theorems import (
            TheoremMetadata, EvaluationSoundnessTheorem,
            AblationIsolationTheorem, CalibrationConsistencyTheorem,
            ClauseCompletenessTheorem, ScoreMonotonicityTheorem,
            EvaluationTheoremRegistry,
        )
        for cls in [TheoremMetadata, EvaluationSoundnessTheorem,
                    AblationIsolationTheorem, CalibrationConsistencyTheorem,
                    ClauseCompletenessTheorem, ScoreMonotonicityTheorem,
                    EvaluationTheoremRegistry]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evaluation.evaluation_design.ablation_design import (
            AblationPlanner, AblationExecutor, AblationAnalyzer,
            AblationDesignRunner,
        )
        for cls in [AblationPlanner, AblationExecutor, AblationAnalyzer,
                    AblationDesignRunner]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evaluation.evaluation_design.clausewise_evaluation import (
            ClauseSpecification, ClausewiseScorer, ClauseWeightCalculator,
            ClausewiseEvaluationRunner,
        )
        for cls in [ClauseSpecification, ClausewiseScorer, ClauseWeightCalculator,
                    ClausewiseEvaluationRunner]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evaluation.evaluation_design.integration import (
            EvaluationEvidenceIntegration, EvaluationPacksIntegration,
            EvaluationOrchestrationIntegration, EvaluationIdeationIntegration,
            EvaluationGeometryIntegration, FullEvaluationIntegration,
        )
        for cls in [EvaluationEvidenceIntegration, EvaluationPacksIntegration,
                    EvaluationOrchestrationIntegration, EvaluationIdeationIntegration,
                    EvaluationGeometryIntegration, FullEvaluationIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evaluation.evaluation_design.calibration_metrics import (
            CalibrationMeasurer, CalibrationRecalibrator,
            ReliabilityDiagramBuilder, CalibrationMetricsRunner,
        )
        for cls in [CalibrationMeasurer, CalibrationRecalibrator,
                    ReliabilityDiagramBuilder, CalibrationMetricsRunner]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    # scaling_limits
    try:
        from jugeo.evaluation.scaling_limits.algorithms import (
            ScalingAlgorithms,
        )
        registry[ScalingAlgorithms.__name__] = ScalingAlgorithms
    except Exception:
        pass
    try:
        from jugeo.evaluation.scaling_limits.manifest import (
            ScalingLimitsManifest, ScalingManifestBuilder,
        )
        for cls in [ScalingLimitsManifest, ScalingManifestBuilder]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evaluation.scaling_limits.theorems import (
            ComplexityBoundTheoremClass, PhaseChangeDetectionSoundnessTheorem,
            ScalingLawValidityTheorem, FundamentalLimitSharpnessTheorem,
            NoFreeScalingTheorem, ScalingTheoremRegistry,
        )
        for cls in [ComplexityBoundTheoremClass, PhaseChangeDetectionSoundnessTheorem,
                    ScalingLawValidityTheorem, FundamentalLimitSharpnessTheorem,
                    NoFreeScalingTheorem, ScalingTheoremRegistry]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evaluation.scaling_limits.integration import (
            ScalingLimitsIntegration,
        )
        registry[ScalingLimitsIntegration.__name__] = ScalingLimitsIntegration
    except Exception:
        pass
    try:
        from jugeo.evaluation.scaling_limits.why_scaling_needs_its_own_theory import (
            ChangeType, RegimeType,
        )
        for cls in [ChangeType, RegimeType]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry
    """Collect evidence classes."""
    registry = {}
    try:
        from jugeo.evidence.trust import (
            TrustLevel, TrustAlgebra, TrustComposition, TrustAttenuation,
            TrustPromotion, TrustCeiling, TrustPolicy, TrustOperation,
            TrustAuditEntry, TrustAuditLog, TrustSerializer,
            TrustDiagnostics, AdmissibilityPredicate, TrustProfile,
        )
        for cls in [
            TrustLevel, TrustAlgebra, TrustComposition, TrustAttenuation,
            TrustPromotion, TrustCeiling, TrustPolicy, TrustOperation,
            TrustAuditEntry, TrustAuditLog, TrustSerializer,
            TrustDiagnostics, AdmissibilityPredicate, TrustProfile,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evidence.certificates import (
            CertificateStatus, Certificate, CertificateBuilder,
            CertificateChain, CertificateAuthority, CertificateStore,
            CertificateVerifier, CertificateMerger,
            CertificateProjection, CertificateDiff,
            ManifestCertificate, CertificateSerializer,
            CertificateDiagnostics, SettlementCertificate,
        )
        for cls in [
            CertificateStatus, Certificate, CertificateBuilder,
            CertificateChain, CertificateAuthority, CertificateStore,
            CertificateVerifier, CertificateMerger,
            CertificateProjection, CertificateDiff,
            ManifestCertificate, CertificateSerializer,
            CertificateDiagnostics, SettlementCertificate,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evidence.channels import (
            EvidenceChannel as EvChannel, EvidenceKind as EvKind,
            ChannelJurisdiction, ChannelConfiguration, EvidenceRequest,
            EvidenceResponse, EvidenceRecord, SupportRoute,
            ClauseSupport, ChannelDescriptor, EvidenceBundle as EvBundle,
            EvidenceFederationRecord, ChannelRouter, ChannelPool,
            ChannelFederation, ChannelMonitor, SolverChannel,
            RuntimeChannel, CopilotChannel, ChannelSerializer,
            ClaimPolarity, ComparisonNormalForm, AggregationPolicy,
            ChannelAdmissibilityError, AggregationPolicyError,
            EvidenceConflictError,
        )
        for cls in [
            EvChannel, EvKind, ChannelJurisdiction, ChannelConfiguration,
            EvidenceRequest, EvidenceResponse, EvidenceRecord,
            SupportRoute, ClauseSupport, ChannelDescriptor, EvBundle,
            EvidenceFederationRecord, ChannelRouter, ChannelPool,
            ChannelFederation, ChannelMonitor, SolverChannel,
            RuntimeChannel, CopilotChannel, ChannelSerializer,
            ClaimPolarity, ComparisonNormalForm, AggregationPolicy,
            ChannelAdmissibilityError, AggregationPolicyError,
            EvidenceConflictError,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evidence.provenance import (
            ProvenanceStep, ProvenanceTrace,
        )
        for cls in [ProvenanceStep, ProvenanceTrace]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evidence.manifests import (
            ObligationPriority, ObstructionKind, EvidenceManifest,
            JudgmentStore, ObligationStore, EvidenceArchive,
            ObstructionStore, CertificateStore as EvCertificateStore,
            ManifestStatistics, ManifestDiff, Manifest,
            ManifestBuilder, ManifestSerializer as EvManifestSerializer,
            ManifestValidator as EvManifestValidator,
            EpochMap, InvalidationGraph,
        )
        for cls in [ObligationPriority, ObstructionKind, EvidenceManifest,
                    JudgmentStore, ObligationStore, EvidenceArchive,
                    ObstructionStore, EvCertificateStore,
                    ManifestStatistics, ManifestDiff, Manifest,
                    ManifestBuilder, EvManifestSerializer,
                    EvManifestValidator,
                    EpochMap, InvalidationGraph]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.evidence.provenance import (
            ProvenanceOperation, ProvenanceNode, ProvenanceGraph,
            ProvenancePath, ProvenanceQuery, ProvenanceValidator,
            ProvenanceInvalidator, ProvenanceExplainer,
            ProvenanceSerializer as EvProvenanceSerializer,
            ProvenanceMerger, ProvenanceStatistics, ProvenanceArchive,
            CircularReasoningDetector,
            ValidationIssue, InvalidationRecord,
        )
        for cls in [ProvenanceOperation, ProvenanceNode, ProvenanceGraph,
                    ProvenancePath, ProvenanceQuery, ProvenanceValidator,
                    ProvenanceInvalidator, ProvenanceExplainer,
                    EvProvenanceSerializer, ProvenanceMerger,
                    ProvenanceStatistics, ProvenanceArchive,
                    CircularReasoningDetector,
                    ValidationIssue, InvalidationRecord]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_judgments_classes():
    """Collect judgment classes."""
    registry = {}
    try:
        from jugeo.judgments.judgment_terms import (
            JudgmentStatus, PropositionKind, EvidenceItemKind,
            ProvenanceSource, Proposition, Carrier, EvidenceItem,
            EvidenceBundle, ResidualObligation, Obstruction as JObstruction,
            TrustAnnotation, Provenance, JudgmentClause, Judgment,
            LocalJudgment, JudgmentBuilder, JudgmentAlgebra,
        )
        for cls in [
            JudgmentStatus, PropositionKind, EvidenceItemKind,
            ProvenanceSource, Proposition, Carrier, EvidenceItem,
            EvidenceBundle, ResidualObligation, JObstruction,
            TrustAnnotation, Provenance, JudgmentClause, Judgment,
            LocalJudgment, JudgmentBuilder, JudgmentAlgebra,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.judgments.sections import (
            Section, SectionFamily, SectionRestriction, GluingStatus,
            SectionGluing, SectionTransport, SheafCondition,
            SectionComparator, SectionBuilder, SectionCache,
            SectionSerializer, SectionDiagnostics,
        )
        for cls in [
            Section, SectionFamily, SectionRestriction, GluingStatus,
            SectionGluing, SectionTransport, SheafCondition,
            SectionComparator, SectionBuilder, SectionCache,
            SectionSerializer, SectionDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.judgments.comparisons import (
            ComparisonResult, ComparisonMode, EvidenceComparisonDetail,
            EvidenceComparator, TrustComparisonDetail, TrustComparator,
            ResidualComparisonDetail, ResidualComparator,
            RefinementWitness as JRefinementWitness, RefinementReport,
            RefinementChecker, EquivalenceReport, EquivalenceChecker,
            ContradictionKind, ContradictionReport, ContradictionDetector,
            CompositeComparisonReport, JudgmentComparator, JudgmentOrder,
            ComparisonHistory, ComparisonSerializer,
            SectionComparisonResult,
        )
        for cls in [
            ComparisonResult, ComparisonMode, EvidenceComparisonDetail,
            EvidenceComparator, TrustComparisonDetail, TrustComparator,
            ResidualComparisonDetail, ResidualComparator,
            JRefinementWitness, RefinementReport, RefinementChecker,
            EquivalenceReport, EquivalenceChecker, ContradictionKind,
            ContradictionReport, ContradictionDetector,
            CompositeComparisonReport, JudgmentComparator, JudgmentOrder,
            ComparisonHistory, ComparisonSerializer,
            SectionComparisonResult,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.judgments.contexts import (
            ContextBinding, EntryType, ContextEntry, SemanticContext,
            JudgmentContext, ContextPresheaf, ContextStack,
            ContextMerger, ContextRestriction, ContextExtension,
            ContextDiff, ContextValidator, ContextSerializer,
            ContextQuery, ContextHistory,
        )
        for cls in [ContextBinding, EntryType, ContextEntry, SemanticContext,
                    JudgmentContext, ContextPresheaf, ContextStack,
                    ContextMerger, ContextRestriction, ContextExtension,
                    ContextDiff, ContextValidator, ContextSerializer,
                    ContextQuery, ContextHistory]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.judgments.exports import (
            ProjectionKind, ClauseExport, JudgmentExport, SectionExport,
        )
        for cls in [ProjectionKind, ClauseExport, JudgmentExport, SectionExport]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_solver_classes():
    """Collect solver classes."""
    registry = {}
    try:
        from jugeo.solver.z3_session import (
            SolveOutcome, FormulaKind, FragmentTag, SolverResult,
            BuiltinAdapter, Z3Formula, Z3Session, Z3SessionPool,
            Z3Encoder, Z3Decoder, Z3Result, Z3QueryBuilder,
            Z3FragmentClassifier, Z3TacticRouter, Z3SessionMonitor,
            Z3Serializer, Z3CopilotAssist,
            SolverAdapter,
        )
        for cls in [
            SolveOutcome, FormulaKind, FragmentTag, SolverResult,
            BuiltinAdapter, Z3Formula, Z3Session, Z3SessionPool,
            Z3Encoder, Z3Decoder, Z3Result, Z3QueryBuilder,
            Z3FragmentClassifier, Z3TacticRouter, Z3SessionMonitor,
            Z3Serializer, Z3CopilotAssist,
            SolverAdapter,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.solver.fragments import (
            Fragment, FragmentSignature, FragmentClassifier,
            FragmentDecomposer, EncodingStrategy, TacticSelector,
            FragmentCache, FragmentStatistics, CopilotFragmentAssist,
            LogicalFragment, SolverFragment,
        )
        for cls in [
            Fragment, FragmentSignature, FragmentClassifier,
            FragmentDecomposer, EncodingStrategy, TacticSelector,
            FragmentCache, FragmentStatistics, CopilotFragmentAssist,
            LogicalFragment, SolverFragment,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.solver.countermodels import (
            FailureClass, RepairType, Countermodel,
            CountermodelExtractor, CountermodelMinimizer,
            CountermodelNormalizer, ObstructionConverter,
            TestCaseGenerator, CountermodelExplainer,
            CountermodelStore, CountermodelComparator,
            RepairHintGenerator,
        )
        for cls in [
            FailureClass, RepairType, Countermodel,
            CountermodelExtractor, CountermodelMinimizer,
            CountermodelNormalizer, ObstructionConverter,
            TestCaseGenerator, CountermodelExplainer,
            CountermodelStore, CountermodelComparator,
            RepairHintGenerator,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.solver.reconstruction import (
            ReconstructionKind, ProofStep as SR_ProofStep,
            WitnessBinding, SortInterpretation, FunctionInterpretation,
            ArrayInterpretation, DatatypeInterpretation,
            ReconstructionResult, ReconstructionReport,
            ProofReconstructor, WitnessReconstructor,
            ModelReconstructor, EvidenceAssembler,
            PartialReconstructor, ReconstructionCache,
            ReconstructionValidator, ReconstructionPipeline,
            ReconstructionStatistics,
        )
        for cls in [ReconstructionKind, SR_ProofStep, WitnessBinding,
                    SortInterpretation, FunctionInterpretation,
                    ArrayInterpretation, DatatypeInterpretation,
                    ReconstructionResult, ReconstructionReport,
                    ProofReconstructor, WitnessReconstructor,
                    ModelReconstructor, EvidenceAssembler,
                    PartialReconstructor, ReconstructionCache,
                    ReconstructionValidator, ReconstructionPipeline,
                    ReconstructionStatistics]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.solver.router import (
            BackendKind, RoutingStrategyKind, VerificationDomain,
            RoutingDecision as SR_RoutingDecision, BackendDescriptor,
            RouterConfiguration, JurisdictionChecker,
            RoutingStrategy as SR_RoutingStrategy,
            CheapestStrategy, FastestStrategy, MostTrustedStrategy,
            RoundRobinStrategy, SmartStrategy,
            RoutingHistory as SR_RoutingHistory, FallbackChain as SR_FallbackChain,
            CopilotFallbackPolicy, RouterMonitor, BatchRouter,
            RouterSerializer, SolverRouter, SolverRoute,
        )
        for cls in [BackendKind, RoutingStrategyKind, VerificationDomain,
                    SR_RoutingDecision, BackendDescriptor, RouterConfiguration,
                    JurisdictionChecker, SR_RoutingStrategy,
                    CheapestStrategy, FastestStrategy, MostTrustedStrategy,
                    RoundRobinStrategy, SmartStrategy, SR_RoutingHistory,
                    SR_FallbackChain, CopilotFallbackPolicy, RouterMonitor,
                    BatchRouter, RouterSerializer, SolverRouter, SolverRoute]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_thesis_classes():
    """Collect thesis classes."""
    registry = {}
    try:
        from jugeo.thesis.semantic_center.models import (
            ClaimStatus, ContributionKind, ProblemDomain,
            IntroductionJuGeoScope, IntroductionJuGeoRecord,
            IntroductionJuGeoSummary, JuGeoWorldview, ThesisClaim,
            ContributionRecord, ProblemClass as TSProblemClass,
        )
        for cls in [
            ClaimStatus, ContributionKind, ProblemDomain,
            IntroductionJuGeoScope, IntroductionJuGeoRecord,
            IntroductionJuGeoSummary, JuGeoWorldview, ThesisClaim,
            ContributionRecord, TSProblemClass,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.research_program.models import (
            ClaimCategory, ClaimStrength, FalsificationOutcome,
            ContributionScope, EvidenceItem as RPEvidenceItem,
            EvidencePlan, FalsificationCondition, FalsificationCriteria,
            ContributionBoundaryItem, ContributionBoundary,
            ResearchQuestion, ThesisClaim as RPThesisClaim,
        )
        for cls in [
            ClaimCategory, ClaimStrength, FalsificationOutcome,
            ContributionScope, RPEvidenceItem, EvidencePlan,
            FalsificationCondition, FalsificationCriteria,
            ContributionBoundaryItem, ContributionBoundary,
            ResearchQuestion, RPThesisClaim,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.semantic_center.algorithms import (
            AlgorithmStatus, AlgorithmState, AlgorithmResult,
            JuGeoBootstrapAlgorithm, SemanticCenterDetectionAlgorithm,
            ClaimVerificationAlgorithm,
        )
        for cls in [AlgorithmStatus, AlgorithmState, AlgorithmResult,
                    JuGeoBootstrapAlgorithm, SemanticCenterDetectionAlgorithm,
                    ClaimVerificationAlgorithm]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.semantic_center.main_contributions import (
            JudgmentGeometryContribution, EvidencePluralityContribution,
            ObstructionPersistenceContribution, TrustAlgebraContribution,
            ContributionCatalog,
        )
        for cls in [JudgmentGeometryContribution, EvidencePluralityContribution,
                    ObstructionPersistenceContribution, TrustAlgebraContribution,
                    ContributionCatalog]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.semantic_center.manifest import (
            IntroductionModuleSurface, BlueprintClassBridge,
            IntroductionJuGeoDependencyMap, IntroductionJuGeoManifest,
        )
        for cls in [IntroductionModuleSurface, BlueprintClassBridge,
                    IntroductionJuGeoDependencyMap, IntroductionJuGeoManifest]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.semantic_center.theorems import (
            ProofStrategy, TheoremStatement, TheoremCatalog,
        )
        for cls in [ProofStrategy, TheoremStatement, TheoremCatalog]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.semantic_center.integration import (
            IntegrationReport, EvidenceChannelBinding,
            ThesisClaimTracker, ManifestIntegrityCheck,
            SemanticCenterIntegration,
        )
        for cls in [IntegrationReport, EvidenceChannelBinding,
                    ThesisClaimTracker, ManifestIntegrityCheck,
                    SemanticCenterIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.research_program.algorithms import (
            VerificationPhase, AccumulationSignal, ResearchAlgorithms,
        )
        for cls in [VerificationPhase, AccumulationSignal, ResearchAlgorithms]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.research_program.falsifiability import (
            TestableProperty, FalsificationTestRunner, ClaimFalsificationMap,
        )
        for cls in [TestableProperty, FalsificationTestRunner, ClaimFalsificationMap]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.research_program.integration import (
            ClaimArtifactRelation, TheoryCodeMap, ResearchIntegration,
        )
        for cls in [ClaimArtifactRelation, TheoryCodeMap, ResearchIntegration]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.research_program.mathematical_ideation import (
            CandidateStructure, KnowledgeBase, NoveltyMeasure,
            IdeationSpec, IdeationRound, DiscoveryEngine,
        )
        for cls in [CandidateStructure, KnowledgeBase, NoveltyMeasure,
                    IdeationSpec, IdeationRound, DiscoveryEngine]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.thesis.research_program.mixed_evidence import (
            EvidenceAtom, EvidencePlurality,
            FederatedEvidence, FederationProtocol,
        )
        for cls in [EvidenceAtom, EvidencePlurality,
                    FederatedEvidence, FederationProtocol]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_runtime_classes():
    """Collect runtime classes."""
    registry = {}
    try:
        from jugeo.runtime.cache import (
            CacheKey, CacheEntry, EvictionStrategy, CachePolicy,
            CacheIndex, CacheStatistics, SemanticCache,
            CacheInvalidator, CacheWarmer, CacheDiagnostics,
            CacheSerializer,
        )
        for cls in [
            CacheKey, CacheEntry, EvictionStrategy, CachePolicy,
            CacheIndex, CacheStatistics, SemanticCache,
            CacheInvalidator, CacheWarmer, CacheDiagnostics,
            CacheSerializer,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.runtime.checkpointing import (
            Checkpoint, CheckpointPolicy, CheckpointDiff,
            CheckpointSerializer, CheckpointStore, CheckpointBuilder,
            CheckpointIntegrity, CheckpointRestorer, CheckpointHistory,
            CheckpointScheduler, CheckpointDiagnostics,
        )
        for cls in [
            Checkpoint, CheckpointPolicy, CheckpointDiff,
            CheckpointSerializer, CheckpointStore, CheckpointBuilder,
            CheckpointIntegrity, CheckpointRestorer, CheckpointHistory,
            CheckpointScheduler, CheckpointDiagnostics,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.runtime.memory import (
            MemoryRegion, MemoryUpdate, MemoryIndex, MemoryGC,
            MemorySnapshot, MemoryTransaction, MemoryQuotaManager,
            MemoryMigration, MemoryDiagnostics, MemorySerializer,
            MemoryNote, SemanticMemory,
        )
        for cls in [
            MemoryRegion, MemoryUpdate, MemoryIndex, MemoryGC,
            MemorySnapshot, MemoryTransaction, MemoryQuotaManager,
            MemoryMigration, MemoryDiagnostics, MemorySerializer,
            MemoryNote, SemanticMemory,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.runtime.invalidation import (
            InvalidationReason, InvalidationGraph, TriggerKind,
            InvalidationEvent, CascadeStrategy, NotificationPolicy,
            InvalidationPolicy, InvalidationCascade, InvalidationTracker,
            InvalidationEngine, RepairScheduler, InvalidationNotifier,
            InvalidationHistory, InvalidationDiagnostics,
            InvalidationSerializer, InvalidationPlan,
        )
        for cls in [
            InvalidationReason, InvalidationGraph, TriggerKind,
            InvalidationEvent, CascadeStrategy, NotificationPolicy,
            InvalidationPolicy, InvalidationCascade, InvalidationTracker,
            InvalidationEngine, RepairScheduler, InvalidationNotifier,
            InvalidationHistory, InvalidationDiagnostics,
            InvalidationSerializer, InvalidationPlan,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.runtime.replay import (
            ReplayStatus, ReplayTrigger, ReplayPolicy, ReplaySeal,
            ReplayRecord, ReplayLedger, ReplayDecision, ReplayReport,
            ReplayEngine,
        )
        for cls in [
            ReplayStatus, ReplayTrigger, ReplayPolicy, ReplaySeal,
            ReplayRecord, ReplayLedger, ReplayDecision, ReplayReport,
            ReplayEngine,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.runtime_defaults import (
            PolicyPreset, GCStrategy, PersistenceBackend,
            DependencyResolutionStrategy, VersionPolicy, FragmentRouting,
            DefaultTrustLevels, ChannelConfig, DefaultEvidenceChannelConfig,
            DefaultDescentConfig, DefaultObstructionPolicy, DimensionBudget,
            DefaultBudgetConfig, DefaultManifestConfig, DefaultSolverConfig,
            DefaultCopilotConfig, DefaultPackConfig, DefaultOrchestrationConfig,
            RuntimeDefaults, DefaultsRegistry, TrustPolicyDefaults,
        )
        for cls in [
            PolicyPreset, GCStrategy, PersistenceBackend,
            DependencyResolutionStrategy, VersionPolicy, FragmentRouting,
            DefaultTrustLevels, ChannelConfig, DefaultEvidenceChannelConfig,
            DefaultDescentConfig, DefaultObstructionPolicy, DimensionBudget,
            DefaultBudgetConfig, DefaultManifestConfig, DefaultSolverConfig,
            DefaultCopilotConfig, DefaultPackConfig, DefaultOrchestrationConfig,
            RuntimeDefaults, DefaultsRegistry, TrustPolicyDefaults,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.bootstrap import (
            SubsystemStatus, SubsystemName, SubsystemRecord,
            JuGeoBootstrap,
        )
        for cls in [SubsystemStatus, SubsystemName, SubsystemRecord,
                    JuGeoBootstrap]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.package_manifest import ManifestCapability
        registry["ManifestCapability"] = ManifestCapability
    except Exception:
        pass
    try:
        from jugeo.errors import (
            FailureChain, FailureFilter, JudgmentError,
            DescentError, EncodingError,
            FailureScope, FailureClassification, EvidenceFamily,
            JuGeoError, StructuredFailure,
        )
        for cls in [FailureChain, FailureFilter, JudgmentError,
                    DescentError, EncodingError,
                    FailureScope, FailureClassification, EvidenceFamily,
                    JuGeoError, StructuredFailure]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


def _collect_packs_classes():
    """Collect packs classes including federation."""
    registry = {}
    try:
        from jugeo.packs.catalog import PackCatalog, PackDescriptor
        for cls in [PackCatalog, PackDescriptor]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    try:
        from jugeo.packs.bridges import PackBridge
        registry[PackBridge.__name__] = PackBridge
    except Exception:
        pass
    try:
        from jugeo.packs.federation import (
            FederationStatus, EvidenceKindLabel, FederationRequest,
            FederationResult, EvidenceCombiner, FederationPlan,
            FederationValidator, FederationEngine, FederationCoordinator,
            FederationCache, FederationHistory, FederationDiagnostics,
            FederationSerializer, PackFederation,
        )
        for cls in [
            FederationStatus, EvidenceKindLabel, FederationRequest,
            FederationResult, EvidenceCombiner, FederationPlan,
            FederationValidator, FederationEngine, FederationCoordinator,
            FederationCache, FederationHistory, FederationDiagnostics,
            FederationSerializer, PackFederation,
        ]:
            registry[cls.__name__] = cls
    except Exception:
        pass
    return registry


# ---------------------------------------------------------------------------
# Subsystem map
# ---------------------------------------------------------------------------

SUBSYSTEM_COLLECTORS = {
    "geometry": _collect_geometry_classes,
    "judgments": _collect_judgments_classes,
    "evidence": lambda: {},
    "encodings": _collect_encodings_classes,
    "generation": _collect_generation_classes,
    "orchestration": _collect_orchestration_classes,
    "ideation": _collect_ideation_classes,
    "foundations": _collect_foundations_classes,
    "problem_modes": _collect_problem_modes_classes,
    "python_runtime": _collect_python_runtime_classes,
    "evaluation": _collect_evaluation_classes,
    "solver": _collect_solver_classes,
    "thesis": _collect_thesis_classes,
    "runtime": _collect_runtime_classes,
    "packs": _collect_packs_classes,
}


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def add_subparser(subparsers):
    """Register the ``catalog`` subcommand."""
    p = subparsers.add_parser(
        "catalog",
        help="Comprehensive subsystem catalog and introspection.",
    )
    p.add_argument("--subsystem", "-s", help="Show only a specific subsystem")
    p.add_argument("--classes", action="store_true", help="List all classes")
    p.add_argument("--count", action="store_true", help="Show class counts per subsystem")
    p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")
    p.add_argument("--verbose", "-v", action="store_true", help="Show class details (module path)")
    p.set_defaults(func=run)


def run(args):
    """Execute the catalog command."""
    import json as json_mod

    subsystems = dict(SUBSYSTEM_COLLECTORS)
    if getattr(args, "subsystem", None):
        if args.subsystem in subsystems:
            subsystems = {args.subsystem: subsystems[args.subsystem]}
        else:
            print(f"Unknown subsystem: {args.subsystem}", file=sys.stderr)
            print(f"Available: {', '.join(sorted(SUBSYSTEM_COLLECTORS.keys()))}")
            return 1

    total = 0
    results: dict[str, list[str]] = {}
    details: dict[str, dict[str, str]] = {}
    for name, collector in sorted(subsystems.items()):
        classes = collector()
        results[name] = sorted(classes.keys())
        details[name] = {
            k: f"{v.__module__}.{v.__qualname__}" for k, v in sorted(classes.items())
        }
        total += len(classes)

    # --- JSON output ---
    if getattr(args, "as_json", False):
        output: dict = {}
        for name, cls_list in sorted(results.items()):
            if getattr(args, "verbose", False):
                output[name] = details[name]
            else:
                output[name] = cls_list
        output["_total"] = total  # type: ignore[assignment]
        print(json_mod.dumps(output, indent=2))
        return 0

    # --- Count-only output ---
    if getattr(args, "count", False):
        print(f"{'Subsystem':<20} {'Classes':>8}")
        print("-" * 30)
        for name, cls_list in sorted(results.items()):
            print(f"{name:<20} {len(cls_list):>8}")
        print("-" * 30)
        print(f"{'TOTAL':<20} {total:>8}")
        return 0

    # --- Default: grouped listing ---
    for name, cls_list in sorted(results.items()):
        print(f"\n{'=' * 60}")
        print(f"  {name.upper()} ({len(cls_list)} classes)")
        print(f"{'=' * 60}")
        show_all = getattr(args, "classes", False)
        verbose = getattr(args, "verbose", False)
        items = cls_list if show_all else cls_list[:10]
        for cls_name in items:
            if verbose:
                print(f"  • {cls_name:<40} {details[name].get(cls_name, '')}")
            else:
                print(f"  • {cls_name}")
        if not show_all and len(cls_list) > 10:
            print(f"  ... and {len(cls_list) - 10} more (use --classes to see all)")

    print(f"\n{'=' * 60}")
    print(f"  Total: {total} registered classes across {len(results)} subsystems")
    print(f"{'=' * 60}")
    return 0
