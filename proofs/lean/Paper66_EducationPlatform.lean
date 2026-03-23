/-
  Paper66_EducationPlatform.lean — Educational Proof Exploration

  Formalizes Paper 66 of the Judgment Geometry series:
    • TrustTier: five pedagogical trust levels (unverified → proof)
    • ExerciseKind: the five canonical exercise types
    • HintLevel: graduated hint system (1–4)
    • ExerciseSpec: exercise specification at a coordinate
    • StepMode: step-mode judgment construction
    • exercise_completeness: k coordinates → k exercise opportunities
    • autograder_soundness: autograder accepts iff valid at required trust
    • hint_monotone: higher hint levels reveal strictly more information

  All theorems proved without sorry.
-/

namespace JudgmentGeometry.EducationPlatform

-- ════════════════════════════════════════════════════════════════════
-- § 1  Pedagogical Trust Tiers
-- ════════════════════════════════════════════════════════════════════

/-- Five trust levels aligned to course progression (weeks 1–10). -/
inductive TrustTier where
  | unverified   -- weeks 1–2: informal claims
  | copilot      -- weeks 3–4: AI-suggested specs
  | runtime      -- weeks 5–6: test-case evidence
  | solver       -- weeks 7–8: SMT encoding
  | proof        -- weeks 9–10: Lean proof
  deriving DecidableEq, Repr, BEq

def TrustTier.toNat : TrustTier → Nat
  | .unverified => 0
  | .copilot    => 1
  | .runtime    => 2
  | .solver     => 3
  | .proof      => 4

instance : LE TrustTier where
  le a b := a.toNat ≤ b.toNat

instance (a b : TrustTier) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

theorem TrustTier.le_refl (t : TrustTier) : t ≤ t := Nat.le_refl _

theorem TrustTier.le_trans {a b c : TrustTier} (h1 : a ≤ b) (h2 : b ≤ c) : a ≤ c :=
  Nat.le_trans h1 h2

theorem TrustTier.toNat_le_four (t : TrustTier) : t.toNat ≤ 4 := by
  cases t <;> simp [TrustTier.toNat] <;> omega

-- ════════════════════════════════════════════════════════════════════
-- § 2  Exercise Kinds
-- ════════════════════════════════════════════════════════════════════

/-- The five canonical exercise types from the pedagogical framework. -/
inductive ExerciseKind where
  | coordinateId         -- identify code coordinates
  | propositionWriting   -- write propositions for coordinates
  | evidenceConstruction -- construct evidence at a trust tier
  | obstructionAnalysis  -- identify obstructions in failing code
  | descentCompletion    -- complete a sheaf descent argument
  deriving DecidableEq, Repr

/-- Minimum trust tier required to attempt each exercise kind. -/
def ExerciseKind.minTier : ExerciseKind → TrustTier
  | .coordinateId         => .unverified
  | .propositionWriting   => .copilot
  | .evidenceConstruction => .runtime
  | .obstructionAnalysis  => .solver
  | .descentCompletion    => .proof

-- ════════════════════════════════════════════════════════════════════
-- § 3  Hint System
-- ════════════════════════════════════════════════════════════════════

/-- Graduated hint levels: higher reveals more. -/
inductive HintLevel where
  | coordOnly   -- level 1: show coordinate identification
  | propSketch  -- level 2: sketch proposition structure
  | evidHint    -- level 3: partial evidence strategy
  | fullEvid    -- level 4: full evidence construction
  deriving DecidableEq, Repr

def HintLevel.toNat : HintLevel → Nat
  | .coordOnly  => 1
  | .propSketch => 2
  | .evidHint   => 3
  | .fullEvid   => 4

/-- Information content monotonically increases with hint level. -/
def HintLevel.informationContent : HintLevel → Nat
  | .coordOnly  => 10
  | .propSketch => 30
  | .evidHint   => 60
  | .fullEvid   => 100

theorem hint_monotone (h1 h2 : HintLevel) (hle : h1.toNat ≤ h2.toNat) :
    h1.informationContent ≤ h2.informationContent := by
  cases h1 <;> cases h2 <;> simp [HintLevel.toNat, HintLevel.informationContent] at * <;> omega

-- ════════════════════════════════════════════════════════════════════
-- § 4  Exercise Specification & Step Mode
-- ════════════════════════════════════════════════════════════════════

/-- A coordinate in the educational site. -/
structure EduCoord where
  name : String
  deriving DecidableEq, Repr

/-- An exercise specification at a single coordinate. -/
structure ExerciseSpec where
  coord     : EduCoord
  kind      : ExerciseKind
  reqTrust  : TrustTier
  deriving Repr

/-- Step mode: produces one exercise opportunity per coordinate. -/
def stepMode (coords : List EduCoord) (kind : ExerciseKind) (tier : TrustTier) :
    List ExerciseSpec :=
  coords.map (fun c => { coord := c, kind := kind, reqTrust := tier })

/-- **Exercise Completeness** (Theorem 4.1): step mode produces exactly
    k exercise opportunities for k coordinates. -/
theorem exercise_completeness (coords : List EduCoord) (kind : ExerciseKind)
    (tier : TrustTier) :
    (stepMode coords kind tier).length = coords.length := by
  simp [stepMode, List.length_map]

-- ════════════════════════════════════════════════════════════════════
-- § 5  Submission & Autograder
-- ════════════════════════════════════════════════════════════════════

/-- A student submission for a single exercise. -/
structure Submission where
  spec       : ExerciseSpec
  achieved   : TrustTier
  hasEvidence : Bool
  deriving Repr

/-- The autograder accepts iff evidence is present and achieved trust
    meets the required tier. -/
def autograderAccepts (sub : Submission) : Bool :=
  sub.hasEvidence && decide (sub.spec.reqTrust ≤ sub.achieved)

/-- **Autograder Soundness** (Proposition 5.1): acceptance iff valid
    evidence at the required trust level. -/
theorem autograder_soundness (sub : Submission) :
    autograderAccepts sub = true ↔
      sub.hasEvidence = true ∧ sub.spec.reqTrust ≤ sub.achieved := by
  simp [autograderAccepts, Bool.and_eq_true]

/-- Rejected submissions have insufficient trust or missing evidence. -/
theorem autograder_rejection (sub : Submission)
    (h : autograderAccepts sub = false) :
    sub.hasEvidence = false ∨ ¬(sub.spec.reqTrust ≤ sub.achieved) := by
  simp [autograderAccepts] at h
  cases hev : sub.hasEvidence with
  | false => left; rfl
  | true => right; simp_all [Bool.and_eq_true]

-- ════════════════════════════════════════════════════════════════════
-- § 6  Course Progression
-- ════════════════════════════════════════════════════════════════════

/-- A course week maps to a trust tier. -/
def weekToTier (week : Nat) : TrustTier :=
  if week ≤ 2 then .unverified
  else if week ≤ 4 then .copilot
  else if week ≤ 6 then .runtime
  else if week ≤ 8 then .solver
  else .proof

/-- Course tier at week 1 is unverified. -/
theorem weekToTier_week1 : weekToTier 1 = .unverified := by decide

/-- Course tier at week 5 is runtime. -/
theorem weekToTier_week5 : weekToTier 5 = .runtime := by decide

/-- Course tier at week 9 is proof. -/
theorem weekToTier_week9 : weekToTier 9 = .proof := by decide

/-- Course tier at week 10 is proof. -/
theorem weekToTier_week10 : weekToTier 10 = .proof := by decide

-- ════════════════════════════════════════════════════════════════════
-- § 7  Summary
-- ════════════════════════════════════════════════════════════════════

theorem educationPlatformSoundness :
    -- (a) Exercise completeness: k coordinates → k opportunities
    (∀ cs kind tier, (stepMode cs kind tier).length = cs.length) ∧
    -- (b) Autograder soundness
    (∀ sub, autograderAccepts sub = true ↔
      sub.hasEvidence = true ∧ sub.spec.reqTrust ≤ sub.achieved) ∧
    -- (c) All trust tiers bounded by 4
    (∀ t : TrustTier, t.toNat ≤ 4) := by
  exact ⟨exercise_completeness, autograder_soundness, TrustTier.toNat_le_four⟩

end JudgmentGeometry.EducationPlatform
