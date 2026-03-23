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
             `Paper11_NerveTheorems,
             `Paper16_PersistentHomology,
             `Paper14_OperadicComposition,
             `Paper20_HomotopyTypeTheory,
             `Paper28_DeRhamCohomology,
             `Paper39_GeneratedContracts]
