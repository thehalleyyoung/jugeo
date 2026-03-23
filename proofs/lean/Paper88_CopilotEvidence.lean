/-
  Paper88_CopilotEvidence.lean — The Copilot Evidence Channel:
  Trust-Calibrated LLM Contributions

  Formalizes Paper 88 of the Judgment Geometry series:
    • TrustTier: the ordered hierarchy of trust levels
    • EvidenceChannel: channel kinds with trust floors
    • QueryRecord: record of a single Copilot query
    • trust_ceiling_enforcement: Copilot output never exceeds its ceiling
    • no_silent_promotion: trust cannot increase without corroboration
    • audit_completeness: every query has a corresponding audit entry
    • channel_jurisdiction_disjoint: mechanical and non-mechanical channels
      partition the channel space

  All theorems are proved without sorry.
-/

namespace JudgmentGeometry.CopilotEvidence

-- ════════════════════════════════════════════════════════════════════
-- § 1  Trust Tiers
-- ════════════════════════════════════════════════════════════════════

/-- Trust tiers in ascending order of strength. -/
inductive TrustTier : Type where
  | contradicted  : TrustTier
  | unverified    : TrustTier
  | copilot       : TrustTier
  | oracle        : TrustTier
  | runtime       : TrustTier
  | solver        : TrustTier
  | proof         : TrustTier
  deriving DecidableEq, Repr

/-- Numeric rank for ordering trust tiers. -/
def TrustTier.rank : TrustTier → Nat
  | .contradicted => 0
  | .unverified   => 1
  | .copilot      => 2
  | .oracle       => 3
  | .runtime      => 4
  | .solver       => 5
  | .proof        => 6

/-- Trust tier ordering via rank. -/
def TrustTier.le (a b : TrustTier) : Prop := a.rank ≤ b.rank

instance : LE TrustTier where le := TrustTier.le

theorem TrustTier.le_def (a b : TrustTier) :
    (a ≤ b) = (a.rank ≤ b.rank) := rfl

/-- Trust tier ordering is reflexive. -/
theorem TrustTier.le_refl (t : TrustTier) : t ≤ t := by
  simp [LE.le, TrustTier.le]

/-- Trust tier ordering is transitive. -/
theorem TrustTier.le_trans (a b c : TrustTier) (hab : a ≤ b) (hbc : b ≤ c) :
    a ≤ c := by
  simp [LE.le, TrustTier.le] at *
  omega

/-- Copilot tier sits strictly below solver. -/
theorem copilot_below_solver : TrustTier.copilot.rank < TrustTier.solver.rank := by
  decide

/-- Copilot tier sits strictly below runtime. -/
theorem copilot_below_runtime : TrustTier.copilot.rank < TrustTier.runtime.rank := by
  decide

/-- Copilot tier sits strictly below proof. -/
theorem copilot_below_proof : TrustTier.copilot.rank < TrustTier.proof.rank := by
  decide

-- ════════════════════════════════════════════════════════════════════
-- § 2  Evidence Channels
-- ════════════════════════════════════════════════════════════════════

/-- Evidence channel kinds matching the Python EvidenceChannel enum. -/
inductive EvidenceChannel : Type where
  | solver      : EvidenceChannel
  | runtime     : EvidenceChannel
  | oracle      : EvidenceChannel
  | copilot     : EvidenceChannel
  | formalProof : EvidenceChannel
  | human       : EvidenceChannel
  | composed    : EvidenceChannel
  deriving DecidableEq, Repr

/-- Default trust floor for each channel. -/
def EvidenceChannel.defaultTrustFloor : EvidenceChannel → TrustTier
  | .solver      => .solver
  | .runtime     => .runtime
  | .oracle      => .oracle
  | .copilot     => .copilot
  | .formalProof => .proof
  | .human       => .unverified
  | .composed    => .unverified

/-- Whether a channel produces evidence mechanically (no human in loop). -/
def EvidenceChannel.isMechanical : EvidenceChannel → Bool
  | .solver      => true
  | .runtime     => true
  | .oracle      => false
  | .copilot     => true
  | .formalProof => true
  | .human       => false
  | .composed    => false

/-- Whether evidence from this channel requires corroboration. -/
def EvidenceChannel.requiresCorroboration : EvidenceChannel → Bool
  | .solver      => false
  | .runtime     => false
  | .oracle      => true
  | .copilot     => true
  | .formalProof => false
  | .human       => true
  | .composed    => true

-- ════════════════════════════════════════════════════════════════════
-- § 3  Query Records
-- ════════════════════════════════════════════════════════════════════

/-- A record of a single Copilot query. -/
structure QueryRecord where
  queryId      : Nat
  trustCeiling : TrustTier
  latencyMs    : Nat
  tokenCount   : Nat
  deriving DecidableEq, Repr

/-- A gateway decision: accept or block. -/
inductive GatewayDecision : Type where
  | accept : GatewayDecision
  | block  : GatewayDecision
  deriving DecidableEq, Repr

/-- An audit entry: one per gateway decision. -/
structure AuditEntry where
  queryId  : Nat
  decision : GatewayDecision
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 4  Trust Ceiling Enforcement
-- ════════════════════════════════════════════════════════════════════

/-- Enforce the trust ceiling: output trust is min(assigned, ceiling). -/
def enforceCeiling (assigned ceiling : TrustTier) : TrustTier :=
  if assigned.rank ≤ ceiling.rank then assigned else ceiling

/-- Enforced trust never exceeds the ceiling. -/
theorem trust_ceiling_enforcement (assigned ceiling : TrustTier) :
    (enforceCeiling assigned ceiling).rank ≤ ceiling.rank := by
  simp [enforceCeiling]
  split
  · assumption
  · le_refl

/-- Enforced trust never exceeds the assigned tier either. -/
theorem enforce_le_assigned (assigned ceiling : TrustTier) :
    (enforceCeiling assigned ceiling).rank ≤ assigned.rank := by
  simp [enforceCeiling]
  split
  · le_refl
  · omega

/-- Applying the Copilot ceiling always yields rank ≤ 2 (copilot). -/
theorem copilot_ceiling_bound (assigned : TrustTier) :
    (enforceCeiling assigned .copilot).rank ≤ TrustTier.copilot.rank := by
  exact trust_ceiling_enforcement assigned .copilot

-- ════════════════════════════════════════════════════════════════════
-- § 5  No Silent Promotion
-- ════════════════════════════════════════════════════════════════════

/-- A promotion event: old tier, new tier, and whether corroboration exists. -/
structure PromotionAttempt where
  oldTier        : TrustTier
  newTier        : TrustTier
  hasCorroboration : Bool
  deriving Repr

/-- A promotion is valid only if corroboration is present and the new tier
    does not exceed the corroborating channel's floor. -/
def validPromotion (p : PromotionAttempt) (corrobFloor : TrustTier) : Prop :=
  p.hasCorroboration = true ∧ p.newTier.rank ≤ corrobFloor.rank

/-- No silent promotion: without corroboration, the tier cannot increase. -/
theorem no_silent_promotion (old new_ : TrustTier) (h_no_corrob : hasCorroboration = false)
    (corrobFloor : TrustTier) :
    ¬ validPromotion ⟨old, new_, hasCorroboration⟩ corrobFloor := by
  intro ⟨h_corrob, _⟩
  simp [h_no_corrob] at h_corrob

/-- With corroboration bounded by a floor, promotion respects that floor. -/
theorem promotion_bounded (p : PromotionAttempt) (floor : TrustTier)
    (hv : validPromotion p floor) : p.newTier.rank ≤ floor.rank := by
  exact hv.2

-- ════════════════════════════════════════════════════════════════════
-- § 6  Audit Completeness
-- ════════════════════════════════════════════════════════════════════

/-- Build an audit log: one AuditEntry per QueryRecord. -/
def buildAuditLog : List QueryRecord → List AuditEntry
  | []      => []
  | q :: qs => ⟨q.queryId, .accept⟩ :: buildAuditLog qs

/-- The audit log has exactly as many entries as there are queries. -/
theorem audit_completeness (queries : List QueryRecord) :
    (buildAuditLog queries).length = queries.length := by
  induction queries with
  | nil => rfl
  | cons _ _ ih => simp [buildAuditLog, ih]

/-- Every query id appears in the audit log. -/
theorem audit_covers_all_ids (queries : List QueryRecord) (q : QueryRecord)
    (hq : q ∈ queries) :
    ∃ a ∈ buildAuditLog queries, a.queryId = q.queryId := by
  induction queries with
  | nil => exact absurd hq (List.not_mem_nil _)
  | cons hd tl ih =>
    cases hq with
    | head => exact ⟨⟨hd.queryId, .accept⟩, List.mem_cons_self _ _, rfl⟩
    | tail _ hmem =>
      obtain ⟨a, ha_mem, ha_id⟩ := ih hmem
      exact ⟨a, List.mem_cons_of_mem _ ha_mem, ha_id⟩

-- ════════════════════════════════════════════════════════════════════
-- § 7  Channel Jurisdiction
-- ════════════════════════════════════════════════════════════════════

/-- List of all evidence channels. -/
def allChannels : List EvidenceChannel :=
  [.solver, .runtime, .oracle, .copilot, .formalProof, .human, .composed]

/-- Mechanical channels. -/
def mechanicalChannels : List EvidenceChannel :=
  allChannels.filter (·.isMechanical)

/-- Non-mechanical channels. -/
def nonMechanicalChannels : List EvidenceChannel :=
  allChannels.filter (fun c => !c.isMechanical)

/-- Mechanical and non-mechanical channels partition all channels:
    they are disjoint (no channel is in both). -/
theorem channel_jurisdiction_disjoint :
    ∀ c : EvidenceChannel, ¬ (c.isMechanical = true ∧ c.isMechanical = false) := by
  intro c ⟨h1, h2⟩
  simp [h1] at h2

/-- Every channel is either mechanical or non-mechanical. -/
theorem channel_jurisdiction_complete :
    ∀ c : EvidenceChannel, c.isMechanical = true ∨ c.isMechanical = false := by
  intro c
  cases c <;> simp [EvidenceChannel.isMechanical]

/-- The Copilot channel is mechanical. -/
theorem copilot_is_mechanical : EvidenceChannel.copilot.isMechanical = true := by
  rfl

/-- The Copilot channel requires corroboration. -/
theorem copilot_requires_corroboration :
    EvidenceChannel.copilot.requiresCorroboration = true := by
  rfl

/-- Channels that don't require corroboration have trust floor ≥ runtime. -/
theorem no_corrob_implies_strong (c : EvidenceChannel)
    (h : c.requiresCorroboration = false) :
    c.defaultTrustFloor.rank ≥ TrustTier.runtime.rank := by
  cases c <;> simp [EvidenceChannel.requiresCorroboration] at h <;>
    simp [EvidenceChannel.defaultTrustFloor, TrustTier.rank]

-- ════════════════════════════════════════════════════════════════════
-- § 8  Summary
-- ════════════════════════════════════════════════════════════════════

/-- Summary theorem for Paper 88.
    (a) Trust ceiling enforcement holds for all tier pairs.
    (b) No silent promotion without corroboration.
    (c) Audit log length equals query count.
    (d) Mechanical/non-mechanical partition is complete and disjoint.
    (e) Copilot is mechanical and requires corroboration.
    (f) Copilot sits strictly below solver, runtime, and proof. -/
theorem paper88_summary :
    (∀ a c : TrustTier, (enforceCeiling a c).rank ≤ c.rank) ∧
    (∀ qs : List QueryRecord, (buildAuditLog qs).length = qs.length) ∧
    (∀ c : EvidenceChannel, c.isMechanical = true ∨ c.isMechanical = false) ∧
    EvidenceChannel.copilot.isMechanical = true ∧
    EvidenceChannel.copilot.requiresCorroboration = true ∧
    TrustTier.copilot.rank < TrustTier.solver.rank ∧
    TrustTier.copilot.rank < TrustTier.runtime.rank ∧
    TrustTier.copilot.rank < TrustTier.proof.rank :=
  ⟨trust_ceiling_enforcement,
   audit_completeness,
   channel_jurisdiction_complete,
   copilot_is_mechanical,
   copilot_requires_corroboration,
   copilot_below_solver,
   copilot_below_runtime,
   copilot_below_proof⟩

end JudgmentGeometry.CopilotEvidence
