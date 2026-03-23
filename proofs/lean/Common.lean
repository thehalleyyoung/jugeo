/-
  Common.lean — Shared definitions for the Judgment Geometry formalization series.

  Defines the core types used across Papers 00–10:
    • CoordinateKind, Coordinate, MorphismKind, Morphism
    • TrustLevel with decidable total order
    • Judgment (simplified 8-component tuple)
    • Basic utility lemmas
-/

namespace JudgmentGeometry

-- ════════════════════════════════════════════════════════════════════
-- § 1  Coordinate system
-- ════════════════════════════════════════════════════════════════════

inductive CoordinateKind where
  | module | function | interface | test | theorem_ | region
  deriving DecidableEq, Repr, BEq

structure Coordinate where
  name : String
  kind : CoordinateKind
  deriving DecidableEq, Repr, BEq

inductive MorphismKind where
  | restriction | inclusion | transport | refinement
  deriving DecidableEq, Repr, BEq

structure Morphism where
  source : Coordinate
  target : Coordinate
  kind   : MorphismKind
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 2  Trust algebra
-- ════════════════════════════════════════════════════════════════════

inductive TrustLevel where
  | contradicted
  | unverified
  | copilot_suggested
  | oracle_proposed
  | human_attested
  | runtime_witnessed
  | solver_discharged
  | mechanically_verified
  deriving DecidableEq, Repr, BEq

def TrustLevel.toNat : TrustLevel → Nat
  | .contradicted          => 0
  | .unverified            => 1
  | .copilot_suggested     => 2
  | .oracle_proposed       => 3
  | .human_attested        => 4
  | .runtime_witnessed     => 5
  | .solver_discharged     => 6
  | .mechanically_verified => 7

instance : LE TrustLevel where
  le a b := a.toNat ≤ b.toNat

instance : LT TrustLevel where
  lt a b := a.toNat < b.toNat

instance (a b : TrustLevel) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

instance (a b : TrustLevel) : Decidable (a < b) :=
  inferInstanceAs (Decidable (a.toNat < b.toNat))

-- Trust toNat is bounded above by 7
theorem TrustLevel.toNat_le_seven (t : TrustLevel) : t.toNat ≤ 7 := by
  cases t <;> simp [TrustLevel.toNat] <;> omega

-- Reflexivity
theorem TrustLevel.le_refl (t : TrustLevel) : t ≤ t := Nat.le_refl _

-- Transitivity
theorem TrustLevel.le_trans {a b c : TrustLevel} (h1 : a ≤ b) (h2 : b ≤ c) : a ≤ c :=
  Nat.le_trans h1 h2

-- Antisymmetry at Nat level
theorem TrustLevel.toNat_injective {a b : TrustLevel} (h : a.toNat = b.toNat) : a = b := by
  cases a <;> cases b <;> simp [TrustLevel.toNat] at h <;> rfl

-- Conservative meet (minimum)
def TrustLevel.meet (a b : TrustLevel) : TrustLevel :=
  if a.toNat ≤ b.toNat then a else b

-- Join (maximum)
def TrustLevel.join (a b : TrustLevel) : TrustLevel :=
  if a.toNat ≤ b.toNat then b else a

-- Bottom and top elements
def TrustLevel.bottom : TrustLevel := .contradicted
def TrustLevel.top    : TrustLevel := .mechanically_verified

theorem TrustLevel.bottom_le (t : TrustLevel) : TrustLevel.bottom ≤ t := by
  show TrustLevel.bottom.toNat ≤ t.toNat
  cases t <;> simp [TrustLevel.bottom, TrustLevel.toNat]

theorem TrustLevel.le_top (t : TrustLevel) : t ≤ TrustLevel.top := by
  show t.toNat ≤ TrustLevel.top.toNat
  cases t <;> simp [TrustLevel.top, TrustLevel.toNat]

-- ════════════════════════════════════════════════════════════════════
-- § 3  Judgment (simplified 8-component)
-- ════════════════════════════════════════════════════════════════════

inductive PropositionKind where
  | structural | behavioral | relational | resource | semantic
  deriving DecidableEq, Repr

structure Proposition where
  kind    : PropositionKind
  formula : String
  deriving DecidableEq, Repr

inductive EvidenceChannel where
  | solver | runtime | oracle | human | composed
  deriving DecidableEq, Repr

structure EvidenceItem where
  channel : EvidenceChannel
  trust   : TrustLevel
  payload : String
  deriving DecidableEq, Repr

structure Judgment where
  coordinate   : Coordinate
  proposition  : Proposition
  carrier      : String
  evidence     : List EvidenceItem
  obligations  : List String
  obstructions : List String
  trust        : TrustLevel
  provenance   : String
  deriving Repr

-- ════════════════════════════════════════════════════════════════════
-- § 4  Utility lemmas on lists
-- ════════════════════════════════════════════════════════════════════

theorem List.length_filter_le {α : Type} (p : α → Bool) (l : List α) :
    (l.filter p).length ≤ l.length := by
  induction l with
  | nil => simp
  | cons x xs ih =>
    simp only [List.filter]
    cases hp : p x <;> simp [hp, List.length] <;> omega

theorem List.length_filter_lt_of_exists_false {α : Type} [DecidableEq α]
    (p : α → Bool) (l : List α) (x : α) (hx : x ∈ l) (hp : p x = false) :
    (l.filter p).length < l.length := by
  induction l with
  | nil => exact absurd hx (List.not_mem_nil _)
  | cons y ys ih =>
    simp only [List.filter]
    cases hmem : (x == y) with
    | true =>
      have heq : x = y := by simpa [BEq.beq] using hmem
      rw [← heq, hp]; simp [List.length]
      exact Nat.lt_succ_of_le (List.length_filter_le p ys)
    | false =>
      have hne : x ≠ y := by simpa [BEq.beq] using hmem
      have hxys : x ∈ ys := by
        cases hx with
        | head => exact absurd rfl hne
        | tail _ h => exact h
      cases hpy : p y
      · simp [hpy, List.length]
        calc (List.filter p ys).length
            ≤ ys.length := List.length_filter_le p ys
          _ < ys.length + 1 := Nat.lt_succ_of_le (Nat.le_refl _)
      · simp [hpy, List.length]
        have := ih hxys
        omega

-- ════════════════════════════════════════════════════════════════════
-- § 5  No-silent-promotion theorem (trust algebra core invariant)
-- ════════════════════════════════════════════════════════════════════

/-- An explicit justification record for trust promotion. -/
structure PromotionJustification where
  reason : String
  from_level : TrustLevel
  to_level   : TrustLevel
  evidence   : String

/-- Trust promotion is valid only when justified and target > source. -/
def validPromotion (j : PromotionJustification) : Prop :=
  j.from_level < j.to_level ∧ j.reason.length > 0

theorem no_silent_promotion (from_level to_level : TrustLevel) (h : from_level < to_level) :
    ∃ (reason : String), reason.length > 0 → validPromotion ⟨reason, from_level, to_level, ""⟩ := by
  exact ⟨"explicit_justification", fun hr => ⟨h, hr⟩⟩

end JudgmentGeometry
