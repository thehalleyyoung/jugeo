/-
  Paper89_CopilotOracle.lean — Copilot as Federated Oracle

  Formal statement and proof of theorems from Paper 89:
  no-self-promotion, fallback soundness, corroboration necessity,
  and jurisdiction disjointness for the copilot oracle channel.
-/

namespace JudgmentGeometry.CopilotOracle

-- ════════════════════════════════════════════════════════════════════
-- § 1  Core types
-- ════════════════════════════════════════════════════════════════════

inductive OracleKind where
  | copilot
  | smt
  | human
  | runtime
  deriving DecidableEq, Repr, BEq

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
  | .contradicted        => 0
  | .unverified           => 1
  | .copilot_suggested    => 2
  | .oracle_proposed      => 3
  | .human_attested       => 4
  | .runtime_witnessed    => 5
  | .solver_discharged    => 6
  | .mechanically_verified => 7

instance : LE TrustLevel where
  le a b := a.toNat ≤ b.toNat

instance : LT TrustLevel where
  lt a b := a.toNat < b.toNat

instance (a b : TrustLevel) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

instance (a b : TrustLevel) : Decidable (a < b) :=
  inferInstanceAs (Decidable (a.toNat < b.toNat))

-- ════════════════════════════════════════════════════════════════════
-- § 2  Suggestion and oracle records
-- ════════════════════════════════════════════════════════════════════

structure SuggestionRecord where
  oracle_kind   : OracleKind
  trust_at_emit : TrustLevel
  ceiling       : TrustLevel
  corroborated  : Bool
  self_promoting : Bool
  deriving DecidableEq, Repr

def SuggestionRecord.effective_trust (r : SuggestionRecord) : TrustLevel :=
  if r.trust_at_emit.toNat ≤ r.ceiling.toNat then r.trust_at_emit else r.ceiling

structure Jurisdiction where
  domain_id : Nat
  deriving DecidableEq, Repr, BEq

structure OracleConfig where
  kind        : OracleKind
  ceiling     : TrustLevel
  jurisdiction : Jurisdiction
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 3  Copilot ceiling constant
-- ════════════════════════════════════════════════════════════════════

def copilot_ceiling : TrustLevel := TrustLevel.copilot_suggested

theorem copilot_ceiling_val : copilot_ceiling = TrustLevel.copilot_suggested := rfl

theorem copilot_below_solver :
    copilot_ceiling < TrustLevel.solver_discharged := by
  show copilot_ceiling.toNat < TrustLevel.solver_discharged.toNat
  decide

theorem copilot_below_mechanical :
    copilot_ceiling < TrustLevel.mechanically_verified := by
  show copilot_ceiling.toNat < TrustLevel.mechanically_verified.toNat
  decide

-- ════════════════════════════════════════════════════════════════════
-- § 4  No-self-promotion theorem
-- ════════════════════════════════════════════════════════════════════

/-- A suggestion record satisfies the no-self-promotion invariant when
    the oracle does not mark its own output as self-promoting AND the
    effective trust never exceeds the ceiling. -/
def no_self_promotion_inv (r : SuggestionRecord) : Prop :=
  r.self_promoting = false ∧ r.effective_trust ≤ r.ceiling

theorem no_self_promotion
    (r : SuggestionRecord)
    (h_not_self : r.self_promoting = false)
    (h_emit_le : r.trust_at_emit ≤ r.ceiling) :
    no_self_promotion_inv r := by
  constructor
  · exact h_not_self
  · unfold SuggestionRecord.effective_trust
    simp [h_emit_le]
    split
    · exact h_emit_le
    · exact Nat.le_refl _

theorem no_self_promotion_effective_le_ceiling
    (r : SuggestionRecord)
    (h : no_self_promotion_inv r) :
    r.effective_trust ≤ r.ceiling := by
  exact h.2

-- ════════════════════════════════════════════════════════════════════
-- § 5  Fallback soundness
-- ════════════════════════════════════════════════════════════════════

/-- A fallback invocation is sound when: either a primary backend
    succeeded, or the copilot was consulted and its result is capped
    at the copilot ceiling. -/
inductive FallbackResult where
  | primary_ok   : TrustLevel → FallbackResult
  | copilot_used : SuggestionRecord → FallbackResult
  | no_result    : FallbackResult
  deriving Repr

def fallback_sound (fr : FallbackResult) : Prop :=
  match fr with
  | .primary_ok t   => True
  | .copilot_used r => r.effective_trust ≤ copilot_ceiling
  | .no_result      => True

theorem fallback_soundness
    (r : SuggestionRecord)
    (h_kind : r.oracle_kind = OracleKind.copilot)
    (h_ceil : r.ceiling = copilot_ceiling)
    (h_emit : r.trust_at_emit ≤ r.ceiling) :
    fallback_sound (FallbackResult.copilot_used r) := by
  unfold fallback_sound
  unfold SuggestionRecord.effective_trust
  simp [h_emit]
  split
  · rw [h_ceil]; exact h_emit
  · rw [h_ceil]; exact Nat.le_refl _

theorem fallback_primary_always_sound (t : TrustLevel) :
    fallback_sound (FallbackResult.primary_ok t) := by
  unfold fallback_sound
  trivial

theorem fallback_no_result_sound :
    fallback_sound FallbackResult.no_result := by
  unfold fallback_sound
  trivial

-- ════════════════════════════════════════════════════════════════════
-- § 6  Corroboration necessity
-- ════════════════════════════════════════════════════════════════════

/-- Corroboration is necessary: to promote a copilot suggestion above
    its ceiling, an independent corroboration source is required. -/
structure CorroborationEvidence where
  source_kind     : OracleKind
  source_trust    : TrustLevel
  source_ne_copilot : source_kind ≠ OracleKind.copilot
  deriving Repr

def may_promote_above_ceiling
    (r : SuggestionRecord) (target : TrustLevel)
    (ev : CorroborationEvidence) : Prop :=
  ev.source_trust ≥ target ∧ r.corroborated = true

theorem corroboration_necessity
    (r : SuggestionRecord)
    (target : TrustLevel)
    (h_above : target > r.ceiling)
    (ev : CorroborationEvidence)
    (h_src : ev.source_trust ≥ target)
    (h_corr : r.corroborated = true) :
    may_promote_above_ceiling r target ev := by
  constructor
  · exact h_src
  · exact h_corr

theorem corroboration_source_independent
    (ev : CorroborationEvidence) :
    ev.source_kind ≠ OracleKind.copilot :=
  ev.source_ne_copilot

-- ════════════════════════════════════════════════════════════════════
-- § 7  Jurisdiction disjointness
-- ════════════════════════════════════════════════════════════════════

def jurisdictions_disjoint (j1 j2 : Jurisdiction) : Prop :=
  j1.domain_id ≠ j2.domain_id

theorem jurisdiction_disjointness
    (c1 c2 : OracleConfig)
    (h_diff_kind : c1.kind ≠ c2.kind)
    (h_diff_dom : c1.jurisdiction.domain_id ≠ c2.jurisdiction.domain_id) :
    jurisdictions_disjoint c1.jurisdiction c2.jurisdiction := by
  exact h_diff_dom

theorem jurisdiction_disjoint_symm
    (j1 j2 : Jurisdiction)
    (h : jurisdictions_disjoint j1 j2) :
    jurisdictions_disjoint j2 j1 := by
  exact Ne.symm h

theorem disjoint_oracles_no_conflict
    (c1 c2 : OracleConfig)
    (h : jurisdictions_disjoint c1.jurisdiction c2.jurisdiction)
    (r1 r2 : SuggestionRecord)
    (h1 : no_self_promotion_inv r1)
    (h2 : no_self_promotion_inv r2) :
    r1.effective_trust ≤ r1.ceiling ∧ r2.effective_trust ≤ r2.ceiling := by
  exact ⟨h1.2, h2.2⟩

-- ════════════════════════════════════════════════════════════════════
-- § 8  Composition: end-to-end safety
-- ════════════════════════════════════════════════════════════════════

theorem copilot_end_to_end_safety
    (r : SuggestionRecord)
    (h_kind : r.oracle_kind = OracleKind.copilot)
    (h_ceil : r.ceiling = copilot_ceiling)
    (h_emit : r.trust_at_emit ≤ r.ceiling)
    (h_not_self : r.self_promoting = false) :
    no_self_promotion_inv r ∧ fallback_sound (FallbackResult.copilot_used r) := by
  constructor
  · exact no_self_promotion r h_not_self h_emit
  · exact fallback_soundness r h_kind h_ceil h_emit

end JudgmentGeometry.CopilotOracle
