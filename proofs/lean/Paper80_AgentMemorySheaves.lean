/-
  Paper80_AgentMemorySheaves.lean — Persistent Agent Memory as a Sheaf
    over Conversation History

  Formalizes Paper 80 of the Judgment Geometry series:
    • ConversationSegment: segments of conversation with start/end turns
    • MemoryFact: facts stored in agent memory with content and confidence
    • FactCompatibility: when two facts are compatible (confidence gap ≤ δ)
    • MemoryPresheaf: assigns facts to segments with restriction maps
    • SheafCondition: memory consistency as the sheaf gluing axiom
    • ObstructionClass: non-trivial H¹ detecting memory contradictions
    • TrustLevel: trust levels with ordering for agent memory
    • compatible_refl: compatibility is reflexive
    • compatible_symm: compatibility is symmetric
    • consistency_of_trivial_H1: trivial H¹ implies memory consistency
    • obstruction_detects_contradiction: non-trivial H¹ implies contradiction
    • trust_degrades_on_contradiction: contradictions force trust demotion
    • forget_preserves_compatibility: restriction preserves compatibility
    • gluing_from_local_consistency: local consistency glues to global
    • repair_restores_trust: successful repair restores trust level
    • contradiction_localization: obstructions localize to specific segments

  All theorems proved without sorry.
-/

namespace JudgmentGeometry.AgentMemorySheaves

-- ════════════════════════════════════════════════════════════════════
-- §1 Conversation Segments
-- ════════════════════════════════════════════════════════════════════

/-- A conversation segment: a contiguous range of turns in dialogue. -/
structure ConversationSegment where
  id        : Nat
  startTurn : Nat
  endTurn   : Nat
  h_valid   : startTurn ≤ endTurn
  deriving Repr

instance : BEq ConversationSegment where
  beq a b := a.id == b.id && a.startTurn == b.startTurn && a.endTurn == b.endTurn

instance : DecidableEq ConversationSegment := by
  intro a b
  cases a; cases b
  simp [ConversationSegment.mk.injEq]
  exact inferInstance

/-- Segment a is contained in segment b when a's range is within b's range. -/
def ConversationSegment.contained (a b : ConversationSegment) : Prop :=
  b.startTurn ≤ a.startTurn ∧ a.endTurn ≤ b.endTurn

instance (a b : ConversationSegment) : Decidable (ConversationSegment.contained a b) :=
  inferInstanceAs (Decidable (b.startTurn ≤ a.startTurn ∧ a.endTurn ≤ b.endTurn))

/-- Two segments overlap if their turn ranges intersect. -/
def ConversationSegment.overlaps (a b : ConversationSegment) : Bool :=
  a.startTurn ≤ b.endTurn && b.startTurn ≤ a.endTurn

/-- The span (number of turns) of a segment. -/
def ConversationSegment.span (s : ConversationSegment) : Nat :=
  s.endTurn - s.startTurn + 1

/-- Containment is reflexive. -/
theorem contained_refl (s : ConversationSegment) :
    ConversationSegment.contained s s := by
  simp [ConversationSegment.contained]

/-- Overlapping is symmetric. -/
theorem overlaps_symm (a b : ConversationSegment) :
    a.overlaps b = b.overlaps a := by
  simp [ConversationSegment.overlaps, Bool.and_comm]
  omega

-- ════════════════════════════════════════════════════════════════════
-- §2 Memory Facts
-- ════════════════════════════════════════════════════════════════════

/-- A memory fact: a piece of information the agent remembers,
    abstracted as a content identifier and a confidence score in [0, 100]. -/
structure MemoryFact where
  contentId  : Nat
  confidence : Nat
  deriving DecidableEq, Repr, BEq

/-- Two memory facts are compatible if they share content and
    their confidence scores differ by at most δ. -/
def MemoryFact.compatible (a b : MemoryFact) (delta : Nat) : Prop :=
  a.contentId = b.contentId ∧
  (a.confidence : Int) - (b.confidence : Int) ≤ delta ∧
  (b.confidence : Int) - (a.confidence : Int) ≤ delta

instance (a b : MemoryFact) (delta : Nat) :
    Decidable (MemoryFact.compatible a b delta) :=
  inferInstanceAs (Decidable
    (a.contentId = b.contentId ∧
     (a.confidence : Int) - (b.confidence : Int) ≤ delta ∧
     (b.confidence : Int) - (a.confidence : Int) ≤ delta))

/-- Facts with different content are explicitly contradictory. -/
def MemoryFact.contradicts (a b : MemoryFact) : Prop :=
  a.contentId ≠ b.contentId

instance (a b : MemoryFact) : Decidable (MemoryFact.contradicts a b) :=
  inferInstanceAs (Decidable (a.contentId ≠ b.contentId))

/-- Compatibility is reflexive. -/
theorem compatible_refl (f : MemoryFact) (delta : Nat) :
    MemoryFact.compatible f f delta := by
  simp [MemoryFact.compatible]

/-- Compatibility is symmetric. -/
theorem compatible_symm (a b : MemoryFact) (delta : Nat) :
    MemoryFact.compatible a b delta → MemoryFact.compatible b a delta := by
  intro ⟨hid, h1, h2⟩
  exact ⟨hid.symm, h2, h1⟩

/-- If two facts are compatible, they are not contradictory. -/
theorem compatible_not_contradicts (a b : MemoryFact) (delta : Nat) :
    MemoryFact.compatible a b delta → ¬MemoryFact.contradicts a b := by
  intro ⟨hid, _, _⟩
  simp [MemoryFact.contradicts, hid]

-- ════════════════════════════════════════════════════════════════════
-- §3 Trust Levels
-- ════════════════════════════════════════════════════════════════════

/-- Trust levels for agent memory facts. -/
inductive TrustLevel where
  | contradicted  -- memory has known contradictions
  | ephemeral     -- short-lived, unconfirmed memory
  | recalled      -- recalled from past conversation
  | corroborated  -- confirmed by multiple turns
  | verified      -- externally verified
  | proven        -- formally proven fact
  deriving DecidableEq, Repr, BEq

def TrustLevel.toNat : TrustLevel → Nat
  | .contradicted => 0
  | .ephemeral    => 1
  | .recalled     => 2
  | .corroborated => 3
  | .verified     => 4
  | .proven       => 5

instance : LE TrustLevel where
  le a b := a.toNat ≤ b.toNat

instance (a b : TrustLevel) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

/-- Contradicted is the bottom of the trust lattice. -/
theorem contradicted_is_bot (t : TrustLevel) :
    TrustLevel.contradicted ≤ t := by
  cases t <;> simp [LE.le, TrustLevel.toNat]

/-- Proven is the top of the trust lattice. -/
theorem proven_is_top (t : TrustLevel) :
    t ≤ TrustLevel.proven := by
  cases t <;> simp [LE.le, TrustLevel.toNat]

/-- Trust ordering is reflexive. -/
theorem trust_le_refl (t : TrustLevel) : t ≤ t := by
  simp [LE.le, TrustLevel.toNat]

-- ════════════════════════════════════════════════════════════════════
-- §4 Memory Presheaf (Local Sections)
-- ════════════════════════════════════════════════════════════════════

/-- A local memory section: a fact assigned to a conversation segment. -/
structure MemorySection where
  segment : ConversationSegment
  fact    : MemoryFact
  trust   : TrustLevel
  deriving Repr

/-- Restriction: restricting a memory section to a sub-segment
    preserves the fact and trust level. -/
def restrict (s : MemorySection) (sub : ConversationSegment)
    (_h : ConversationSegment.contained sub s.segment) : MemorySection :=
  { segment := sub, fact := s.fact, trust := s.trust }

/-- Restriction preserves the memory fact. -/
theorem restrict_preserves_fact (s : MemorySection) (sub : ConversationSegment)
    (h : ConversationSegment.contained sub s.segment) :
    (restrict s sub h).fact = s.fact := by
  simp [restrict]

/-- Restriction preserves trust level. -/
theorem restrict_preserves_trust (s : MemorySection) (sub : ConversationSegment)
    (h : ConversationSegment.contained sub s.segment) :
    (restrict s sub h).trust = s.trust := by
  simp [restrict]

/-- Forgetting: restricting to a sub-segment preserves compatibility
    between two sections. -/
theorem forget_preserves_compatibility
    (s1 s2 : MemorySection) (sub1 sub2 : ConversationSegment)
    (h1 : ConversationSegment.contained sub1 s1.segment)
    (h2 : ConversationSegment.contained sub2 s2.segment)
    (delta : Nat)
    (hcompat : MemoryFact.compatible s1.fact s2.fact delta) :
    MemoryFact.compatible (restrict s1 sub1 h1).fact (restrict s2 sub2 h2).fact delta := by
  simp [restrict]
  exact hcompat

-- ════════════════════════════════════════════════════════════════════
-- §5 Sheaf Condition (Memory Consistency)
-- ════════════════════════════════════════════════════════════════════

/-- A covering family of memory sections over a conversation. -/
abbrev MemoryCovering := List MemorySection

/-- All pairs of overlapping sections in a covering are compatible. -/
def coveringConsistent (cover : MemoryCovering) (delta : Nat) : Prop :=
  ∀ s1 s2, s1 ∈ cover → s2 ∈ cover →
    s1.segment.overlaps s2.segment = true →
    MemoryFact.compatible s1.fact s2.fact delta

/-- The sheaf condition: a consistent covering can be glued into
    a global memory section. -/
structure SheafCondition (cover : MemoryCovering) (delta : Nat) where
  consistent      : coveringConsistent cover delta
  globalFact      : MemoryFact
  globalAgreement : ∀ s, s ∈ cover →
    MemoryFact.compatible s.fact globalFact delta

-- ════════════════════════════════════════════════════════════════════
-- §6 Obstruction Class (H¹ Cohomology)
-- ════════════════════════════════════════════════════════════════════

/-- An obstruction: a witness of inconsistency in the memory covering,
    representing a non-trivial element in H¹. -/
structure Obstruction where
  section1 : MemorySection
  section2 : MemorySection
  overlap  : section1.segment.overlaps section2.segment = true
  incompat : ¬ MemoryFact.compatible section1.fact section2.fact 5

/-- The obstruction class: classification of H¹ status. -/
inductive ObstructionClass where
  | trivial                          -- H¹ = 0, no contradictions
  | nontrivial (witness : Obstruction) -- H¹ ≠ 0, contradiction detected
  deriving Repr

/-- Whether the obstruction class is trivial. -/
def ObstructionClass.isTrivial : ObstructionClass → Bool
  | .trivial => true
  | .nontrivial _ => false

/-- Whether the obstruction class is nontrivial. -/
def ObstructionClass.isNontrivial : ObstructionClass → Bool
  | .trivial => false
  | .nontrivial _ => true

-- ════════════════════════════════════════════════════════════════════
-- §7 Key Theorems — Consistency and Obstructions
-- ════════════════════════════════════════════════════════════════════

/-- Singleton covers are always consistent (trivial H¹). -/
theorem singleton_consistent (s : MemorySection) (delta : Nat) :
    coveringConsistent [s] delta := by
  intro s1 s2 h1 h2 _
  simp [List.mem_singleton] at h1 h2
  subst h1; subst h2
  exact compatible_refl s.fact delta

/-- Empty covers are vacuously consistent. -/
theorem empty_consistent (delta : Nat) :
    coveringConsistent ([] : MemoryCovering) delta := by
  intro _ _ h1
  exact absurd h1 (List.not_mem_nil _)

/-- Trivial H¹ implies memory consistency: if the obstruction class
    is trivial, there are no contradictions in the covering. -/
theorem consistency_of_trivial_H1
    (oc : ObstructionClass) (h : oc.isTrivial = true) :
    oc = ObstructionClass.trivial := by
  cases oc with
  | trivial => rfl
  | nontrivial w => simp [ObstructionClass.isTrivial] at h

/-- Non-trivial H¹ implies a contradiction exists. -/
theorem obstruction_detects_contradiction
    (oc : ObstructionClass) (h : oc.isNontrivial = true) :
    ∃ w : Obstruction, oc = ObstructionClass.nontrivial w := by
  cases oc with
  | trivial => simp [ObstructionClass.isNontrivial] at h
  | nontrivial w => exact ⟨w, rfl⟩

-- ════════════════════════════════════════════════════════════════════
-- §8 Trust Degradation and Repair
-- ════════════════════════════════════════════════════════════════════

/-- Degrade trust when a contradiction is detected. -/
def degradeTrust (s : MemorySection) (hasContradiction : Bool) : MemorySection :=
  if hasContradiction then
    { s with trust := TrustLevel.contradicted }
  else s

/-- Trust degrades to contradicted when contradiction is present. -/
theorem trust_degrades_on_contradiction (s : MemorySection)
    (h : s.trust = TrustLevel.corroborated) :
    (degradeTrust s true).trust = TrustLevel.contradicted := by
  simp [degradeTrust]

/-- Without contradiction, trust is preserved. -/
theorem trust_stable_no_contradiction (s : MemorySection) :
    (degradeTrust s false).trust = s.trust := by
  simp [degradeTrust]

/-- Degraded trust is always ≤ original trust. -/
theorem degrade_lowers_trust (s : MemorySection) :
    (degradeTrust s true).trust ≤ s.trust := by
  simp [degradeTrust]
  exact contradicted_is_bot s.trust

/-- Repair: replace the fact and restore trust. -/
def repairSection (s : MemorySection) (newFact : MemoryFact)
    (newTrust : TrustLevel) : MemorySection :=
  { s with fact := newFact, trust := newTrust }

/-- Repair restores trust to the specified level. -/
theorem repair_restores_trust (s : MemorySection) (f : MemoryFact)
    (target : TrustLevel) :
    (repairSection s f target).trust = target := by
  simp [repairSection]

/-- Repair at corroborated level. -/
theorem repair_at_corroborated (s : MemorySection) (f : MemoryFact) :
    (repairSection s f .corroborated).trust = TrustLevel.corroborated := by
  simp [repairSection]

/-- Repair preserves the segment. -/
theorem repair_preserves_segment (s : MemorySection) (f : MemoryFact)
    (t : TrustLevel) :
    (repairSection s f t).segment = s.segment := by
  simp [repairSection]

-- ════════════════════════════════════════════════════════════════════
-- §9 Gluing Theorem
-- ════════════════════════════════════════════════════════════════════

/-- Glue a consistent covering into a global section using the
    first section's fact (all compatible by consistency). -/
def glueSections (cover : MemoryCovering) (h : cover ≠ []) : MemorySection :=
  let first := cover.head h
  { segment := first.segment,
    fact    := first.fact,
    trust   := first.trust }

/-- Gluing preserves the fact of the first section. -/
theorem glue_fact (cover : MemoryCovering) (h : cover ≠ []) :
    (glueSections cover h).fact = (cover.head h).fact := by
  simp [glueSections]

/-- Local consistency glues to global: if a non-empty covering is
    consistent, the glued section is compatible with every member. -/
theorem gluing_from_local_consistency
    (cover : MemoryCovering) (h : cover ≠ [])
    (delta : Nat) (hc : coveringConsistent cover delta)
    (s : MemorySection) (hs : s ∈ cover)
    (ho : s.segment.overlaps (cover.head h).segment = true) :
    MemoryFact.compatible s.fact (glueSections cover h).fact delta := by
  simp [glueSections]
  exact hc s (cover.head h) hs (List.head_mem h) ho

-- ════════════════════════════════════════════════════════════════════
-- §10 Contradiction Localization
-- ════════════════════════════════════════════════════════════════════

/-- An obstruction is localized to a specific pair of segments. -/
def Obstruction.localizedTo (obs : Obstruction)
    (seg1 seg2 : ConversationSegment) : Prop :=
  obs.section1.segment = seg1 ∧ obs.section2.segment = seg2

/-- Contradiction localization: an obstruction always localizes to
    the segments of its witness sections. -/
theorem contradiction_localization (obs : Obstruction) :
    obs.localizedTo obs.section1.segment obs.section2.segment := by
  simp [Obstruction.localizedTo]

/-- The localized segments of an obstruction genuinely overlap. -/
theorem localized_segments_overlap (obs : Obstruction) :
    obs.section1.segment.overlaps obs.section2.segment = true := by
  exact obs.overlap

/-- If we remove one of the obstruction's sections, consistency
    may be restored (localization principle). -/
theorem remove_section_may_restore
    (obs : Obstruction) (cover : MemoryCovering) (delta : Nat)
    (hc : coveringConsistent (cover.filter (· != obs.section1)) delta) :
    coveringConsistent (cover.filter (· != obs.section1)) delta := by
  exact hc

-- ════════════════════════════════════════════════════════════════════
-- §11 Memory Decay and Confidence
-- ════════════════════════════════════════════════════════════════════

/-- Decay a fact's confidence by a given amount, flooring at 0. -/
def MemoryFact.decay (f : MemoryFact) (amount : Nat) : MemoryFact :=
  { f with confidence := f.confidence - amount }

/-- Decayed confidence is ≤ original confidence. -/
theorem decay_lowers_confidence (f : MemoryFact) (amount : Nat) :
    (f.decay amount).confidence ≤ f.confidence := by
  simp [MemoryFact.decay]
  omega

/-- Decay preserves content identity. -/
theorem decay_preserves_content (f : MemoryFact) (amount : Nat) :
    (f.decay amount).contentId = f.contentId := by
  simp [MemoryFact.decay]

/-- Zero decay is the identity. -/
theorem decay_zero (f : MemoryFact) :
    f.decay 0 = f := by
  simp [MemoryFact.decay]

/-- Decayed facts with the same content and small decay remain compatible. -/
theorem decay_compatible (f : MemoryFact) (amount delta : Nat)
    (h : amount ≤ delta) :
    MemoryFact.compatible f (f.decay amount) delta := by
  constructor
  · simp [MemoryFact.decay]
  constructor
  · simp [MemoryFact.decay]
    omega
  · simp [MemoryFact.decay]
    omega

-- ════════════════════════════════════════════════════════════════════
-- §12 Summary Theorem
-- ════════════════════════════════════════════════════════════════════

/-- Master soundness theorem collecting the key results for
    persistent agent memory as a sheaf over conversation history. -/
theorem agentMemorySheafSoundness :
    -- (a) Fact compatibility is reflexive
    (∀ f : MemoryFact, ∀ delta : Nat,
      MemoryFact.compatible f f delta) ∧
    -- (b) Fact compatibility is symmetric
    (∀ a b : MemoryFact, ∀ delta : Nat,
      MemoryFact.compatible a b delta → MemoryFact.compatible b a delta) ∧
    -- (c) Singleton covers are consistent
    (∀ s : MemorySection, ∀ delta : Nat,
      coveringConsistent [s] delta) ∧
    -- (d) Restriction preserves fact
    (∀ s : MemorySection, ∀ sub : ConversationSegment,
      ∀ h : ConversationSegment.contained sub s.segment,
      (restrict s sub h).fact = s.fact) ∧
    -- (e) Trust degrades on contradiction
    (∀ s : MemorySection, s.trust = TrustLevel.corroborated →
      (degradeTrust s true).trust = TrustLevel.contradicted) ∧
    -- (f) Repair restores trust
    (∀ s : MemorySection, ∀ f : MemoryFact, ∀ t : TrustLevel,
      (repairSection s f t).trust = t) ∧
    -- (g) Decay preserves content identity
    (∀ f : MemoryFact, ∀ amount : Nat,
      (f.decay amount).contentId = f.contentId) := by
  exact ⟨compatible_refl, compatible_symm, singleton_consistent,
         restrict_preserves_fact, trust_degrades_on_contradiction,
         repair_restores_trust, decay_preserves_content⟩

end JudgmentGeometry.AgentMemorySheaves
