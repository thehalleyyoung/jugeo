/-
  Paper49_CyclicMaturity.lean — Cyclic Maturity: Self-Improving Verification
  Through Feedback Loops

  Formalizes Paper 49 of the Judgment Geometry series:
    • MaturityLevel: a five-element totally ordered type (Prototype … Mature)
    • CyclePhase: a four-phase improvement cycle with a cyclic transition
    • CycleRecord: evidence for one complete orbit
    • CapabilityExpander: monotone capability-set growth
    • advanceLevel: a function that never decreases the level
    • monoMaturity: the main theorem — the level sequence is non-decreasing
    • Corollaries: bounded run length, steady-state convergence, global
      monotonicity in a federation

  All theorems are proved without sorry.
-/

namespace JudgmentGeometry.CyclicMaturity

-- ════════════════════════════════════════════════════════════════════
-- § 1  MaturityLevel
-- ════════════════════════════════════════════════════════════════════

/-- The five maturity levels, totally ordered Prototype < Operational <
    Federated < SelfImproving < Mature. -/
inductive MaturityLevel where
  | prototype     : MaturityLevel
  | operational   : MaturityLevel
  | federated     : MaturityLevel
  | selfImproving : MaturityLevel
  | mature        : MaturityLevel
  deriving DecidableEq, Repr, Inhabited

/-- Encode MaturityLevel as a natural number 0–4. -/
def MaturityLevel.toNat : MaturityLevel → Nat
  | .prototype     => 0
  | .operational   => 1
  | .federated     => 2
  | .selfImproving => 3
  | .mature        => 4

/-- The ordering on MaturityLevel is the natural number ordering on codes. -/
instance : LE MaturityLevel where
  le a b := a.toNat ≤ b.toNat

instance : LT MaturityLevel where
  lt a b := a.toNat < b.toNat

/-- Decidability of ≤ on MaturityLevel. -/
instance (a b : MaturityLevel) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

/-- Decidability of < on MaturityLevel. -/
instance (a b : MaturityLevel) : Decidable (a < b) :=
  inferInstanceAs (Decidable (a.toNat < b.toNat))

/-- The chain: prototype < operational < federated < selfImproving < mature. -/
theorem maturityLevel_chain :
    MaturityLevel.prototype     < MaturityLevel.operational   ∧
    MaturityLevel.operational   < MaturityLevel.federated     ∧
    MaturityLevel.federated     < MaturityLevel.selfImproving ∧
    MaturityLevel.selfImproving < MaturityLevel.mature := by
  decide

/-- ≤ is reflexive on MaturityLevel. -/
theorem maturityLevel_le_refl (a : MaturityLevel) : a ≤ a :=
  Nat.le_refl _

/-- ≤ is transitive on MaturityLevel. -/
theorem maturityLevel_le_trans {a b c : MaturityLevel}
    (hab : a ≤ b) (hbc : b ≤ c) : a ≤ c :=
  Nat.le_trans hab hbc

/-- mature is the maximum element. -/
theorem maturityLevel_le_mature (a : MaturityLevel) : a ≤ .mature := by
  cases a <;> decide

-- ════════════════════════════════════════════════════════════════════
-- § 2  CyclePhase
-- ════════════════════════════════════════════════════════════════════

/-- The four phases of one improvement cycle, executed in order. -/
inductive CyclePhase where
  | verify    : CyclePhase   -- phase 0: run current doctrine
  | analyze   : CyclePhase   -- phase 1: identify gaps
  | extend    : CyclePhase   -- phase 2: expand capabilities
  | reVerify  : CyclePhase   -- phase 3: re-run with updated doctrine
  deriving DecidableEq, Repr, Inhabited

/-- Cyclic successor: the next phase in the improvement cycle. -/
def CyclePhase.next : CyclePhase → CyclePhase
  | .verify   => .analyze
  | .analyze  => .extend
  | .extend   => .reVerify
  | .reVerify => .verify

/-- Index of a phase (0–3). -/
def CyclePhase.toNat : CyclePhase → Nat
  | .verify   => 0
  | .analyze  => 1
  | .extend   => 2
  | .reVerify => 3

/-- After four steps, next returns to the original phase. -/
theorem cyclePhase_period (p : CyclePhase) :
    p.next.next.next.next = p := by
  cases p <;> rfl

/-- Every phase has a unique successor. -/
theorem cyclePhase_next_injective {p q : CyclePhase}
    (h : p.next = q.next) : p = q := by
  cases p <;> cases q <;> simp_all [CyclePhase.next]

-- ════════════════════════════════════════════════════════════════════
-- § 3  CycleRecord
-- ════════════════════════════════════════════════════════════════════

/-- A CycleRecord captures the level before and after one improvement cycle. -/
structure CycleRecord where
  levelBefore : MaturityLevel
  levelAfter  : MaturityLevel
  /-- Structural monotonicity: each cycle does not decrease the level. -/
  mono        : levelBefore ≤ levelAfter
  deriving Repr

/-- A trivial record where the level does not change (maintenance cycle). -/
def CycleRecord.maintenance (ℓ : MaturityLevel) : CycleRecord :=
  { levelBefore := ℓ
    levelAfter  := ℓ
    mono        := maturityLevel_le_refl ℓ }

/-- An advancement record where the level increases by one step. -/
def CycleRecord.advance (ℓ nextℓ : MaturityLevel)
    (h : ℓ ≤ nextℓ) : CycleRecord :=
  { levelBefore := ℓ
    levelAfter  := nextℓ
    mono        := h }

-- ════════════════════════════════════════════════════════════════════
-- § 4  advanceLevel
-- ════════════════════════════════════════════════════════════════════

/-- Attempt to advance one level.  Returns the next level if advancement
    criteria are met (modelled here by a Boolean predicate), otherwise
    returns the current level unchanged. -/
def advanceLevel (ℓ : MaturityLevel) (criteriamet : Bool) : MaturityLevel :=
  if criteriamet then
    match ℓ with
    | .prototype     => .operational
    | .operational   => .federated
    | .federated     => .selfImproving
    | .selfImproving => .mature
    | .mature        => .mature   -- already at maximum; no regression
  else
    ℓ                              -- criteria not met; level unchanged

/-- advanceLevel never decreases the level. -/
theorem advanceLevel_ge (ℓ : MaturityLevel) (b : Bool) :
    ℓ ≤ advanceLevel ℓ b := by
  unfold advanceLevel
  split
  · cases ℓ <;> decide
  · exact maturityLevel_le_refl ℓ

/-- advanceLevel at mature always returns mature. -/
theorem advanceLevel_mature (b : Bool) :
    advanceLevel .mature b = .mature := by
  unfold advanceLevel
  split
  · rfl
  · rfl

/-- If criteria are not met, advanceLevel is the identity. -/
theorem advanceLevel_false (ℓ : MaturityLevel) :
    advanceLevel ℓ false = ℓ := by
  unfold advanceLevel; rfl

/-- If criteria are met at a non-maximal level, the level strictly increases. -/
theorem advanceLevel_true_lt (ℓ : MaturityLevel) (h : ℓ ≠ .mature) :
    ℓ < advanceLevel ℓ true := by
  unfold advanceLevel
  simp only [ite_true]
  cases ℓ with
  | prototype     => decide
  | operational   => decide
  | federated     => decide
  | selfImproving => decide
  | mature        => exact absurd rfl h

-- ════════════════════════════════════════════════════════════════════
-- § 5  CyclicSystem
-- ════════════════════════════════════════════════════════════════════

/-- A CyclicSystem is a model of a JuGeo maturity system:
    an initial level and a sequence of Boolean criteria (one per cycle). -/
structure CyclicSystem where
  init     : MaturityLevel
  criteria : Nat → Bool        -- criteria(i) = true iff cycle i advances

/-- The level of the system after n cycles. -/
def CyclicSystem.levelAt (sys : CyclicSystem) : Nat → MaturityLevel
  | 0     => sys.init
  | n + 1 => advanceLevel (sys.levelAt n) (sys.criteria n)

-- ════════════════════════════════════════════════════════════════════
-- § 6  Monotonic Maturity Theorem
-- ════════════════════════════════════════════════════════════════════

/-- Main theorem: the level sequence of any CyclicSystem is non-decreasing. -/
theorem monoMaturity (sys : CyclicSystem) (i : Nat) :
    sys.levelAt i ≤ sys.levelAt (i + 1) := by
  simp only [CyclicSystem.levelAt]
  exact advanceLevel_ge (sys.levelAt i) (sys.criteria i)

/-- The level is non-decreasing over any interval [i, j] with i ≤ j. -/
theorem monoMaturity_interval (sys : CyclicSystem) (i j : Nat) (h : i ≤ j) :
    sys.levelAt i ≤ sys.levelAt j := by
  induction h with
  | refl      => exact maturityLevel_le_refl _
  | step hle ih => exact maturityLevel_le_trans ih (monoMaturity sys _)

-- ════════════════════════════════════════════════════════════════════
-- § 7  Corollaries
-- ════════════════════════════════════════════════════════════════════

/-- Corollary: a level once reached is never lost. -/
theorem levelNotLost (sys : CyclicSystem) (ℓ : MaturityLevel) (i j : Nat)
    (hle : i ≤ j) (hreach : ℓ ≤ sys.levelAt i) :
    ℓ ≤ sys.levelAt j :=
  maturityLevel_le_trans hreach (monoMaturity_interval sys i j hle)

/-- Corollary: the system is eventually at most 4 advances away from mature,
    starting from any level. -/
theorem maxAdvancesToMature (ℓ : MaturityLevel) :
    (MaturityLevel.mature.toNat - ℓ.toNat) ≤ 4 := by
  cases ℓ <;> decide

/-- Corollary: if the system reaches mature at cycle i, it stays there. -/
theorem staysMature (sys : CyclicSystem) (i j : Nat) (hle : i ≤ j)
    (h : sys.levelAt i = .mature) :
    sys.levelAt j = .mature := by
  have hm : sys.levelAt i ≤ sys.levelAt j :=
    monoMaturity_interval sys i j hle
  rw [h] at hm
  have hbound := maturityLevel_le_mature (sys.levelAt j)
  have heq : (sys.levelAt j).toNat = 4 := Nat.le_antisymm hbound hm
  revert heq
  cases sys.levelAt j <;> simp [MaturityLevel.toNat]

/-- Corollary: steady-state convergence — once mature, always mature.
    Specialisation of staysMature to the case j = i + 1. -/
theorem steadyStateMature (sys : CyclicSystem) (i : Nat)
    (h : sys.levelAt i = .mature) :
    sys.levelAt (i + 1) = .mature :=
  staysMature sys i (i + 1) (Nat.le_succ i) h

-- ════════════════════════════════════════════════════════════════════
-- § 8  CapabilityExpander Monotonicity
-- ════════════════════════════════════════════════════════════════════

/-- A capability set is represented as a finite list of names. -/
abbrev CapabilitySet := List String

/-- Add a capability; idempotent if already present. -/
def expandCapability (caps : CapabilitySet) (name : String) : CapabilitySet :=
  if name ∈ caps then caps else caps ++ [name]

/-- expandCapability is monotone: the original set is a sub-list of the result. -/
theorem expandCapability_subset (caps : CapabilitySet) (name : String) :
    ∀ c ∈ caps, c ∈ expandCapability caps name := by
  intro c hc
  unfold expandCapability
  split
  · exact hc
  · exact List.mem_append_left _ hc

/-- expandCapability never shrinks the capability set (size is non-decreasing). -/
theorem expandCapability_length_ge (caps : CapabilitySet) (name : String) :
    caps.length ≤ (expandCapability caps name).length := by
  unfold expandCapability
  split
  · exact Nat.le_refl _
  · simp [List.length_append]

/-- After k expansions, the set is at least as large as the initial set. -/
theorem expandMany_length_ge (caps₀ : CapabilitySet) (names : List String) :
    caps₀.length ≤ (names.foldl expandCapability caps₀).length := by
  induction names generalizing caps₀ with
  | nil       => exact Nat.le_refl _
  | cons n ns ih =>
    simp only [List.foldl_cons]
    exact Nat.le_trans (expandCapability_length_ge caps₀ n)
                       (ih (expandCapability caps₀ n))

-- ════════════════════════════════════════════════════════════════════
-- § 9  Federation Monotonicity
-- ════════════════════════════════════════════════════════════════════

/-- A federated system is a finite collection of CyclicSystems. -/
abbrev Federation := List CyclicSystem

/-- Recursive minimum of mapped values, default for empty. -/
def mapMin (f : CyclicSystem → Nat) (default : Nat) : List CyclicSystem → Nat
  | []      => default
  | x :: xs => Nat.min (f x) (mapMin f (f x) xs)

/-- The global minimum level across all nodes. -/
def Federation.minLevel (fed : Federation) (i : Nat) : Nat :=
  mapMin (fun sys => sys.levelAt i |>.toNat) 4 fed

/-- Helper: the minimum is ≤ each element in the list. -/
theorem mapMin_le_elem (f : CyclicSystem → Nat) (d : Nat)
    (l : List CyclicSystem) (sys : CyclicSystem) (hs : sys ∈ l) :
    mapMin f d l ≤ f sys := by
  induction l generalizing d with
  | nil => exact absurd hs (List.not_mem_nil _)
  | cons hd tl ih =>
    simp only [mapMin]
    rcases List.mem_cons.mp hs with rfl | h
    · exact Nat.min_le_left _ _
    · exact Nat.le_trans (Nat.min_le_right _ _) (ih (f hd) h)

private theorem nat_min_le_min {a b c d : Nat} (h1 : a ≤ c) (h2 : b ≤ d) :
    Nat.min a b ≤ Nat.min c d := by
  simp only [Nat.min_def]; split <;> split <;> omega

/-- mapMin is monotone: if f ≤ g pointwise then mapMin f ≤ mapMin g. -/
theorem mapMin_mono (f g : CyclicSystem → Nat) (d₁ d₂ : Nat)
    (hd : d₁ ≤ d₂) (hfg : ∀ sys, f sys ≤ g sys)
    (l : List CyclicSystem) :
    mapMin f d₁ l ≤ mapMin g d₂ l := by
  induction l generalizing d₁ d₂ with
  | nil => exact hd
  | cons hd_sys tl ih =>
    simp only [mapMin]
    exact nat_min_le_min (hfg hd_sys) (ih (f hd_sys) (g hd_sys) (hfg hd_sys))

/-- Global monotonicity: the minimum level across all federation nodes
    is non-decreasing. -/
theorem federation_minLevel_mono (fed : Federation) (i : Nat) :
    fed.minLevel i ≤ fed.minLevel (i + 1) := by
  unfold Federation.minLevel
  exact mapMin_mono _ _ 4 4 (Nat.le_refl _)
    (fun sys => monoMaturity sys i) fed

-- ════════════════════════════════════════════════════════════════════
-- § 10  TheoremStatus encoding
-- ════════════════════════════════════════════════════════════════════

/-- The proof-lifecycle status of a maturity theorem, mirroring the
    Python TheoremStatus enum. -/
inductive TheoremStatus where
  | conjecture    : TheoremStatus
  | partialProof  : TheoremStatus
  | proved        : TheoremStatus
  | refuted       : TheoremStatus
  | vacuous       : TheoremStatus
  deriving DecidableEq, Repr, Inhabited

/-- A theorem record, mirroring MaturityTheorem. -/
structure MaturityTheoremRecord where
  name   : String
  status : TheoremStatus
  deriving Repr

/-- Only proved or vacuous theorems may be used as dependencies. -/
def TheoremStatus.isDependable : TheoremStatus → Bool
  | .proved  => true
  | .vacuous => true
  | _        => false

theorem proved_isDependable :
    TheoremStatus.proved.isDependable = true := rfl

theorem conjecture_not_dependable :
    TheoremStatus.conjecture.isDependable = false := rfl

-- ════════════════════════════════════════════════════════════════════
-- § 11  ProofRecord
-- ════════════════════════════════════════════════════════════════════

/-- A proof record captures the outcome of executing a proof step.
    Mirrors the Python ProofRecord dataclass. -/
structure ProofRecord where
  theoremName  : String
  status       : TheoremStatus
  durationMs   : Float
  provedLevel  : MaturityLevel    -- the level at which the proof was produced
  deriving Repr

/-- A ProofRecord is valid if its status is proved or vacuous. -/
def ProofRecord.isValid (pr : ProofRecord) : Bool :=
  pr.status.isDependable

/-- A valid proof record at level ℓ contributes to the maturity score. -/
theorem validProof_contributes (pr : ProofRecord) (h : pr.isValid = true) :
    pr.status = .proved ∨ pr.status = .vacuous := by
  simp only [ProofRecord.isValid, TheoremStatus.isDependable] at h
  cases hs : pr.status <;> simp_all

-- ════════════════════════════════════════════════════════════════════
-- § 12  Summary
-- ════════════════════════════════════════════════════════════════════

/-- Summary: the level sequence produced by any CyclicSystem is non-decreasing,
    capability expansion is monotone, and federation preserves monotonicity.
    All lemmas are proved without sorry. -/
theorem paper49_main_results :
    -- (1) Monotonic maturity
    (∀ (sys : CyclicSystem) (i : Nat), sys.levelAt i ≤ sys.levelAt (i + 1)) ∧
    -- (2) Level never lost across an interval
    (∀ (sys : CyclicSystem) (i j : Nat), i ≤ j →
        sys.levelAt i ≤ sys.levelAt j) ∧
    -- (3) Capability expansion is monotone
    (∀ (caps : CapabilitySet) (name : String),
        caps.length ≤ (expandCapability caps name).length) ∧
    -- (4) Federation minimum level is non-decreasing
    (∀ (fed : Federation) (i : Nat),
        fed.minLevel i ≤ fed.minLevel (i + 1)) := by
  exact ⟨monoMaturity, monoMaturity_interval, expandCapability_length_ge,
         federation_minLevel_mono⟩

end JudgmentGeometry.CyclicMaturity
