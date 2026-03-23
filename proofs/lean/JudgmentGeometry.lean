/-
  JudgmentGeometry.lean — Library root for the Judgment Geometry formalization.

  Imports all five paper formalizations:
    Paper 01: Semantic Sites (Grothendieck topologies for programs)
    Paper 02: Judgment Algebra (the 8-tuple algebra)
    Paper 03: Descent Obstructions (Čech cohomology for proofs)
    Paper 04: Trust Algebra (bounded distributive lattice)
    Paper 05: Fragment Routing (SMT-LIB fragment-aware VC routing)
-/

-- NOTE: These are namespace-based imports.  In a Lake project these
--       would be `import JudgmentGeometry.Paper01_SemanticSites` etc.
--       For standalone checking, each file is self-contained and can
--       be verified independently with `lean <file>.lean`.

-- To verify all files at once, run:
--   lean proofs/lean/Paper01_SemanticSites.lean
--   lean proofs/lean/Paper02_JudgmentAlgebra.lean
--   lean proofs/lean/Paper03_DescentObstructions.lean
--   lean proofs/lean/Paper04_TrustAlgebra.lean
--   lean proofs/lean/Paper05_FragmentRouting.lean

namespace JudgmentGeometry

/-- The Judgment Geometry formalization library.

    Papers formalized:
    • Paper 01 — Semantic Sites: Grothendieck topologies for program coordinates
    • Paper 02 — Judgment Algebra: The 8-tuple with cut admissibility
    • Paper 03 — Descent Obstructions: Čech cohomology and gluing
    • Paper 04 — Trust Algebra: Bounded distributive lattice (fully proved)
    • Paper 05 — Fragment Routing: SMT-LIB fragment dispatch with trust bounds

    Key results:
    • Site axioms (identity, stability, transitivity) for program coordinates
    • Cut admissibility with trust monotonicity
    • Descent theorem: compatible families glue uniquely
    • Trust levels form a bounded distributive lattice (all 12 laws proved)
    • No silent promotion: empty justification ⟹ promotion fails
    • Routing soundness: dispatched backends cover the requested fragment
    • Trust ceiling: routed trust ≤ min(query trust, backend ceiling)
-/
theorem library_root : True := trivial

end JudgmentGeometry
