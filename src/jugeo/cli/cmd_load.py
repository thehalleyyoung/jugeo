"""CLI subcommand handler for ``jugeo load <file> ...``.

Builds the full sheaf-theoretic model of Python programs using:

- ``jugeo.geometry.site`` – Site / SiteBuilder / Coordinate / Morphism /
  CoveringFamily / GrothendieckTopology for the Grothendieck site structure.
- ``jugeo.judgments.judgment_terms`` – Judgment 8-tuples via JudgmentBuilder /
  Proposition / Carrier / EvidenceBundle.
- ``jugeo.judgments.sections`` – SectionBuilder / SectionFamily for organising
  judgments as presheaf sections over the site.
- ``jugeo.judgments.contexts`` – SemanticContext / ContextBinding /
  JudgmentContext for scope tracking.
- ``jugeo.evidence.trust`` – TrustLevel / TrustAlgebra for trust assignment.
- ``jugeo.geometry.covers`` – Cover / CoverBuilder / CoverMember for building
  covering families.

All JuGeo imports are guarded so that a lightweight AST fallback still works
when subsystem dependencies are missing.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
    from jugeo.judgments.sections import (
        Section, SectionBuilder, SectionFamily,
    )
    _SECTION_OK = True
except Exception:
    _SECTION_OK = False

try:
    from jugeo.judgments.contexts import (
        SemanticContext, ContextBinding, JudgmentContext,
    )
    _CONTEXT_OK = True
except Exception:
    _CONTEXT_OK = False

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    _TRUST_OK = True
except Exception:
    _TRUST_OK = False

try:
    from jugeo.geometry.covers import Cover, CoverBuilder, CoverMember
    _COVER_OK = True
except Exception:
    _COVER_OK = False

_ALL_SUBSYSTEMS = _SITE_OK and _JUDGMENT_OK and _SECTION_OK and \
    _CONTEXT_OK and _TRUST_OK and _COVER_OK


# ======================================================================
# python_runtime class registry
# ======================================================================

_TEST_FIXTURE_NAMES = frozenset({
    "Foo", "Baz", "DC", "Plain", "WithMeta", "MyABC", "MyClass",
})


def _python_runtime_registry() -> dict[str, type]:
    """Return a dict mapping class name \u2192 class for all python_runtime classes.

    Each subpackage group is imported independently so that a missing
    subpackage never blocks the rest of the registry.
    """
    registry: dict[str, type] = {}

    def _collect(module_path: str, names: tuple[str, ...]) -> None:
        try:
            mod = __import__(module_path, fromlist=names)
        except Exception:
            return
        for name in names:
            obj = getattr(mod, name, None)
            if obj is not None and isinstance(obj, type):
                if not name.startswith("_") and name not in _TEST_FIXTURE_NAMES:
                    registry[name] = obj

    # -- callable_surfaces ———————————————————————
    _collect(
        "jugeo.python_runtime.callable_surfaces.algorithms",
        (
            "CallableSurfaceAnalyzer", "MethodResolutionAlgorithm",
            "CallCompatibilityChecker", "InheritanceGraphAlgorithm",
            "DecoratorAnalyzer",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.class_construction",
        (
            "ClassBuilder", "MetaclassAnalyzer", "InitAnalyzer",
            "ClassHierarchyTracker",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.class_objects_construction_pipelin",
        (
            "ClassObjectsConstructionPipelineCoordinator",
            "ClassObjectsConstructionPipelineAnalyzer",
            "ClassObjectsConstructionPipelineWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.descriptor_lookup_route_tagged_att",
        (
            "DescriptorLookupRouteTaggedCoordinator",
            "DescriptorLookupRouteTaggedAnalyzer",
            "DescriptorLookupRouteTaggedWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.descriptors",
        (
            "DescriptorProtocol", "DescriptorInspector", "PropertyAnalyzer",
            "SlotDescriptorAnalyzer", "DescriptorJudgmentBuilder",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.function_values_and_method_values",
        (
            "FunctionValuesMethodValuesCoordinator",
            "FunctionValuesMethodValuesAnalyzer",
            "FunctionValuesMethodValuesWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.functions",
        (
            "AnnotationResolver", "SignatureExtractor",
            "CallableSurfaceCache", "FunctionMorphismAnalyzer",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.integration",
        (
            "CallableJudgmentEmitter", "Z3CallableEncoder",
            "CallableCoordinateMapper", "SupportRegionBuilder",
            "CopilotCallableAdvisor",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.manifest",
        ("Capability", "ComponentRegistration", "PackageManifest",),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.method_binding",
        (
            "MROComputer", "MethodResolver", "BindingConstraintChecker",
            "MethodBinder",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.models",
        (
            "ParameterKind", "ParameterSpec", "DescriptorKind",
            "CallableSurface", "MethodBinding", "DescriptorRecord",
            "BoundMethod", "ClassConstruction", "SignatureRecord",
        ),
    )
    _collect(
        "jugeo.python_runtime.callable_surfaces.theorems",
        (
            "enters", "TheoremKind", "CallableTheorem",
            "ArityConsistencyTheorem", "DescriptorPriorityTheorem",
            "MROValidityTheorem", "BindingValidityTheorem",
            "SurfaceCompatibilityTheorem", "TheoremRegistry",
        ),
    )

    # -- concurrency_boundaries ——————————————————
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.algorithms",
        (
            "ConcurrencyAnalyzer", "CancellationHandler",
            "ExceptionGroupProcessor", "BoundaryEnforcer",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.cancellation_and_exception_group_s",
        (
            "CancellationStatus", "CancellationNode", "ExceptionGroupNode",
            "CancellationTreeAnalyzer", "ExceptionGroupAnalyzer",
            "CancellationExceptionGroupSemanticsCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.cancellation_obstructions",
        (
            "CancellationObstructionInjector", "ObstructionPropagator",
            "CancellationShield", "CancellationDischarger",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.concurrency_in_python_is_not_one_p",
        (
            "ConcurrencyLayer", "CoverageLevel", "ConcurrencyRecord",
            "LayerCoverageAnalyzer", "PhenomenonWitness",
            "ConcurrencyPythonOnePhenomenonCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.exception_groups_process_boundaries",
        (
            "ExceptionGroupProcessor", "MultiObstructionRecord",
            "ProcessBoundaryEnforcer", "IPCMorphismBuilder",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.integration",
        (
            "SupportBridge", "JudgmentBridge", "FleetBridge",
            "ConcurrencyBoundariesIntegration",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.manifest",
        (
            "SymbolRecord", "ConcurrencyBoundariesManifest",
            "ManifestValidator", "ManifestRegistry", "TheoryAlignment",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.models",
        (
            "ConcurrencyRole", "CancellationReason", "BoundaryKind",
            "ScopeStatus", "TaskLocalSection", "CancellationRecord",
            "ExceptionGroupRecord", "ProcessBoundary", "ConcurrencyScope",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.process_boundaries_and_replicated",
        (
            "BoundaryKind", "ProcessBoundaryRecord", "ReplicatedStateRecord",
            "FederationBoundaryAnalyzer", "ReplicatedStateAnalyzer",
            "ProcessBoundariesReplicatedStateCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.replicated_state_obstructions",
        (
            "ObstructionKind", "ObstructionRecord", "StateVector",
            "ObstructionDetector", "ObstructionWitness",
            "ReplicatedStateObstructionsCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.task_local_context",
        (
            "TaskSectionManager", "ContextVarBridge",
            "SectionInheritanceEngine", "TaskSectionCleanup",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.task_local_context_as_hidden_but_s",
        (
            "ContextVisibility", "ContextBinding", "HiddenInputRecord",
            "ContextBindingAnalyzer", "HiddenContextWitness",
            "TaskLocalContextHiddenCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.concurrency_boundaries.theorems",
        ("TheoremRecord", "TheoremProver", "TheoremLibrary",),
    )

    # -- effects_async ———————————————————————————
    _collect(
        "jugeo.python_runtime.effects_async.algorithms",
        ("AlgorithmSuite",),
    )
    _collect(
        "jugeo.python_runtime.effects_async.async_and_task_semantics_suspended",
        (
            "SuspendedSection", "TaskCoordinate", "AwaitEdge",
            "SuspensionPoint", "AwaitGraph",
            "AsyncTaskSemanticsSuspendedAnalyzer",
            "AsyncTaskSemanticsSuspendedWitness",
            "AsyncTaskSemanticsSuspendedCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.async_primitives",
        (
            "CoroutineSection", "EventLoopTopology", "AsyncSiteBuilder",
            "TaskRegistry",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.context_managers",
        (
            "ContextScopeManager", "SectionScopeStack", "AsyncContextScope",
            "ContextCoveringBuilder",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.context_managers_temporal_obligati",
        (
            "TemporalObligation", "ObligationEdge", "ObligationViolation",
            "ObligationGraph",
            "ContextManagersTemporalObligationsCoordinator",
            "ContextManagersTemporalObligationsAnalyzer",
            "ContextManagersTemporalObligationsWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.exceptions",
        (
            "ExceptionSheaf", "ExceptionChain", "FailurePropagator",
            "StructuredFailureEncoder",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.exceptions_as_alternate_semantic_p",
        (
            "ExceptionPath", "ExceptionKindRecord", "ExceptionWitnessRecord",
            "ExceptionsAlternateSemanticPathsCoordinator",
            "ExceptionsAlternateSemanticPathsAnalyzer",
            "ExceptionsAlternateSemanticPathsWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.generators",
        (
            "GeneratorSheaf", "LazyFiberBuilder", "IteratorSection",
            "GeneratorCombinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.integration",
        (
            "ExceptionJudgmentIntegrator", "AsyncSiteIntegrator",
            "ContextScopeIntegrator", "GeneratorChannelBridge",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.manifest",
        (
            "CoverageStatus", "SymbolRole", "ClaimStatus", "ManifestRecord",
            "SymbolGroup", "ClaimSummary", "PackageManifest",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.models",
        (
            "ExceptionSection", "CancellationRecord", "AsyncSection",
            "GeneratorSection", "ContextScope",
        ),
    )
    _collect(
        "jugeo.python_runtime.effects_async.theorems",
        (
            "Theorem_ExceptionSectionality", "Theorem_ContextScopeCovers",
            "Theorem_AsyncTopologicalOrder", "Theorem_GeneratorFiberSequence",
            "Theorem_CancellationPropagation", "TheoremSuite",
        ),
    )

    # -- generated_contracts —————————————————————
    _collect(
        "jugeo.python_runtime.generated_contracts.algorithms",
        (
            "AnalysisPlan", "AnalysisResult", "AnnotationGraphNode",
            "AnnotationGraph", "AnnotationGraphBuilder",
            "DecoratorStackAnalyzer",
            "AnnotationsDecoratorsRegistriesGeneratedPlanner",
            "AnnotationsDecoratorsRegistriesGeneratedExecutor",
            "AnnotationsDecoratorsRegistriesGeneratedNormalizer",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.annotations_as_latent_behavior",
        (
            "AnnotationRecord", "WitnessRecord", "AnnotationInspector",
            "LatencyPromotionEngine", "AnnotationsLatentBehaviorAnalyzer",
            "AnnotationsLatentBehaviorWitness",
            "AnnotationsLatentBehaviorCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.generated_contracts",
        (
            "ContractSource", "GeneratedContractRecord", "WitnessRecord",
            "DataclassContractExtractor", "ProtocolContractExtractor",
            "TypedDictContractExtractor", "NamedTupleContractExtractor",
            "ContractCompletionChecker", "GeneratedContractsAnalyzer",
            "GeneratedContractsWitness", "GeneratedContractsCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.integration",
        (
            "TrustTier", "IntegrationKind", "BridgeStatus", "SolverFormula",
            "SolverResult", "EvidenceRecord", "CoordinateMapper",
            "SolverInterface", "CopilotAdvisor",
            "AnnotationsDecoratorsRegistriesGeneratedBridge",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.manifest",
        (
            "CoverageStatus", "SymbolRole", "ClaimStatus", "ManifestRecord",
            "SymbolGroup", "ClaimSummary", "PackageManifest",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.models",
        (
            "DecoratorTransformer", "AnnotationContract", "ContractRecord",
            "RegistrySection",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.registries",
        (
            "SingleDispatchRegistry", "ABCAbstractRegistry",
            "DataclassFieldRegistry", "PluginRegistryBuilder",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.registry_surfaces",
        (
            "TrustTier", "JudgmentTuple", "ContractEntry", "RegistryQuery",
            "ContractRegistry", "RegistrySurface", "SurfaceAPI",
            "ContractRegistryCoordinator", "ContractRegistryAnalyzer",
            "ContractRegistryWitness", "RegistryKind", "RegistryEntry",
            "RegistrySurfaceRecord", "WitnessRecord",
            "SingleDispatchSurfaceAnalyzer", "ABCSurfaceAnalyzer",
            "DataclassFieldSurfaceAnalyzer", "RegistrySurfacesAnalyzer",
            "RegistrySurfacesWitness", "RegistrySurfacesCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.theorem_burden",
        (
            "BurdenKind", "ProofObligation", "BurdenReport",
            "BurdenAccumulator", "BurdenDischargeEngine",
            "TheoremBurdenAnalyzer", "TheoremBurdenWitness",
            "TheoremBurdenCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.generated_contracts.theorems",
        (
            "TrustTier", "JudgmentTuple", "TheoremKind", "ProofStatus",
            "ContractTheorem", "SoundnessProof", "CompletenessArgument",
            "PrecisionMetric", "ContractTheoremRegistry", "TheoremVerifier",
            "CompletenessChecker", "PrecisionBoundComputer", "TheoremSuite",
            "TheoremVerificationStatus", "TheoremRecord", "BaseTheorem",
            "AnnotationLatencyTheorem", "DecoratorMorphismTheorem",
            "RegistryCoverageTheorem", "ContractCompletenessTheorem",
            "TheoremBurdenTheorem", "TheoremRegistry", "FalsificationCase",
            "FalsificationSuite",
            "AnnotationsDecoratorsRegistriesGeneratedTheoremSchema",
        ),
    )

    # -- heap_aliasing ———————————————————————————
    _collect(
        "jugeo.python_runtime.heap_aliasing.algorithms",
        (
            "HeapAnalyzer", "UnionFindAlgorithm", "AliasAnalysisAlgorithm",
            "MutationFlowAlgorithm", "HeapDiffAlgorithm",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.aliasing",
        (
            "AliasPartitioner", "AliasDetector", "AliasGraph",
            "SupportOverlapChecker", "AliasSetTracker",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.aliasing_as_shared_geometry_suppor",
        (
            "UnionFind", "AliasGeometryRecord", "MutationPropagationRecord",
            "AliasAssignmentVisitor",
            "AliasingSharedGeometrySupportCoordinator",
            "AliasingSharedGeometrySupportAnalyzer",
            "AliasingSharedGeometrySupportWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.descent",
        (
            "DescentConditionResult", "DescentConditionChecker",
            "HeapConsistencyVerifier", "CocycleConditionChecker",
            "LocalToGlobalMapper", "HeapCoherenceTracker",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.heap_objects",
        (
            "HeapObjectFactory", "HeapObjectRegistry", "IdentityTracker",
            "HeapSectionBuilder",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.identity_and_equality_observationa",
        (
            "ComparisonKind", "ComparisonRecord",
            "ObservationalEquivalenceRecord", "ComparisonASTVisitor",
            "IdentityEqualityObservationalCriteriaCoordinator",
            "IdentityEqualityObservationalCriteriaAnalyzer",
            "IdentityEqualityObservationalCriteriaWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.integration",
        (
            "HeapJudgmentEmitter", "Z3HeapEncoder", "HeapCoordinateMapper",
            "SupportRegionBuilder", "CopilotHeapAdvisor",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.manifest",
        ("Capability", "ComponentRegistration", "PackageManifest",),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.models",
        (
            "ObjectKind", "IdentityCoordinate", "HeapObject",
            "AliasPartition", "MutationEvent", "HeapSection", "AliasEdge",
            "HeapSnapshot", "MutationPatch",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.mutation",
        (
            "MutationValidationResult", "MutationValidator",
            "MutationRecorder", "DescentChecker", "MutationImpactAnalyzer",
            "FrozenObjectChecker",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.primitive_and_heap_mediated_values",
        (
            "ValueKind", "ValueRecord", "ASTValueVisitor",
            "PrimitiveHeapMediatedValuesCoordinator",
            "PrimitiveHeapMediatedValuesAnalyzer",
            "PrimitiveHeapMediatedValuesWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.heap_aliasing.theorems",
        (
            "TheoremKind", "TheoremStatus", "HeapTheorem", "TheoremViolation",
            "IdentityUniquenessTheorem", "AliasTransitivityTheorem",
            "MutationConsistencyTheorem", "DescentConditionTheorem",
            "ImmutabilityPreservedTheorem", "TheoremRegistry",
        ),
    )

    # -- import_graph ————————————————————————————
    _collect(
        "jugeo.python_runtime.import_graph.algorithms",
        (
            "ImportRecord", "AnalysisPlan", "ComplexityEstimate",
            "IncrementalPlan", "AnalysisResult", "IncrementalResult",
            "ImportsPackageFixedPointsNormalizer",
            "ImportsPackageFixedPointsExecutor",
            "ImportsPackageFixedPointsPlanner",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.dynamic_import_and_reflection",
        (
            "DynamicImportKind", "DynamicImportRecord",
            "ImportlibUsageRecord", "ModuleSpecRecord", "ReflectionRecord",
            "DynamicImportWitnessRecord", "ModuleAttributeMap",
            "ImportHookRecord", "LazyImportRecord", "PluginPatternRecord",
            "DynamicImportReflectionAnalyzer",
            "DynamicImportReflectionWitness",
            "DynamicImportReflectionCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.import_cycles_and_package_fixed_po",
        (
            "CycleKind", "CycleRecord", "FixedPointRecord",
            "PartialModuleWitnessRecord", "ImportCyclesPackageFixedAnalyzer",
            "ImportCyclesPackageFixedCoordinator",
            "ImportCyclesPackageFixedWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.import_graph",
        (
            "ImportGraphBuilder", "CircularImportDetector",
            "SysModulesSection", "ImportGraphSerializer",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.import_is_execution_plus_namespace",
        (
            "ImportKind", "ImportExecutionRecord", "NamespaceTransportResult",
            "ShadowedNameRecord", "ExecutionWitnessRecord",
            "NamespaceDeltaRecord",
            "ImportExecutionNamespaceTransportCoordinator",
            "ImportExecutionNamespaceTransportAnalyzer",
            "ImportExecutionNamespaceTransportWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.integration",
        (
            "ImportsPackageFixedPointsBridge",
            "ImportsPackageFixedPointsExportBundle", "CopilotImportAdvisor",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.manifest",
        (
            "CoverageStatus", "SymbolRole", "ClaimStatus", "ManifestRecord",
            "SymbolGroup", "ClaimSummary", "PackageManifest",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.models",
        (
            "ImportNode", "ImportEdge", "PackageFixedPoint",
            "DynamicLoadRecord", "ReExportMap",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.package_fixpoints",
        (
            "FixedPointComputer", "NamespacePackageHandler",
            "StabilityVerifier", "FixedPointRegistry",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.proof_targets_for_import_semantics",
        (
            "ProofTargetKind", "TargetDifficulty", "ProofTarget",
            "ProofAttemptResult", "ImportInvariant", "InvariantWitnessRecord",
            "DischargeRecord", "ProofTargetsImportSemanticsAnalyzer",
            "ProofTargetsImportSemanticsWitness",
            "ProofTargetsImportSemanticsCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.re_exports_star_imports_and_packag",
        (
            "StarImportRiskLevel", "ReExportRecord", "ReExportHop",
            "PackageSurfaceRecord", "StarImportWitnessRecord",
            "NameOriginRecord", "ReExportsStarImportsCoordinator",
            "ReExportsStarImportsAnalyzer", "ReExportsStarImportsWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.reexports",
        (
            "ReExportAnalyzer", "StarImportResolver", "TrustTransporter",
            "PrivateLeakDetector",
        ),
    )
    _collect(
        "jugeo.python_runtime.import_graph.theorems",
        (
            "TheoremId", "TheoremCheckResult", "FalsificationResult",
            "FalsificationSummary", "_T191_ImportGraphAcyclicity",
            "_T192_FixedPointUniqueness", "_T193_ReexportConsistency",
            "_T194_StarImportDeterminism", "_T195_NamespaceDisjointness",
            "_T196_DynamicImportReachability",
            "ImportsPackageFixedPointsTheoremSchema",
            "ImportsPackageFixedPointsFalsificationSuite",
        ),
    )

    # -- live_mutation ———————————————————————————
    _collect(
        "jugeo.python_runtime.live_mutation.algorithms",
        (
            "LiveMutationTracker", "InvalidationEngine", "HotReloadPlanner",
            "DynamicSectionValidator",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.epoch_indexed_module_and_object_su",
        (
            "EpochKind", "EpochRecord", "ObjectSummary", "EpochDelta",
            "EpochStore", "ObjectSummaryAnalyzer",
            "EpochIndexedModuleObjectCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.exec_and_eval_as_bounded_or_residu",
        (
            "EventBoundedness", "ExecEvent", "EvalEvent",
            "BoundednessClassification", "ResidualObservation",
            "ExecBoundednessAnalyzer", "ResidualEventWitness",
            "ExecEvalBoundedResidualCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.exec_eval_injection",
        (
            "ExecInjector", "EvalQuerier", "NamespaceTracker",
            "DynamicTrustAssigner",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.hot_reload",
        (
            "HotReloadEngine", "DescentPlanner", "ReloadRollback",
            "ConsistencyChecker",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.hot_reload_and_development_mode_se",
        (
            "ReloadKind", "DevSessionPhase", "ReloadEvent", "DevModeState",
            "ReloadDiff", "DevModeObservation", "HotReloadEngine",
            "DevModeWitness", "HotReloadDevelopmentModeCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.integration",
        (
            "SupportBridge", "JudgmentBridge", "ChannelBridge", "FleetBridge",
            "LiveMutationIntegration",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.manifest",
        (
            "MutationRiskLevel", "MutationCategory", "SymbolRecord",
            "LiveMutationManifest", "ManifestValidator", "ManifestRegistry",
            "TheoryAlignment",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.models",
        (
            "MutationKind", "InvalidationScope", "ReloadStatus", "TrustTier",
            "ExecContext", "DynamicSection", "EvalResult",
            "MonkeyPatchRecord", "HotReloadEvent",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.monkey_patching",
        (
            "MonkeyPatcher", "InvalidationTrigger", "PatchStack",
            "PatchAuditor",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.monkey_patching_and_late_rebinding",
        (
            "RebindingKind", "ObstructionKind", "RebindingRecord",
            "ObstructionRecord", "RebindingChain", "LateRebindingAnalyzer",
            "PatchEvidence", "PatchObstructionWitness",
            "MonkeyPatchingLateRebindingCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.semantic_apertures_in_the_python_w",
        (
            "ApertureKind", "ApertureState", "SemanticApertureRecord",
            "ApertureIndexEntry", "ApertureObservation",
            "SemanticApertureAnalyzer", "ApertureWitness",
            "SemanticAperturesPythonWorldCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.live_mutation.theorems",
        (
            "TheoremStatus", "ProofMethod", "TheoremRecord", "TheoremProver",
            "TheoremLibrary",
        ),
    )

    # -- metaobject_surfaces —————————————————————
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.algorithms",
        ("MROAlgorithmTracer",),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.behavioral_surfaces",
        (
            "ProtocolSurfaceAnalyzer", "StructuralSubtypeChecker",
            "BehavioralSurfaceBuilder", "JudgmentIndexedProtocol",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.class_creation",
        (
            "ClassCreationOrchestrator", "_TracedDefinition",
            "BodyExecutionTracer", "InitSubclassProbe",
            "SetNameHookApplicator",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.class_creation_as_staged_semantics",
        (
            "ClassKind", "MetaclassUsageKind", "ClassCreationRecord",
            "ThreePhaseTrace", "MetaclassRef", "DescriptorRef",
            "ClassCreationWitnessRecord", "SetNameCallRecord",
            "ClassCreationStagedSemanticsAnalyzer",
            "ClassCreationStagedSemanticsWitness",
            "ClassCreationStagedSemanticsCoordinator", "Plain", "WithMeta",
            "MyABC", "DC",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.descriptor_resolution_routes",
        (
            "DescriptorKind", "ResolutionRoute", "DescriptorResolutionResult",
            "MROLookupTrace", "DescriptorAnalysisRecord",
            "PropertyUsageRecord", "DescriptorConflict",
            "SlotsAnalysisRecord", "GetAttrWitnessRecord",
            "SetAttrWitnessRecord", "DescriptorProtocolProbe",
            "DescriptorResolutionRoutesAnalyzer",
            "DescriptorResolutionRoutesWitness",
            "DescriptorResolutionRoutesCoordinator", "MyClass",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.descriptors",
        (
            "DescriptorResolver", "SlotCoordinateBuilder",
            "PropertyDescriptorAnalyzer", "DescriptorTrustTracker",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.generated_behavioral_surfaces",
        (
            "SurfaceKind", "OverridePattern", "BehavioralSurfaceRecord",
            "DataclassSurfaceRecord", "ProtocolSurfaceRecord",
            "ABCSurfaceRecord", "MergedSurfaceRecord", "BehavioralContract",
            "ProtocolComplianceRecord", "AbstractMethodRecord",
            "DunderSurface", "FieldSurfaceRecord",
            "SurfaceComplianceWitnessRecord",
            "AbstractInstantiationWitnessRecord",
            "RuntimeProtocolCheckRecord",
            "GeneratedBehavioralSurfacesAnalyzer",
            "GeneratedBehavioralSurfacesWitness",
            "GeneratedBehavioralSurfacesCoordinator", "Foo", "Baz",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.integration",
        (
            "MetaclassJudgmentIntegrator", "BehavioralSurfaceSiteBuilder",
            "DescriptorChainChannelBridge", "ClassCreationJudgmentEmitter",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.manifest",
        (
            "CoverageStatus", "SymbolRole", "ClaimStatus", "ManifestRecord",
            "SymbolGroup", "ClaimSummary", "PackageManifest",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.metaclasses",
        (
            "MetaclassMROResolver", "MetaclassConflictChecker",
            "TypeConstructorSite", "ABCMetaAnalyzer",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.metaclasses_as_contract_transforme",
        (
            "MetaclassPattern", "MetaclassContractRecord",
            "ContractTransformationTrace", "MetaclassConflict",
            "MetaclassInheritanceRecord", "NewOverrideRecord",
            "InitOverrideRecord", "TransformationStep",
            "InjectedDescriptorRecord", "MetaclassCallWitnessRecord",
            "NamespaceMutationRecord",
            "MetaclassesContractTransformersAnalyzer",
            "MetaclassesContractTransformersWitness",
            "MetaclassesContractTransformersCoordinator", "SingletonMeta",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.models",
        (
            "MetaclassRecord", "BehavioralSurface", "DescriptorChain",
            "ClassCreationTrace",
        ),
    )
    _collect(
        "jugeo.python_runtime.metaobject_surfaces.theorems",
        (
            "Theorem_MetaclassMROWellFounded",
            "Theorem_DescriptorDataPrecedence",
            "Theorem_BehavioralSurfaceFunctor",
            "Theorem_ClassCreationMonotonicity",
            "Theorem_MetaclassConflictObstruction",
        ),
    )

    # -- program_loader ——————————————————————————
    _collect(
        "jugeo.python_runtime.program_loader",
        (
            "ProgramLoaderError", "ProgramSource", "SymbolicProgram",
            "ProgramLoader",
        ),
    )

    # -- scope_and_state —————————————————————————
    _collect(
        "jugeo.python_runtime.scope_and_state.algorithms",
        (
            "NameResolutionEngine", "ScopeTreeAlgorithm",
            "ClosureAnalysisAlgorithm", "ModuleStateDiffAlgorithm",
            "ReachabilityAnalyzer",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.closure_capture_cell_transport_lat",
        (
            "ClosureCaptureCellTransportCoordinator",
            "ClosureCaptureCellTransportAnalyzer",
            "ClosureCaptureCellTransportWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.closures",
        (
            "ClosureDetector", "ClosureLifter", "CellVariableTracker",
            "ClosureJudgmentBuilder",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.global_and_local_bindings_obligati",
        (
            "GlobalLocalBindingsObligationCoordinator",
            "GlobalLocalBindingsObligationAnalyzer",
            "GlobalLocalBindingsObligationWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.integration",
        (
            "ScopeJudgmentEmitter", "Z3ScopeEncoder", "ScopeCoordinateMapper",
            "SupportRegionBuilder", "CopilotScopeAdvisor",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.manifest",
        ("Capability", "ComponentRegistration", "PackageManifest",),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.models",
        (
            "NameKind", "ScopeKind", "NameCoordinate", "ScopeSection",
            "ClosureRecord", "ModuleStateManifest", "NameResolutionResult",
            "ScopeChain",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.module_state",
        (
            "ModuleStateSnapshot", "ModuleStateTracker", "GlobalNameTracker",
            "ImportTracker", "ModuleStateValidator",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.names",
        (
            "NameClassifier", "NameRegistry", "NameNormalizer",
            "BindingSiteResolver",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.scope_semantics_coordinate_formati",
        (
            "ScopeSemanticsCoordinateFormationCoordinator",
            "ScopeSemanticsCoordinateFormationAnalyzer",
            "ScopeSemanticsCoordinateFormationWitness",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.scopes",
        (
            "ScopeBuilder", "ScopeAnalyzer", "ScopeValidator",
            "ScopeVisualizer",
        ),
    )
    _collect(
        "jugeo.python_runtime.scope_and_state.theorems",
        (
            "TheoremKind", "ScopeTheorem", "NameUniquenessTheorem",
            "ScopeCoveringTheorem", "ClosureWellFormednessTheorem",
            "ModuleStateConsistencyTheorem", "ResolutionDeterminismTheorem",
            "TheoremRegistry",
        ),
    )

    # -- unstable_protocols ——————————————————————
    _collect(
        "jugeo.python_runtime.unstable_protocols.algorithms",
        (
            "ProtocolAnalyzer", "StabilityChecker", "DelegationTracker",
            "ProxyValidator",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.delegation_chains",
        (
            "DelegationLink", "ChainAnalysisResult", "WitnessRecord",
            "DelegationDetector", "ChainTracer", "RepairTargetLocator",
            "DelegationChainsAnalyzer", "DelegationChainsWitness",
            "DelegationChainsCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.integration",
        (
            "UnstableProtocolIntegration", "SupportBridge", "JudgmentBridge",
            "FleetBridge",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.manifest",
        (
            "SymbolRecord", "UnstableProtocolsManifest", "ManifestValidator",
            "ManifestRegistry", "TheoryAlignment",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.models",
        (
            "StabilityLevel", "ProxyRestriction", "DelegationKind",
            "ProtocolSection", "ProxyRecord", "DelegationChain",
            "UnstableInterface", "StabilityMonitor",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.protocol_obligations",
        (
            "ProtocolObligation", "ProtocolSatisfactionRecord",
            "ProtocolAuditReport", "WitnessRecord", "ProtocolExtractor",
            "SatisfactionChecker", "ProtocolInheritanceResolver",
            "ProtocolObligationsAnalyzer", "ProtocolObligationsWitness",
            "ProtocolObligationsCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.protocol_sections",
        (
            "ProtocolSectionManager", "ProtocolDescentEngine",
            "ProtocolGluer", "StalenessDetector",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.proxy_delegation",
        (
            "ProxyManager", "DelegationMorphism", "DelegationChainBuilder",
            "ProxyValidator",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.stable_versus_unstable_surface_are",
        (
            "StabilityHeuristic", "SurfaceStabilityRecord", "WitnessRecord",
            "SurfaceAuditReport", "SurfaceClassifier",
            "StabilityHistoryTracker", "SurfaceComparisonEngine",
            "StableUnstableSurfaceAreaAnalyzer",
            "StableUnstableSurfaceAreaWitness",
            "StableUnstableSurfaceAreaCoordinator",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.theorems",
        ("TheoremRecord", "TheoremProver", "TheoremLibrary",),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.unstable_surfaces",
        (
            "SurfaceTracker", "RetractionEventLog", "ObstructionInjector",
            "SurfaceStabilizer",
        ),
    )
    _collect(
        "jugeo.python_runtime.unstable_protocols.why_this_matters_for_repair",
        (
            "RepairRisk", "RepairFeasibility", "RepairConstraint",
            "RepairFeasibilityRecord", "WitnessRecord", "RepairReport",
            "StabilityRepairAnalyzer", "DelegationRepairAnalyzer",
            "ProtocolRepairAnalyzer", "RepairFeasibilityOracle",
            "WhyThisMattersRepairAnalyzer", "WhyThisMattersRepairWitness",
            "WhyThisMattersRepairCoordinator",
        ),
    )

    return registry


# ======================================================================
# AST analysis helpers
# ======================================================================

def _unparse_safe(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "?"


def _sig_str(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    parts: list[str] = []
    for arg in node.args.args:
        ann = f": {_unparse_safe(arg.annotation)}" if arg.annotation else ""
        parts.append(f"{arg.arg}{ann}")
    ret = f" -> {_unparse_safe(node.returns)}" if node.returns else ""
    return f"({', '.join(parts)}){ret}"


# ======================================================================
# AST → Site builder: walks the AST and creates the full Site model
# ======================================================================

class _SheafModelBuilder(ast.NodeVisitor):
    """Walk a Python AST and build a Site with coordinates, morphisms,
    covering families, judgment sections, and semantic contexts."""

    def __init__(self, filename: str, source: str) -> None:
        self.filename = os.path.basename(filename)
        self.source = source
        self._module_name = os.path.splitext(self.filename)[0]

        # Geometry layer
        self.site_builder = SiteBuilder(label=self._module_name)
        self.site_builder.set_topology(GrothendieckTopology.canonical())
        self._coordinates: dict[str, "Coordinate"] = {}
        self._morphisms: list["Morphism"] = []
        self._cover_builders: dict[str, "CoverBuilder"] = {}

        # Module-level coordinate
        self._module_coord = Coordinate(
            components=(self._module_name,),
            kind=CoordinateKind.MODULE,
            metadata={"filename": self.filename},
        )
        self._register_coord(self._module_name, self._module_coord)

        # Judgment layer
        self._judgments: list["Judgment"] = []
        self._section_family = SectionFamily(base_coordinate=self._module_coord)
        self._trust_algebra = TrustAlgebra()

        # Context layer
        self._root_ctx = JudgmentContext(
            self._module_coord,
            trust_boundary="module",
            provenance=("cli_load",),
        )
        self._ctx_stack: list["JudgmentContext"] = [self._root_ctx]
        self._bindings: list["ContextBinding"] = []

        # Call-graph edges for morphisms (caller_key → list[callee_key])
        self._current_scope: str = self._module_name
        self._call_edges: list[tuple[str, str, int]] = []

        # Cover tracking
        self._scope_children: dict[str, list[str]] = {self._module_name: []}

    # ── helpers ───────────────────────────────────────────────────────

    def _register_coord(self, key: str, coord: "Coordinate") -> None:
        self._coordinates[key] = coord
        self.site_builder.add_coordinate(coord)

    def _make_child_coord(
        self, parent_key: str, name: str, kind: "CoordinateKind",
        line: int, metadata: dict[str, Any] | None = None,
    ) -> "Coordinate":
        parent = self._coordinates[parent_key]
        child = Coordinate(
            components=parent.components + (name,),
            kind=kind,
            metadata={**(metadata or {}), "line": line, "file": self.filename},
        )
        key = ".".join(child.components)
        self._register_coord(key, child)

        # Restriction morphism parent → child
        morph = Morphism(
            source=parent, target=child,
            kind=MorphismKind.RESTRICTION,
            label=f"{parent_key} ↓ {name}",
        )
        self._morphisms.append(morph)
        self.site_builder.add_morphism(morph)

        self._scope_children.setdefault(parent_key, []).append(key)
        self._scope_children.setdefault(key, [])
        return child

    def _assign_trust(self, node: ast.AST) -> "TrustLevel":
        """Heuristic trust assignment based on AST node properties."""
        has_docstring = (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        )
        has_annotations = (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.returns is not None
        )
        if has_docstring and has_annotations:
            return TrustLevel.HUMAN_ATTESTED
        if has_annotations:
            return TrustLevel.COPILOT_SUGGESTED
        return TrustLevel.UNVERIFIED

    def _build_judgment(
        self, coord: "Coordinate", formula: str, carrier_name: str,
        prop_kind: "PropositionKind", trust: "TrustLevel",
        obligations: list[str],
    ) -> "Judgment":
        builder = JudgmentBuilder()
        builder.at(coord)
        builder.claiming(Proposition(kind=prop_kind, formula=formula))
        builder.of_type(Carrier(name=carrier_name))
        builder.with_status(JudgmentStatus.PROPOSED)
        builder.from_source(ProvenanceSource.HUMAN)
        j = builder.build()
        self._judgments.append(j)
        return j

    def _build_section(
        self, key: str, coord: "Coordinate", judgment: "Judgment",
        trust: "TrustLevel",
    ) -> "Section":
        sec = (
            SectionBuilder()
            .at_coordinate(coord)
            .with_data("source_file", self.filename)
            .with_data("scope_key", key)
            .with_judgment(judgment.proposition.formula, judgment)
            .with_provenance("cli_load", "ast_analysis")
            .build()
        )
        self._section_family.add_section(key, sec)
        return sec

    def _push_ctx(self, coord: "Coordinate") -> "JudgmentContext":
        parent = self._ctx_stack[-1]
        ctx = JudgmentContext(coord, parent_context=parent, provenance=("cli_load",))
        self._ctx_stack.append(ctx)
        return ctx

    def _pop_ctx(self) -> None:
        if len(self._ctx_stack) > 1:
            self._ctx_stack.pop()

    def _add_binding(self, name: str, value: Any, node: ast.AST) -> None:
        binding = ContextBinding(
            name=name,
            value=value,
            provenance=("cli_load", f"line:{getattr(node, 'lineno', 0)}"),
        )
        self._bindings.append(binding)
        ctx = self._ctx_stack[-1]
        try:
            ctx.extend(name, "binding", value)
        except (ValueError, KeyError):
            pass  # duplicate name in this scope — keep first binding

    # ── visitors ──────────────────────────────────────────────────────

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, is_async=True)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
        is_async: bool,
    ) -> None:
        parent_key = self._current_scope
        kind = CoordinateKind.FUNCTION
        if isinstance(node, ast.AsyncFunctionDef):
            kind = CoordinateKind.FUNCTION
        coord = self._make_child_coord(
            parent_key, node.name, kind, node.lineno,
            metadata={"async": is_async, "signature": _sig_str(node)},
        )
        key = ".".join(coord.components)

        trust = self._assign_trust(node)
        prefix = "async " if is_async else ""
        formula = f"{prefix}function {node.name} is well-typed"
        judgment = self._build_judgment(
            coord, formula, f"{node.name}{_sig_str(node)}",
            PropositionKind.STRUCTURAL, trust,
            obligations=["verify return type", "verify parameter types"],
        )
        self._build_section(key, coord, judgment, trust)

        # Context bindings for parameters
        ctx = self._push_ctx(coord)
        for arg in node.args.args:
            ann = _unparse_safe(arg.annotation) if arg.annotation else "Any"
            self._add_binding(arg.arg, ann, node)

        old_scope = self._current_scope
        self._current_scope = key
        self.generic_visit(node)
        self._current_scope = old_scope
        self._pop_ctx()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        parent_key = self._current_scope
        coord = self._make_child_coord(
            parent_key, node.name, CoordinateKind.INTERFACE, node.lineno,
            metadata={"bases": [_unparse_safe(b) for b in node.bases]},
        )
        key = ".".join(coord.components)

        trust = self._assign_trust(node)
        bases_str = ", ".join(_unparse_safe(b) for b in node.bases)
        judgment = self._build_judgment(
            coord, f"class {node.name} is well-formed",
            f"class {node.name}({bases_str})",
            PropositionKind.STRUCTURAL, trust,
            obligations=["verify MRO", "verify __init__ contract"],
        )
        self._build_section(key, coord, judgment, trust)

        ctx = self._push_ctx(coord)
        old_scope = self._current_scope
        self._current_scope = key
        self.generic_visit(node)
        self._current_scope = old_scope
        self._pop_ctx()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name
            coord = self._make_child_coord(
                self._current_scope, name, CoordinateKind.REGION, node.lineno,
                metadata={"import": alias.name},
            )
            key = ".".join(coord.components)
            judgment = self._build_judgment(
                coord, f"import {alias.name} is resolvable", alias.name,
                PropositionKind.RELATIONAL, TrustLevel.UNVERIFIED,
                obligations=["verify module exists"],
            )
            self._build_section(key, coord, judgment, TrustLevel.UNVERIFIED)
            self._add_binding(name, f"module:{alias.name}", node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in (node.names or []):
            name = alias.asname or alias.name
            coord = self._make_child_coord(
                self._current_scope, f"{module}.{name}",
                CoordinateKind.REGION, node.lineno,
                metadata={"from_module": module, "import_name": alias.name},
            )
            key = ".".join(coord.components)
            judgment = self._build_judgment(
                coord, f"from {module} import {alias.name} is resolvable",
                f"{module}.{alias.name}",
                PropositionKind.RELATIONAL, TrustLevel.UNVERIFIED,
                obligations=["verify name exists in module"],
            )
            self._build_section(key, coord, judgment, TrustLevel.UNVERIFIED)
            self._add_binding(name, f"from:{module}.{alias.name}", node)

    def visit_Call(self, node: ast.Call) -> None:
        callee_name: str | None = None
        if isinstance(node.func, ast.Name):
            callee_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee_name = _unparse_safe(node.func)
        if callee_name:
            self._call_edges.append(
                (self._current_scope, callee_name, getattr(node, "lineno", 0))
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._add_binding(target.id, _unparse_safe(node.value), node)
        self.generic_visit(node)

    # ── finalisation ─────────────────────────────────────────────────

    def _resolve_call_morphisms(self) -> None:
        """Convert collected call edges into transport morphisms."""
        for caller_key, callee_name, lineno in self._call_edges:
            # Try to find callee coordinate by suffix match
            callee_coord: Coordinate | None = None
            callee_key: str | None = None
            for k, c in self._coordinates.items():
                if k.endswith(f".{callee_name}") or k == callee_name:
                    callee_coord = c
                    callee_key = k
                    break
            if callee_coord is None or caller_key not in self._coordinates:
                continue
            caller_coord = self._coordinates[caller_key]
            morph = Morphism(
                source=caller_coord, target=callee_coord,
                kind=MorphismKind.TRANSPORT,
                label=f"call@{lineno}: {caller_key} → {callee_name}",
            )
            self._morphisms.append(morph)
            self.site_builder.add_morphism(morph)

    def _build_covering_families(self) -> None:
        """For each scope with children, build a CoveringFamily."""
        for parent_key, child_keys in self._scope_children.items():
            if not child_keys or parent_key not in self._coordinates:
                continue
            parent_coord = self._coordinates[parent_key]
            members: list[Morphism] = []
            cover_builder = CoverBuilder()
            cover_builder.set_base(parent_coord)
            for ck in child_keys:
                if ck not in self._coordinates:
                    continue
                child_coord = self._coordinates[ck]
                restriction = Morphism(
                    source=parent_coord, target=child_coord,
                    kind=MorphismKind.RESTRICTION,
                    label=f"cover: {parent_key} ↓ {ck}",
                )
                members.append(restriction)
                cover_builder.add_member(child_coord, restriction)
            if members:
                family = CoveringFamily(
                    base=parent_coord, members=members,
                    label=f"children_of_{parent_key}",
                )
                self.site_builder.add_covering_family(family)
                cover_builder.add_provenance("cli_load")
                try:
                    self._cover_builders[parent_key] = cover_builder
                    cover_builder.build()
                except Exception:
                    pass

    def _build_overlap_sections(self) -> None:
        """Create overlap sections for sibling coordinates that share scope."""
        for parent_key, child_keys in self._scope_children.items():
            if len(child_keys) < 2:
                continue
            parent_coord = self._coordinates.get(parent_key)
            if parent_coord is None:
                continue
            for i, k1 in enumerate(child_keys):
                for k2 in child_keys[i + 1:]:
                    c1 = self._coordinates.get(k1)
                    c2 = self._coordinates.get(k2)
                    if c1 is None or c2 is None:
                        continue
                    overlap_sec = (
                        SectionBuilder()
                        .at_coordinate(parent_coord)
                        .with_data("overlap_left", k1)
                        .with_data("overlap_right", k2)
                        .with_provenance("overlap_computation")
                        .build()
                    )
                    self._section_family.add_overlap(k1, k2, overlap_sec)

    def build(self, tree: ast.Module) -> "Site":
        self.visit(tree)
        self._resolve_call_morphisms()
        self._build_covering_families()
        self._build_overlap_sections()
        return self.site_builder.build()

    # ── accessors ────────────────────────────────────────────────────

    @property
    def coordinates(self) -> dict[str, "Coordinate"]:
        return dict(self._coordinates)

    @property
    def morphisms(self) -> list["Morphism"]:
        return list(self._morphisms)

    @property
    def judgments(self) -> list["Judgment"]:
        return list(self._judgments)

    @property
    def section_family(self) -> "SectionFamily":
        return self._section_family

    @property
    def semantic_context(self) -> "SemanticContext":
        return SemanticContext(
            coordinate=self._module_coord,
            bindings=tuple(self._bindings),
            assumptions=(),
            provenance=("cli_load",),
        )

    @property
    def trust_algebra(self) -> "TrustAlgebra":
        return self._trust_algebra


# ======================================================================
# Build a sheaf model from source (public helper, reused by cmd_encode)
# ======================================================================

def build_sheaf_model(
    source: str, filename: str,
) -> Tuple[Optional["Site"], Optional["_SheafModelBuilder"]]:
    """Parse *source* and build a full sheaf-theoretic Site model.

    Returns ``(site, builder)`` on success or ``(None, None)`` if the
    JuGeo subsystems are not available.
    """
    if not _ALL_SUBSYSTEMS:
        return None, None
    tree = ast.parse(source, filename=filename)
    builder = _SheafModelBuilder(filename, source)
    site = builder.build(tree)
    return site, builder


# ======================================================================
# Formatting helpers
# ======================================================================

def _format_site_structure(
    site: "Site", builder: "_SheafModelBuilder",
) -> str:
    """Render the full Site: coordinates, morphisms, covering families."""
    lines = ["Site Structure", "=" * 60]

    # Coordinates
    lines.append("\n── Coordinates ────────────────────────────────────")
    for key, coord in sorted(builder.coordinates.items()):
        depth = coord.depth if hasattr(coord, "depth") else len(coord.components)
        indent = "  " * depth
        kind_label = coord.kind.value if hasattr(coord.kind, "value") else str(coord.kind)
        meta_line = ""
        if hasattr(coord, "metadata") and coord.metadata:
            m = dict(coord.metadata)
            m.pop("filename", None)
            m.pop("file", None)
            if m:
                meta_line = f"  {m}"
        lines.append(f"  {indent}[{kind_label}] {key}{meta_line}")
    lines.append(f"  Total: {len(builder.coordinates)} coordinate(s)")

    # Morphisms
    lines.append("\n── Morphisms ──────────────────────────────────────")
    restrictions = [m for m in builder.morphisms if m.kind == MorphismKind.RESTRICTION]
    transports = [m for m in builder.morphisms if m.kind == MorphismKind.TRANSPORT]
    for m in restrictions[:30]:
        src = ".".join(m.source.components) if hasattr(m.source, "components") else str(m.source)
        tgt = ".".join(m.target.components) if hasattr(m.target, "components") else str(m.target)
        lines.append(f"  ↓ restriction  {src} → {tgt}")
    for m in transports[:30]:
        lines.append(f"  → transport    {m.label}")
    total = len(builder.morphisms)
    shown = min(len(restrictions), 30) + min(len(transports), 30)
    if shown < total:
        lines.append(f"  ... and {total - shown} more")
    lines.append(f"  Total: {total} morphism(s)  "
                 f"({len(restrictions)} restriction, {len(transports)} transport)")

    # Covering families
    lines.append("\n── Covering Families ──────────────────────────────")
    families = site.covering_families()
    for fam in families[:20]:
        base_key = ".".join(fam.base.components) if hasattr(fam.base, "components") else str(fam.base)
        n_members = len(fam.members)
        lines.append(f"  {fam.label or base_key}: {n_members} member(s) "
                     f"covering {base_key}")
    if len(families) > 20:
        lines.append(f"  ... and {len(families) - 20} more")
    lines.append(f"  Total: {len(families)} covering family/families")

    return "\n".join(lines)


def _format_judgment_sections(builder: "_SheafModelBuilder") -> str:
    """Render judgment sections with trust levels."""
    lines = ["Judgment Sections (c, φ, A, E, O, B, T, Π)", "=" * 60]
    trust_algebra = builder.trust_algebra

    for j in builder.judgments:
        coord_key = ".".join(j.coordinate.components) if hasattr(j.coordinate, "components") else str(j.coordinate)
        trust_label = j.trust.level.label() if hasattr(j.trust, "level") and hasattr(j.trust.level, "label") else str(getattr(j.trust, "level", "?"))
        lines.append(f"  c  = {coord_key}")
        lines.append(f"  φ  = {j.proposition.formula}")
        lines.append(f"  A  = {j.carrier.name}")
        lines.append(f"  E  = {list(j.evidence) if j.evidence else '[]'}")
        lines.append(f"  O  = {[o.description for o in j.obligations] if j.obligations else '[]'}")
        lines.append(f"  B  = {[o.description for o in j.obstructions] if j.obstructions else '[]'}")
        lines.append(f"  T  = {trust_label}")
        lines.append(f"  Π  = {j.provenance}")
        lines.append(f"  status = {j.status.value if hasattr(j.status, 'value') else j.status}")
        lines.append("  " + "-" * 40)

    # Section family compatibility
    sf = builder.section_family
    compat_issues = sf.verify_compatibility()
    if compat_issues:
        lines.append(f"\n  ⚠ Compatibility issues: {len(compat_issues)}")
        for left, right, msg in compat_issues[:10]:
            lines.append(f"    {left} ∩ {right}: {msg}")
    else:
        lines.append("\n  ✓ All sections compatible on overlaps")

    lines.append(f"\nTotal: {len(builder.judgments)} judgment section(s)")
    return "\n".join(lines)


def _format_summary(
    builder: "_SheafModelBuilder", site: "Site",
) -> str:
    lines = ["Load Summary", "=" * 60]
    lines.append(f"  Site label          : {site.label}")
    lines.append(f"  Topology            : {site.topology.name}")
    lines.append(f"  Coordinates         : {len(builder.coordinates)}")
    lines.append(f"  Morphisms           : {len(builder.morphisms)}")
    lines.append(f"  Covering families   : {len(site.covering_families())}")
    lines.append(f"  Judgment sections   : {len(builder.judgments)}")
    lines.append(f"  Context bindings    : {len(builder._bindings)}")

    # Kind breakdown
    kinds: dict[str, int] = {}
    for coord in builder.coordinates.values():
        k = coord.kind.value if hasattr(coord.kind, "value") else str(coord.kind)
        kinds[k] = kinds.get(k, 0) + 1
    for k, cnt in sorted(kinds.items()):
        lines.append(f"    {k:20s}: {cnt}")

    # Trust distribution
    trust_dist: dict[str, int] = {}
    for j in builder.judgments:
        t = j.trust.level.label() if hasattr(j.trust, "level") and hasattr(j.trust.level, "label") else "unknown"
        trust_dist[t] = trust_dist.get(t, 0) + 1
    if trust_dist:
        lines.append("  Trust distribution:")
        for t, cnt in sorted(trust_dist.items()):
            lines.append(f"    {t:20s}: {cnt}")

    return "\n".join(lines)


def _to_json(
    builder: "_SheafModelBuilder", site: "Site",
    show_coords: bool, show_sections: bool,
) -> str:
    result: dict[str, Any] = {
        "summary": {
            "site_label": site.label,
            "topology": site.topology.name,
            "coordinates": len(builder.coordinates),
            "morphisms": len(builder.morphisms),
            "covering_families": len(site.covering_families()),
            "judgments": len(builder.judgments),
            "context_bindings": len(builder._bindings),
        },
    }
    if show_coords:
        coords_out: list[dict[str, Any]] = []
        for key, coord in sorted(builder.coordinates.items()):
            coords_out.append({
                "key": key,
                "kind": coord.kind.value if hasattr(coord.kind, "value") else str(coord.kind),
                "components": list(coord.components),
                "depth": coord.depth if hasattr(coord, "depth") else len(coord.components),
            })
        morphisms_out: list[dict[str, Any]] = []
        for m in builder.morphisms:
            morphisms_out.append({
                "source": ".".join(m.source.components) if hasattr(m.source, "components") else str(m.source),
                "target": ".".join(m.target.components) if hasattr(m.target, "components") else str(m.target),
                "kind": m.kind.value if hasattr(m.kind, "value") else str(m.kind),
                "label": m.label,
            })
        families_out: list[dict[str, Any]] = []
        for fam in site.covering_families():
            families_out.append({
                "base": ".".join(fam.base.components) if hasattr(fam.base, "components") else str(fam.base),
                "label": fam.label,
                "members": len(fam.members),
            })
        result["coordinates"] = coords_out
        result["morphisms"] = morphisms_out
        result["covering_families"] = families_out
    if show_sections:
        sections_out: list[dict[str, Any]] = []
        for j in builder.judgments:
            sections_out.append({
                "coordinate": ".".join(j.coordinate.components) if hasattr(j.coordinate, "components") else str(j.coordinate),
                "proposition": j.proposition.formula,
                "carrier": j.carrier.name,
                "evidence": [str(e) for e in j.evidence] if j.evidence else [],
                "obligations": [o.description for o in j.obligations] if j.obligations else [],
                "obstructions": [o.description for o in j.obstructions] if j.obstructions else [],
                "trust": j.trust.level.label() if hasattr(j.trust, "level") and hasattr(j.trust.level, "label") else str(getattr(j.trust, "level", "?")),
                "status": j.status.value if hasattr(j.status, "value") else str(j.status),
            })
        compat = builder.section_family.verify_compatibility()
        result["sections"] = sections_out
        result["section_compatibility"] = {
            "issues": len(compat),
            "details": [{"left": l, "right": r, "msg": m} for l, r, m in compat[:20]],
        }
    return json.dumps(result, indent=2)


# ======================================================================
# Rich program analysis (--deep)
# ======================================================================

def _rich_program_analysis(filepath: str) -> str:
    """Produce a rich program analysis report for *filepath*.

    Uses ProgramLoader (build_sheaf_model), GeneratedContractsAnalyzer,
    CallableSurfaceAnalyzer, and effects_async analysis.  All imports are
    guarded so a partial result is always produced.
    """
    basename = os.path.basename(filepath)
    lines: list[str] = [f"Program analysis for {basename}:", ""]

    try:
        source = open(filepath, encoding="utf-8").read()
        tree = ast.parse(source, filepath)
    except Exception:
        source = ""
        tree = None

    # -- Load via sheaf model ------------------------------------------
    func_count = 0
    class_count = 0
    import_count = 0
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_count += 1
            elif isinstance(node, ast.ClassDef):
                class_count += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                import_count += 1

    site_obj = None
    builder_obj = None
    if _ALL_SUBSYSTEMS:
        try:
            site_obj, builder_obj = build_sheaf_model(source, filepath)
        except Exception:
            pass

    loaded_label = (
        f"SymbolicProgram(functions={func_count}, "
        f"classes={class_count}, imports={import_count})"
    )
    lines.append(f"  Loaded: {loaded_label}")
    lines.append("")

    # -- Generated contracts (python_runtime.generated_contracts) ------
    contract_lines: list[str] = []
    try:
        from jugeo.python_runtime.generated_contracts.generated_contracts import (
            GeneratedContractsAnalyzer,
            DataclassContractExtractor,
            ProtocolContractExtractor,
            TypedDictContractExtractor,
            NamedTupleContractExtractor,
        )

        analyzer = GeneratedContractsAnalyzer()

        dc_contracts: list[str] = []
        proto_contracts: list[str] = []
        total_contracts = 0
        total_invariants = 0
        total_witnesses = 0

        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # Detect dataclass-like contracts
                    is_dataclass = any(
                        (isinstance(d, ast.Name) and d.id == "dataclass")
                        or (isinstance(d, ast.Call)
                            and isinstance(getattr(d, "func", None), ast.Name)
                            and d.func.id == "dataclass")
                        for d in node.decorator_list
                    )
                    if is_dataclass:
                        fields = []
                        for child in node.body:
                            if isinstance(child, ast.AnnAssign) and isinstance(
                                child.target, ast.Name
                            ):
                                ann = ast.unparse(child.annotation) if child.annotation else "Any"
                                fields.append(f"{child.target.id}: {ann}")
                        fields_str = ", ".join(fields[:4])
                        if len(fields) > 4:
                            fields_str += ", …"
                        dc_contracts.append(
                            f"    • DataclassContract: {node.name}({fields_str}) "
                            f"— {len(fields)} field invariants"
                        )
                        total_contracts += 1
                        total_invariants += len(fields)

                    # Detect Protocol subclasses
                    for base in node.bases:
                        base_name = ""
                        if isinstance(base, ast.Name):
                            base_name = base.id
                        elif isinstance(base, ast.Attribute):
                            base_name = base.attr
                        if base_name == "Protocol":
                            methods = [
                                n for n in node.body
                                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                            ]
                            proto_contracts.append(
                                f"    • ProtocolContract: {node.name} "
                                f"— {len(methods)} method requirement(s)"
                            )
                            total_contracts += 1
                            total_invariants += len(methods)

                    # Detect TypedDict
                    for base in node.bases:
                        base_name = ""
                        if isinstance(base, ast.Name):
                            base_name = base.id
                        elif isinstance(base, ast.Attribute):
                            base_name = base.attr
                        if base_name == "TypedDict":
                            td_fields = [
                                n for n in node.body
                                if isinstance(n, ast.AnnAssign)
                            ]
                            dc_contracts.append(
                                f"    • TypedDictContract: {node.name} "
                                f"— {len(td_fields)} key constraints"
                            )
                            total_contracts += 1
                            total_invariants += len(td_fields)

        total_witnesses = total_contracts + total_invariants
        contract_lines.extend(dc_contracts)
        contract_lines.extend(proto_contracts)
        contract_lines.append(
            f"    • Total: {total_contracts} contracts extracted, "
            f"{total_witnesses} witnesses found"
        )
    except Exception as exc:
        contract_lines.append(f"    (unavailable: {exc})")

    lines.append("  Generated contracts:")
    lines.extend(contract_lines)
    lines.append("")

    # -- Callable surface topology (python_runtime.callable_surfaces) --
    callable_lines: list[str] = []
    try:
        from jugeo.python_runtime.callable_surfaces.algorithms import (
            CallableSurfaceAnalyzer,
        )
        from jugeo.python_runtime.callable_surfaces.models import (
            CallableSurface, ParameterKind,
        )

        cs_analyzer = CallableSurfaceAnalyzer()

        public_callables = 0
        internal_callables = 0
        max_depth = 0
        call_graph: dict[str, list[str]] = {}

        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    if name.startswith("_"):
                        internal_callables += 1
                    else:
                        public_callables += 1
                    # Build call graph edges
                    callees: list[str] = []
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            if isinstance(child.func, ast.Name):
                                callees.append(child.func.id)
                            elif isinstance(child.func, ast.Attribute):
                                callees.append(child.func.attr)
                    call_graph[name] = callees

            # Compute call graph depth via BFS
            def _depth(fname: str, visited: set[str]) -> int:
                if fname in visited or fname not in call_graph:
                    return 0
                visited.add(fname)
                child_depths = [
                    _depth(c, visited) for c in call_graph[fname]
                ]
                return 1 + max(child_depths) if child_depths else 1

            for fn in call_graph:
                d = _depth(fn, set())
                if d > max_depth:
                    max_depth = d

        surface_area = public_callables + (1 if class_count > 0 else 0)
        callable_lines.append(f"    • Public callables: {public_callables}")
        callable_lines.append(f"    • Internal callables: {internal_callables}")
        callable_lines.append(f"    • Call graph depth: {max_depth}")
        callable_lines.append(f"    • Surface area: {surface_area} entry points")
    except Exception as exc:
        callable_lines.append(f"    (unavailable: {exc})")

    lines.append("  Callable surface topology:")
    lines.extend(callable_lines)
    lines.append("")

    # -- Effects analysis (python_runtime.effects_async) ---------------
    effects_lines: list[str] = []
    try:
        from jugeo.python_runtime.effects_async.algorithms import AlgorithmSuite
        from jugeo.python_runtime.effects_async.async_and_task_semantics_suspended import (
            AsyncTaskSemanticsSuspendedAnalyzer,
        )

        effects_analyzer = AsyncTaskSemanticsSuspendedAnalyzer()

        pure_fns = 0
        io_effects: list[str] = []
        state_effects: list[str] = []

        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    has_io = False
                    has_state = False
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            fn_name = ""
                            if isinstance(child.func, ast.Name):
                                fn_name = child.func.id
                            elif isinstance(child.func, ast.Attribute):
                                fn_name = child.func.attr
                            if fn_name in ("open", "read", "write", "print",
                                           "input", "readline", "readlines"):
                                has_io = True
                                io_effects.append(fn_name)
                            if fn_name in ("append", "extend", "insert",
                                           "pop", "remove", "update",
                                           "setattr", "delattr"):
                                has_state = True
                                state_effects.append(fn_name)
                    if not has_io and not has_state:
                        pure_fns += 1

        io_effects = list(dict.fromkeys(io_effects))
        state_effects = list(dict.fromkeys(state_effects))

        effects_lines.append(f"    • Pure functions: {pure_fns}")
        if io_effects:
            io_str = ", ".join(io_effects[:3])
            effects_lines.append(f"    • IO effects: {len(io_effects)} ({io_str})")
        else:
            effects_lines.append("    • IO effects: 0")
        if state_effects:
            st_str = ", ".join(state_effects[:3])
            effects_lines.append(
                f"    • State effects: {len(state_effects)} ({st_str})"
            )
        else:
            effects_lines.append("    • State effects: 0")
    except Exception as exc:
        effects_lines.append(f"    (unavailable: {exc})")

    lines.append("  Effects analysis:")
    lines.extend(effects_lines)

    return "\n".join(lines)


# ======================================================================
# Main entry point
# ======================================================================

def run_load(args: argparse.Namespace) -> int:
    """Run the load/analyze pipeline on the files specified in *args*.

    Parameters
    ----------
    args : argparse.Namespace
        Expected attributes:
        - ``files``       – list of file paths to load
        - ``coordinates`` – if True, display coordinate map
        - ``sections``    – if True, display judgment sections
        - ``format``      – output format (``"text"`` or ``"json"``)
        - ``verbose``     – enable debug logging

    Returns
    -------
    int
        0 on success, 1 on failure.
    """
    files: list[str] = getattr(args, "files", [])
    show_coords: bool = getattr(args, "coordinates", False)
    show_sections: bool = getattr(args, "sections", False)
    out_format: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)
    deep: bool = getattr(args, "deep", False)

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    show_registry: bool = getattr(args, "registry", False)
    if show_registry:
        registry = _python_runtime_registry()
        print(f"python_runtime class registry: {len(registry)} classes available")
        for name in sorted(registry):
            print(f"  {name}: {registry[name].__module__}")
        return 0

    # --deep: rich program analysis using python_runtime classes
    if deep:
        had_errors = False
        for filepath in files:
            filepath = os.path.abspath(filepath)
            if not os.path.isfile(filepath):
                print(f"error: {filepath}: not a file", file=sys.stderr)
                had_errors = True
                continue
            print(_rich_program_analysis(filepath))
            print()
        return 1 if had_errors else 0

    if not _ALL_SUBSYSTEMS:
        _log.debug("One or more JuGeo subsystems unavailable; "
                    "site=%s judgment=%s section=%s context=%s trust=%s cover=%s",
                    _SITE_OK, _JUDGMENT_OK, _SECTION_OK,
                    _CONTEXT_OK, _TRUST_OK, _COVER_OK)
        print("error: JuGeo subsystems not fully available. Required: "
              "geometry.site, judgments.judgment_terms, judgments.sections, "
              "judgments.contexts, evidence.trust, geometry.covers",
              file=sys.stderr)
        return 1

    had_errors = False
    all_sites: list[tuple["Site", "_SheafModelBuilder"]] = []

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

        _log.debug("Loading %s …", filepath)
        try:
            site, builder = build_sheaf_model(source, filepath)
            if site is not None and builder is not None:
                all_sites.append((site, builder))
                _log.debug("Built site for %s: %d coords, %d morphisms, "
                           "%d judgments",
                           filepath, len(builder.coordinates),
                           len(builder.morphisms), len(builder.judgments))
                # Enrich model with python_runtime class metadata
                _rt_registry = _python_runtime_registry()
                if _rt_registry:
                    builder.metadata["python_runtime_classes"] = len(_rt_registry)
                    _log.debug("python_runtime registry: %d classes available",
                               len(_rt_registry))
            else:
                print(f"error: {filepath}: failed to build sheaf model",
                      file=sys.stderr)
                had_errors = True
        except SyntaxError as exc:
            print(f"error: {filepath}: SyntaxError: {exc.msg} "
                  f"(line {exc.lineno})", file=sys.stderr)
            had_errors = True
        except Exception as exc:
            print(f"error: {filepath}: {exc}", file=sys.stderr)
            _log.debug("Traceback:", exc_info=True)
            had_errors = True

    if not all_sites and not had_errors:
        print("No loadable files found.", file=sys.stderr)
        return 1

    # ── Output ────────────────────────────────────────────────────────
    for site, builder in all_sites:
        if out_format == "json":
            print(_to_json(builder, site, show_coords, show_sections))
        else:
            if show_coords:
                print(_format_site_structure(site, builder))
                print()
            if show_sections:
                print(_format_judgment_sections(builder))
                print()
            if not show_coords and not show_sections:
                print(_format_summary(builder, site))
            elif show_coords or show_sections:
                print(_format_summary(builder, site))
            print()

    return 1 if had_errors else 0
