/-
  Paper67_CodeReviewAutomation.lean — Automated Code Review

  Formalizes Paper 67 of the Judgment Geometry series:
    • Severity: four review severity levels (pass, info, warning, critical)
    • ReviewCoord: diff-mapped code coordinate
    • ReviewFinding: assessment at a coordinate with trust and severity
    • DiffAnalyzer: maps changed coordinates from diffs
    • reviewSeverity: classifies severity from trust/obstruction data
    • extractFindings: canonical review pipeline
    • review_completeness: every changed coordinate gets assessed
    • zero_false_positives: correct code yields only PASS severity
    • severity_monotone: lower trust → higher severity

  All theorems proved without sorry.
-/

namespace JudgmentGeometry.CodeReviewAutomation

-- ════════════════════════════════════════════════════════════════════
-- § 1  Review Severity
-- ════════════════════════════════════════════════════════════════════

/-- Four review severity levels, ordered from benign to critical. -/
inductive Severity where
  | pass     -- trust ∈ {solver, proof}: no issues
  | info     -- trust ∈ {runtime, oracle}: informational
  | warning  -- trust ≤ copilot, no obstructions
  | critical -- obstructions present or contradicted trust
  deriving DecidableEq, Repr, BEq

def Severity.toNat : Severity → Nat
  | .pass     => 0
  | .info     => 1
  | .warning  => 2
  | .critical => 3

instance : LE Severity where
  le a b := a.toNat ≤ b.toNat

instance (a b : Severity) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

-- ════════════════════════════════════════════════════════════════════
-- § 2  Trust Levels (local, lightweight)
-- ════════════════════════════════════════════════════════════════════

/-- Trust levels for review assessment (simplified from Common). -/
inductive ReviewTrust where
  | contradicted | unverified | copilot | runtime
  | oracle | solver | proof
  deriving DecidableEq, Repr, BEq

def ReviewTrust.toNat : ReviewTrust → Nat
  | .contradicted => 0
  | .unverified   => 1
  | .copilot      => 2
  | .runtime      => 3
  | .oracle       => 4
  | .solver       => 5
  | .proof        => 6

instance : LE ReviewTrust where
  le a b := a.toNat ≤ b.toNat

instance (a b : ReviewTrust) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

-- ════════════════════════════════════════════════════════════════════
-- § 3  Review Coordinates and Findings
-- ════════════════════════════════════════════════════════════════════

/-- A coordinate in the diff-mapped code site. -/
structure ReviewCoord where
  file : String
  line : Nat
  deriving DecidableEq, Repr

/-- A review finding: trust level, obstruction count, computed severity. -/
structure ReviewFinding where
  coord         : ReviewCoord
  trust         : ReviewTrust
  obstructions  : Nat        -- number of obstructions found
  severity      : Severity
  deriving Repr

-- ════════════════════════════════════════════════════════════════════
-- § 4  Severity Classification
-- ════════════════════════════════════════════════════════════════════

/-- Classify severity from trust level and obstruction count.
    Matches the four-tier scheme from Section 3 of the paper. -/
def reviewSeverity (trust : ReviewTrust) (numObstructions : Nat) : Severity :=
  if numObstructions > 0 then .critical
  else match trust with
    | .contradicted => .critical
    | .unverified | .copilot => .warning
    | .runtime | .oracle => .info
    | .solver | .proof => .pass

/-- Correct code (solver+ trust, no obstructions) always gets PASS. -/
theorem zero_false_positives (trust : ReviewTrust)
    (hobs : numObstructions = 0)
    (htrust : ReviewTrust.solver ≤ trust) :
    reviewSeverity trust numObstructions = .pass := by
  subst hobs
  have : trust.toNat ≥ 5 := htrust
  cases trust <;> simp_all [reviewSeverity, ReviewTrust.toNat]

/-- Obstructions always produce CRITICAL severity. -/
theorem obstructions_critical (trust : ReviewTrust) (n : Nat) (hn : n > 0) :
    reviewSeverity trust n = .critical := by
  simp [reviewSeverity, hn]

-- ════════════════════════════════════════════════════════════════════
-- § 5  Review Pipeline
-- ════════════════════════════════════════════════════════════════════

/-- Input: a changed coordinate with its trust assessment and obstructions. -/
structure DiffEntry where
  coord        : ReviewCoord
  trust        : ReviewTrust
  obstructions : Nat
  deriving Repr

/-- The canonical review pipeline: produce one finding per diff entry. -/
def extractFindings : List DiffEntry → List ReviewFinding
  | [] => []
  | e :: rest =>
    { coord := e.coord,
      trust := e.trust,
      obstructions := e.obstructions,
      severity := reviewSeverity e.trust e.obstructions }
    :: extractFindings rest

@[simp] theorem extractFindings_nil : extractFindings [] = [] := rfl

/-- **Review Completeness** (Theorem 3.1): every changed coordinate
    in the diff receives a review assessment. -/
theorem review_completeness (entries : List DiffEntry) :
    (extractFindings entries).length = entries.length := by
  induction entries with
  | nil => rfl
  | cons _ _ ih => simp [extractFindings, ih]

/-- Findings preserve the coordinate order from the diff. -/
theorem finding_coord_match (entries : List DiffEntry) (i : Nat)
    (hi : i < entries.length) :
    (extractFindings entries)[i]?.map ReviewFinding.coord =
      entries[i]?.map DiffEntry.coord := by
  induction entries generalizing i with
  | nil => simp at hi
  | cons e rest ih =>
    cases i with
    | zero => simp [extractFindings]
    | succ j =>
      simp [extractFindings]
      exact ih j (by simp [List.length] at hi; omega)

-- ════════════════════════════════════════════════════════════════════
-- § 6  Severity Monotonicity
-- ════════════════════════════════════════════════════════════════════

/-- Lower trust (with zero obstructions) produces at least as high severity. -/
theorem severity_monotone (t1 t2 : ReviewTrust)
    (hle : t1.toNat ≤ t2.toNat) :
    (reviewSeverity t2 0).toNat ≤ (reviewSeverity t1 0).toNat := by
  cases t1 <;> cases t2 <;>
    simp [reviewSeverity, ReviewTrust.toNat, Severity.toNat] at *
  all_goals omega

-- ════════════════════════════════════════════════════════════════════
-- § 7  Comment Generation
-- ════════════════════════════════════════════════════════════════════

/-- A review comment generated from a finding. -/
structure ReviewComment where
  location  : String
  severity  : Severity
  message   : String
  deriving Repr

/-- Generate a human-readable comment from a finding. -/
def generateComment (f : ReviewFinding) : ReviewComment :=
  { location := f.coord.file ++ ":" ++ toString f.coord.line,
    severity := f.severity,
    message := match f.severity with
      | .pass     => "Verified"
      | .info     => "Runtime-verified, consider formal proof"
      | .warning  => "Low trust, needs stronger evidence"
      | .critical => "Critical: obstructions detected" }

/-- Generated comments preserve severity from findings. -/
theorem comment_severity_preserved (f : ReviewFinding) :
    (generateComment f).severity = f.severity := by
  simp [generateComment]

-- ════════════════════════════════════════════════════════════════════
-- § 8  Summary
-- ════════════════════════════════════════════════════════════════════

theorem codeReviewSoundness :
    -- (a) Completeness: every diff entry gets a finding
    (∀ entries, (extractFindings entries).length = entries.length) ∧
    -- (b) Zero false positives on correct code
    (∀ trust, ReviewTrust.solver ≤ trust →
      reviewSeverity trust 0 = .pass) ∧
    -- (c) Obstructions always critical
    (∀ trust n, n > 0 → reviewSeverity trust n = .critical) := by
  refine ⟨review_completeness, ?_, obstructions_critical⟩
  intro trust htrust
  exact zero_false_positives trust rfl htrust

end JudgmentGeometry.CodeReviewAutomation
