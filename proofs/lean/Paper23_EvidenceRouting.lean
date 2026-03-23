/-
  Paper23_EvidenceRouting.lean — Mixed Evidence Routing: Theory-Aware Dispatch
  of Verification Conditions

  Formalizes Paper 23 of the Judgment Geometry series:
    • EvidenceChannel taxonomy (Z3, LLM, RT, Human, Composite)
    • RoutingStrategy enumeration (five dispatch modes)
    • Fragment type system (QF_LIA, QF_LRA, QF_BV, QF_UF, MIXED, UNKNOWN)
    • TrustLevel ordering (Nat-based, self-contained)
    • LogicalFragment and VerificationCondition structures
    • Sound split predicate (SoundSplit)
    • EvidenceItem with trust tier
    • allFragmentsValid predicate
    • Routing Soundness Theorem: if all fragments are valid, VC is valid
    • Trust-meet lemma: the meet of trust tiers is a lower bound for each
    • Trust Reassembly Theorem: bundle trust ≤ every item's trust tier
    • No-silent-promotion corollary
    • Channel soundness predicate and soundness preservation

  All theorems are proved without sorry.
-/

namespace JudgmentGeometry.Paper23

-- ════════════════════════════════════════════════════════════════════
-- § 1  Trust Level (Nat-based, self-contained)
-- ════════════════════════════════════════════════════════════════════

/-- Trust levels encoded as natural numbers.
    0 = CONTRADICTED, 1 = UNVERIFIED, 2 = COPILOT, 3 = ORACLE,
    4 = RUNTIME, 5 = SOLVER, 6 = PROOF. -/
abbrev TrustLevel := Nat

def CONTRADICTED : TrustLevel := 0
def UNVERIFIED   : TrustLevel := 1
def COPILOT      : TrustLevel := 2
def ORACLE       : TrustLevel := 3
def RUNTIME      : TrustLevel := 4
def SOLVER       : TrustLevel := 5
def PROOF        : TrustLevel := 6

theorem trust_chain :
    CONTRADICTED < UNVERIFIED ∧ UNVERIFIED < COPILOT ∧ COPILOT < ORACLE ∧
    ORACLE < RUNTIME ∧ RUNTIME < SOLVER ∧ SOLVER < PROOF := by
  decide

-- ════════════════════════════════════════════════════════════════════
-- § 2  SMT-LIB Fragment Types
-- ════════════════════════════════════════════════════════════════════

/-- SMT-LIB logic fragments supported by the JuGeo solver pipeline. -/
inductive Fragment where
  | QF_LIA  -- quantifier-free linear integer arithmetic
  | QF_LRA  -- quantifier-free linear real arithmetic
  | QF_BV   -- quantifier-free fixed-width bitvectors
  | QF_UF   -- quantifier-free uninterpreted functions
  | STRINGS -- string constraints
  | MIXED   -- combination of multiple fragments
  | UNKNOWN -- unclassifiable formula
  deriving DecidableEq, Repr, BEq

/-- A fragment is pure if it is not MIXED or UNKNOWN. -/
def Fragment.isPure : Fragment → Bool
  | .QF_LIA  => true
  | .QF_LRA  => true
  | .QF_BV   => true
  | .QF_UF   => true
  | .STRINGS => true
  | .MIXED   => false
  | .UNKNOWN => false

theorem qf_lia_pure   : Fragment.isPure .QF_LIA  = true := rfl
theorem qf_lra_pure   : Fragment.isPure .QF_LRA  = true := rfl
theorem qf_bv_pure    : Fragment.isPure .QF_BV   = true := rfl
theorem qf_uf_pure    : Fragment.isPure .QF_UF   = true := rfl
theorem strings_pure  : Fragment.isPure .STRINGS = true := rfl
theorem mixed_impure  : Fragment.isPure .MIXED   = false := rfl
theorem unknown_impure: Fragment.isPure .UNKNOWN = false := rfl

-- ════════════════════════════════════════════════════════════════════
-- § 3  Evidence Channels
-- ════════════════════════════════════════════════════════════════════

/-- Named evidence channels recognized by the JuGeo runtime.
    Each channel corresponds to a distinct trust domain. -/
inductive EvidenceChannel where
  | Z3             -- SMT solver backend (highest automated trust)
  | CopilotLLM     -- LLM oracle (heuristic suggestions)
  | RuntimeWitness -- runtime heap witness
  | Human          -- manual review
  | Composite      -- federation of multiple channels
  deriving DecidableEq, Repr

/-- Trust ceiling for each evidence channel. -/
def EvidenceChannel.trustCeiling : EvidenceChannel → TrustLevel
  | .Z3             => SOLVER
  | .CopilotLLM     => COPILOT
  | .RuntimeWitness => RUNTIME
  | .Human          => ORACLE
  | .Composite      => COPILOT   -- conservative: min of constituents

theorem z3_ceiling_is_solver :
    EvidenceChannel.trustCeiling .Z3 = SOLVER := rfl

theorem copilot_ceiling_is_copilot :
    EvidenceChannel.trustCeiling .CopilotLLM = COPILOT := rfl

theorem copilot_ceiling_lt_solver :
    EvidenceChannel.trustCeiling .CopilotLLM < EvidenceChannel.trustCeiling .Z3 := by
  decide

-- ════════════════════════════════════════════════════════════════════
-- § 4  Routing Strategy
-- ════════════════════════════════════════════════════════════════════

/-- Routing optimisation objective. -/
inductive RoutingStrategy where
  | StrictJurisdiction -- smallest jurisdiction covering the fragment
  | CostOptimal        -- lowest cost backend
  | LatencyOptimal     -- lowest latency backend
  | TrustOptimal       -- highest trust ceiling backend
  | LoadBalanced       -- semantic-weight distribution
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 5  Logical Fragment and Verification Condition
-- ════════════════════════════════════════════════════════════════════

/-- A logical fragment: a pure-theory sub-formula with an assigned channel. -/
structure LogicalFragment where
  kind    : Fragment
  channel : EvidenceChannel
  /-- The fragment must be pure (not MIXED or UNKNOWN) to be dispatchable. -/
  isPure  : kind.isPure = true
  deriving Repr

/-- A verification condition: a (possibly mixed) formula represented as a
    list of logical fragments that together cover it. -/
structure VerificationCondition where
  /-- The list of pure-theory fragments that partition the VC. -/
  fragments : List LogicalFragment

-- ════════════════════════════════════════════════════════════════════
-- § 6  Evidence Items and Bundles
-- ════════════════════════════════════════════════════════════════════

/-- An evidence item: the result of dispatching one fragment to a backend.
    Carries a trust tier and a validity witness. -/
structure EvidenceItem where
  fragment  : LogicalFragment
  trustTier : TrustLevel
  /-- The trust tier must not exceed the channel's ceiling. -/
  trustBounded : trustTier ≤ fragment.channel.trustCeiling
  deriving Repr

/-- The trust tier of an item is bounded by its channel's ceiling. -/
theorem item_trust_le_ceiling (item : EvidenceItem) :
    item.trustTier ≤ item.fragment.channel.trustCeiling :=
  item.trustBounded

-- ════════════════════════════════════════════════════════════════════
-- § 7  Sound Split Predicate
-- ════════════════════════════════════════════════════════════════════

/-- A split of a VC into fragments is sound if every fragment is valid
    implies the VC is valid.  We model validity abstractly via a predicate. -/
def SoundSplit
    (validFrag : LogicalFragment → Prop)
    (validVC   : Prop)
    (frags     : List LogicalFragment) : Prop :=
  (∀ f ∈ frags, validFrag f) → validVC

/-- The empty split (no fragments) is trivially sound when validVC holds
    independently.  This captures pure conditions that need no splitting. -/
theorem empty_split_sound_of_valid
    (validFrag : LogicalFragment → Prop)
    (validVC   : Prop)
    (h         : validVC) :
    SoundSplit validFrag validVC [] := by
  intro _
  exact h

-- ════════════════════════════════════════════════════════════════════
-- § 8  All Fragments Valid Predicate
-- ════════════════════════════════════════════════════════════════════

/-- All fragments in a list are valid under a given validity predicate. -/
def allFragmentsValid
    (validFrag : LogicalFragment → Prop)
    (frags     : List LogicalFragment) : Prop :=
  ∀ f ∈ frags, validFrag f

theorem allFragmentsValid_nil (validFrag : LogicalFragment → Prop) :
    allFragmentsValid validFrag [] := by
  intro f hf
  exact absurd hf (List.not_mem_nil f)

theorem allFragmentsValid_cons
    (validFrag : LogicalFragment → Prop)
    (f         : LogicalFragment)
    (fs        : List LogicalFragment)
    (hf        : validFrag f)
    (hfs       : allFragmentsValid validFrag fs) :
    allFragmentsValid validFrag (f :: fs) := by
  intro g hg
  cases hg with
  | head      => exact hf
  | tail _ hg => exact hfs g hg

-- ════════════════════════════════════════════════════════════════════
-- § 9  Routing Soundness Theorem
-- ════════════════════════════════════════════════════════════════════

/-- **Routing Soundness Theorem**

    Let vc be a verification condition with fragments frags, and suppose
    the split (frags from vc) is sound.  If all fragments are valid, then
    the original VC is valid.

    This is the central soundness result: theory-aware dispatch of fragments
    to specialised backends, followed by reassembly, preserves validity
    of the original mixed condition. -/
theorem routingSoundness
    (validFrag : LogicalFragment → Prop)
    (validVC   : Prop)
    (vc        : VerificationCondition)
    (split     : SoundSplit validFrag validVC vc.fragments)
    (hAll      : allFragmentsValid validFrag vc.fragments) :
    validVC :=
  split hAll

-- ════════════════════════════════════════════════════════════════════
-- § 10  Trust-Meet Reassembly
-- ════════════════════════════════════════════════════════════════════

/-- The meet (minimum) of a list of trust levels.
    The meet of an empty list is PROOF (top of the lattice). -/
def trustMeet : List TrustLevel → TrustLevel
  | []      => PROOF
  | t :: ts => min t (trustMeet ts)

/-- The trust meet is a lower bound for every element. -/
theorem trustMeet_le_all (ts : List TrustLevel) :
    ∀ t ∈ ts, trustMeet ts ≤ t := by
  induction ts with
  | nil  => intro t ht; exact absurd ht (List.not_mem_nil t)
  | cons hd tl ih =>
    intro t ht
    cases ht with
    | head =>
      show min hd (trustMeet tl) ≤ hd
      exact Nat.min_le_left _ _
    | tail _ hmem =>
      show min hd (trustMeet tl) ≤ t
      exact Nat.le_trans (Nat.min_le_right _ _) (ih t hmem)

/-- The bundle trust tier is the meet of all item trust tiers. -/
def bundleTrust (items : List EvidenceItem) : TrustLevel :=
  trustMeet (items.map (·.trustTier))

/-- **Trust Reassembly Theorem**

    The bundle trust tier (the meet of all item trust tiers) is ≤ every
    individual item's trust tier.  No reassembly can inflate trust above
    what any single contributing item warrants. -/
theorem trustReassembly (items : List EvidenceItem) :
    ∀ item ∈ items, bundleTrust items ≤ item.trustTier := by
  intro item hitem
  unfold bundleTrust
  apply trustMeet_le_all
  exact List.mem_map_of_mem (·.trustTier) hitem

-- ════════════════════════════════════════════════════════════════════
-- § 11  No Silent Promotion Corollary
-- ════════════════════════════════════════════════════════════════════

/-- **No Silent Promotion**

    The bundle trust tier does not exceed any item's channel ceiling.
    Mixed evidence routing cannot inflate trust beyond the minimum
    ceiling of the participating backends. -/
theorem noSilentPromotion (items : List EvidenceItem) :
    ∀ item ∈ items,
      bundleTrust items ≤ item.fragment.channel.trustCeiling := by
  intro item hitem
  exact Nat.le_trans
    (trustReassembly items item hitem)
    (item_trust_le_ceiling item)

-- ════════════════════════════════════════════════════════════════════
-- § 12  Channel Soundness Predicate and Preservation
-- ════════════════════════════════════════════════════════════════════

/-- A channel is sound if every evidence item produced through it has
    trust tier ≤ the channel's ceiling.  (This is already guaranteed by
    EvidenceItem.trustBounded; this predicate makes the property explicit.) -/
def channelSound (ch : EvidenceChannel) : Prop :=
  ∀ item : EvidenceItem, item.fragment.channel = ch →
    item.trustTier ≤ ch.trustCeiling

/-- Every EvidenceItem is produced by a sound channel: the trustBounded
    field guarantees channel soundness structurally. -/
theorem all_channels_sound (ch : EvidenceChannel) :
    channelSound ch := by
  intro item hch
  rw [← hch]
  exact item.trustBounded

-- ════════════════════════════════════════════════════════════════════
-- § 13  Dispatch Completeness (every fragment gets an item)
-- ════════════════════════════════════════════════════════════════════

/-- A dispatch is complete if every fragment in the VC has a corresponding
    evidence item in the result list. -/
def dispatchComplete
    (vc    : VerificationCondition)
    (items : List EvidenceItem) : Prop :=
  ∀ f ∈ vc.fragments,
    ∃ item ∈ items, item.fragment = f

/-- Every evidence channel has trust ceiling ≤ PROOF. -/
theorem channel_ceiling_le_proof (ch : EvidenceChannel) :
    ch.trustCeiling ≤ PROOF := by
  cases ch <;> decide

/-- Every evidence item has trust tier ≤ PROOF. -/
theorem item_trust_le_proof (item : EvidenceItem) :
    item.trustTier ≤ PROOF :=
  Nat.le_trans item.trustBounded (channel_ceiling_le_proof item.fragment.channel)

/-- The bundle trust tier of a singleton list equals the item's trust tier.
    Requires trust tiers ≤ PROOF (which holds for all EvidenceItems). -/
theorem bundleTrust_singleton (item : EvidenceItem) :
    bundleTrust [item] = item.trustTier := by
  have h := item_trust_le_proof item
  show min item.trustTier PROOF = item.trustTier
  exact Nat.min_eq_left h

/-- The bundle trust tier of two items is ≤ the first item's trust tier. -/
theorem bundleTrust_pair_le_left (i1 i2 : EvidenceItem) :
    bundleTrust [i1, i2] ≤ i1.trustTier := by
  show min i1.trustTier (min i2.trustTier PROOF) ≤ i1.trustTier
  exact Nat.min_le_left _ _

/-- The bundle trust tier of two items is ≤ the second item's trust tier. -/
theorem bundleTrust_pair_le_right (i1 i2 : EvidenceItem) :
    bundleTrust [i1, i2] ≤ i2.trustTier := by
  show min i1.trustTier (min i2.trustTier PROOF) ≤ i2.trustTier
  exact Nat.le_trans (Nat.min_le_right _ _) (Nat.min_le_left _ _)

-- ════════════════════════════════════════════════════════════════════
-- § 14  Routing with Full Soundness and Trust Bound
-- ════════════════════════════════════════════════════════════════════

/-- A routing result packages a VC, its dispatched items, and the proof that:
    (a) the split is sound, and (b) dispatch is complete. -/
structure RoutingResult where
  vc        : VerificationCondition
  items     : List EvidenceItem
  validFrag : LogicalFragment → Prop
  validVC   : Prop
  splitSnd  : SoundSplit validFrag validVC vc.fragments
  dispComp  : dispatchComplete vc items

/-- Given a RoutingResult where all fragments are valid, the VC is valid
    and the bundle trust tier bounds every item. -/
theorem routingResult_sound_and_trust
    (r    : RoutingResult)
    (hAll : allFragmentsValid r.validFrag r.vc.fragments) :
    r.validVC ∧ (∀ item ∈ r.items, bundleTrust r.items ≤ item.trustTier) :=
  ⟨routingSoundness r.validFrag r.validVC r.vc r.splitSnd hAll,
   trustReassembly r.items⟩

end JudgmentGeometry.Paper23
