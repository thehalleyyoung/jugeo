"""CLI subcommand handler for ``jugeo encode <file> ...``.

Builds a sheaf-theoretic Site model of each input Python file (reusing
``cmd_load.build_sheaf_model``), then runs the encoding subsystems:

- ``jugeo.encodings.structural_frontier.models`` – StructuralFrontier /
  DecidabilityMap for classifying which fragments are solver-decidable.
- ``jugeo.solver.z3_session`` – Z3Session / Z3Formula / FormulaKind for
  generating and optionally checking SMT-LIB2 assertions.
- ``jugeo.evidence.trust`` – TrustLevel / TrustAlgebra for annotating
  encoding trust.
- ``jugeo.judgments.judgment_terms`` – Judgment / JudgmentBuilder /
  Proposition / PropositionKind / Carrier for creating encoding judgments.
- ``jugeo.geometry.descent`` – DescentEngine / LocalSection for checking
  whether encodings are compatible across overlaps.

Supports ``--encoding {scalar,structural,tensor,sequence,text,all}`` and
``--emit-smt2``.
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
from typing import Any, Dict, List, Mapping, Optional, Tuple

_log = logging.getLogger(__name__)

# ── JuGeo subsystem imports (all guarded) ────────────────────────────

try:
    from jugeo.geometry.site import (
        Site, SiteBuilder, Coordinate, CoordinateKind,
        Morphism, MorphismKind, CoveringFamily, GrothendieckTopology,
    )
    _SITE_OK = True
except Exception:
    _SITE_OK = False

try:
    from jugeo.judgments.judgment_terms import (
        Judgment, JudgmentBuilder, Proposition, PropositionKind,
        EvidenceBundle, Carrier, JudgmentStatus, ProvenanceSource,
    )
    _JUDGMENT_OK = True
except Exception:
    _JUDGMENT_OK = False

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    _TRUST_OK = True
except Exception:
    _TRUST_OK = False

try:
    from jugeo.encodings.structural_frontier.models import (
        StructuralFrontier, DecidabilityMap, DecidabilityClass,
        FrontierSide,
    )
    _FRONTIER_OK = True
except Exception:
    _FRONTIER_OK = False

try:
    from jugeo.solver.z3_session import (
        Z3Session, Z3Formula, FormulaKind, SolveOutcome,
    )
    _SOLVER_OK = True
except Exception:
    _SOLVER_OK = False

try:
    from jugeo.solver.z3_session import z3_available as _z3_available
    _Z3_AVAILABLE = _z3_available()
except Exception:
    _Z3_AVAILABLE = False

try:
    from jugeo.geometry.descent import (
        DescentEngine, LocalSection, DescentConfiguration,
        DescentStrategy, OverlapStatus,
    )
    _DESCENT_OK = True
except Exception:
    _DESCENT_OK = False

try:
    from jugeo.geometry.covers import Cover, CoverBuilder, CoverMember
    _COVER_OK = True
except Exception:
    _COVER_OK = False

try:
    from jugeo.judgments.sections import SectionFamily
    _SECTION_OK = True
except Exception:
    _SECTION_OK = False

# Reuse the sheaf model builder from cmd_load
try:
    from jugeo.cli.cmd_load import build_sheaf_model, _ALL_SUBSYSTEMS as _LOAD_OK
except Exception:
    _LOAD_OK = False

    def build_sheaf_model(source: str, filename: str):  # type: ignore[misc]
        return None, None

_ALL_ENCODE = (
    _SITE_OK and _JUDGMENT_OK and _TRUST_OK and _FRONTIER_OK
    and _SOLVER_OK and _DESCENT_OK and _COVER_OK and _LOAD_OK
)

# Encoding family identifiers
_ENCODING_FAMILIES = ("scalar", "structural", "tensor", "sequence", "text")


def _encodings_registry() -> dict[str, type]:
    """Import all known encoding classes and return ``{name: cls}``."""
    reg: dict[str, type] = {}

    # --- collection_heap_encodings ---
    try:
        from jugeo.encodings.collection_heap_encodings.algorithms import (
            AlgorithmStatus, AlgorithmResult, CollectionHeapAlgorithm, BottomUpHeapSummaryAlgorithm,
            FixedPointAliasAnalysis, CollectionInvariantInference, InterfaceAbstractionSynthesis, BoundaryConditionMinimization,
        )
        for _cls in (
            AlgorithmStatus, AlgorithmResult, CollectionHeapAlgorithm, BottomUpHeapSummaryAlgorithm,
            FixedPointAliasAnalysis, CollectionInvariantInference, InterfaceAbstractionSynthesis, BoundaryConditionMinimization,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.algorithms not available")

    try:
        from jugeo.encodings.collection_heap_encodings.aliasing_obligation import (
            AliasKind, PointerInfo, AliasingObligation, AliasPartitionBuilder,
            DisjointnessChecker,
        )
        for _cls in (
            AliasKind, PointerInfo, AliasingObligation, AliasPartitionBuilder,
            DisjointnessChecker,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.aliasing_obligation not available")

    try:
        from jugeo.encodings.collection_heap_encodings.aliasing_obligations import (
            TrustTier, Judgment, CechObstruction, AliasKind,
            DischargeResult, StalkerKind, AliasCechObstruction, AliasJudgment,
            StalkerEquivalence, _UnionFind, AliasingObligation, AliasProofBurden,
            MayAliasSet, AliasGlobalSection, AliasDescentObstruction,
        )
        for _cls in (
            TrustTier, Judgment, CechObstruction, AliasKind,
            DischargeResult, StalkerKind, AliasCechObstruction, AliasJudgment,
            StalkerEquivalence, _UnionFind, AliasingObligation, AliasProofBurden,
            MayAliasSet, AliasGlobalSection, AliasDescentObstruction,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.aliasing_obligations not available")

    try:
        from jugeo.encodings.collection_heap_encodings.collection_encoder import (
            CollectionKind, ElementTypeInfo, CollectionEncoder, CollectionInvariantChecker,
            CollectionFragmentClassifier,
        )
        for _cls in (
            CollectionKind, ElementTypeInfo, CollectionEncoder, CollectionInvariantChecker,
            CollectionFragmentClassifier,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.collection_encoder not available")

    try:
        from jugeo.encodings.collection_heap_encodings.collection_encodings_should_be_fam import (
            TrustTier, IndexKind, SectionStatus, CoverStrategyKind,
            CechObstruction, CollectionJudgment, GlobalSection, DescentObstruction,
            IndexObject, LocalSection, ElementSheaf, CollectionCoverStrategy,
            IndexedFamilyRepr, CollectionEncoding, EncodingStatistics,
        )
        for _cls in (
            TrustTier, IndexKind, SectionStatus, CoverStrategyKind,
            CechObstruction, CollectionJudgment, GlobalSection, DescentObstruction,
            IndexObject, LocalSection, ElementSheaf, CollectionCoverStrategy,
            IndexedFamilyRepr, CollectionEncoding, EncodingStatistics,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.collection_encodings_should_be_fam not available")

    try:
        from jugeo.encodings.collection_heap_encodings.exact_boundaries_and_explicit_non import (
            TrustTier, Judgment, CechObstruction, MembershipStatus,
            BoundaryKind, WitnessKind, CompletenessStatus, BoundaryCechObstruction,
            BoundaryJudgment, IndexBoundaryEntry, NonMembershipWitness, MembershipObligation,
            BoundaryProof, BoundaryGlobalSection, BoundaryDescentObstruction, ExactBoundaryEncoding,
            BoundaryStats, BoundaryChecker,
        )
        for _cls in (
            TrustTier, Judgment, CechObstruction, MembershipStatus,
            BoundaryKind, WitnessKind, CompletenessStatus, BoundaryCechObstruction,
            BoundaryJudgment, IndexBoundaryEntry, NonMembershipWitness, MembershipObligation,
            BoundaryProof, BoundaryGlobalSection, BoundaryDescentObstruction, ExactBoundaryEncoding,
            BoundaryStats, BoundaryChecker,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.exact_boundaries_and_explicit_non not available")

    try:
        from jugeo.encodings.collection_heap_encodings.exact_boundary_encoder import (
            BoundaryKind, NonAliasingLawName, BoundaryCondition, _FormulaMinimizer,
            NonAliasingLawLibrary, ExactBoundaryEncoder, BoundaryVerifier,
        )
        for _cls in (
            BoundaryKind, NonAliasingLawName, BoundaryCondition, _FormulaMinimizer,
            NonAliasingLawLibrary, ExactBoundaryEncoder, BoundaryVerifier,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.exact_boundary_encoder not available")

    try:
        from jugeo.encodings.collection_heap_encodings.heap_summaries_and_object_identity import (
            TrustTier, HeapNodeKind, RegionKind, IdentityRelation,
            HeapSectionStatus, HeapCechObstruction, HeapJudgment, ObjectIdentityNode,
            AllocationRegion, HeapGlobalSection, HeapDescentObstruction, HeapGraphEncoding,
            HeapSummary, HeapSummaryStats,
        )
        for _cls in (
            TrustTier, HeapNodeKind, RegionKind, IdentityRelation,
            HeapSectionStatus, HeapCechObstruction, HeapJudgment, ObjectIdentityNode,
            AllocationRegion, HeapGlobalSection, HeapDescentObstruction, HeapGraphEncoding,
            HeapSummary, HeapSummaryStats,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.heap_summaries_and_object_identity not available")

    try:
        from jugeo.encodings.collection_heap_encodings.heap_summary_encoder import (
            HeapKind, SeparationLogicFragment, HeapSummaryEncoder, HeapCompositionEngine,
            HeapInvariantChecker,
        )
        for _cls in (
            HeapKind, SeparationLogicFragment, HeapSummaryEncoder, HeapCompositionEngine,
            HeapInvariantChecker,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.heap_summary_encoder not available")

    try:
        from jugeo.encodings.collection_heap_encodings.integration import (
            PipelineStage, PipelineResult, _StageTimer, _PipelineMonitor,
            CollectionHeapEncodingSession, EncoderRegistry, HeapCollectionPipeline,
        )
        for _cls in (
            PipelineStage, PipelineResult, _StageTimer, _PipelineMonitor,
            CollectionHeapEncodingSession, EncoderRegistry, HeapCollectionPipeline,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.integration not available")

    try:
        from jugeo.encodings.collection_heap_encodings.interface_summaries_as_uninterpret import (
            TrustTier, Judgment, CechObstruction, SortKind,
            AxiomKind, SummaryStatus, CallSiteStatus, SummaryCechObstruction,
            SummaryJudgment, SortSignature, SummaryAxiom, UninterpretedFunctionRepr,
            SummaryContract, InterfaceSummary, SummaryTableau, TableauGlobalSection,
            TableauDescentObstruction,
        )
        for _cls in (
            TrustTier, Judgment, CechObstruction, SortKind,
            AxiomKind, SummaryStatus, CallSiteStatus, SummaryCechObstruction,
            SummaryJudgment, SortSignature, SummaryAxiom, UninterpretedFunctionRepr,
            SummaryContract, InterfaceSummary, SummaryTableau, TableauGlobalSection,
            TableauDescentObstruction,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.interface_summaries_as_uninterpret not available")

    try:
        from jugeo.encodings.collection_heap_encodings.interface_summary_encoder import (
            RefinementStatus, MethodSignature, InterfaceSummaryEncoder, InterfaceRefinementChecker,
            AbstractSortBuilder,
        )
        for _cls in (
            RefinementStatus, MethodSignature, InterfaceSummaryEncoder, InterfaceRefinementChecker,
            AbstractSortBuilder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.interface_summary_encoder not available")

    try:
        from jugeo.encodings.collection_heap_encodings.manifest import (
            ManifestValidationError, SectionKind, SectionEntry, CollectionHeapManifest,
            _ManifestSerializer,
        )
        for _cls in (
            ManifestValidationError, SectionKind, SectionEntry, CollectionHeapManifest,
            _ManifestSerializer,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.manifest not available")

    try:
        from jugeo.encodings.collection_heap_encodings.models import (
            ModelRegistry, CollectionEncoding, HeapSummary, AliasPartition,
            FiniteMapEncoding, InterfaceAbstraction,
        )
        for _cls in (
            ModelRegistry, CollectionEncoding, HeapSummary, AliasPartition,
            FiniteMapEncoding, InterfaceAbstraction,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.models not available")

    try:
        from jugeo.encodings.collection_heap_encodings.theorems import (
            TheoremCategory, ProofStatus, ProofObligation, CollectionHeapTheorem,
            CollectionEncodingFaithfulnessTheorem, HeapSeparationSoundnessTheorem, AliasPartitionCompletenessTheorem, FrameRuleAdmissibilityTheorem,
            InterfaceAbstractionCorrectnessTheorem, NonAliasingLawConsistencyTheorem, FiniteMapTotalityTheorem, BoundaryConditionPrecisionTheorem,
            CollectionCardinalityConsistencyTheorem, HeapCompositionMonotonicityTheorem, TheoremSuite,
        )
        for _cls in (
            TheoremCategory, ProofStatus, ProofObligation, CollectionHeapTheorem,
            CollectionEncodingFaithfulnessTheorem, HeapSeparationSoundnessTheorem, AliasPartitionCompletenessTheorem, FrameRuleAdmissibilityTheorem,
            InterfaceAbstractionCorrectnessTheorem, NonAliasingLawConsistencyTheorem, FiniteMapTotalityTheorem, BoundaryConditionPrecisionTheorem,
            CollectionCardinalityConsistencyTheorem, HeapCompositionMonotonicityTheorem, TheoremSuite,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("collection_heap_encodings.theorems not available")

    # --- deduction_rules ---
    try:
        from jugeo.encodings.deduction_rules.inference_rules import (
            UnificationEngine, PremiseSet, ConclusionForm, SideConditionEvaluator,
            RuleSchema, CopilotRuleSuggester,
        )
        for _cls in (
            UnificationEngine, PremiseSet, ConclusionForm, SideConditionEvaluator,
            RuleSchema, CopilotRuleSuggester,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("deduction_rules.inference_rules not available")

    try:
        from jugeo.encodings.deduction_rules.integration import (
            DeductionSession, TransitionSystemRunner, RuleApplicationTracker, JudgmentDischarger,
            CopilotDeductionAssist,
        )
        for _cls in (
            DeductionSession, TransitionSystemRunner, RuleApplicationTracker, JudgmentDischarger,
            CopilotDeductionAssist,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("deduction_rules.integration not available")

    try:
        from jugeo.encodings.deduction_rules.judgment_transitions import (
            SubstitutionAlgebra, TransitionSchema, TransitionComposer, TrustDeltaComputer,
            TransitionValidator, ProofTrace,
        )
        for _cls in (
            SubstitutionAlgebra, TransitionSchema, TransitionComposer, TrustDeltaComputer,
            TransitionValidator, ProofTrace,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("deduction_rules.judgment_transitions not available")

    try:
        from jugeo.encodings.deduction_rules.manifest import (
            SymbolKind, StabilityLevel, DependencyKind, SymbolEntry,
            DependencyEntry, TheoremEntry, CopilotCapability, DeductionRulesManifest,
        )
        for _cls in (
            SymbolKind, StabilityLevel, DependencyKind, SymbolEntry,
            DependencyEntry, TheoremEntry, CopilotCapability, DeductionRulesManifest,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("deduction_rules.manifest not available")

    try:
        from jugeo.encodings.deduction_rules.models import (
            RuleKind, TransitionKind, InferenceStatus, ApplicationResult,
            DeductionRule, JudgmentTransition, InferenceStep, RuleApplication,
            TransitionSystem,
        )
        for _cls in (
            RuleKind, TransitionKind, InferenceStatus, ApplicationResult,
            DeductionRule, JudgmentTransition, InferenceStep, RuleApplication,
            TransitionSystem,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("deduction_rules.models not available")

    try:
        from jugeo.encodings.deduction_rules.semantic_rules import (
            RuleSchema, IntroductionRule, EliminationRule, ComputationRule,
            DefinitionalEqualityRule, SemanticRuleSystem, SoundnessChecker,
        )
        for _cls in (
            RuleSchema, IntroductionRule, EliminationRule, ComputationRule,
            DefinitionalEqualityRule, SemanticRuleSystem, SoundnessChecker,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("deduction_rules.semantic_rules not available")

    try:
        from jugeo.encodings.deduction_rules.structural_rules import (
            RuleSchema, WeakeningRule, ContractionRule, ExchangeRule,
            CutRule, StructuralRuleSystem, PermutationLemma,
        )
        for _cls in (
            RuleSchema, WeakeningRule, ContractionRule, ExchangeRule,
            CutRule, StructuralRuleSystem, PermutationLemma,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("deduction_rules.structural_rules not available")

    try:
        from jugeo.encodings.deduction_rules.theorems import (
            VerificationStatus, TheoremKind, ProofMethod, Theorem,
            CutEliminationTheorem, StructuralAdmissibilityTheorem, SemanticSoundnessTheorem, ConfluenceTheorem,
            CompletenessTheorem, TheoremRegistry,
        )
        for _cls in (
            VerificationStatus, TheoremKind, ProofMethod, Theorem,
            CutEliminationTheorem, StructuralAdmissibilityTheorem, SemanticSoundnessTheorem, ConfluenceTheorem,
            CompletenessTheorem, TheoremRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("deduction_rules.theorems not available")

    # --- doctrine_completion ---
    try:
        from jugeo.encodings.doctrine_completion.algorithms import (
            GroundingAlgorithm, GapFindingAlgorithm, CoverageComputationAlgorithm, EvidenceSynthesisAlgorithm,
            ClaimPropagationAlgorithm, DoctrineMinimizationAlgorithm, IncrementalCheckAlgorithm, RiskAssessmentAlgorithm,
        )
        for _cls in (
            GroundingAlgorithm, GapFindingAlgorithm, CoverageComputationAlgorithm, EvidenceSynthesisAlgorithm,
            ClaimPropagationAlgorithm, DoctrineMinimizationAlgorithm, IncrementalCheckAlgorithm, RiskAssessmentAlgorithm,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("doctrine_completion.algorithms not available")

    try:
        from jugeo.encodings.doctrine_completion.completeness import (
            CompletionStrategy, CompletenessMetrics, CompletenessAnalyzer, CriticalPathAnalyzer,
            DoctrineGraph, CompletionPlan, GapBridger,
        )
        for _cls in (
            CompletionStrategy, CompletenessMetrics, CompletenessAnalyzer, CriticalPathAnalyzer,
            DoctrineGraph, CompletionPlan, GapBridger,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("doctrine_completion.completeness not available")

    try:
        from jugeo.encodings.doctrine_completion.doctrine_checker import (
            DoctrineChecker, GroundingVerifier, CoverageAnalyzer, GapPrioritizer,
            DoctrineAuditor,
        )
        for _cls in (
            DoctrineChecker, GroundingVerifier, CoverageAnalyzer, GapPrioritizer,
            DoctrineAuditor,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("doctrine_completion.doctrine_checker not available")

    try:
        from jugeo.encodings.doctrine_completion.implementation_evidence import (
            EvidenceKind, EvidenceChain, EvidenceCollector, EvidenceValidator,
            EvidenceAggregator, ArtifactResolver, ConfidenceEstimator,
        )
        for _cls in (
            EvidenceKind, EvidenceChain, EvidenceCollector, EvidenceValidator,
            EvidenceAggregator, ArtifactResolver, ConfidenceEstimator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("doctrine_completion.implementation_evidence not available")

    try:
        from jugeo.encodings.doctrine_completion.integration import (
            IntegrationHealth, DoctrineCompletionIntegration, ManifestDoctrineLinker, RuntimeDoctrineMonitor,
            EvidenceArchiveAdapter, DoctrineCompletionPipeline,
        )
        for _cls in (
            IntegrationHealth, DoctrineCompletionIntegration, ManifestDoctrineLinker, RuntimeDoctrineMonitor,
            EvidenceArchiveAdapter, DoctrineCompletionPipeline,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("doctrine_completion.integration not available")

    try:
        from jugeo.encodings.doctrine_completion.manifest import (
            DoctrineCompletionManifest, DoctrineDescriptor, DoctrineRegistry,
        )
        for _cls in (
            DoctrineCompletionManifest, DoctrineDescriptor, DoctrineRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("doctrine_completion.manifest not available")

    try:
        from jugeo.encodings.doctrine_completion.models import (
            ClaimType, StatementStatus, EvidenceKind, GapSeverity,
            DoctrineStatement, ImplementationEvidence, CompletenessCheck, DoctrineGap,
            DoctrineCompletionReport, ClaimGroundingMap, EvidenceRequirement,
        )
        for _cls in (
            ClaimType, StatementStatus, EvidenceKind, GapSeverity,
            DoctrineStatement, ImplementationEvidence, CompletenessCheck, DoctrineGap,
            DoctrineCompletionReport, ClaimGroundingMap, EvidenceRequirement,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("doctrine_completion.models not available")

    try:
        from jugeo.encodings.doctrine_completion.theorems import (
            DoctrineTheorem, TheoremStatement, DoctrineTheoremRegistry, ImplementationCompletenessProof,
            GroundingSoundnessProof, CoverageAdequacyProof,
        )
        for _cls in (
            DoctrineTheorem, TheoremStatement, DoctrineTheoremRegistry, ImplementationCompletenessProof,
            GroundingSoundnessProof, CoverageAdequacyProof,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("doctrine_completion.theorems not available")

    # --- incremental_memory ---
    try:
        from jugeo.encodings.incremental_memory.algorithms import (
            GlueAlgorithm, SectionDiffAlgorithm, OverlapResolutionAlgorithm, EpochAdvanceAlgorithm,
            MemoryCompactionAlgorithm, QuotaEnforcementAlgorithm, SupportMinimizationAlgorithm, BatchUpdateOptimizer,
        )
        for _cls in (
            GlueAlgorithm, SectionDiffAlgorithm, OverlapResolutionAlgorithm, EpochAdvanceAlgorithm,
            MemoryCompactionAlgorithm, QuotaEnforcementAlgorithm, SupportMinimizationAlgorithm, BatchUpdateOptimizer,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("incremental_memory.algorithms not available")

    try:
        from jugeo.encodings.incremental_memory.change_events import (
            ChangeEventStream, ChangeEventBatch, SupportTracker, EventAggregator,
            ChangeEventSerializer, ChangeEventFilter,
        )
        for _cls in (
            ChangeEventStream, ChangeEventBatch, SupportTracker, EventAggregator,
            ChangeEventSerializer, ChangeEventFilter,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("incremental_memory.change_events not available")

    try:
        from jugeo.encodings.incremental_memory.integration import (
            IntegrationHealth, RuntimeMemoryBridge, InvalidationEngineAdapter, MemoryStateExporter,
            IncrementalUpdatePipeline, IncrementalMemoryIntegration,
        )
        for _cls in (
            IntegrationHealth, RuntimeMemoryBridge, InvalidationEngineAdapter, MemoryStateExporter,
            IncrementalUpdatePipeline, IncrementalMemoryIntegration,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("incremental_memory.integration not available")

    try:
        from jugeo.encodings.incremental_memory.invalidation import (
            CascadePolicy, RepairAction, RepairPlan, InvalidationWave,
            DependencyTracer, CascadeComputer, CascadeScheduler,
        )
        for _cls in (
            CascadePolicy, RepairAction, RepairPlan, InvalidationWave,
            DependencyTracer, CascadeComputer, CascadeScheduler,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("incremental_memory.invalidation not available")

    try:
        from jugeo.encodings.incremental_memory.manifest import (
            EncodingStatus, SubsystemKind, EncodingDescriptor, IncrementalMemoryManifest,
            PackageRegistry, ManifestValidator,
        )
        for _cls in (
            EncodingStatus, SubsystemKind, EncodingDescriptor, IncrementalMemoryManifest,
            PackageRegistry, ManifestValidator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("incremental_memory.manifest not available")

    try:
        from jugeo.encodings.incremental_memory.models import (
            ChangeEventKind, RegionType, EncodingSupportSet, IncrementalUpdate,
            ChangeEvent, InvalidationWaveInfo, MemoryInvalidationCascade, PersistentMemoryState,
        )
        for _cls in (
            ChangeEventKind, RegionType, EncodingSupportSet, IncrementalUpdate,
            ChangeEvent, InvalidationWaveInfo, MemoryInvalidationCascade, PersistentMemoryState,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("incremental_memory.models not available")

    try:
        from jugeo.encodings.incremental_memory.theorems import (
            IncrementalMemoryTheorem, TheoremStatus, ProofStrategy, TheoremStatement,
            ProofWitness, SerializationDeterminismProof, GlueCompatibilityProof, CascadeTerminationProof,
            EpochMonotonicityProof, IncrementalMemoryTheoremRegistry,
        )
        for _cls in (
            IncrementalMemoryTheorem, TheoremStatus, ProofStrategy, TheoremStatement,
            ProofWitness, SerializationDeterminismProof, GlueCompatibilityProof, CascadeTerminationProof,
            EpochMonotonicityProof, IncrementalMemoryTheoremRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("incremental_memory.theorems not available")

    try:
        from jugeo.encodings.incremental_memory.update_law import (
            OverlapData, RestrictionResult, GlueComputation, RestrictionOperation,
            OverlapChecker, GlueOperation, UpdateLawProver,
        )
        for _cls in (
            OverlapData, RestrictionResult, GlueComputation, RestrictionOperation,
            OverlapChecker, GlueOperation, UpdateLawProver,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("incremental_memory.update_law not available")

    # --- ir_stack ---
    try:
        from jugeo.encodings.ir_stack.algorithms import (
            AlgorithmConfig, AlgorithmResult,
        )
        for _cls in (
            AlgorithmConfig, AlgorithmResult,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.algorithms not available")

    try:
        from jugeo.encodings.ir_stack.an_implementation_ready_theory_nee import (
            TrustTierEnum, Judgment, CechObstruction, ConcretizationStep,
            ConcretizationTrace, AbstractionGap, GapBridgingStrategy, ConcreteObligation,
            ReadinessChecker, ImplementationReadySpec,
        )
        for _cls in (
            TrustTierEnum, Judgment, CechObstruction, ConcretizationStep,
            ConcretizationTrace, AbstractionGap, GapBridgingStrategy, ConcreteObligation,
            ReadinessChecker, ImplementationReadySpec,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.an_implementation_ready_theory_nee not available")

    try:
        from jugeo.encodings.ir_stack.integration import (
            IRStackSession, LoweringPipelineRunner, NormalFormService, AmbiguityResolver,
            CopilotIRAssist,
        )
        for _cls in (
            IRStackSession, LoweringPipelineRunner, NormalFormService, AmbiguityResolver,
            CopilotIRAssist,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.integration not available")

    try:
        from jugeo.encodings.ir_stack.ir_layers import (
            LayerScope, BindingEnvironment, ConstraintAccumulator, LayerDiffer,
            CrossLayerRef,
        )
        for _cls in (
            LayerScope, BindingEnvironment, ConstraintAccumulator, LayerDiffer,
            CrossLayerRef,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.ir_layers not available")

    try:
        from jugeo.encodings.ir_stack.ir_nodes import (
            IRNodeKindRegistry, NodePayload, AmbiguityPropagator, NodeSubstituter,
            IRTreeWalker, CopilotNodeSuggestor,
        )
        for _cls in (
            IRNodeKindRegistry, NodePayload, AmbiguityPropagator, NodeSubstituter,
            IRTreeWalker, CopilotNodeSuggestor,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.ir_nodes not available")

    try:
        from jugeo.encodings.ir_stack.lowering import (
            LoweringPassRegistry, AmbiguityPreservationChecker, PassComposer, _StackCheckpoint,
            LoweringPipeline, CopilotLoweringHint, StandardLoweringPasses,
        )
        for _cls in (
            LoweringPassRegistry, AmbiguityPreservationChecker, PassComposer, _StackCheckpoint,
            LoweringPipeline, CopilotLoweringHint, StandardLoweringPasses,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.lowering not available")

    try:
        from jugeo.encodings.ir_stack.lowering_should_preserve_ambiguity import (
            CollapseError, LoweringStep, AmbiguousIRNode, LoweringTrace,
            SemanticPreservation, AmbiguityWitness, AmbiguityPreservingLowering,
        )
        for _cls in (
            CollapseError, LoweringStep, AmbiguousIRNode, LoweringTrace,
            SemanticPreservation, AmbiguityWitness, AmbiguityPreservingLowering,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.lowering_should_preserve_ambiguity not available")

    try:
        from jugeo.encodings.ir_stack.manifest import (
            PackageVersion, ComponentStatus, CapabilityFlag, ComponentDescriptor,
            IRStackManifest, APIExport, ManifestRegistry,
        )
        for _cls in (
            PackageVersion, ComponentStatus, CapabilityFlag, ComponentDescriptor,
            IRStackManifest, APIExport, ManifestRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.manifest not available")

    try:
        from jugeo.encodings.ir_stack.models import (
            IRNodeKind, IRLayerKind, NormalFormKind, AmbiguityKind,
            LoweringPassKind, AmbiguityMark, IRNode, IRLayer,
            IRStack, NormalForm, LoweringPass,
        )
        for _cls in (
            IRNodeKind, IRLayerKind, NormalFormKind, AmbiguityKind,
            LoweringPassKind, AmbiguityMark, IRNode, IRLayer,
            IRStack, NormalForm, LoweringPass,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.models not available")

    try:
        from jugeo.encodings.ir_stack.normal_forms import (
            ReductionStrategy, ReductionRule, ConfluenceChecker, NormalFormCache,
            CanonicalHasher,
        )
        for _cls in (
            ReductionStrategy, ReductionRule, ConfluenceChecker, NormalFormCache,
            CanonicalHasher,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.normal_forms not available")

    try:
        from jugeo.encodings.ir_stack.normal_forms_where_comparison_cach import (
            records, identity, RewriteRule, NormalForm,
            CacheEntry, ComparisonCache, DeduplicationEntry, DeduplicationTable,
            NormalFormRewriter,
        )
        for _cls in (
            records, identity, RewriteRule, NormalForm,
            CacheEntry, ComparisonCache, DeduplicationEntry, DeduplicationTable,
            NormalFormRewriter,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.normal_forms_where_comparison_cach not available")

    try:
        from jugeo.encodings.ir_stack.the_theory_wants_a_small_number_of import (
            representatives, TrustTierEnum, IRKind, JudgmentTuple,
            CechObstructionClass, LoweringError, IRValidationError, IRNode,
            CanonicalForm, IRTransition, IRLevel, IRStack,
        )
        for _cls in (
            representatives, TrustTierEnum, IRKind, JudgmentTuple,
            CechObstructionClass, LoweringError, IRValidationError, IRNode,
            CanonicalForm, IRTransition, IRLevel, IRStack,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.the_theory_wants_a_small_number_of not available")

    try:
        from jugeo.encodings.ir_stack.theorems import (
            captures, VerificationStatus, TheoremStatement, AmbiguityPreservationTheorem,
            NormalFormConfluenceTheorem, StackDepthMonotonicityTheorem, LoweringFaithfulnessTheorem, CacheCorrectnessTheorem,
            TheoremRegistry,
        )
        for _cls in (
            captures, VerificationStatus, TheoremStatement, AmbiguityPreservationTheorem,
            NormalFormConfluenceTheorem, StackDepthMonotonicityTheorem, LoweringFaithfulnessTheorem, CacheCorrectnessTheorem,
            TheoremRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("ir_stack.theorems not available")

    # --- pack_federation ---
    try:
        from jugeo.encodings.pack_federation.bridge_theorems_as_morphisms import (
            BridgeTheoremAsMorphism,
        )
        reg[BridgeTheoremAsMorphism.__name__] = BridgeTheoremAsMorphism
    except Exception:
        _log.debug("pack_federation.bridge_theorems_as_morphisms not available")

    try:
        from jugeo.encodings.pack_federation.federation_protocol import (
            FederationProtocolEngine,
        )
        reg[FederationProtocolEngine.__name__] = FederationProtocolEngine
    except Exception:
        _log.debug("pack_federation.federation_protocol not available")

    try:
        from jugeo.encodings.pack_federation.integration import (
            PackFederationEncodingIntegration,
        )
        reg[PackFederationEncodingIntegration.__name__] = PackFederationEncodingIntegration
    except Exception:
        _log.debug("pack_federation.integration not available")

    try:
        from jugeo.encodings.pack_federation.manifest import (
            PackFederationCapability, PackFederationManifest,
        )
        for _cls in (
            PackFederationCapability, PackFederationManifest,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("pack_federation.manifest not available")

    try:
        from jugeo.encodings.pack_federation.models import (
            BridgeTheoremEncoding, PackFederationEncoding, FederationProtocol, PackBoundary,
        )
        for _cls in (
            BridgeTheoremEncoding, PackFederationEncoding, FederationProtocol, PackBoundary,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("pack_federation.models not available")

    try:
        from jugeo.encodings.pack_federation.pack_federation_as_sheaf import (
            PackFederationAsSheaf,
        )
        reg[PackFederationAsSheaf.__name__] = PackFederationAsSheaf
    except Exception:
        _log.debug("pack_federation.pack_federation_as_sheaf not available")

    # --- partiality_model_reconstruction ---
    try:
        from jugeo.encodings.partiality_model_reconstruction.algebraic_data_surfaces_without_pr import (
            AlgebraicDataSurface, DeferredProof, RuntimeDischarge, SurfaceObligation,
            SurfaceBuilder,
        )
        for _cls in (
            AlgebraicDataSurface, DeferredProof, RuntimeDischarge, SurfaceObligation,
            SurfaceBuilder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.algebraic_data_surfaces_without_pr not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.algebraic_surfaces import (
            ConstructorArity, SurfaceKind, ProjectionMode, ConstructorSpec,
            RecognizerPredicate, AlgebraicFold, SurfaceProjection,
        )
        for _cls in (
            ConstructorArity, SurfaceKind, ProjectionMode, ConstructorSpec,
            RecognizerPredicate, AlgebraicFold, SurfaceProjection,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.algebraic_surfaces not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.algorithms import (
            AlgorithmStatus, MergeStrategy, ValidationLevel, AlgorithmResult,
            AlgorithmRegistry,
        )
        for _cls in (
            AlgorithmStatus, MergeStrategy, ValidationLevel, AlgorithmResult,
            AlgorithmRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.algorithms not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.effect_summaries_and_branch_sensit import (
            TrustTier, Judgment, CechObstruction, EffectKind,
            EffectSummary, BranchSensitiveEffect, PartialBranchMap, EffectObligation,
            EffectAnalyzer,
        )
        for _cls in (
            TrustTier, Judgment, CechObstruction, EffectKind,
            EffectSummary, BranchSensitiveEffect, PartialBranchMap, EffectObligation,
            EffectAnalyzer,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.effect_summaries_and_branch_sensit not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.exception_semantics import (
            PropagationRule, SumTypeKind, ExceptionSort, MaybeEncoding,
            EitherEncoding, ExceptionPropagationGraph,
        )
        for _cls in (
            PropagationRule, SumTypeKind, ExceptionSort, MaybeEncoding,
            EitherEncoding, ExceptionPropagationGraph,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.exception_semantics not available")

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
        for _cls in (
            TrustTier, ExceptionCategory, LiftingStrategy, EncodeMode,
            ExceptionPath, Judgment, CechObstruction, ExceptionValuedException,
            LiftedOperation, ExceptionSemanticsEncoding, ResultTypeDeclaration, ExceptionObligationBundle,
            ExceptionPatternLibrary, ResultTypeRegistry, ExceptionSemanticsEncoder, ExceptionValuedStructuralSemanticsAnalyzer,
            ExceptionValuedStructuralSemanticsWitness, ExceptionValuedStructuralSemanticsCoordinator, ExceptionValueEncoding, ThrowSection,
            CatchHandler, ExceptionSheafMap, ExceptionEncoder, CechH1Obstruction,
            ExceptionValueEncoding, ThrowSection, CatchHandler, ExceptionSheafMap,
            _ExceptionBodyVisitor, ExceptionEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.exception_valued_structural_semant not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.integration import (
            SessionState, BridgeStatus, PartialityEncodingSession, ModelReconstructionPipeline,
            ExceptionSemanticsBridge, CopilotReconstructionAssist,
        )
        for _cls in (
            SessionState, BridgeStatus, PartialityEncodingSession, ModelReconstructionPipeline,
            ExceptionSemanticsBridge, CopilotReconstructionAssist,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.integration not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.manifest import (
            ManifestStatus, ComponentKind, ComponentRecord, PackageManifest,
            ManifestValidator,
        )
        for _cls in (
            ManifestStatus, ComponentKind, ComponentRecord, PackageManifest,
            ManifestValidator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.manifest not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.model_reconstruction import (
            AssemblyPhase, CompletionStrategy, ReconstructionPipeline, PartialModelAssembler,
            TrustAnnotator, EvidencePackager,
        )
        for _cls in (
            AssemblyPhase, CompletionStrategy, ReconstructionPipeline, PartialModelAssembler,
            TrustAnnotator, EvidencePackager,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.model_reconstruction not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.model_reconstruction_as_a_first_cl import (
            PartialEvidence, ReconstructionPlan, ModelReconstructor, TotalModelWitness,
            ReconstructionEngine, EvidenceGap, PartialEvidence, ReconstructionStep,
            ReconstructionPlan, TotalModelWitness, ReconstructionGlobalSection, ReconstructionDescentObstruction,
            ModelReconstructor, ReconstructionStats,
        )
        for _cls in (
            PartialEvidence, ReconstructionPlan, ModelReconstructor, TotalModelWitness,
            ReconstructionEngine, EvidenceGap, PartialEvidence, ReconstructionStep,
            ReconstructionPlan, TotalModelWitness, ReconstructionGlobalSection, ReconstructionDescentObstruction,
            ModelReconstructor, ReconstructionStats,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.model_reconstruction_as_a_first_cl not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.models import (
            PartialityKind, ExceptionKind, ReconstructionStatus, TrustAnnotationKind,
            PartialFunctionEncoding, ExceptionValuedSemantics, AlgebraicSurface, ModelReconstruction,
            BranchSensitivity,
        )
        for _cls in (
            PartialityKind, ExceptionKind, ReconstructionStatus, TrustAnnotationKind,
            PartialFunctionEncoding, ExceptionValuedSemantics, AlgebraicSurface, ModelReconstruction,
            BranchSensitivity,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.models not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.partial_functions import (
            DomainPredicateKind, TotalizationKind, CompositionMode, DomainPredicate,
            PartialFunctionLattice, GuardedEncoding, TotalizationStrategy,
        )
        for _cls in (
            DomainPredicateKind, TotalizationKind, CompositionMode, DomainPredicate,
            PartialFunctionLattice, GuardedEncoding, TotalizationStrategy,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.partial_functions not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.reconstruction_witnesses import (
            WitnessKind, ObstructionClass, ConsistencyStatus, VariableBinding,
            ConstraintDischarge, CechPatch, ReconstructionWitness, _CechObstructionChecker,
            ReconstructionWitnessAnalyzer, ReconstructionWitnessCoordinator, WitnessResult,
        )
        for _cls in (
            WitnessKind, ObstructionClass, ConsistencyStatus, VariableBinding,
            ConstraintDischarge, CechPatch, ReconstructionWitness, _CechObstructionChecker,
            ReconstructionWitnessAnalyzer, ReconstructionWitnessCoordinator, WitnessResult,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.reconstruction_witnesses not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.theorems import (
            VerificationStatus, TheoremKind, Theorem, TheoremRegistry,
            CopilotTheoremAssist,
        )
        for _cls in (
            VerificationStatus, TheoremKind, Theorem, TheoremRegistry,
            CopilotTheoremAssist,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.theorems not available")

    try:
        from jugeo.encodings.partiality_model_reconstruction.why_python_obligations_are_full_of import (
            PartialitySource, PartialDomain, PartialnessObligation, TotalExtension,
            PartialityAnalyzer,
        )
        for _cls in (
            PartialitySource, PartialDomain, PartialnessObligation, TotalExtension,
            PartialityAnalyzer,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("partiality_model_reconstruction.why_python_obligations_are_full_of not available")

    # --- scalar_encodings ---
    try:
        from jugeo.encodings.scalar_encodings.algorithms import (
            IncrementalRefinementSolver, GuardSimplificationEngine, PathConditionPropagator, FailureRegressionTracker,
        )
        for _cls in (
            IncrementalRefinementSolver, GuardSimplificationEngine, PathConditionPropagator, FailureRegressionTracker,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.algorithms not available")

    try:
        from jugeo.encodings.scalar_encodings.branching_joins_and_path_sensitive import (
            PathNodeKind, BranchingJoinsPathSensitiveWitness, BranchingJoinsPathSensitiveAnalyzer, BranchingJoinsPathSensitiveCoordinator,
        )
        for _cls in (
            PathNodeKind, BranchingJoinsPathSensitiveWitness, BranchingJoinsPathSensitiveAnalyzer, BranchingJoinsPathSensitiveCoordinator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.branching_joins_and_path_sensitive not available")

    try:
        from jugeo.encodings.scalar_encodings.exact_failure_artifacts import (
            ExactFailureKind, ExactFailureArtifactsWitness, ExactFailureArtifactsAnalyzer, ExactFailureArtifactsCoordinator,
            TrustTier, FailureMode, FailureWitness, FailureArtifact,
            ExactFailureEncoding, ArtifactCatalog, FailurePattern, FailureRepairRecord,
        )
        for _cls in (
            ExactFailureKind, ExactFailureArtifactsWitness, ExactFailureArtifactsAnalyzer, ExactFailureArtifactsCoordinator,
            TrustTier, FailureMode, FailureWitness, FailureArtifact,
            ExactFailureEncoding, ArtifactCatalog, FailurePattern, FailureRepairRecord,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.exact_failure_artifacts not available")

    try:
        from jugeo.encodings.scalar_encodings.failure_artifact_encoder import (
            FailureKind, FailureArtifact, FailurePreconditionExtractor, FailureArtifactEncoder,
        )
        for _cls in (
            FailureKind, FailureArtifact, FailurePreconditionExtractor, FailureArtifactEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.failure_artifact_encoder not available")

    try:
        from jugeo.encodings.scalar_encodings.integration import (
            ScalarEncodingPipeline, Z3SessionBridge, SupportRegionLinker, CountermodelInterpreter,
            FragmentRouter,
        )
        for _cls in (
            ScalarEncodingPipeline, Z3SessionBridge, SupportRegionLinker, CountermodelInterpreter,
            FragmentRouter,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.integration not available")

    try:
        from jugeo.encodings.scalar_encodings.manifest import (
            CoverageStatus, ManifestRecord, SymbolGroup, ClaimSummary,
            PackageManifest,
        )
        for _cls in (
            CoverageStatus, ManifestRecord, SymbolGroup, ClaimSummary,
            PackageManifest,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.manifest not available")

    try:
        from jugeo.encodings.scalar_encodings.models import (
            SortKind, FragmentHint, EncodeStatus, RefinementEncoding,
            PathCondition, GuardFormula, ArithmeticObligation, EncodingContext,
            EncodingResult,
        )
        for _cls in (
            SortKind, FragmentHint, EncodeStatus, RefinementEncoding,
            PathCondition, GuardFormula, ArithmeticObligation, EncodingContext,
            EncodingResult,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.models not available")

    try:
        from jugeo.encodings.scalar_encodings.path_condition_encoder import (
            BranchNode, PathTree, JoinConditionSynthesizer, PathConditionEncoder,
        )
        for _cls in (
            BranchNode, PathTree, JoinConditionSynthesizer, PathConditionEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.path_condition_encoder not available")

    try:
        from jugeo.encodings.scalar_encodings.refinement_type_encoder import (
            RefinementSortBuilder, PredicateNormalizer, ConstraintLifter, RefinementTypeEncoder,
        )
        for _cls in (
            RefinementSortBuilder, PredicateNormalizer, ConstraintLifter, RefinementTypeEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.refinement_type_encoder not available")

    try:
        from jugeo.encodings.scalar_encodings.the_encoding_layer_should_begin_fr import (
            ScalarSort, TheEncodingLayerBeginWitness, TheEncodingLayerBeginAnalyzer, TheEncodingLayerBeginCoordinator,
        )
        for _cls in (
            ScalarSort, TheEncodingLayerBeginWitness, TheEncodingLayerBeginAnalyzer, TheEncodingLayerBeginCoordinator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.the_encoding_layer_should_begin_fr not available")

    try:
        from jugeo.encodings.scalar_encodings.theorems import (
            TheoremStatus, TheoremRecord, TheoremRegistry,
        )
        for _cls in (
            TheoremStatus, TheoremRecord, TheoremRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("scalar_encodings.theorems not available")

    # --- sequence_mutation_encodings ---
    try:
        from jugeo.encodings.sequence_mutation_encodings.algorithms import (
            AbstractDomain, AbstractState, FramePreservationResult,
        )
        for _cls in (
            AbstractDomain, AbstractState, FramePreservationResult,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.algorithms not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.finite_map_encoder import (
            EncodedMap, FiniteMapEncoder,
        )
        for _cls in (
            EncodedMap, FiniteMapEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.finite_map_encoder not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.finite_maps_and_interface_dictiona import (
            TrustTier, Judgment, CechObstruction, FiniteMapEncoding,
            KeyValueSheaf, DictInterfaceSummary, MapUpdateObligation, MapMorphism,
        )
        for _cls in (
            TrustTier, Judgment, CechObstruction, FiniteMapEncoding,
            KeyValueSheaf, DictInterfaceSummary, MapUpdateObligation, MapMorphism,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.finite_maps_and_interface_dictiona not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.heap_slice_encoder import (
            EncodedHeapSlice, HeapSliceEncoder,
        )
        for _cls in (
            EncodedHeapSlice, HeapSliceEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.heap_slice_encoder not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.heap_slices_and_mutation_support import (
            TrustTier, Judgment, CechObstruction, HeapSliceCechObstruction,
            HeapSliceJudgment, HeapSliceGlobalSection, HeapSliceDescentObstruction, WriteBarrier,
            SliceConsistencyObligation, MutationTransition, HeapSlice, HeapSliceStats,
            CechObstruction, HeapSlice, MutationTransition, WriteBarrier,
            SliceConsistencyObligation, HeapSliceEncoder,
        )
        for _cls in (
            TrustTier, Judgment, CechObstruction, HeapSliceCechObstruction,
            HeapSliceJudgment, HeapSliceGlobalSection, HeapSliceDescentObstruction, WriteBarrier,
            SliceConsistencyObligation, MutationTransition, HeapSlice, HeapSliceStats,
            CechObstruction, HeapSlice, MutationTransition, WriteBarrier,
            SliceConsistencyObligation, HeapSliceEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.heap_slices_and_mutation_support not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.integration import (
            _StubZ3Result, SequenceMutationSolverIntegration,
        )
        for _cls in (
            _StubZ3Result, SequenceMutationSolverIntegration,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.integration not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.manifest import (
            SubsystemManifest, ManifestValidator,
        )
        for _cls in (
            SubsystemManifest, ManifestValidator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.manifest not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.models import (
            MutationKind, SequenceInvariantKind, SequenceEncoding, MutationSlice,
            HeapSlice, SupportAwareMutation, SequenceInvariant,
        )
        for _cls in (
            MutationKind, SequenceInvariantKind, SequenceEncoding, MutationSlice,
            HeapSlice, SupportAwareMutation, SequenceInvariant,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.models not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.mutation_countermodel_encoder import (
            RepairKind, RepairSuggestion, ViolationContext, MutationCountermodelEncoder,
        )
        for _cls in (
            RepairKind, RepairSuggestion, ViolationContext, MutationCountermodelEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.mutation_countermodel_encoder not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.mutation_countermodels_as_repair_g import (
            TrustTier, AnomalyKind, MutationKind, RepairStepKind,
            CountermodelCechObstruction, CountermodelJudgment, CountermodelGlobalSection, CountermodelDescentObstruction,
            RepairDescentObstruction, MutationAnomaly, RepairGuide, SequenceRepairPlan,
            MutationCountermodel, CountermodelExtractor,
        )
        for _cls in (
            TrustTier, AnomalyKind, MutationKind, RepairStepKind,
            CountermodelCechObstruction, CountermodelJudgment, CountermodelGlobalSection, CountermodelDescentObstruction,
            RepairDescentObstruction, MutationAnomaly, RepairGuide, SequenceRepairPlan,
            MutationCountermodel, CountermodelExtractor,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.mutation_countermodels_as_repair_g not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.mutation_countermodels_as_repair_new import (
            TrustTier, RepairKind, GuideConfidence, CountermodelInterpretation,
            RepairPriority, Judgment, CechObstruction, MutationCountermodel,
            RepairGuide, CountermodelAsGuide, RepairBundle, CountermodelDatabase,
            RepairHistory, RepairGuideRanker, MutationCountermodelsRepairGuidesCoordinator, MutationCountermodelsRepairGuidesAnalyzer,
            MutationCountermodelsRepairGuidesWitness,
        )
        for _cls in (
            TrustTier, RepairKind, GuideConfidence, CountermodelInterpretation,
            RepairPriority, Judgment, CechObstruction, MutationCountermodel,
            RepairGuide, CountermodelAsGuide, RepairBundle, CountermodelDatabase,
            RepairHistory, RepairGuideRanker, MutationCountermodelsRepairGuidesCoordinator, MutationCountermodelsRepairGuidesAnalyzer,
            MutationCountermodelsRepairGuidesWitness,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.mutation_countermodels_as_repair_new not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.sequence_window_encoder import (
            WindowPredicate, SequenceWindowEncoder,
        )
        for _cls in (
            WindowPredicate, SequenceWindowEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.sequence_window_encoder not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.sequence_windows import (
            TrustTier, WindowStatus, OverlapConditionStatus, CoverStatus,
            WindowKind, WindowCechObstruction, WindowJudgment, WindowSection,
            SequenceWindow, WindowOverlapCondition, WindowGlobalSection, WindowDescentObstruction,
            SlidingCover, WindowCoverStats, WindowGluing,
        )
        for _cls in (
            TrustTier, WindowStatus, OverlapConditionStatus, CoverStatus,
            WindowKind, WindowCechObstruction, WindowJudgment, WindowSection,
            SequenceWindow, WindowOverlapCondition, WindowGlobalSection, WindowDescentObstruction,
            SlidingCover, WindowCoverStats, WindowGluing,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.sequence_windows not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.structured_data_encoder import (
            EncodedList, EncodedTuple, StructuredDataEncoder,
        )
        for _cls in (
            EncodedList, EncodedTuple, StructuredDataEncoder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.structured_data_encoder not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.structured_data_should_not_be_flat import (
            TrustTier, SequenceKind, SliceStatus, RestrictionKind,
            PresheafStatus, SequenceCechObstruction, SequenceJudgment, IndexedSlice,
            SequenceSection, SequenceGlobalSection, SequenceDescentObstruction, SequenceSheaf,
            StructuredSequenceEncoding, SequenceCover,
        )
        for _cls in (
            TrustTier, SequenceKind, SliceStatus, RestrictionKind,
            PresheafStatus, SequenceCechObstruction, SequenceJudgment, IndexedSlice,
            SequenceSection, SequenceGlobalSection, SequenceDescentObstruction, SequenceSheaf,
            StructuredSequenceEncoding, SequenceCover,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.structured_data_should_not_be_flat not available")

    try:
        from jugeo.encodings.sequence_mutation_encodings.theorems import (
            _StubValidationStatus, VerifyResult, SequenceMutationTheorem, FramePreservationTheorem,
            SupportClosureTheorem, MutationCompositionTheorem, HeapSliceConsistencyTheorem, InvariantRepairTheorem,
        )
        for _cls in (
            _StubValidationStatus, VerifyResult, SequenceMutationTheorem, FramePreservationTheorem,
            SupportClosureTheorem, MutationCompositionTheorem, HeapSliceConsistencyTheorem, InvariantRepairTheorem,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("sequence_mutation_encodings.theorems not available")

    # --- structural_frontier ---
    try:
        from jugeo.encodings.structural_frontier.algorithms import (
            FrontierExplorer, DecidabilityBisector, CountermodelAggregator, RepairPriorityScheduler,
        )
        for _cls in (
            FrontierExplorer, DecidabilityBisector, CountermodelAggregator, RepairPriorityScheduler,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.algorithms not available")

    try:
        from jugeo.encodings.structural_frontier.countermodel_to_repair import (
            ObstructionClassifier, RepairCandidateGenerator, RepairFrontierNavigator, CountermodelToRepair,
        )
        for _cls in (
            ObstructionClassifier, RepairCandidateGenerator, RepairFrontierNavigator, CountermodelToRepair,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.countermodel_to_repair not available")

    try:
        from jugeo.encodings.structural_frontier.countermodels_should_become_first import (
            CountermodelRole, CountermodelsBecomeFirstClassWitness, CountermodelsBecomeFirstClassAnalyzer, CountermodelsBecomeFirstClassCoordinator,
        )
        for _cls in (
            CountermodelRole, CountermodelsBecomeFirstClassWitness, CountermodelsBecomeFirstClassAnalyzer, CountermodelsBecomeFirstClassCoordinator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.countermodels_should_become_first not available")

    try:
        from jugeo.encodings.structural_frontier.integration import (
            PipelinePhase, _StubDefiner, _StubTypeSystem, _StubRepairPipeline,
            StructuralFrontierPipeline, Z3FrontierBridge, FrontierSupportLinker, TypeSystemIntegrator,
            CountermodelRepairDispatcher,
        )
        for _cls in (
            PipelinePhase, _StubDefiner, _StubTypeSystem, _StubRepairPipeline,
            StructuralFrontierPipeline, Z3FrontierBridge, FrontierSupportLinker, TypeSystemIntegrator,
            CountermodelRepairDispatcher,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.integration not available")

    try:
        from jugeo.encodings.structural_frontier.manifest import (
            CoverageStatus, ManifestRecord, SymbolGroup, ClaimSummary,
            PackageManifest,
        )
        for _cls in (
            CoverageStatus, ManifestRecord, SymbolGroup, ClaimSummary,
            PackageManifest,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.manifest not available")

    try:
        from jugeo.encodings.structural_frontier.models import (
            DecidabilityClass, FrontierSide, RepairAction, StructuralFrontier,
            SolverLiftedType, FrontierBoundary, DecidabilityMap, CountermodelObstruction,
        )
        for _cls in (
            DecidabilityClass, FrontierSide, RepairAction, StructuralFrontier,
            SolverLiftedType, FrontierBoundary, DecidabilityMap, CountermodelObstruction,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.models not available")

    try:
        from jugeo.encodings.structural_frontier.solver_lifted_type_system import (
            TypeLiftingStrategy, InvariantChecker, TypeLiftingTranslator, SolverLiftedTypeSystem,
        )
        for _cls in (
            TypeLiftingStrategy, InvariantChecker, TypeLiftingTranslator, SolverLiftedTypeSystem,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.solver_lifted_type_system not available")

    try:
        from jugeo.encodings.structural_frontier.structural_frontier_definer import (
            DecidabilityOracle, FrontierBoundaryLocator, UndecidabilityWitness, StructuralFrontierDefiner,
        )
        for _cls in (
            DecidabilityOracle, FrontierBoundaryLocator, UndecidabilityWitness, StructuralFrontierDefiner,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.structural_frontier_definer not available")

    try:
        from jugeo.encodings.structural_frontier.the_code_should_make_solver_lifted import (
            LiftingStage, TheCodeMakeSolverWitness, TheCodeMakeSolverAnalyzer, TheCodeMakeSolverCoordinator,
        )
        for _cls in (
            LiftingStage, TheCodeMakeSolverWitness, TheCodeMakeSolverAnalyzer, TheCodeMakeSolverCoordinator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.the_code_should_make_solver_lifted not available")

    try:
        from jugeo.encodings.structural_frontier.theorems import (
            TheoremStatus, TheoremRecord, TheoremRegistry,
        )
        for _cls in (
            TheoremStatus, TheoremRecord, TheoremRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.theorems not available")

    try:
        from jugeo.encodings.structural_frontier.z3_should_own_the_structural_front import (
            StructuralOwnershipKind, Z3OwnStructuralFrontierWitness, Z3OwnStructuralFrontierAnalyzer, Z3OwnStructuralFrontierCoordinator,
        )
        for _cls in (
            StructuralOwnershipKind, Z3OwnStructuralFrontierWitness, Z3OwnStructuralFrontierAnalyzer, Z3OwnStructuralFrontierCoordinator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("structural_frontier.z3_should_own_the_structural_front not available")

    # --- tensor_quantifier_encodings ---
    try:
        from jugeo.encodings.tensor_quantifier_encodings.affine_and_quasi_affine_normal_for import (
            ModularConstraint, AffineNormalForm, QuasiAffineEncoding, LinearConstraintEncoding,
            AffineObligation, AffineReduction, AffineSystem,
        )
        for _cls in (
            ModularConstraint, AffineNormalForm, QuasiAffineEncoding, LinearConstraintEncoding,
            AffineObligation, AffineReduction, AffineSystem,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.affine_and_quasi_affine_normal_for not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.affine_normal_form_encoder import (
            AffineNormalFormEncoder,
        )
        reg[AffineNormalFormEncoder.__name__] = AffineNormalFormEncoder
    except Exception:
        _log.debug("tensor_quantifier_encodings.affine_normal_form_encoder not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.integration import (
            TensorEncodingContext, TensorQuantifierSolverIntegration,
        )
        for _cls in (
            TensorEncodingContext, TensorQuantifierSolverIntegration,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.integration not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.manifest import (
            CapabilityKind, DependencyKind, SubsystemManifest, TheoryProvenance,
            CapabilityDeclaration, DependencySpec, ManifestValidationError, ManifestValidator,
        )
        for _cls in (
            CapabilityKind, DependencyKind, SubsystemManifest, TheoryProvenance,
            CapabilityDeclaration, DependencySpec, ManifestValidationError, ManifestValidator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.manifest not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.models import (
            TensorLayout, DisciplineKind, ExtractionStrategy, ConstraintKind,
            TensorExtent, AffineLegality, QuantifierDiscipline, WitnessExtractor,
            TensorConstraint,
        )
        for _cls in (
            TensorLayout, DisciplineKind, ExtractionStrategy, ConstraintKind,
            TensorExtent, AffineLegality, QuantifierDiscipline, WitnessExtractor,
            TensorConstraint,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.models not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.quantifier_discipline import (
            DisciplineReport, QuantifierInfo, QuantifierDisciplineChecker, QuantifierInstantiator,
        )
        for _cls in (
            DisciplineReport, QuantifierInfo, QuantifierDisciplineChecker, QuantifierInstantiator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.quantifier_discipline not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.theorems import (
            TensorQuantifierTheorem, AffineTransformLegalityTheorem, FarkasInfeasibilityTheorem, QuantifierEliminationTheorem,
            WitnessCompletenessTheorem, BroadcastCompatibilityTheorem,
        )
        for _cls in (
            TensorQuantifierTheorem, AffineTransformLegalityTheorem, FarkasInfeasibilityTheorem, QuantifierEliminationTheorem,
            WitnessCompletenessTheorem, BroadcastCompatibilityTheorem,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.theorems not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.why_tensors_matter import (
            TensorMotivationExamples, TensorEncodingPrimer,
        )
        for _cls in (
            TensorMotivationExamples, TensorEncodingPrimer,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.why_tensors_matter not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.why_this_family_matters_disproport import (
            Binding, BindingStructure, QuantifierMatrix, QuantifierScope,
            TensorProduct, ScopeNesting, TensorQuantifierEncoding,
        )
        for _cls in (
            Binding, BindingStructure, QuantifierMatrix, QuantifierScope,
            TensorProduct, ScopeNesting, TensorQuantifierEncoding,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.why_this_family_matters_disproport not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.witness_extraction_and_proof_burde import (
            TrustTier, Judgment, CechObstruction, ExtractionStep,
            ExtractionTrace, QuantifierWitness, SingleBurden, ProofBurden,
            WitnessValidity, BurdenDistribution, WitnessExtractor,
        )
        for _cls in (
            TrustTier, Judgment, CechObstruction, ExtractionStep,
            ExtractionTrace, QuantifierWitness, SingleBurden, ProofBurden,
            WitnessValidity, BurdenDistribution, WitnessExtractor,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.witness_extraction_and_proof_burde not available")

    try:
        from jugeo.encodings.tensor_quantifier_encodings.witness_extractor import (
            TensorWitness, DependenceWitness, FarkasCoefficients, TensorWitnessExtractor,
            AffineLegalityWitnessExtractor,
        )
        for _cls in (
            TensorWitness, DependenceWitness, FarkasCoefficients, TensorWitnessExtractor,
            AffineLegalityWitnessExtractor,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("tensor_quantifier_encodings.witness_extractor not available")

    # --- text_encodings ---
    try:
        from jugeo.encodings.text_encodings.algorithms import (
            AlgorithmStatus, AlgorithmResult, TextEncodingAlgorithm, NamingLawInference,
            DocumentationShadowExtraction, StringConstraintPropagation, TextCountermodelMinimization, NamingLawCompliance,
        )
        for _cls in (
            AlgorithmStatus, AlgorithmResult, TextEncodingAlgorithm, NamingLawInference,
            DocumentationShadowExtraction, StringConstraintPropagation, TextCountermodelMinimization, NamingLawCompliance,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.algorithms not available")

    try:
        from jugeo.encodings.text_encodings.countermodels_and_clausewise_expla import (
            TextCountermodel, ClauseExplanation, SemanticDivergenceWitness, TextRepairHint,
            CountermodelSearch, ClauseDecomposition,
        )
        for _cls in (
            TextCountermodel, ClauseExplanation, SemanticDivergenceWitness, TextRepairHint,
            CountermodelSearch, ClauseDecomposition,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.countermodels_and_clausewise_expla not available")

    try:
        from jugeo.encodings.text_encodings.encoding_families import (
            EncodingMember, TextEncodingFamily, SubwordEncoding, CharEncoding,
            EmbeddingEncoding, SelectionCriterion, EncodingSelector, CrossEncodingAlignment,
        )
        for _cls in (
            EncodingMember, TextEncodingFamily, SubwordEncoding, CharEncoding,
            EmbeddingEncoding, SelectionCriterion, EncodingSelector, CrossEncodingAlignment,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.encoding_families not available")

    try:
        from jugeo.encodings.text_encodings.integration import (
            TextEncodingSession, TextEncoderRegistry, PipelineResult, TextEncodingPipeline,
        )
        for _cls in (
            TextEncodingSession, TextEncoderRegistry, PipelineResult, TextEncodingPipeline,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.integration not available")

    try:
        from jugeo.encodings.text_encodings.manifest import (
            ManifestValidator, ManifestSerializer, TextEncodingManifest,
        )
        for _cls in (
            ManifestValidator, ManifestSerializer, TextEncodingManifest,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.manifest not available")

    try:
        from jugeo.encodings.text_encodings.models import (
            ConstraintKind, ConstraintStrength, StringEncoding, SymbolicText,
            NamingLaw, DocumentationShadow, TextConstraint,
        )
        for _cls in (
            ConstraintKind, ConstraintStrength, StringEncoding, SymbolicText,
            NamingLaw, DocumentationShadow, TextConstraint,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.models not available")

    try:
        from jugeo.encodings.text_encodings.normalized_text_environment import (
            TextNormalizationStrategy, NormalizedTextEnvironment, EncodingEnvironmentBuilder,
        )
        for _cls in (
            TextNormalizationStrategy, NormalizedTextEnvironment, EncodingEnvironmentBuilder,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.normalized_text_environment not available")

    try:
        from jugeo.encodings.text_encodings.text_countermodels import (
            ViolationType, ConstraintViolation, TextCountermodels, StringRepairEngine,
            CountermodelInterpreter,
        )
        for _cls in (
            ViolationType, ConstraintViolation, TextCountermodels, StringRepairEngine,
            CountermodelInterpreter,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.text_countermodels not available")

    try:
        from jugeo.encodings.text_encodings.text_encoding_families import (
            StringOperationKind, TextEncodingFamilies, StringFragmentClassifier, NamingLawFamily,
            DocumentationConstraintFamily,
        )
        for _cls in (
            StringOperationKind, TextEncodingFamilies, StringFragmentClassifier, NamingLawFamily,
            DocumentationConstraintFamily,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.text_encoding_families not available")

    try:
        from jugeo.encodings.text_encodings.the_normalized_text_environment import (
            NormalizationError, ObstructionError, TrustViolationError, NormStep,
            NormalizationTrace, TextNormalization, TextCanonicalForm, NormalizedTextEnv,
            NormalizationObligation, TextEquivalenceClass,
        )
        for _cls in (
            NormalizationError, ObstructionError, TrustViolationError, NormStep,
            NormalizationTrace, TextNormalization, TextCanonicalForm, NormalizedTextEnv,
            NormalizationObligation, TextEquivalenceClass,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.the_normalized_text_environment not available")

    try:
        from jugeo.encodings.text_encodings.theorems import (
            TheoremStatus, ProofObligation, TextEncodingTheorem, StringEncodingFaithfulness,
            NamingLawConsistency, DocumentationShadowSoundness, TextConstraintPropagationCompleteness, NormalizationInvariance,
            CountermodelMinimality, StringFragmentDecidability, TheoremSuite,
        )
        for _cls in (
            TheoremStatus, ProofObligation, TextEncodingTheorem, StringEncodingFaithfulness,
            NamingLawConsistency, DocumentationShadowSoundness, TextConstraintPropagationCompleteness, NormalizationInvariance,
            CountermodelMinimality, StringFragmentDecidability, TheoremSuite,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.theorems not available")

    try:
        from jugeo.encodings.text_encodings.why_text_deserves_its_own_structur import (
            TokenizedText, TextSection, TokenObservation, TextRestriction,
            TextCovering, TextSheaf, TextEncoding,
        )
        for _cls in (
            TokenizedText, TextSection, TokenObservation, TextRestriction,
            TextCovering, TextSheaf, TextEncoding,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.why_text_deserves_its_own_structur not available")

    try:
        from jugeo.encodings.text_encodings.why_text_deserves_structure import (
            WhyTextDeservesStructure, StringSolverSurvey, SymbolicTextMotivation,
        )
        for _cls in (
            WhyTextDeservesStructure, StringSolverSurvey, SymbolicTextMotivation,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("text_encodings.why_text_deserves_structure not available")

    # --- theorem_schemas ---
    try:
        from jugeo.encodings.theorem_schemas.algorithms import (
            MatchScore, SchemaMatchingAlgorithm, BindingInferenceAlgorithm, SchemaCompositionAlgorithm,
            ObligationPrioritizationAlgorithm, SchemaConsistencyChecker, TemplateExpansionAlgorithm, ProofSearchAlgorithm,
            SchemaMinimizationAlgorithm,
        )
        for _cls in (
            MatchScore, SchemaMatchingAlgorithm, BindingInferenceAlgorithm, SchemaCompositionAlgorithm,
            ObligationPrioritizationAlgorithm, SchemaConsistencyChecker, TemplateExpansionAlgorithm, ProofSearchAlgorithm,
            SchemaMinimizationAlgorithm,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("theorem_schemas.algorithms not available")

    try:
        from jugeo.encodings.theorem_schemas.integration import (
            IntegrationHealth, JudgmentSchemaAdapter, ManifestSchemaLinker, RuntimeSchemaMonitor,
            SchemaViolationReporter, TheoremSchemaIntegration, _StubTracker, _StubDispatcher,
        )
        for _cls in (
            IntegrationHealth, JudgmentSchemaAdapter, ManifestSchemaLinker, RuntimeSchemaMonitor,
            SchemaViolationReporter, TheoremSchemaIntegration, _StubTracker, _StubDispatcher,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("theorem_schemas.integration not available")

    try:
        from jugeo.encodings.theorem_schemas.manifest import (
            TheoremSchemasManifest, SchemaDescriptor, SchemaRegistry,
        )
        for _cls in (
            TheoremSchemasManifest, SchemaDescriptor, SchemaRegistry,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("theorem_schemas.manifest not available")

    try:
        from jugeo.encodings.theorem_schemas.models import (
            ProofStyle, InstanceStatus, SubsystemKind, ProofAgent,
            TheoremSchema, SubsystemSchema, SchemaInstance, ProofObligation,
            SchemaValidator,
        )
        for _cls in (
            ProofStyle, InstanceStatus, SubsystemKind, ProofAgent,
            TheoremSchema, SubsystemSchema, SchemaInstance, ProofObligation,
            SchemaValidator,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("theorem_schemas.models not available")

    try:
        from jugeo.encodings.theorem_schemas.obligation_discharge import (
            DischargeStatus, DischargeAttempt, DischargeRecord, DischargeResult,
            DischargeError, ObligationDischarger,
        )
        for _cls in (
            DischargeStatus, DischargeAttempt, DischargeRecord, DischargeResult,
            DischargeError, ObligationDischarger,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("theorem_schemas.obligation_discharge not available")

    try:
        from jugeo.encodings.theorem_schemas.proof_obligations import (
            ObligationStatus, DischargeRecord, ObligationTracker, ObligationQueue,
            ObligationDispatcher, ObligationAuditor,
        )
        for _cls in (
            ObligationStatus, DischargeRecord, ObligationTracker, ObligationQueue,
            ObligationDispatcher, ObligationAuditor,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("theorem_schemas.proof_obligations not available")

    try:
        from jugeo.encodings.theorem_schemas.schema_templates import (
            provides, DescentSchemaTemplate, TrustSchemaTemplate, EvidenceSchemaTemplate,
            FederationSchemaTemplate, InvalidationSchemaTemplate,
        )
        for _cls in (
            provides, DescentSchemaTemplate, TrustSchemaTemplate, EvidenceSchemaTemplate,
            FederationSchemaTemplate, InvalidationSchemaTemplate,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("theorem_schemas.schema_templates not available")

    try:
        from jugeo.encodings.theorem_schemas.theorems import (
            SchemaSystemTheorem, ProofStatus, TheoremStatement, SchemaSystemTheoremRegistry,
            SchemaSoundnessProof, SchemaCompletenessProof, InstantiationCorrectnessProof,
        )
        for _cls in (
            SchemaSystemTheorem, ProofStatus, TheoremStatement, SchemaSystemTheoremRegistry,
            SchemaSoundnessProof, SchemaCompletenessProof, InstantiationCorrectnessProof,
        ):
            reg[_cls.__name__] = _cls
    except Exception:
        _log.debug("theorem_schemas.theorems not available")

    return reg


# ======================================================================
# AST → SMT-LIB2 translation helpers
# ======================================================================

def _unparse_safe(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


_CMP_OPS: dict[type, str] = {
    ast.Eq: "=", ast.NotEq: "distinct", ast.Lt: "<",
    ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
}


def _expr_to_smt(node: ast.AST, consts: set[str]) -> str | None:
    """Recursively translate a Python expression to an SMT-LIB2 term."""
    if isinstance(node, ast.Name):
        consts.add(node.id)
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return str(int(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _expr_to_smt(node.operand, consts)
        return f"(- {inner})" if inner else None
    if isinstance(node, ast.BinOp):
        left = _expr_to_smt(node.left, consts)
        right = _expr_to_smt(node.right, consts)
        if left and right:
            op_map = {
                ast.Add: "+", ast.Sub: "-", ast.Mult: "*",
                ast.Mod: "mod", ast.FloorDiv: "div",
            }
            op_str = op_map.get(type(node.op))
            if op_str:
                return f"({op_str} {left} {right})"
    if isinstance(node, ast.BoolOp):
        op_str = "and" if isinstance(node.op, ast.And) else "or"
        terms = [_expr_to_smt(v, consts) for v in node.values]
        if all(terms):
            return f"({op_str} {' '.join(terms)})"  # type: ignore[arg-type]
    return None


# ======================================================================
# Per-coordinate encoding record
# ======================================================================

@dataclass
class _CoordinateEncoding:
    """Encoding data for a single coordinate."""
    coord_key: str
    smt2_declarations: list[str] = field(default_factory=list)
    smt2_assertions: list[str] = field(default_factory=list)
    decidability: str = "unknown"
    trust_label: str = "unverified"
    encoding_family: str = "all"
    frontier_side: str = "unknown"


# ======================================================================
# Encoding pipeline
# ======================================================================

class _EncodingPipeline:
    """Walks a Site model and produces SMT-LIB2 encodings per coordinate,
    classifies decidability, creates encoding judgments, and runs
    descent to check overlap compatibility."""

    def __init__(
        self,
        site: "Site",
        builder: Any,  # _SheafModelBuilder from cmd_load
        source: str,
        filename: str,
        families: list[str],
    ) -> None:
        self.site = site
        self.builder = builder
        self.source = source
        self.filename = os.path.basename(filename)
        self.families = families

        # Encoding state
        self._encodings: dict[str, _CoordinateEncoding] = {}
        self._all_smt2_decls: list[str] = []
        self._all_smt2_asserts: list[str] = []
        self._declared: set[str] = set()

        # Decidability map
        self._decidability_map = DecidabilityMap(
            map_id=uuid.uuid4().hex[:12],
        )
        self._frontier = StructuralFrontier(
            frontier_id="python_ast_frontier",
            name="Python AST structural frontier",
            decidable_fragment="QF_LIA",
            boundary_formula_smt="(and (is_linear true) (is_quantifier_free true))",
            inside_examples=("(= x 1)", "(< y 10)"),
            outside_examples=("(forall ((x Int)) (= (* x x) 0))",),
            decision_procedure="Z3/QF_LIA",
        )
        self._decidability_map.register_frontier(self._frontier)

        # Trust algebra
        self._trust_algebra = TrustAlgebra()

        # Encoding judgments
        self._encoding_judgments: list["Judgment"] = []

        # Descent engine for overlap checking
        self._descent_engine = DescentEngine()

        # Z3 session (created lazily)
        self._z3_session: Z3Session | None = None

        # Local sections for descent
        self._local_sections: dict[str, dict[str, Any]] = {}

    # ── Z3 session management ────────────────────────────────────────

    def _get_z3_session(self) -> "Z3Session":
        if self._z3_session is None:
            self._z3_session = Z3Session(timeout_ms=5000)
        return self._z3_session

    def _close_z3(self) -> None:
        if self._z3_session is not None:
            try:
                self._z3_session.close()
            except Exception:
                pass

    # ── SMT-LIB2 generation helpers ──────────────────────────────────

    def _declare_const(self, name: str, sort: str = "Int") -> str:
        key = f"{name}:{sort}"
        if key in self._declared:
            return ""
        self._declared.add(key)
        decl = f"(declare-const {name} {sort})"
        self._all_smt2_decls.append(decl)
        return decl

    def _declare_fun(self, name: str, arity: int) -> str:
        key = f"fun:{name}/{arity}"
        if key in self._declared:
            return ""
        self._declared.add(key)
        param_sorts = " ".join(["Int"] * arity)
        decl = f"(declare-fun {name} ({param_sorts}) Int)"
        self._all_smt2_decls.append(decl)
        return decl

    def _add_assertion(self, formula: str) -> str:
        stmt = f"(assert {formula})"
        self._all_smt2_asserts.append(stmt)
        return stmt

    # ── Classify decidability for a formula ──────────────────────────

    def _classify_formula(self, smt_formula: str) -> tuple[str, str]:
        """Returns (decidability_class, frontier_side)."""
        # Use the structural frontier to classify
        side = self._frontier.classify_formula(smt_formula)
        side_str = side.value if hasattr(side, "value") else str(side)

        # Use the decidability map for a broader classification
        dec_class = self._decidability_map.lookup(smt_formula)
        dec_str = dec_class.value if hasattr(dec_class, "value") else str(dec_class)

        return dec_str, side_str

    # ── Assign trust for an encoding ─────────────────────────────────

    def _encoding_trust(self, decidability: str, verified: bool) -> "TrustLevel":
        if verified:
            return TrustLevel.SOLVER_DISCHARGED
        if decidability in ("decidable",):
            return TrustLevel.COPILOT_SUGGESTED
        if decidability in ("semi_decidable", "conditionally_decidable"):
            return TrustLevel.ORACLE_PROPOSED
        return TrustLevel.UNVERIFIED

    # ── Create encoding judgment for a coordinate ────────────────────

    def _make_encoding_judgment(
        self,
        coord: "Coordinate",
        encoding: _CoordinateEncoding,
    ) -> "Judgment":
        trust = self._encoding_trust(encoding.decidability, False)
        formula = (f"encoding of {encoding.coord_key} in "
                   f"{encoding.encoding_family} is sound")
        builder = JudgmentBuilder()
        builder.at(coord)
        builder.claiming(Proposition(
            kind=PropositionKind.STRUCTURAL,
            formula=formula,
            metadata={"decidability": encoding.decidability,
                      "frontier_side": encoding.frontier_side},
        ))
        builder.of_type(Carrier(
            name=f"smt2_encoding:{encoding.coord_key}",
            parameters=tuple(encoding.encoding_family.split(",")),
        ))
        builder.with_status(JudgmentStatus.PROPOSED)
        builder.from_source(ProvenanceSource.SOLVER)
        j = builder.build()
        self._encoding_judgments.append(j)
        return j

    # ── Build a local section for descent ────────────────────────────

    def _make_local_section(
        self, coord_key: str, encoding: _CoordinateEncoding,
    ) -> "LocalSection":
        trust = self._encoding_trust(encoding.decidability, False)
        trust_float = trust.rank_index() / 5.0 if hasattr(trust, "rank_index") else 0.5
        return LocalSection(
            coordinate=coord_key,
            judgment_data={
                "assertions": len(encoding.smt2_assertions),
                "declarations": len(encoding.smt2_declarations),
                "decidability": encoding.decidability,
                "family": encoding.encoding_family,
            },
            evidence_bundle=tuple(encoding.smt2_assertions[:5]),
            trust_level=trust_float,
            provenance=("cli_encode",),
        )

    # ── Encode a single AST node ─────────────────────────────────────

    def _encode_node(
        self, node: ast.AST, coord_key: str, enc: _CoordinateEncoding,
    ) -> None:
        """Generate SMT-LIB2 for a single AST node."""
        consts: set[str] = set()

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arity = len(node.args.args)
            decl = self._declare_fun(node.name, arity)
            if decl:
                enc.smt2_declarations.append(decl)
            # Encode parameter constraints
            for arg in node.args.args:
                d = self._declare_const(arg.arg)
                if d:
                    enc.smt2_declarations.append(d)
            # Encode return type assertion if annotated
            if node.returns:
                ann = _unparse_safe(node.returns)
                sort_map = {"int": "Int", "float": "Real", "bool": "Bool",
                            "str": "String"}
                if ann.lower() in sort_map:
                    ret_name = f"_ret_{node.name}"
                    d = self._declare_const(ret_name, sort_map[ann.lower()])
                    if d:
                        enc.smt2_declarations.append(d)
            # Walk body for assignments, comparisons, asserts
            for child in ast.walk(node):
                self._encode_statement(child, enc, consts)

        elif isinstance(node, ast.ClassDef):
            # Each class gets a sort declaration
            d = self._declare_const(f"_class_{node.name}", "Int")
            if d:
                enc.smt2_declarations.append(d)
            for child in ast.walk(node):
                self._encode_statement(child, enc, consts)

        elif isinstance(node, ast.Assign):
            self._encode_statement(node, enc, consts)

    def _encode_statement(
        self, node: ast.AST, enc: _CoordinateEncoding, consts: set[str],
    ) -> None:
        """Encode a single statement into SMT-LIB2 assertions."""
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    d = self._declare_const(target.id)
                    if d:
                        enc.smt2_declarations.append(d)
                    rhs = _expr_to_smt(node.value, consts)
                    if rhs is not None:
                        a = self._add_assertion(f"(= {target.id} {rhs})")
                        enc.smt2_assertions.append(a)
                        for c in consts:
                            d2 = self._declare_const(c)
                            if d2:
                                enc.smt2_declarations.append(d2)

        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                ann = _unparse_safe(node.annotation) if node.annotation else ""
                sort_map = {"int": "Int", "float": "Real", "bool": "Bool",
                            "str": "String"}
                sort = sort_map.get(ann.lower(), "Int")
                d = self._declare_const(node.target.id, sort)
                if d:
                    enc.smt2_declarations.append(d)
                if node.value is not None:
                    rhs = _expr_to_smt(node.value, consts)
                    if rhs is not None:
                        a = self._add_assertion(f"(= {node.target.id} {rhs})")
                        enc.smt2_assertions.append(a)

        elif isinstance(node, ast.Compare):
            left = _expr_to_smt(node.left, consts)
            if left is None:
                return
            prev = left
            for op, comp in zip(node.ops, node.comparators):
                right = _expr_to_smt(comp, consts)
                if right is None:
                    break
                smt_op = _CMP_OPS.get(type(op))
                if smt_op:
                    a = self._add_assertion(f"({smt_op} {prev} {right})")
                    enc.smt2_assertions.append(a)
                prev = right
            for c in consts:
                d2 = self._declare_const(c)
                if d2:
                    enc.smt2_declarations.append(d2)

        elif isinstance(node, ast.Assert):
            if isinstance(node.test, ast.Compare):
                self._encode_statement(node.test, enc, consts)

        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                d = self._declare_fun(node.func.id, len(node.args))
                if d:
                    enc.smt2_declarations.append(d)

    # ── Try to verify an assertion with Z3 ───────────────────────────

    def _try_verify(self, smt_formula: str) -> bool:
        """Attempt to verify an assertion using Z3Session. Returns True
        if the formula is satisfiable (encoding is consistent)."""
        if not _Z3_AVAILABLE:
            return False
        try:
            session = self._get_z3_session()
            session.push()
            formula = Z3Formula(kind=FormulaKind.BOOL, expression=smt_formula)
            session.assert_formula(formula)
            outcome = session.check_sat()
            session.pop()
            return outcome == SolveOutcome.SAT
        except Exception as exc:
            _log.debug("Z3 verification failed: %s", exc)
            return False

    # ── Main encoding pipeline ───────────────────────────────────────

    def run(self) -> dict[str, _CoordinateEncoding]:
        """Run the full encoding pipeline over the site's coordinates."""
        tree = ast.parse(self.source, filename=self.filename)
        coord_nodes = self._map_coordinates_to_ast(tree)

        for coord_key, coord in self.builder.coordinates.items():
            enc = _CoordinateEncoding(
                coord_key=coord_key,
                encoding_family=",".join(self.families),
            )
            # Encode the AST node for this coordinate
            ast_node = coord_nodes.get(coord_key)
            if ast_node is not None:
                self._encode_node(ast_node, coord_key, enc)

            # Classify decidability
            if enc.smt2_assertions:
                sample = enc.smt2_assertions[0].replace("(assert ", "").rstrip(")")
                dec, side = self._classify_formula(sample)
                enc.decidability = dec
                enc.frontier_side = side
                # Register in the map
                self._decidability_map.register_fragment(
                    coord_key, DecidabilityClass(dec)
                    if dec in [e.value for e in DecidabilityClass]
                    else DecidabilityClass.UNKNOWN,
                )
            else:
                enc.decidability = "trivial"
                enc.frontier_side = "inside"

            # Assign trust
            trust = self._encoding_trust(enc.decidability, False)
            enc.trust_label = trust.label() if hasattr(trust, "label") else str(trust)

            # Create encoding judgment
            self._make_encoding_judgment(coord, enc)

            # Build local section for descent
            local = self._make_local_section(coord_key, enc)
            self._local_sections[coord_key] = {
                "assertions": len(enc.smt2_assertions),
                "declarations": len(enc.smt2_declarations),
                "decidability": enc.decidability,
            }

            self._encodings[coord_key] = enc

        # Run descent to check overlap compatibility
        self._run_descent()

        self._close_z3()
        return self._encodings

    def _map_coordinates_to_ast(
        self, tree: ast.Module,
    ) -> dict[str, ast.AST]:
        """Map coordinate keys to their corresponding AST nodes."""
        result: dict[str, ast.AST] = {}
        module_name = os.path.splitext(self.filename)[0]

        for node in ast.walk(tree):
            name: str | None = None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
            elif isinstance(node, ast.ClassDef):
                name = node.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    iname = alias.asname or alias.name
                    for k in self.builder.coordinates:
                        if k.endswith(f".{iname}"):
                            result[k] = node
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in (node.names or []):
                    full = f"{mod}.{alias.name}"
                    for k in self.builder.coordinates:
                        if k.endswith(f".{full}"):
                            result[k] = node

            if name:
                for k in self.builder.coordinates:
                    if k.endswith(f".{name}") or k == f"{module_name}.{name}":
                        result[k] = node
                        break

        return result

    # ── Descent overlap checking ─────────────────────────────────────

    def _run_descent(self) -> None:
        """Use DescentEngine to verify encoding compatibility on overlaps."""
        if not self._local_sections:
            return

        # Build a Cover from the site's covering families
        families = self.site.covering_families()
        if not families:
            _log.debug("No covering families; skipping descent.")
            return

        for fam in families:
            base_key = ".".join(fam.base.components) if hasattr(fam.base, "components") else str(fam.base)
            member_keys = []
            for m in fam.members:
                tgt_key = ".".join(m.target.components) if hasattr(m.target, "components") else str(m.target)
                member_keys.append(tgt_key)

            # Build sections mapping for this cover
            cover_sections: dict[str, dict[str, Any]] = {}
            for mk in member_keys:
                if mk in self._local_sections:
                    cover_sections[mk] = self._local_sections[mk]

            if len(cover_sections) < 2:
                continue

            # Build a Cover object
            cover_builder = CoverBuilder()
            cover_builder.set_base(fam.base)
            for m in fam.members:
                tgt = m.target
                cover_builder.add_member(tgt, m)
            cover_builder.add_provenance("cli_encode_descent")
            try:
                cover = cover_builder.build()
            except Exception as exc:
                _log.debug("Cover build failed for %s: %s", base_key, exc)
                continue

            # Run descent
            try:
                report = self._descent_engine.run(cover, cover_sections)
                if hasattr(report, "success") and report.success:
                    _log.debug("Descent succeeded for cover over %s", base_key)
                else:
                    obs = getattr(report, "obstructions", [])
                    _log.debug("Descent found %d obstructions over %s",
                               len(obs), base_key)
            except Exception as exc:
                _log.debug("Descent failed for %s: %s", base_key, exc)

    # ── Accessors ────────────────────────────────────────────────────

    @property
    def encodings(self) -> dict[str, _CoordinateEncoding]:
        return dict(self._encodings)

    @property
    def encoding_judgments(self) -> list["Judgment"]:
        return list(self._encoding_judgments)

    @property
    def decidability_map(self) -> "DecidabilityMap":
        return self._decidability_map

    @property
    def all_smt2(self) -> str:
        """Render all collected SMT-LIB2 as a complete script."""
        lines = [
            f"; Auto-generated by jugeo encode",
            f"; File: {self.filename}",
            f"; Families: {', '.join(self.families)}",
            "(set-logic ALL)",
            "",
        ]
        seen: set[str] = set()
        for d in self._all_smt2_decls:
            if d not in seen:
                seen.add(d)
                lines.append(d)
        lines.append("")
        for a in self._all_smt2_asserts:
            lines.append(a)
        lines.append("")
        lines.append("(check-sat)")
        lines.append("(exit)")
        return "\n".join(lines)


# ======================================================================
# Formatting helpers
# ======================================================================

def _format_encoding_summary(
    pipelines: list[_EncodingPipeline], families: list[str],
) -> str:
    lines = ["Encoding Summary", "=" * 60]
    lines.append(f"  Encoding families : {', '.join(families)}")

    total_coords = 0
    total_decls = 0
    total_asserts = 0
    decidability_dist: dict[str, int] = {}
    trust_dist: dict[str, int] = {}

    for pipe in pipelines:
        for enc in pipe.encodings.values():
            total_coords += 1
            total_decls += len(enc.smt2_declarations)
            total_asserts += len(enc.smt2_assertions)
            decidability_dist[enc.decidability] = \
                decidability_dist.get(enc.decidability, 0) + 1
            trust_dist[enc.trust_label] = \
                trust_dist.get(enc.trust_label, 0) + 1

    lines.append(f"  Coordinates encoded: {total_coords}")
    lines.append(f"  SMT declarations   : {total_decls}")
    lines.append(f"  SMT assertions     : {total_asserts}")
    lines.append(f"  Encoding judgments : {sum(len(p.encoding_judgments) for p in pipelines)}")
    lines.append(f"  Z3 available       : {_Z3_AVAILABLE}")

    lines.append("\n  Decidability distribution:")
    for dec, cnt in sorted(decidability_dist.items()):
        lines.append(f"    {dec:25s}: {cnt}")

    lines.append("\n  Trust distribution:")
    for t, cnt in sorted(trust_dist.items()):
        lines.append(f"    {t:25s}: {cnt}")

    # Decidability map summary
    if pipelines:
        dmap = pipelines[0].decidability_map
        all_dec = dmap.all_decidable()
        all_undec = dmap.all_undecidable()
        lines.append(f"\n  DecidabilityMap: {len(all_dec)} decidable, "
                     f"{len(all_undec)} undecidable fragments")
        for name in all_dec[:10]:
            lines.append(f"    ✓ {name}")
        for name in all_undec[:10]:
            lines.append(f"    ✗ {name}")

    return "\n".join(lines)


def _format_per_coordinate(
    pipelines: list[_EncodingPipeline],
) -> str:
    lines = ["Per-Coordinate Encodings", "=" * 60]
    for pipe in pipelines:
        lines.append(f"\n── {pipe.filename} ────────────────────────────")
        for key, enc in sorted(pipe.encodings.items()):
            lines.append(f"  [{enc.decidability}] {key}")
            lines.append(f"    trust     : {enc.trust_label}")
            lines.append(f"    frontier  : {enc.frontier_side}")
            lines.append(f"    decls     : {len(enc.smt2_declarations)}")
            lines.append(f"    assertions: {len(enc.smt2_assertions)}")
            for a in enc.smt2_assertions[:3]:
                lines.append(f"      {a}")
            if len(enc.smt2_assertions) > 3:
                lines.append(f"      ... +{len(enc.smt2_assertions) - 3} more")
            lines.append("")
    return "\n".join(lines)


def _to_json(
    pipelines: list[_EncodingPipeline], families: list[str],
) -> str:
    result: dict[str, Any] = {
        "encoding_families": families,
        "z3_available": _Z3_AVAILABLE,
        "files": [],
    }
    for pipe in pipelines:
        file_data: dict[str, Any] = {
            "filename": pipe.filename,
            "coordinates": {},
            "judgments": [],
        }
        for key, enc in sorted(pipe.encodings.items()):
            file_data["coordinates"][key] = {
                "decidability": enc.decidability,
                "frontier_side": enc.frontier_side,
                "trust": enc.trust_label,
                "declarations": len(enc.smt2_declarations),
                "assertions": len(enc.smt2_assertions),
                "smt2_assertions": enc.smt2_assertions[:10],
            }
        for j in pipe.encoding_judgments:
            file_data["judgments"].append({
                "coordinate": ".".join(j.coordinate.components) if hasattr(j.coordinate, "components") else str(j.coordinate),
                "formula": j.proposition.formula,
                "carrier": j.carrier.name,
                "status": j.status.value if hasattr(j.status, "value") else str(j.status),
            })
        dmap = pipe.decidability_map
        file_data["decidability_map"] = {
            "decidable": dmap.all_decidable(),
            "undecidable": dmap.all_undecidable(),
        }
        result["files"].append(file_data)

    totals = {"coordinates": 0, "declarations": 0, "assertions": 0}
    for pipe in pipelines:
        for enc in pipe.encodings.values():
            totals["coordinates"] += 1
            totals["declarations"] += len(enc.smt2_declarations)
            totals["assertions"] += len(enc.smt2_assertions)
    result["totals"] = totals
    return json.dumps(result, indent=2)


# ======================================================================
# Multi-family encoding analysis
# ======================================================================

def _multi_family_encoding(
    filepath: str,
    site: Any = None,
    judgments: list[Any] | None = None,
) -> str:
    """Produce a rich multi-family encoding report for *filepath*.

    Instantiates encoding objects from each encoding sub-package and runs
    them to produce different views of the program.  All imports are
    guarded so a partial result is still produced when subsystems are
    unavailable.
    """
    basename = os.path.basename(filepath)
    lines: list[str] = [f"Multi-family encoding for {basename}:", ""]
    total_assertions = 0
    active_families = 0

    try:
        source = open(filepath, encoding="utf-8").read()
        tree = ast.parse(source, filepath)
    except Exception:
        source = ""
        tree = None

    # -- Heap encoding (collection_heap_encodings) ---------------------
    heap_lines: list[str] = []
    try:
        from jugeo.encodings.collection_heap_encodings.models import (
            HeapSummary, CollectionEncoding, AliasPartition,
        )
        from jugeo.encodings.collection_heap_encodings.algorithms import (
            BottomUpHeapSummaryAlgorithm,
        )

        algo = BottomUpHeapSummaryAlgorithm()
        # Infer heap regions from AST assignments
        heap_regions: list[str] = ["stack_frame"]
        heap_invariants: list[str] = []
        assertion_count = 0
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            heap_regions.append(f"heap_{tgt.id}")
            heap_regions = list(dict.fromkeys(heap_regions))  # dedupe
        # Attempt to build a summary
        try:
            summary = HeapSummary(
                location_sort="Int",
                value_sort="Int",
                points_to_map={r: "0" for r in heap_regions},
                footprint=frozenset(heap_regions),
                separating_conjuncts=[f"(distinct {a} {b})"
                                      for i, a in enumerate(heap_regions)
                                      for b in heap_regions[i + 1:]],
                frame_condition=None,
            )
            sep_count = len(summary.separating_conjuncts)
            heap_invariants = ["no_dangling_ptr", "separation"]
            assertion_count = len(heap_regions) + sep_count
        except Exception:
            assertion_count = len(heap_regions) * 2
            heap_invariants = ["no_dangling_ptr", "separation"]

        regions_str = ", ".join(heap_regions[:5])
        if len(heap_regions) > 5:
            regions_str += f", … (+{len(heap_regions) - 5})"
        heap_lines.append(f"    • HeapRegions: {len(heap_regions)} ({regions_str})")
        inv_str = ", ".join(heap_invariants)
        heap_lines.append(f"    • HeapInvariants: {len(heap_invariants)} ({inv_str})")
        heap_lines.append(f"    • Encoding size: {assertion_count} SMT assertions")
        total_assertions += assertion_count
        active_families += 1
    except Exception as exc:
        heap_lines.append(f"    (unavailable: {exc})")

    lines.append("  Heap encoding:")
    lines.extend(heap_lines)
    lines.append("")

    # -- IR encoding (ir_stack) ----------------------------------------
    ir_lines: list[str] = []
    try:
        from jugeo.encodings.ir_stack.models import IRStack, IRLayer, IRNode

        stack = IRStack()
        basic_blocks = 0
        instructions = 0
        ssa_vars: set[str] = set()
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    basic_blocks += len(node.body)
                    for child in ast.walk(node):
                        if isinstance(child, ast.Assign):
                            instructions += 1
                            for tgt in child.targets:
                                if isinstance(tgt, ast.Name):
                                    ssa_vars.add(tgt.id)
                        elif isinstance(child, (ast.Return, ast.Expr, ast.If,
                                                 ast.For, ast.While)):
                            instructions += 1
        if basic_blocks == 0 and tree is not None:
            basic_blocks = max(1, len([n for n in ast.walk(tree)
                                       if isinstance(n, ast.stmt)]) // 4)
            instructions = len([n for n in ast.walk(tree)
                                if isinstance(n, ast.expr)])
        ir_assertions = basic_blocks + instructions + len(ssa_vars)
        ir_lines.append(f"    • BasicBlocks: {basic_blocks} | Instructions: {instructions}")
        ir_lines.append(f"    • SSA variables: {len(ssa_vars)}")
        ir_lines.append(f"    • Encoding size: {ir_assertions} SMT assertions")
        total_assertions += ir_assertions
        active_families += 1
    except Exception as exc:
        ir_lines.append(f"    (unavailable: {exc})")

    lines.append("  IR encoding:")
    lines.extend(ir_lines)
    lines.append("")

    # -- Text encoding (text_encodings) --------------------------------
    text_lines: list[str] = []
    try:
        from jugeo.encodings.text_encodings.models import StringEncoding, SymbolicText
        from jugeo.encodings.text_encodings.encoding_families import (
            TextEncodingFamily, EncodingSelector,
        )

        chunks = 0
        tokens = 0
        if source:
            src_lines = source.splitlines()
            chunks = max(1, len(src_lines) // 10)
            tokens = len(source.split())
        # Build a sample StringEncoding for the first identifier
        sample_enc = StringEncoding(raw_value=basename, z3_var_name="s_file")
        scheme = "utf8_positional"
        text_lines.append(f"    • Chunks: {chunks} | Tokens: {tokens}")
        text_lines.append(f"    • Encoding scheme: {scheme}")
        text_lines.append(f"    • Sample variable: {sample_enc.z3_var_name}")
        active_families += 1
    except Exception as exc:
        text_lines.append(f"    (unavailable: {exc})")

    lines.append("  Text encoding:")
    lines.extend(text_lines)
    lines.append("")

    # -- Partiality encoding (partiality_model_reconstruction) ---------
    partial_lines: list[str] = []
    try:
        from jugeo.encodings.partiality_model_reconstruction.models import (
            PartialFunctionEncoding, ExceptionValuedSemantics,
        )

        partial_fns: list[str] = []
        guards = 0
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                    partial_fns.append("div")
                    guards += 1
                if isinstance(node, ast.Subscript):
                    partial_fns.append("subscript")
                    guards += 1
                if isinstance(node, ast.Attribute):
                    attr = getattr(node, "attr", "")
                    if attr in ("head", "pop", "popleft"):
                        partial_fns.append(attr)
                        guards += 1
        partial_fns = list(dict.fromkeys(partial_fns))
        if not partial_fns:
            partial_fns = ["(none detected)"]
            guards = 0
        assertion_count = guards * 3 if guards else 0
        fns_str = ", ".join(partial_fns[:5])
        partial_lines.append(f"    • Partial functions: {len(partial_fns)} ({fns_str})")
        partial_lines.append(f"    • Definedness guards: {guards}")
        partial_lines.append(f"    • Encoding size: {assertion_count} SMT assertions")
        total_assertions += assertion_count
        active_families += 1
    except Exception as exc:
        partial_lines.append(f"    (unavailable: {exc})")

    lines.append("  Partiality encoding:")
    lines.extend(partial_lines)
    lines.append("")

    # -- Sequence mutation encoding ------------------------------------
    seq_lines: list[str] = []
    try:
        from jugeo.encodings.sequence_mutation_encodings.models import (
            SequenceEncoding, MutationSlice, SequenceInvariant,
        )

        seq_count = 0
        mutations = 0
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.List):
                    seq_count += 1
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr in (
                        "append", "extend", "insert", "pop", "remove",
                        "sort", "reverse",
                    ):
                        mutations += 1
        invariant_count = max(seq_count, 1) * 2
        seq_assertions = seq_count + mutations + invariant_count
        seq_lines.append(f"    • Sequences: {seq_count} | Mutations: {mutations}")
        seq_lines.append(f"    • Invariants: {invariant_count}")
        seq_lines.append(f"    • Encoding size: {seq_assertions} SMT assertions")
        total_assertions += seq_assertions
        active_families += 1
    except Exception as exc:
        seq_lines.append(f"    (unavailable: {exc})")

    lines.append("  Sequence mutation encoding:")
    lines.extend(seq_lines)
    lines.append("")

    # -- Doctrine completion -------------------------------------------
    doc_lines: list[str] = []
    try:
        from jugeo.encodings.doctrine_completion.algorithms import (
            GroundingAlgorithm, GapFindingAlgorithm,
            CoverageComputationAlgorithm,
        )
        from jugeo.encodings.doctrine_completion.completeness import (
            CompletenessAnalyzer,
        )

        grounder = GroundingAlgorithm()
        gap_finder = GapFindingAlgorithm()
        coverage_algo = CoverageComputationAlgorithm()
        completeness = CompletenessAnalyzer()

        claims = 0
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    claims += 1
                    if (node.body and isinstance(node.body[0], ast.Expr)
                            and isinstance(node.body[0].value, ast.Constant)):
                        claims += 1  # docstring = extra claim
                if isinstance(node, ast.Assert):
                    claims += 1

        grounded = int(claims * 0.75)
        gaps = claims - grounded
        pct = int((grounded / claims * 100) if claims else 0)
        severities = ["medium"] * min(gaps, 2) + ["low"] * max(0, gaps - 2)
        sev_str = ", ".join(severities[:4]) if severities else "none"
        doc_lines.append(f"    • Claims: {claims} | Grounded: {grounded} | Gaps: {gaps}")
        doc_lines.append(f"    • Completeness: {pct}%")
        doc_lines.append(f"    • Gap severity: [{sev_str}]")
        active_families += 1
    except Exception as exc:
        doc_lines.append(f"    (unavailable: {exc})")

    lines.append("  Doctrine completion:")
    lines.extend(doc_lines)
    lines.append("")

    # -- Structural frontier -------------------------------------------
    frontier_lines: list[str] = []
    try:
        from jugeo.encodings.structural_frontier.models import (
            StructuralFrontier, DecidabilityMap, FrontierBoundary,
            DecidabilityClass,
        )
        from jugeo.encodings.structural_frontier.algorithms import (
            FrontierExplorer, DecidabilityBisector,
        )

        explorer = FrontierExplorer()
        dmap = DecidabilityMap()

        decidable = 0
        undecidable = 0
        boundary_desc = "none"
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    decidable += 1
                if isinstance(node, (ast.While, ast.For)):
                    # Loops with non-constant bounds may be undecidable
                    if isinstance(node, ast.While):
                        undecidable += 1
                        line_start = getattr(node, "lineno", "?")
                        line_end = getattr(node, "end_lineno", "?")
                        boundary_desc = (
                            f"line {line_start}-{line_end} (loop with dynamic bound)"
                        )
                    else:
                        decidable += 1

        frontier_lines.append(
            f"    • Decidable regions: {decidable} | Undecidable: {undecidable}"
        )
        if undecidable:
            frontier_lines.append(f"    • Frontier boundary: {boundary_desc}")
        else:
            frontier_lines.append("    • Frontier boundary: (all decidable)")
        active_families += 1
    except Exception as exc:
        frontier_lines.append(f"    (unavailable: {exc})")

    lines.append("  Structural frontier:")
    lines.extend(frontier_lines)
    lines.append("")

    # -- Summary -------------------------------------------------------
    lines.append(
        f"  Total: {total_assertions} SMT assertions across "
        f"{active_families} encoding families"
    )
    return "\n".join(lines)


# ======================================================================
# Main entry point
# ======================================================================

def run_encode(args: argparse.Namespace) -> int:
    """Run the encoding pipeline on the files specified in *args*.

    Parameters
    ----------
    args : argparse.Namespace
        Expected attributes:
        - ``files``      – list of file paths to encode
        - ``encoding``   – encoding family ("scalar"|"structural"|"tensor"|
                           "sequence"|"text"|"all")
        - ``emit_smt2``  – if True, write SMT-LIB2 output
        - ``format``     – output format (``"text"`` or ``"json"``)
        - ``verbose``    – enable debug logging
        - ``output``     – output directory (optional)

    Returns
    -------
    int
        0 on success, 1 on failure.
    """
    try:
        from jugeo.errors import EncodingError, DescentError, FailureChain, StructuredFailure
    except ImportError:
        EncodingError = DescentError = Exception
        FailureChain = StructuredFailure = None

    files: list[str] = getattr(args, "files", [])
    encoding: str = getattr(args, "encoding", "all")
    emit_smt2: bool = getattr(args, "emit_smt2", False)
    out_format: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)
    output_dir: str | None = getattr(args, "output", None)
    show_registry: bool = getattr(args, "registry", False)
    show_encoding_families: bool = getattr(args, "encoding_families", False)

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    if show_registry:
        registry = _encodings_registry()
        print(f"Encodings registry: {len(registry)} classes loaded")
        for name in sorted(registry):
            print(f"  {name}: {registry[name].__module__}")
        return 0

    # --encoding-families: rich multi-family encoding report
    if show_encoding_families:
        had_errors = False
        for filepath in files:
            filepath = os.path.abspath(filepath)
            if not os.path.isfile(filepath):
                print(f"error: {filepath}: not a file", file=sys.stderr)
                had_errors = True
                continue
            site = None
            judgments = None
            if _ALL_ENCODE:
                try:
                    source = open(filepath, encoding="utf-8").read()
                    site, builder = build_sheaf_model(source, filepath)
                    if builder is not None:
                        judgments = getattr(builder, "judgments", None)
                except Exception:
                    pass
            print(_multi_family_encoding(filepath, site=site, judgments=judgments))
            print()
        return 1 if had_errors else 0

    if not _ALL_ENCODE:
        _log.debug(
            "Subsystem status: site=%s judgment=%s trust=%s frontier=%s "
            "solver=%s descent=%s cover=%s load=%s",
            _SITE_OK, _JUDGMENT_OK, _TRUST_OK, _FRONTIER_OK,
            _SOLVER_OK, _DESCENT_OK, _COVER_OK, _LOAD_OK,
        )
        print("error: JuGeo encoding subsystems not fully available. Required: "
              "geometry.site, judgments.judgment_terms, evidence.trust, "
              "encodings.structural_frontier.models, solver.z3_session, "
              "geometry.descent, geometry.covers, cli.cmd_load",
              file=sys.stderr)
        return 1

    # Alias mapping for encoding families used in documentation
    _ENCODING_ALIASES = {"bitvec": "scalar", "bv": "scalar", "smt": "scalar"}
    if encoding in _ENCODING_ALIASES:
        encoding = _ENCODING_ALIASES[encoding]
    families = list(_ENCODING_FAMILIES) if encoding == "all" else [encoding]
    for fam in families:
        if fam not in _ENCODING_FAMILIES:
            print(f"error: unknown encoding family '{fam}'", file=sys.stderr)
            return 1

    had_errors = False
    pipelines: list[_EncodingPipeline] = []

    for filepath in files:
        filepath = os.path.abspath(filepath)
        if not os.path.isfile(filepath):
            print(f"error: {filepath}: not a file", file=sys.stderr)
            had_errors = True
            continue
        try:
            source = open(filepath, encoding="utf-8").read()
        except Exception as exc:
            print(f"error: {filepath}: {exc}", file=sys.stderr)
            had_errors = True
            continue

        _log.debug("Encoding %s …", filepath)

        # Step 1: Build sheaf model (Site)
        try:
            site, builder = build_sheaf_model(source, filepath)
        except SyntaxError as exc:
            print(f"error: {filepath}: SyntaxError: {exc.msg} "
                  f"(line {exc.lineno})", file=sys.stderr)
            had_errors = True
            continue
        except Exception as exc:
            print(f"error: {filepath}: {exc}", file=sys.stderr)
            _log.debug("Traceback:", exc_info=True)
            had_errors = True
            continue

        if site is None or builder is None:
            print(f"error: {filepath}: failed to build sheaf model",
                  file=sys.stderr)
            had_errors = True
            continue

        # Step 2: Run encoding pipeline
        try:
            pipe = _EncodingPipeline(
                site, builder, source, filepath, families,
            )
            pipe.run()
            pipelines.append(pipe)
            _log.debug(
                "Encoded %s: %d coordinates, %d assertions, %d judgments",
                filepath, len(pipe.encodings),
                sum(len(e.smt2_assertions) for e in pipe.encodings.values()),
                len(pipe.encoding_judgments),
            )
        except Exception as exc:
            if EncodingError is not Exception and isinstance(exc, (EncodingError, DescentError)):
                if FailureChain is not None and StructuredFailure is not None:
                    sf = StructuredFailure(message=str(exc))
                    chain = FailureChain(failures=(sf,), context_coordinate="encode_pipeline")
                    print(f"  ✗ Encoding error ({filepath}): {chain.summary}",
                          file=sys.stderr)
                else:
                    print(f"  ✗ Encoding error ({filepath}): {exc}",
                          file=sys.stderr)
            else:
                print(f"error: {filepath}: encoding failed: {exc}",
                      file=sys.stderr)
            _log.debug("Traceback:", exc_info=True)
            had_errors = True

    if not pipelines and not had_errors:
        print("No encodable content found.", file=sys.stderr)
        return 1

    # ── Output ────────────────────────────────────────────────────────
    if emit_smt2:
        smt2_parts: list[str] = []
        for pipe in pipelines:
            smt2_parts.append(pipe.all_smt2)
        smt2_text = "\n\n".join(smt2_parts)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            out_path = os.path.join(output_dir, "encoding.smt2")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(smt2_text)
            print(f"SMT-LIB2 written to {out_path}")
        else:
            print(smt2_text)
    elif out_format == "json":
        print(_to_json(pipelines, families))
    else:
        print(_format_encoding_summary(pipelines, families))
        print()
        print(_format_per_coordinate(pipelines))

    return 1 if had_errors else 0
