/-
  Paper42_OracleFederation.lean — Lean 4 companion to Paper 42 of the
  Judgment Geometry series.

  "Oracle Federation: Combining Multiple Verification Backends"

  Formalises:
    · The oracle model (evidence sources with trust ceilings).
    · Ceiling enforcement and its idempotence.
    · The federation protocol (foldl meet over contributing oracles).
    · Backend-kind trust defaults and descriptor invariants.
    · The Federation Soundness Theorem (Theorem 7.1 in the paper):
        for every item in the contributing set, the federated trust
        level is at most that item's trust level — no silent promotion.

  All theorems proved without sorry.
-/

namespace JudgmentGeometry.Paper42

-- ════════════════════════════════════════════════════════════════════════
-- § 1  Trust-level algebra
-- ════════════════════════════════════════════════════════════════════════

/-- Trust levels are represented as natural numbers 0–7, matching the
    eight-tier lattice of the JuGeo trust algebra. -/
abbrev TrustLevel := Nat

namespace Trust

def contradicted        : TrustLevel := 0
def unverified          : TrustLevel := 1
def copilot_suggested   : TrustLevel := 2
def oracle_proposed     : TrustLevel := 3
def human_attested      : TrustLevel := 4
def runtime_witnessed   : TrustLevel := 5
def solver_discharged   : TrustLevel := 6
def mechanically_proven : TrustLevel := 7

/-- Conservative meet: the lower (more sceptical) of two trust levels. -/
def meet (a b : TrustLevel) : TrustLevel := min a b

theorem meet_le_left  (a b : TrustLevel) : meet a b ≤ a := Nat.min_le_left  a b
theorem meet_le_right (a b : TrustLevel) : meet a b ≤ b := Nat.min_le_right a b
theorem meet_comm     (a b : TrustLevel) : meet a b = meet b a := Nat.min_comm a b

theorem meet_assoc (a b c : TrustLevel) :
    meet (meet a b) c = meet a (meet b c) := Nat.min_assoc a b c

theorem meet_idempotent (a : TrustLevel) : meet a a = a := Nat.min_self a

/-- The meet is the greatest lower bound. -/
theorem meet_le_iff (a b c : TrustLevel) :
    c ≤ meet a b ↔ c ≤ a ∧ c ≤ b := Nat.le_min

/-- Enforce a trust ceiling: cap at `ceil`. -/
def enforce_ceiling (t ceil : TrustLevel) : TrustLevel := meet t ceil

theorem enforce_ceiling_le_ceil (t ceil : TrustLevel) :
    enforce_ceiling t ceil ≤ ceil := meet_le_right t ceil

theorem enforce_ceiling_le_self (t ceil : TrustLevel) :
    enforce_ceiling t ceil ≤ t := meet_le_left t ceil

theorem enforce_ceiling_idempotent (t ceil : TrustLevel) :
    enforce_ceiling (enforce_ceiling t ceil) ceil = enforce_ceiling t ceil := by
  simp [enforce_ceiling, meet]

end Trust

-- ════════════════════════════════════════════════════════════════════════
-- § 2  Backend kinds and descriptors
-- ════════════════════════════════════════════════════════════════════════

/-- The six backend-kind categories. -/
inductive BackendKind : Type where
  | z3      : BackendKind   -- SMT solver (Z3 / CVC5)
  | runtime : BackendKind   -- dynamic witness collection
  | oracle  : BackendKind   -- external oracle service
  | copilot : BackendKind   -- AI coding assistant
  | prover  : BackendKind   -- interactive theorem prover
  | human   : BackendKind   -- human annotation / review
  deriving DecidableEq, Repr, BEq

/-- Default trust ceiling for each backend kind. -/
def BackendKind.defaultCeiling : BackendKind → TrustLevel
  | .z3      => Trust.solver_discharged
  | .runtime => Trust.runtime_witnessed
  | .oracle  => Trust.oracle_proposed
  | .copilot => Trust.copilot_suggested
  | .prover  => Trust.mechanically_proven
  | .human   => Trust.human_attested

/-- A backend descriptor records the static properties of one oracle. -/
structure BackendDescriptor where
  name         : String
  kind         : BackendKind
  trustCeiling : TrustLevel
  priority     : Int  := 0
  isAvailable  : Bool := true

/-- The descriptor's ceiling must not exceed its kind's default ceiling. -/
def BackendDescriptor.ceilingSound (bd : BackendDescriptor) : Prop :=
  bd.trustCeiling ≤ bd.kind.defaultCeiling

-- ════════════════════════════════════════════════════════════════════════
-- § 3  Oracle model and evidence items
-- ════════════════════════════════════════════════════════════════════════

/-- An oracle: evidence source with identity and trust ceiling. -/
structure Oracle where
  id           : String
  trustCeiling : TrustLevel

/-- An evidence item: a claim produced by an oracle at a trust level. -/
structure Evidence where
  oracleId   : String
  claim      : String
  trustLevel : TrustLevel
  deriving Repr

/-- Enforce the oracle's trust ceiling on a piece of evidence. -/
def Oracle.enforceCeiling (o : Oracle) (e : Evidence) : Evidence :=
  { e with trustLevel := Trust.enforce_ceiling e.trustLevel o.trustCeiling }

/-- After ceiling enforcement, trust does not exceed the oracle's ceiling. -/
theorem Oracle.enforceCeiling_bounded (o : Oracle) (e : Evidence) :
    (o.enforceCeiling e).trustLevel ≤ o.trustCeiling :=
  Trust.enforce_ceiling_le_ceil e.trustLevel o.trustCeiling

/-- Ceiling enforcement does not raise the evidence's trust level. -/
theorem Oracle.enforceCeiling_nonincreasing (o : Oracle) (e : Evidence) :
    (o.enforceCeiling e).trustLevel ≤ e.trustLevel :=
  Trust.enforce_ceiling_le_self e.trustLevel o.trustCeiling

/-- Ceiling enforcement is idempotent. -/
theorem Oracle.enforceCeiling_idempotent (o : Oracle) (e : Evidence) :
    o.enforceCeiling (o.enforceCeiling e) = o.enforceCeiling e := by
  simp only [Oracle.enforceCeiling, Trust.enforce_ceiling]
  congr 1
  exact Trust.enforce_ceiling_idempotent e.trustLevel o.trustCeiling

-- ════════════════════════════════════════════════════════════════════════
-- § 4  Federation protocol
-- ════════════════════════════════════════════════════════════════════════

/-- The federated trust level: fold the meet over all evidence items,
    starting from the top trust tier (neutral element of meet). -/
def federatedTrust (items : List Evidence) : TrustLevel :=
  items.foldl (fun acc e => Trust.meet acc e.trustLevel) Trust.mechanically_proven

-- ════════════════════════════════════════════════════════════════════════
-- § 5  Key lemmas
-- ════════════════════════════════════════════════════════════════════════

/-- A foldl-meet starting below `target` stays below `target`. -/
theorem foldl_meet_le_target
    (init target : TrustLevel)
    (items : List Evidence)
    (h : init ≤ target) :
    items.foldl (fun acc e => Trust.meet acc e.trustLevel) init ≤ target := by
  induction items generalizing init with
  | nil  => simpa
  | cons hd tl ih =>
    simp only [List.foldl_cons]
    apply ih
    exact Nat.le_trans (Trust.meet_le_left init hd.trustLevel) h

/-- For every item in the list, the foldl-meet is ≤ that item's trust. -/
theorem foldl_meet_le_mem
    (init : TrustLevel)
    (items : List Evidence)
    (item : Evidence)
    (hmem : item ∈ items) :
    items.foldl (fun acc e => Trust.meet acc e.trustLevel) init ≤ item.trustLevel := by
  induction items generalizing init with
  | nil  => exact absurd hmem (List.not_mem_nil _)
  | cons hd tl ih =>
    simp only [List.foldl_cons]
    rcases List.mem_cons.mp hmem with rfl | hmem_tl
    · -- item is the head: need foldl (meet (meet init item.tl) ...) tl ≤ item.tl
      apply foldl_meet_le_target
      exact Trust.meet_le_right init item.trustLevel
    · -- item is in the tail: use IH with new init = meet init hd.tl
      exact ih _ hmem_tl

-- ════════════════════════════════════════════════════════════════════════
-- § 6  Federation Soundness Theorem
-- ════════════════════════════════════════════════════════════════════════

/-- **Federation Soundness** (Theorem 7.1).
    The federated trust level is at most the trust level of every
    contributing evidence item.  No oracle's contribution can be
    silently promoted through federation. -/
theorem federation_soundness
    (items : List Evidence)
    (item  : Evidence)
    (hmem  : item ∈ items) :
    federatedTrust items ≤ item.trustLevel :=
  foldl_meet_le_mem Trust.mechanically_proven items item hmem

/-- **No silent promotion** (Corollary 7.2).
    The federated trust cannot strictly exceed any item's trust. -/
theorem no_silent_promotion
    (items : List Evidence)
    (item  : Evidence)
    (hmem  : item ∈ items) :
    ¬ (item.trustLevel < federatedTrust items) := by
  intro hlt
  exact Nat.not_le.mpr hlt (federation_soundness items item hmem)

/-- The federated trust of a singleton list equals that item's trust. -/
theorem federation_singleton (e : Evidence) :
    federatedTrust [e] = Trust.meet Trust.mechanically_proven e.trustLevel := by
  simp [federatedTrust]

/-- Auxiliary: foldl-meet is monotone in its initial accumulator. -/
private theorem foldl_meet_mono (init₁ init₂ : TrustLevel) (items : List Evidence)
    (h : init₁ ≤ init₂) :
    items.foldl (fun acc e => Trust.meet acc e.trustLevel) init₁ ≤
    items.foldl (fun acc e => Trust.meet acc e.trustLevel) init₂ := by
  induction items generalizing init₁ init₂ with
  | nil => exact h
  | cons hd tl ih =>
    simp only [List.foldl_cons]
    apply ih
    unfold Trust.meet
    exact Nat.le_min.mpr ⟨Nat.le_trans (Nat.min_le_left init₁ hd.trustLevel) h,
                           Nat.min_le_right init₁ hd.trustLevel⟩

/-- Adding an item to the contributing set never raises the federated trust. -/
theorem federation_antimonotone
    (items : List Evidence)
    (e : Evidence) :
    federatedTrust (e :: items) ≤ federatedTrust items := by
  simp only [federatedTrust, List.foldl_cons]
  apply foldl_meet_mono
  exact Trust.meet_le_left Trust.mechanically_proven e.trustLevel

-- ════════════════════════════════════════════════════════════════════════
-- § 7  Ceiling-bounded federation
-- ════════════════════════════════════════════════════════════════════════

/-- If all items in `items` were ceiling-enforced by oracle `o`,
    the federated trust is at most `o.trustCeiling`. -/
theorem federation_bounded_by_ceiling
    (o     : Oracle)
    (items : List Evidence)
    (hall  : ∀ e ∈ items, e.trustLevel ≤ o.trustCeiling)
    (hne   : items ≠ []) :
    federatedTrust items ≤ o.trustCeiling := by
  match items, hne with
  | hd :: _, _ =>
    exact Nat.le_trans
      (federation_soundness _ hd (List.mem_cons_self hd _))
      (hall hd (List.mem_cons_self hd _))

/-- Composed with ceiling enforcement: the federated trust of enforced
    items does not exceed the oracle's ceiling. -/
theorem federation_of_enforced
    (o     : Oracle)
    (items : List Evidence)
    (hne   : items ≠ []) :
    federatedTrust (items.map (o.enforceCeiling)) ≤ o.trustCeiling := by
  match items, hne with
  | hd :: tl, _ =>
    have hmem : o.enforceCeiling hd ∈ (hd :: tl).map o.enforceCeiling :=
      List.mem_map_of_mem o.enforceCeiling (List.mem_cons_self hd tl)
    exact Nat.le_trans (federation_soundness _ _ hmem) (Oracle.enforceCeiling_bounded o hd)

-- ════════════════════════════════════════════════════════════════════════
-- § 8  Copilot trust bound (Corollary 7.3)
-- ════════════════════════════════════════════════════════════════════════

/-- A Copilot oracle has trust ceiling ≤ `copilot_suggested`. -/
def copilotOracle (id : String) : Oracle :=
  { id := id, trustCeiling := Trust.copilot_suggested }

/-- Evidence produced under a Copilot oracle ceiling never exceeds the
    `copilot_suggested` tier, even when federated with higher-trust oracles. -/
theorem copilot_trust_bound
    (items : List Evidence)
    (copilot_item : Evidence)
    (hceil : copilot_item.trustLevel ≤ Trust.copilot_suggested)
    (hmem  : copilot_item ∈ items) :
    federatedTrust items ≤ Trust.copilot_suggested :=
  Nat.le_trans (federation_soundness items copilot_item hmem) hceil

end JudgmentGeometry.Paper42
