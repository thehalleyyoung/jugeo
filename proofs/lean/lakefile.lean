import Lake
open Lake DSL

package «judgment-geometry» where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]

@[default_target]
lean_lib «JudgmentGeometry» where
  srcDir := "."
  roots := #[`Common, `JudgmentGeometry,
             `Paper00_Seminal, `Paper01_SemanticSites,
             `Paper02_JudgmentAlgebra, `Paper03_DescentObstructions,
             `Paper04_TrustAlgebra, `Paper05_FragmentRouting,
             `Paper06_SemanticMoves, `Paper07_PythonEffects,
             `Paper08_TreatySynthesis, `Paper09_ProofCarryingPython,
             `Paper10_Evaluation,
             `Paper11_CoverDesign, `Paper12_InhabitantFleets,
             `Paper13_ScalarEncodings, `Paper14_DiscoveryEngine,
             `Paper15_TheoremEcologies, `Paper16_BudgetAllocation,
             `Paper16_PersistentHomology,
             `Paper17_FleetCompetition, `Paper18_HeapAliasing,
             `Paper19_AuthorityDelegation, `Paper20_CountermodelExtraction,
             `Paper21_DoctrineCompletion, `Paper22_TreatyMemory,
             `Paper23_EvidenceRouting, `Paper24_AsyncEffects,
             `Paper25_MetaobjectAnalysis, `Paper26_ImportGraph,
             `Paper27_CertificateChains, `Paper28_BugDetection,
             `Paper29_RepairSemantics, `Paper30_SemanticControl,
             `Paper32_SequenceEncodings, `Paper33_TextEncodings,
             `Paper34_DeductionRules, `Paper35_PartialityModels,
             `Paper36_AblationMethodology, `Paper37_PackFederation,
             `Paper38_SemanticCaching, `Paper39_GeneratedContracts,
             `Paper40_ReplayGluing, `Paper41_TensorEncodings,
             `Paper42_OracleFederation, `Paper43_Hypercovers,
             `Paper44_JudgmentProducts, `Paper45_CallableSurfaces,
             `Paper46_SemanticFutures, `Paper47_SpecSatisfaction,
             `Paper48_LiveMutation, `Paper49_CyclicMaturity,
             `Paper50_SemanticCenters]
