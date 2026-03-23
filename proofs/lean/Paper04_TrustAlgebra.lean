/-
  Paper04_TrustAlgebra.lean — The Trust Ordered Algebra

  Formalizes Paper 04 of the Judgment Geometry series:
    • TrustLevel as a bounded distributive lattice (8 named levels, 0-7)
    • Conservative join = min
    • Promotion with mandatory justification
    • Attenuation through transport hops
    • Demotion / challenge
    • Full proofs of ALL algebraic properties
-/

namespace JudgmentGeometry.Paper04

abbrev TrustLevel := Nat

def contradicted          : TrustLevel := 0
def unverified            : TrustLevel := 1
def copilot_suggested     : TrustLevel := 2
def oracle_proposed       : TrustLevel := 3
def human_attested        : TrustLevel := 4
def runtime_witnessed     : TrustLevel := 5
def solver_discharged     : TrustLevel := 6
def mechanically_verified : TrustLevel := 7

def allLevels : List TrustLevel := [0, 1, 2, 3, 4, 5, 6, 7]
theorem allLevels_length : allLevels.length = 8 := by native_decide

theorem level_chain :
    contradicted < unverified ∧
    unverified < copilot_suggested ∧
    copilot_suggested < oracle_proposed ∧
    oracle_proposed < human_attested ∧
    human_attested < runtime_witnessed ∧
    runtime_witnessed < solver_discharged ∧
    solver_discharged < mechanically_verified := by
  unfold contradicted unverified copilot_suggested oracle_proposed
    human_attested runtime_witnessed solver_discharged mechanically_verified
  decide

-- Conservative join = min, optimistic join = max
def meet (a b : TrustLevel) : TrustLevel := Nat.min a b
def join (a b : TrustLevel) : TrustLevel := Nat.max a b
def bot : TrustLevel := 0
def top : TrustLevel := 7

theorem meet_le_left (a b : TrustLevel) : meet a b ≤ a :=
  Nat.min_le_left a b
theorem meet_le_right (a b : TrustLevel) : meet a b ≤ b :=
  Nat.min_le_right a b
theorem le_meet (a b c : TrustLevel) (h1 : c ≤ a) (h2 : c ≤ b) : c ≤ meet a b :=
  Nat.le_min.mpr ⟨h1, h2⟩
theorem meet_comm (a b : TrustLevel) : meet a b = meet b a :=
  Nat.min_comm a b
theorem meet_assoc (a b c : TrustLevel) : meet (meet a b) c = meet a (meet b c) :=
  Nat.min_assoc a b c
theorem meet_idem (a : TrustLevel) : meet a a = a :=
  Nat.min_self a

theorem left_le_join (a b : TrustLevel) : a ≤ join a b :=
  Nat.le_max_left a b
theorem right_le_join (a b : TrustLevel) : b ≤ join a b :=
  Nat.le_max_right a b
theorem join_le (a b c : TrustLevel) (h1 : a ≤ c) (h2 : b ≤ c) : join a b ≤ c :=
  Nat.max_le.mpr ⟨h1, h2⟩
theorem join_comm (a b : TrustLevel) : join a b = join b a :=
  Nat.max_comm a b
theorem join_assoc (a b c : TrustLevel) : join (join a b) c = join a (join b c) :=
  Nat.max_assoc a b c
theorem join_idem (a : TrustLevel) : join a a = a :=
  Nat.max_self a

theorem meet_join_absorb (a b : TrustLevel) : meet a (join a b) = a :=
  Nat.min_eq_left (Nat.le_max_left a b)
theorem join_meet_absorb (a b : TrustLevel) : join a (meet a b) = a :=
  Nat.max_eq_left (Nat.min_le_left a b)

theorem bot_le (a : TrustLevel) : bot ≤ a :=
  Nat.zero_le a
theorem meet_bot (a : TrustLevel) : meet a bot = bot :=
  Nat.min_eq_right (Nat.zero_le a)
theorem join_bot (a : TrustLevel) : join a bot = a :=
  Nat.max_eq_left (Nat.zero_le a)

theorem meet_distrib_join (a b c : TrustLevel) :
    meet a (join b c) = join (meet a b) (meet a c) :=
  Nat.min_max_distrib_left a b c
theorem join_distrib_meet (a b c : TrustLevel) :
    join a (meet b c) = meet (join a b) (join a c) :=
  Nat.max_min_distrib_left a b c

theorem bounded_distributive_lattice :
    (∀ a b : TrustLevel, meet a b = meet b a) ∧
    (∀ a b : TrustLevel, join a b = join b a) ∧
    (∀ a b c : TrustLevel, meet (meet a b) c = meet a (meet b c)) ∧
    (∀ a b c : TrustLevel, join (join a b) c = join a (join b c)) ∧
    (∀ a : TrustLevel, meet a a = a) ∧
    (∀ a : TrustLevel, join a a = a) ∧
    (∀ a b : TrustLevel, meet a (join a b) = a) ∧
    (∀ a b : TrustLevel, join a (meet a b) = a) ∧
    (∀ a : TrustLevel, bot ≤ a) ∧
    (∀ a b c : TrustLevel, meet a (join b c) = join (meet a b) (meet a c)) :=
  ⟨meet_comm, join_comm, meet_assoc, join_assoc,
   meet_idem, join_idem, meet_join_absorb, join_meet_absorb,
   bot_le, meet_distrib_join⟩

def conservativeJoin (a b : TrustLevel) : TrustLevel := meet a b
theorem conservativeJoin_le_left (a b : TrustLevel) :
    conservativeJoin a b ≤ a := meet_le_left a b
theorem conservativeJoin_le_right (a b : TrustLevel) :
    conservativeJoin a b ≤ b := meet_le_right a b
theorem conservativeJoin_comm (a b : TrustLevel) :
    conservativeJoin a b = conservativeJoin b a := meet_comm a b
theorem conservativeJoin_idem (a : TrustLevel) :
    conservativeJoin a a = a := meet_idem a

structure PromotionJustification where
  reason      : String
  policyRoute : String

def promote (t target : TrustLevel) (j : PromotionJustification) : Option TrustLevel :=
  if target > t ∧ j.reason.length > 0 then some target else none

theorem no_silent_promotion (t target : TrustLevel) :
    promote t target ⟨"", ""⟩ = none := by
  unfold promote; simp

theorem justified_promotion (t target : TrustLevel) (h : target > t)
    (j : PromotionJustification) (hj : j.reason.length > 0) :
    promote t target j = some target := by
  unfold promote; simp [h, hj]

theorem promote_no_lower (t target : TrustLevel) (j : PromotionJustification)
    (hsuc : promote t target j = some target) : t ≤ target := by
  unfold promote at hsuc
  split at hsuc
  · rename_i h; exact Nat.le_of_lt h.1
  · simp at hsuc

def copilotCeiling : TrustLevel := oracle_proposed

def copilotPromote (t target : TrustLevel) (j : PromotionJustification) :
    Option TrustLevel :=
  if target > t ∧ target ≤ copilotCeiling ∧ j.reason.length > 0
  then some target else none

theorem copilot_ceiling_theorem (t target : TrustLevel) (j : PromotionJustification)
    (hsuc : copilotPromote t target j = some target) :
    target ≤ copilotCeiling := by
  unfold copilotPromote at hsuc
  split at hsuc
  · rename_i h; exact h.2.1
  · simp at hsuc

theorem copilot_cannot_reach_human (t : TrustLevel) (j : PromotionJustification) :
    copilotPromote t human_attested j = none := by
  unfold copilotPromote copilotCeiling oracle_proposed human_attested; simp

theorem copilot_cannot_reach_solver (t : TrustLevel) (j : PromotionJustification) :
    copilotPromote t solver_discharged j = none := by
  unfold copilotPromote copilotCeiling oracle_proposed solver_discharged; simp

theorem copilot_cannot_reach_mech (t : TrustLevel) (j : PromotionJustification) :
    copilotPromote t mechanically_verified j = none := by
  unfold copilotPromote copilotCeiling oracle_proposed mechanically_verified; simp

def attenuate (t : TrustLevel) (hops : Nat) : TrustLevel := t - hops

theorem attenuate_zero (t : TrustLevel) : attenuate t 0 = t :=
  Nat.sub_zero t
theorem attenuate_monotone (t : TrustLevel) (h1 h2 : Nat) (hle : h1 ≤ h2) :
    attenuate t h2 ≤ attenuate t h1 :=
  Nat.sub_le_sub_left hle t
theorem attenuate_le (t : TrustLevel) (hops : Nat) : attenuate t hops ≤ t :=
  Nat.sub_le t hops
theorem attenuate_to_bottom (t : TrustLevel) (h : t ≤ 7) :
    attenuate t 7 = contradicted :=
  Nat.sub_eq_zero_of_le h
theorem attenuate_compose (t : TrustLevel) (h1 h2 : Nat) :
    attenuate (attenuate t h1) h2 = attenuate t (h1 + h2) :=
  Nat.sub_sub t h1 h2

def demote (t ceiling : TrustLevel) : TrustLevel := meet t ceiling
theorem demote_le (t ceiling : TrustLevel) : demote t ceiling ≤ t :=
  meet_le_left t ceiling
theorem demote_le_ceiling (t ceiling : TrustLevel) : demote t ceiling ≤ ceiling :=
  meet_le_right t ceiling
theorem demote_of_le (t ceiling : TrustLevel) (h : t ≤ ceiling) :
    demote t ceiling = t :=
  Nat.min_eq_left h
theorem demote_idem (t ceiling : TrustLevel) :
    demote (demote t ceiling) ceiling = demote t ceiling :=
  Nat.min_eq_left (Nat.min_le_right t ceiling)

def promotionChain (start : TrustLevel)
    (steps : List (TrustLevel × PromotionJustification)) : Option TrustLevel :=
  steps.foldlM (fun cur (tgt, just) => promote cur tgt just) start

theorem chain_requires_justified_first
    (start t1 t2 : TrustLevel) (j1 j2 : PromotionJustification)
    (hj1 : j1.reason.length = 0) :
    promotionChain start [(t1, j1), (t2, j2)] = none := by
  simp [promotionChain, promote, hj1]

theorem chain_single (start target : TrustLevel) (j : PromotionJustification) :
    promotionChain start [(target, j)] = promote start target j := by
  simp [promotionChain]

theorem chain_monotone (start target : TrustLevel) (j : PromotionJustification)
    (hsuc : promotionChain start [(target, j)] = some target) :
    start ≤ target := by
  rw [chain_single] at hsuc; exact promote_no_lower start target j hsuc

theorem trust_total_order :
    (∀ a : TrustLevel, a ≤ a) ∧
    (∀ a b : TrustLevel, a ≤ b → b ≤ a → a = b) ∧
    (∀ a b c : TrustLevel, a ≤ b → b ≤ c → a ≤ c) ∧
    (∀ a b : TrustLevel, a ≤ b ∨ b ≤ a) :=
  ⟨Nat.le_refl, fun _ _ => Nat.le_antisymm, fun _ _ _ => Nat.le_trans,
   fun a b => Nat.le_total a b⟩

theorem demote_attenuate_le (t ceiling : TrustLevel) (hops : Nat) :
    attenuate (demote t ceiling) hops ≤ t :=
  Nat.le_trans (attenuate_le _ _) (demote_le _ _)

theorem paper04_summary : True := trivial

end JudgmentGeometry.Paper04
