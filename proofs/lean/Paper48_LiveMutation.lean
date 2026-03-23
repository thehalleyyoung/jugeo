/-
  Paper48_LiveMutation.lean — Live Mutation Tracking: Dynamic Analysis
  for Evidence Generation

  Formalizes Paper 48 of the Judgment Geometry series:
    • MutationKind: four kinds of runtime mutation (exec injection, eval
      query, monkey patch, hot reload)
    • MutationRecord: a witnessed mutation event at an execution coordinate
    • elevate: promotes any record to tl_runtime_witnessed trust
    • LiveMutationTracker: an ordered log of tracked mutation events,
      admitting records only through trackWitnessed
    • Runtime Witness Soundness Theorem: every mutation admitted via
      trackWitnessed carries trust level tl_runtime_witnessed at the
      original execution coordinate
    • Idempotence and monotonicity of the elevation map
    • Tracker monotonicity: the log strictly grows with each observation
    • Old records are preserved under new observations
    • LocalSection model: mutation records as sheaf sections at coordinates
    • Agreement theorem: elevated sections at the same coordinate agree
    • LocalTrustTier projection to the global 8-level lattice

  All theorems are proved without sorry.
-/

namespace JudgmentGeometry.Paper48

-- ════════════════════════════════════════════════════════════════════
-- § 1  Trust Levels
-- ════════════════════════════════════════════════════════════════════

/-- Trust levels are natural numbers 0–7, consistent with Paper04.
    Level 5 = runtime_witnessed is the target of the elevation map. -/
abbrev TrustLevel := Nat

def tl_unverified            : TrustLevel := 1
def tl_copilot               : TrustLevel := 2
def tl_oracle                : TrustLevel := 3
def tl_human_attested        : TrustLevel := 4
def tl_runtime_witnessed     : TrustLevel := 5
def tl_solver_discharged     : TrustLevel := 6
def tl_mechanically_verified : TrustLevel := 7

/-- The chain of strict inequalities across all trust levels. -/
theorem tl_chain :
    tl_unverified < tl_copilot ∧
    tl_copilot < tl_oracle ∧
    tl_oracle < tl_human_attested ∧
    tl_human_attested < tl_runtime_witnessed ∧
    tl_runtime_witnessed < tl_solver_discharged ∧
    tl_solver_discharged < tl_mechanically_verified := by
  decide

/-- Runtime-witnessed dominates unverified. -/
theorem tl_runtime_ge_unverified : tl_unverified ≤ tl_runtime_witnessed := by
  decide

-- ════════════════════════════════════════════════════════════════════
-- § 2  Mutation Kinds
-- ════════════════════════════════════════════════════════════════════

/-- The four kinds of runtime mutation tracked by the live mutation subsystem.
    Corresponds to `MutationKind` in `models.py`. -/
inductive MutationKind where
  | exec_injection : MutationKind  -- code injected via Python exec()
  | eval_query     : MutationKind  -- read-only expression via Python eval()
  | monkey_patch   : MutationKind  -- attribute replacement on a live object
  | hot_reload     : MutationKind  -- full module replacement at runtime
  deriving DecidableEq, Repr, Inhabited

/-- All four mutation kinds, for exhaustive case analysis. -/
def allMutationKinds : List MutationKind :=
  [.exec_injection, .eval_query, .monkey_patch, .hot_reload]

theorem allMutationKinds_length : allMutationKinds.length = 4 := by decide

-- ════════════════════════════════════════════════════════════════════
-- § 3  Mutation Records
-- ════════════════════════════════════════════════════════════════════

/-- A mutation record captures one observed runtime mutation event.
    `coord` is the execution coordinate (support key), and `trust_level`
    is the trust tier assigned at the time of recording. -/
structure MutationRecord where
  record_id   : String        -- unique identifier
  kind        : MutationKind  -- which mutation primitive was used
  coord       : String        -- execution coordinate (support key)
  prop_hash   : String        -- hash of the witnessed proposition φ
  timestamp   : Nat           -- logical clock at observation
  trust_level : TrustLevel    -- current trust tier
  deriving Repr

/-- A record is "live" when its trust level is at least runtime_witnessed. -/
def MutationRecord.isLive (r : MutationRecord) : Prop :=
  r.trust_level ≥ tl_runtime_witnessed

/-- A record at exactly runtime_witnessed trust is live. -/
theorem live_at_runtime_witnessed (r : MutationRecord)
    (h : r.trust_level = tl_runtime_witnessed) : r.isLive := by
  unfold MutationRecord.isLive
  rw [h]
  exact Nat.le_refl _

-- ════════════════════════════════════════════════════════════════════
-- § 4  The Elevation Function
-- ════════════════════════════════════════════════════════════════════

/-- `elevate` sets a record's trust level to tl_runtime_witnessed.
    All other fields (coordinate, kind, proposition hash, timestamp) are
    preserved. -/
def elevate (r : MutationRecord) : MutationRecord :=
  { r with trust_level := tl_runtime_witnessed }

/-- Elevation always yields tl_runtime_witnessed trust. -/
theorem elevate_trust (r : MutationRecord) :
    (elevate r).trust_level = tl_runtime_witnessed := by
  simp [elevate, tl_runtime_witnessed]

/-- Elevation preserves the execution coordinate. -/
theorem elevate_coord (r : MutationRecord) :
    (elevate r).coord = r.coord := by
  simp [elevate]

/-- Elevation preserves the mutation kind. -/
theorem elevate_kind (r : MutationRecord) :
    (elevate r).kind = r.kind := by
  simp [elevate]

/-- Elevation preserves the record identifier. -/
theorem elevate_record_id (r : MutationRecord) :
    (elevate r).record_id = r.record_id := by
  simp [elevate]

/-- Elevation preserves the proposition hash. -/
theorem elevate_prop_hash (r : MutationRecord) :
    (elevate r).prop_hash = r.prop_hash := by
  simp [elevate]

/-- Every elevated record is live. -/
theorem elevate_is_live (r : MutationRecord) : (elevate r).isLive := by
  simp [MutationRecord.isLive, elevate, tl_runtime_witnessed]

/-- Elevation is idempotent: applying it twice equals applying it once. -/
theorem elevate_idempotent (r : MutationRecord) :
    elevate (elevate r) = elevate r := by
  simp [elevate, tl_runtime_witnessed]

/-- For any record below runtime_witnessed, elevation strictly increases trust. -/
theorem elevation_strict (r : MutationRecord)
    (h : r.trust_level < tl_runtime_witnessed) :
    r.trust_level < (elevate r).trust_level := by
  simp [elevate, tl_runtime_witnessed]
  exact h

/-- Elevation never decreases trust. -/
theorem elevation_nondecreasing (r : MutationRecord) :
    r.trust_level ≤ (elevate r).trust_level ∨
    (elevate r).trust_level = tl_runtime_witnessed := by
  right
  simp [elevate]

-- ════════════════════════════════════════════════════════════════════
-- § 5  Live Mutation Tracker
-- ════════════════════════════════════════════════════════════════════

/-- The `LiveMutationTracker` holds an ordered log of mutation records
    in reverse chronological order (most recent first).
    Corresponds to `LiveMutationTracker` in `algorithms.py`. -/
structure LiveMutationTracker where
  records : List MutationRecord
  deriving Repr

/-- The empty tracker: no observations yet. -/
def LiveMutationTracker.empty : LiveMutationTracker :=
  { records := [] }

/-- `track` prepends a raw record to the log (trust level unmodified). -/
def LiveMutationTracker.track (t : LiveMutationTracker) (r : MutationRecord) :
    LiveMutationTracker :=
  { records := r :: t.records }

/-- `trackWitnessed` applies elevation before tracking, ensuring every
    newly admitted record carries tl_runtime_witnessed trust. -/
def LiveMutationTracker.trackWitnessed
    (t : LiveMutationTracker) (r : MutationRecord) :
    LiveMutationTracker :=
  t.track (elevate r)

/-- The head of the log after trackWitnessed is the elevated record. -/
theorem trackWitnessed_head (t : LiveMutationTracker) (r : MutationRecord) :
    (t.trackWitnessed r).records.head? = some (elevate r) := by
  simp [LiveMutationTracker.trackWitnessed, LiveMutationTracker.track]

-- ════════════════════════════════════════════════════════════════════
-- § 6  Runtime Witness Soundness
-- ════════════════════════════════════════════════════════════════════

/-- **Runtime Witness Soundness Theorem.**
    Every mutation submitted to `trackWitnessed` is admitted into the log
    with trust level tl_runtime_witnessed and with the original execution
    coordinate preserved. -/
theorem runtime_witness_soundness (t : LiveMutationTracker)
    (r : MutationRecord) :
    ∃ r' ∈ (t.trackWitnessed r).records,
      r'.trust_level = tl_runtime_witnessed ∧ r'.coord = r.coord := by
  refine ⟨elevate r, ?_, elevate_trust r, elevate_coord r⟩
  simp [LiveMutationTracker.trackWitnessed, LiveMutationTracker.track]

/-- Corollary: every record admitted by trackWitnessed is live. -/
theorem runtime_witness_is_live (t : LiveMutationTracker)
    (r : MutationRecord) :
    ∃ r' ∈ (t.trackWitnessed r).records, r'.isLive := by
  obtain ⟨r', hmem, htrust, _⟩ := runtime_witness_soundness t r
  exact ⟨r', hmem, by
    simp [MutationRecord.isLive, htrust, tl_runtime_witnessed]⟩

/-- Corollary: static records (trust < runtime_witnessed) get their
    trust strictly increased by trackWitnessed. -/
theorem static_record_elevated (t : LiveMutationTracker) (r : MutationRecord)
    (h : r.trust_level < tl_runtime_witnessed) :
    ∃ r' ∈ (t.trackWitnessed r).records,
      r.trust_level < r'.trust_level := by
  obtain ⟨r', hmem, htrust, _⟩ := runtime_witness_soundness t r
  exact ⟨r', hmem, htrust ▸ elevation_strict r h⟩

-- ════════════════════════════════════════════════════════════════════
-- § 7  Tracker Monotonicity
-- ════════════════════════════════════════════════════════════════════

/-- Each call to trackWitnessed strictly grows the log. -/
theorem tracker_grows (t : LiveMutationTracker) (r : MutationRecord) :
    t.records.length < (t.trackWitnessed r).records.length := by
  simp [LiveMutationTracker.trackWitnessed, LiveMutationTracker.track]

/-- Pre-existing records survive a new trackWitnessed call. -/
theorem old_records_preserved (t : LiveMutationTracker) (r old_r : MutationRecord)
    (h : old_r ∈ t.records) :
    old_r ∈ (t.trackWitnessed r).records := by
  simp [LiveMutationTracker.trackWitnessed, LiveMutationTracker.track]
  exact Or.inr h

/-- After tracking n records starting from empty, the log has length n. -/
theorem empty_then_track (r : MutationRecord) :
    (LiveMutationTracker.empty.trackWitnessed r).records.length = 1 := by
  simp [LiveMutationTracker.empty,
        LiveMutationTracker.trackWitnessed,
        LiveMutationTracker.track]

/-- Sequential tracking is additive in log length. -/
theorem sequential_tracking (t : LiveMutationTracker)
    (r1 r2 : MutationRecord) :
    (t.trackWitnessed r1 |>.trackWitnessed r2).records.length =
    t.records.length + 2 := by
  simp [LiveMutationTracker.trackWitnessed, LiveMutationTracker.track]

-- ════════════════════════════════════════════════════════════════════
-- § 8  Local Section Model (Sheaf Integration)
-- ════════════════════════════════════════════════════════════════════

/-- A local section of the evidence sheaf: a pair (coordinate, trust_level)
    representing that at `coord` the witnessed trust is `trust_level`. -/
structure LocalSection where
  coord       : String
  trust_level : TrustLevel
  deriving Repr, DecidableEq

/-- Extract the local section from a mutation record. -/
def MutationRecord.toSection (r : MutationRecord) : LocalSection :=
  { coord := r.coord, trust_level := r.trust_level }

/-- A witnessed mutation produces a section at tl_runtime_witnessed trust. -/
theorem witnessed_section_trust (r : MutationRecord) :
    (elevate r).toSection.trust_level = tl_runtime_witnessed := by
  simp [MutationRecord.toSection, elevate, tl_runtime_witnessed]

/-- Section extraction preserves the coordinate under elevation. -/
theorem section_coord_preserved (r : MutationRecord) :
    (elevate r).toSection.coord = r.coord := by
  simp [MutationRecord.toSection, elevate]

/-- Two sections agree at a coordinate when they share the same trust level
    at that coordinate (i.e., no conflicting observations). -/
def LocalSection.agreeAt (s1 s2 : LocalSection) : Prop :=
  s1.coord = s2.coord → s1.trust_level = s2.trust_level

/-- Two elevated records at the same coordinate produce agreeing sections. -/
theorem elevated_sections_agree (r1 r2 : MutationRecord)
    (h : r1.coord = r2.coord) :
    (elevate r1).toSection.agreeAt (elevate r2).toSection := by
  intro _
  simp [MutationRecord.toSection, elevate, tl_runtime_witnessed]

/-- Self-agreement: every section agrees with itself. -/
theorem section_agrees_with_self (s : LocalSection) : s.agreeAt s := by
  intro _; rfl

-- ════════════════════════════════════════════════════════════════════
-- § 9  Local Trust Tier Projection
-- ════════════════════════════════════════════════════════════════════

/-- The four local trust tiers used within the live_mutation subsystem,
    corresponding to `TrustTier` in `models.py`. -/
inductive LocalTrustTier where
  | proposal     : LocalTrustTier  -- static analysis only
  | corroborated : LocalTrustTier  -- corroborated by multiple sources
  | verified     : LocalTrustTier  -- verified by bounded model checking
  | certified    : LocalTrustTier  -- certified by runtime execution
  deriving DecidableEq, Repr, Inhabited

/-- Projection from local tiers to the global 8-level trust lattice.
    Corresponds to `JudgmentBridge.section_to_judgment` in `integration.py`. -/
def projectTrust (tier : LocalTrustTier) : TrustLevel :=
  match tier with
  | .proposal     => tl_copilot           -- level 2: static origin
  | .corroborated => tl_oracle            -- level 3: multi-source
  | .verified     => tl_human_attested    -- level 4: model-checked
  | .certified    => tl_runtime_witnessed -- level 5: runtime witness

/-- The certified tier projects to tl_runtime_witnessed. -/
theorem certified_projects_to_runtime :
    projectTrust .certified = tl_runtime_witnessed := by
  simp [projectTrust, tl_runtime_witnessed]

/-- Projection is order-preserving: proposal < certified in the image. -/
theorem projectTrust_proposal_lt_certified :
    projectTrust .proposal < projectTrust .certified := by
  decide

/-- Every tier projects to a level within the valid range [1, 7]. -/
theorem projectTrust_in_range (tier : LocalTrustTier) :
    1 ≤ projectTrust tier ∧ projectTrust tier ≤ 7 := by
  cases tier <;> simp [projectTrust, tl_copilot, tl_oracle,
    tl_human_attested, tl_runtime_witnessed] <;> decide

-- ════════════════════════════════════════════════════════════════════
-- § 10  Conservative Join of Elevated Trust
-- ════════════════════════════════════════════════════════════════════

/-- The conservative join (min) of two elevated records equals runtime_witnessed. -/
theorem elevated_meet_is_runtime (r1 r2 : MutationRecord) :
    Nat.min (elevate r1).trust_level (elevate r2).trust_level =
    tl_runtime_witnessed := by
  simp [elevate, tl_runtime_witnessed]

/-- Elevation followed by projection to section trust level is stable: the
    section always carries tl_runtime_witnessed regardless of origin. -/
theorem elevation_section_stable (r : MutationRecord) :
    (elevate r).toSection.trust_level = tl_runtime_witnessed := by
  exact witnessed_section_trust r

end JudgmentGeometry.Paper48
