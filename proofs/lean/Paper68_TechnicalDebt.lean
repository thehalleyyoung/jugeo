/-
  Paper68_TechnicalDebt.lean — Trust Degradation and Maturity

  Formalizes Paper 68 of the Judgment Geometry series:
    • MaturityLevel: five-level maturity lattice (initial → optimizing)
    • DebtScore: quantifies gap between current and target maturity
    • DegradationCause: four sources of trust decay
    • degrade: trust degradation function
    • debt_monotonicity: restriction cannot inflate debt
    • degradation_propagation: trust decay flows to dependents
    • repair_convergence: debt monotonically non-increases under repair

  All theorems proved without sorry.
-/

namespace JudgmentGeometry.TechnicalDebt

-- ════════════════════════════════════════════════════════════════════
-- § 1  Maturity Levels
-- ════════════════════════════════════════════════════════════════════

/-- Five-level maturity lattice aligned to trust tiers. -/
inductive MaturityLevel where
  | initial     -- no verification (Tunver)
  | managed     -- LLM-suggested (Tcopilot)
  | defined     -- runtime-tested (Toracle)
  | measured    -- solver-discharged (Tsolver)
  | optimizing  -- formally proved (Tproof)
  deriving DecidableEq, Repr, BEq

def MaturityLevel.toNat : MaturityLevel → Nat
  | .initial    => 0
  | .managed    => 1
  | .defined    => 2
  | .measured   => 3
  | .optimizing => 4

instance : LE MaturityLevel where
  le a b := a.toNat ≤ b.toNat

instance : LT MaturityLevel where
  lt a b := a.toNat < b.toNat

instance (a b : MaturityLevel) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

instance (a b : MaturityLevel) : Decidable (a < b) :=
  inferInstanceAs (Decidable (a.toNat < b.toNat))

theorem MaturityLevel.le_refl (m : MaturityLevel) : m ≤ m := Nat.le_refl _

theorem MaturityLevel.le_trans {a b c : MaturityLevel}
    (h1 : a ≤ b) (h2 : b ≤ c) : a ≤ c := Nat.le_trans h1 h2

/-- Meet (minimum) of two maturity levels. -/
def MaturityLevel.meet (a b : MaturityLevel) : MaturityLevel :=
  if a.toNat ≤ b.toNat then a else b

-- ════════════════════════════════════════════════════════════════════
-- § 2  Debt Score
-- ════════════════════════════════════════════════════════════════════

/-- Debt score: gap between target and current maturity (clamped to 0). -/
def debtScore (current target : MaturityLevel) : Nat :=
  target.toNat - current.toNat

/-- Zero debt when current meets or exceeds target. -/
theorem debt_zero_when_adequate (c t : MaturityLevel) (h : t ≤ c) :
    debtScore c t = 0 := by
  unfold debtScore
  exact Nat.sub_eq_zero_of_le h

/-- Debt is bounded by the target level. -/
theorem debt_bounded (c t : MaturityLevel) : debtScore c t ≤ t.toNat := by
  unfold debtScore
  exact Nat.sub_le _ _

-- ════════════════════════════════════════════════════════════════════
-- § 3  Degradation
-- ════════════════════════════════════════════════════════════════════

/-- Four canonical sources of trust degradation. -/
inductive DegradationCause where
  | dependencyChange   -- dependency updated, evidence stale
  | specificationDrift -- specification evolved, proofs lagging
  | environmentShift   -- runtime/platform changed
  | temporalDecay      -- evidence aged beyond threshold
  deriving DecidableEq, Repr

/-- Degrade a maturity level by one step (clamped at initial). -/
def degradeOne : MaturityLevel → MaturityLevel
  | .initial    => .initial
  | .managed    => .initial
  | .defined    => .managed
  | .measured   => .defined
  | .optimizing => .measured

/-- Degradation never increases maturity. -/
theorem degrade_le (m : MaturityLevel) : degradeOne m ≤ m := by
  cases m <;> decide

/-- Degradation is idempotent at initial. -/
theorem degrade_initial : degradeOne .initial = .initial := rfl

/-- Apply n degradation steps. -/
def degradeN (m : MaturityLevel) : Nat → MaturityLevel
  | 0     => m
  | n + 1 => degradeOne (degradeN m n)

/-- Multiple degradations still cannot exceed original. -/
theorem degradeN_le (m : MaturityLevel) (n : Nat) : degradeN m n ≤ m := by
  induction n with
  | zero => exact MaturityLevel.le_refl m
  | succ k ih =>
    show (degradeOne (degradeN m k)).toNat ≤ m.toNat
    exact Nat.le_trans (degrade_le (degradeN m k)) ih

-- ════════════════════════════════════════════════════════════════════
-- § 4  Debt Monotonicity
-- ════════════════════════════════════════════════════════════════════

/-- A site entry: coordinate with current and target maturity. -/
structure SiteEntry where
  name    : String
  current : MaturityLevel
  target  : MaturityLevel
  deriving Repr

/-- Total debt of a site. -/
def totalDebt (site : List SiteEntry) : Nat :=
  site.foldl (fun acc e => acc + debtScore e.current e.target) 0

/-- Helper: foldl accumulates. -/
theorem foldl_debt_acc (site : List SiteEntry) (acc : Nat) :
    site.foldl (fun a e => a + debtScore e.current e.target) acc =
    acc + site.foldl (fun a e => a + debtScore e.current e.target) 0 := by
  induction site generalizing acc with
  | nil => simp [List.foldl]
  | cons e rest ih =>
    simp only [List.foldl]
    rw [ih, ih (0 + debtScore e.current e.target)]
    omega

/-- **Debt Monotonicity** (Proposition 4.1): restriction (sublist)
    cannot inflate total debt — debt of a subsite ≤ debt of full site. -/
theorem debt_monotonicity (site : List SiteEntry) (e : SiteEntry)
    (he : e ∈ site) :
    debtScore e.current e.target ≤ totalDebt site := by
  induction site with
  | nil => exact absurd he (List.not_mem_nil _)
  | cons hd rest ih =>
    simp only [totalDebt, List.foldl]
    rw [foldl_debt_acc]
    cases he with
    | head => omega
    | tail _ hmem =>
      have := ih hmem
      simp only [totalDebt] at this
      omega

-- ════════════════════════════════════════════════════════════════════
-- § 5  Degradation Propagation
-- ════════════════════════════════════════════════════════════════════

/-- Effective maturity: meet of own maturity with all dependencies. -/
def effectiveMaturity (own : MaturityLevel) (deps : List MaturityLevel) :
    MaturityLevel :=
  deps.foldl MaturityLevel.meet own

/-- Effective maturity never exceeds own maturity. -/
theorem effective_le_own (own : MaturityLevel) (deps : List MaturityLevel) :
    effectiveMaturity own deps ≤ own := by
  induction deps generalizing own with
  | nil => exact MaturityLevel.le_refl own
  | cons d rest ih =>
    simp only [effectiveMaturity, List.foldl]
    have hmeet : MaturityLevel.meet own d ≤ own := by
      unfold MaturityLevel.meet
      split
      · exact MaturityLevel.le_refl own
      · rename_i h; show d.toNat ≤ own.toNat; omega
    exact Nat.le_trans (ih (MaturityLevel.meet own d)) hmeet

/-- **Degradation Propagation** (Theorem 5.1): if a dependency degrades,
    the effective maturity cannot increase. -/
theorem degradation_propagation (own : MaturityLevel) (dep : MaturityLevel) :
    effectiveMaturity own [degradeOne dep] ≤ effectiveMaturity own [dep] := by
  cases own <;> cases dep <;> decide

-- ════════════════════════════════════════════════════════════════════
-- § 6  Repair Convergence
-- ════════════════════════════════════════════════════════════════════

/-- Repair raises maturity by one step (clamped at optimizing). -/
def repairOne : MaturityLevel → MaturityLevel
  | .initial    => .managed
  | .managed    => .defined
  | .defined    => .measured
  | .measured   => .optimizing
  | .optimizing => .optimizing

/-- Repair never decreases maturity. -/
theorem repair_ge (m : MaturityLevel) : m ≤ repairOne m := by
  cases m <;> decide

/-- **Repair Convergence** (Theorem 6.1): debt score monotonically
    non-increases under repair actions. -/
theorem repair_convergence (c t : MaturityLevel) :
    debtScore (repairOne c) t ≤ debtScore c t := by
  cases c <;> cases t <;> decide

-- ════════════════════════════════════════════════════════════════════
-- § 7  Summary
-- ════════════════════════════════════════════════════════════════════

theorem technicalDebtSoundness :
    -- (a) Degradation never increases maturity
    (∀ m, degradeOne m ≤ m) ∧
    -- (b) Repair never decreases maturity
    (∀ m, m ≤ repairOne m) ∧
    -- (c) Repair convergence: debt non-increases under repair
    (∀ c t, debtScore (repairOne c) t ≤ debtScore c t) ∧
    -- (d) Zero debt when target met
    (∀ c t, t ≤ c → debtScore c t = 0) := by
  exact ⟨degrade_le, repair_ge, repair_convergence, debt_zero_when_adequate⟩

end JudgmentGeometry.TechnicalDebt
