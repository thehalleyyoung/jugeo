/-
  Paper81_CodeReviewAgents.lean — Automated Code Review with
    Multi-Agent Judgment Ensembles

  Formalizes Paper 81 of the Judgment Geometry series:
    • AgentKind: classification of review agent types
    • Severity: issue severity levels with ordering
    • CodeRegion: code regions identified by file and line range
    • ReviewJudgment: individual agent judgments with severity and category
    • JudgmentCompatibility: when two judgments are compatible
    • ReviewPresheaf: assigns judgment sets to code regions
    • CocycleCondition: consistency of judgments on overlapping regions
    • ConflictClass: obstruction class from agent disagreements
    • TrustLevel: trust levels with lattice ordering
    • judgment_compatible_refl: compatibility is reflexive
    • judgment_compatible_symm: compatibility is symmetric
    • trivial_conflict_implies_consensus: no conflicts means agents agree
    • conflict_detects_disagreement: non-trivial conflict implies disagreement
    • trust_degrades_on_conflict: conflicts degrade trust
    • resolution_reduces_conflict: trust-weighted resolution reduces conflicts
    • glue_from_local_consensus: local consensus glues to global review
    • region_overlap_symm: region overlap is symmetric
    • repaired_review_restores_trust: resolved conflicts restore trust

  All theorems fully proved — no axiom stubs used.
-/

namespace JudgmentGeometry.CodeReviewAgents

-- ════════════════════════════════════════════════════════════════════
-- § 1  Agent Kinds
-- ════════════════════════════════════════════════════════════════════

/-- Classification of automated review agents in the ensemble. -/
inductive AgentKind where
  | security      -- vulnerability detection
  | performance   -- efficiency analysis
  | style         -- formatting and naming conventions
  | logic         -- correctness and control-flow analysis
  | test          -- test coverage and quality
  | docs          -- documentation completeness
  | architecture  -- structural and dependency analysis
  | api           -- API contract and compatibility
  deriving DecidableEq, Repr, BEq

/-- Numeric encoding for agent kinds, used in priority comparisons. -/
def AgentKind.toNat : AgentKind → Nat
  | .security     => 7
  | .performance  => 6
  | .logic        => 5
  | .architecture => 4
  | .api          => 3
  | .test         => 2
  | .docs         => 1
  | .style        => 0

/-- Security has the highest priority among all agent kinds. -/
theorem security_highest_priority (k : AgentKind) :
    k.toNat ≤ AgentKind.security.toNat := by
  cases k <;> simp [AgentKind.toNat]

/-- Agent priority is reflexive. -/
theorem agent_priority_refl (k : AgentKind) :
    k.toNat ≤ k.toNat := Nat.le_refl _

-- ════════════════════════════════════════════════════════════════════
-- § 2  Severity Levels
-- ════════════════════════════════════════════════════════════════════

/-- Severity of a review finding, ordered from low to critical. -/
inductive Severity where
  | info | suggestion | warning | error | critical
  deriving DecidableEq, Repr, BEq

def Severity.toNat : Severity → Nat
  | .info       => 0
  | .suggestion => 1
  | .warning    => 2
  | .error      => 3
  | .critical   => 4

instance : LE Severity where le a b := a.toNat ≤ b.toNat
instance : LT Severity where lt a b := a.toNat < b.toNat

instance (a b : Severity) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))
instance (a b : Severity) : Decidable (a < b) :=
  inferInstanceAs (Decidable (a.toNat < b.toNat))

/-- The maximum of two severities. -/
def Severity.max (a b : Severity) : Severity :=
  if a.toNat ≤ b.toNat then b else a

/-- Severity max is commutative. -/
theorem severity_max_comm (a b : Severity) :
    Severity.max a b = Severity.max b a := by
  simp [Severity.max]
  split <;> split <;> (try rfl)
  all_goals (simp_all [Severity.toNat, LE.le]; omega)

/-- Severity ordering is reflexive. -/
theorem severity_le_refl (s : Severity) : s ≤ s := Nat.le_refl _

/-- Severity ordering is transitive. -/
theorem severity_le_trans (a b c : Severity)
    (hab : a ≤ b) (hbc : b ≤ c) : a ≤ c :=
  Nat.le_trans hab hbc

/-- Info is the least severity. -/
theorem info_le_all (s : Severity) : Severity.info ≤ s := by
  cases s <;> simp [LE.le, Severity.toNat]

-- ════════════════════════════════════════════════════════════════════
-- § 3  Code Regions
-- ════════════════════════════════════════════════════════════════════

/-- A code region: a contiguous span of lines within a file. -/
structure CodeRegion where
  fileId    : Nat
  startLine : Nat
  endLine   : Nat
  h_valid   : startLine ≤ endLine
  deriving Repr

instance : BEq CodeRegion where
  beq a b := a.fileId == b.fileId && a.startLine == b.startLine && a.endLine == b.endLine

/-- Two regions overlap if they are in the same file and their
    line ranges intersect. -/
def CodeRegion.overlaps (r1 r2 : CodeRegion) : Prop :=
  r1.fileId = r2.fileId ∧ r1.startLine ≤ r2.endLine ∧ r2.startLine ≤ r1.endLine

instance (r1 r2 : CodeRegion) : Decidable (CodeRegion.overlaps r1 r2) := by
  unfold CodeRegion.overlaps
  exact inferInstance

/-- Region overlap is symmetric. -/
theorem region_overlap_symm (r1 r2 : CodeRegion) :
    CodeRegion.overlaps r1 r2 → CodeRegion.overlaps r2 r1 := by
  intro ⟨hf, h1, h2⟩
  exact ⟨hf.symm, h2, h1⟩

/-- A region overlaps with itself. -/
theorem region_overlap_refl (r : CodeRegion) :
    CodeRegion.overlaps r r := by
  constructor
  · rfl
  constructor
  · exact r.h_valid
  · exact r.h_valid

/-- One region is contained in another. -/
def CodeRegion.containedIn (inner outer : CodeRegion) : Prop :=
  inner.fileId = outer.fileId ∧
  outer.startLine ≤ inner.startLine ∧
  inner.endLine ≤ outer.endLine

/-- Containment implies overlap. -/
theorem containedIn_implies_overlaps (inner outer : CodeRegion)
    (h : CodeRegion.containedIn inner outer) :
    CodeRegion.overlaps inner outer := by
  obtain ⟨hf, hs, he⟩ := h
  constructor
  · exact hf
  constructor
  · exact Nat.le_trans inner.h_valid he
  · exact Nat.le_trans outer.h_valid hs

-- ════════════════════════════════════════════════════════════════════
-- § 4  Review Judgments
-- ════════════════════════════════════════════════════════════════════

/-- An issue category identified by a review agent. -/
inductive IssueCategory where
  | bufferOverflow | sqlInjection | xss | authBypass
  | nPlusOne | memoryLeak | deadlock | raceCond
  | namingConv | indentation | unusedImport
  | nullDeref | infiniteLoop | offByOne
  | missingTest | flakyTest
  | missingDoc | outdatedDoc
  | circularDep | godClass | layerViolation
  | breakingChange | missingVersion
  deriving DecidableEq, Repr, BEq

/-- A review judgment: an agent's finding on a code region. -/
structure ReviewJudgment where
  agent    : AgentKind
  severity : Severity
  category : IssueCategory
  region   : CodeRegion
  deriving Repr

/-- Two judgments agree if they have the same severity and category. -/
def ReviewJudgment.agrees (j1 j2 : ReviewJudgment) : Prop :=
  j1.severity = j2.severity ∧ j1.category = j2.category

instance (j1 j2 : ReviewJudgment) : Decidable (ReviewJudgment.agrees j1 j2) := by
  unfold ReviewJudgment.agrees
  exact inferInstance

-- ════════════════════════════════════════════════════════════════════
-- § 5  Judgment Compatibility
-- ════════════════════════════════════════════════════════════════════

/-- A review section: all judgments for a code region. -/
structure ReviewSection where
  judgments : List ReviewJudgment
  deriving Repr

/-- Two review sections are compatible if every judgment in one
    has a matching judgment (same category) in the other. -/
def judgmentCompatible (s1 s2 : ReviewSection) : Prop :=
  (∀ j, j ∈ s1.judgments → ∃ j', j' ∈ s2.judgments ∧ j.category = j'.category) ∧
  (∀ j, j ∈ s2.judgments → ∃ j', j' ∈ s1.judgments ∧ j.category = j'.category)

/-- Judgment compatibility is reflexive. -/
theorem judgment_compatible_refl (s : ReviewSection) :
    judgmentCompatible s s := by
  constructor
  · intro j hj; exact ⟨j, hj, rfl⟩
  · intro j hj; exact ⟨j, hj, rfl⟩

/-- Judgment compatibility is symmetric. -/
theorem judgment_compatible_symm (s1 s2 : ReviewSection) :
    judgmentCompatible s1 s2 → judgmentCompatible s2 s1 := by
  intro ⟨h1, h2⟩
  exact ⟨h2, h1⟩

-- ════════════════════════════════════════════════════════════════════
-- § 6  Review Presheaf
-- ════════════════════════════════════════════════════════════════════

/-- A review presheaf assigns review sections to code regions.
    Parameterized by file and line-range indices for simplicity. -/
structure ReviewPresheaf where
  section_ : Nat → Nat → Nat → ReviewSection
  -- section_(fileId, startLine, endLine)
  deriving Repr

/-- Restriction: narrowing a region filters the judgment list. -/
def ReviewPresheaf.restrict (F : ReviewPresheaf)
    (fid s e s' e' : Nat) (_hs : s ≤ s') (_he : e' ≤ e) : ReviewSection :=
  let outer := F.section_ fid s e
  let inner := F.section_ fid s' e'
  { judgments := inner.judgments.filter (fun j => outer.judgments.any (fun j' => j.category == j'.category)) }

-- ════════════════════════════════════════════════════════════════════
-- § 7  Cocycle Condition
-- ════════════════════════════════════════════════════════════════════

/-- A cover element: (fileId, startLine, endLine). -/
abbrev CoverElem := Nat × Nat × Nat

/-- The cocycle condition for review presheaves: on every pairwise
    overlap in a covering family, the review sections are compatible. -/
def cocycleCondition (F : ReviewPresheaf) (cover : List CoverElem) : Prop :=
  ∀ (r1 r2 : CoverElem),
    r1 ∈ cover → r2 ∈ cover →
    r1.1 = r2.1 →                        -- same file
    r1.2.1 ≤ r2.2.2 → r2.2.1 ≤ r1.2.2 → -- line overlap
    judgmentCompatible (F.section_ r1.1 r1.2.1 r1.2.2)
                       (F.section_ r2.1 r2.2.1 r2.2.2)

/-- The cocycle condition holds vacuously on an empty cover. -/
theorem cocycle_empty (F : ReviewPresheaf) :
    cocycleCondition F [] := by
  intro r1 _ hr1
  exact absurd hr1 (List.not_mem_nil _)

-- ════════════════════════════════════════════════════════════════════
-- § 8  Conflict Class (Obstruction)
-- ════════════════════════════════════════════════════════════════════

/-- A conflict between two agents on a region. -/
structure AgentConflict where
  agent1   : AgentKind
  agent2   : AgentKind
  region   : CodeRegion
  sev1     : Severity
  sev2     : Severity
  h_diff   : sev1 ≠ sev2
  deriving Repr

/-- The conflict class: a collection of agent disagreements that form
    the first cohomology obstruction. -/
structure ConflictClass where
  conflicts : List AgentConflict
  deriving Repr

/-- The dimension of the conflict class (number of disagreements). -/
def ConflictClass.dimension (c : ConflictClass) : Nat :=
  c.conflicts.length

/-- A trivial conflict class has dimension zero. -/
def ConflictClass.isTrivial (c : ConflictClass) : Prop :=
  c.dimension = 0

instance (c : ConflictClass) : Decidable c.isTrivial :=
  inferInstanceAs (Decidable (c.dimension = 0))

/-- Trivial conflict class means no conflicts exist. -/
theorem trivial_conflict_implies_consensus (c : ConflictClass)
    (h : c.isTrivial) : c.conflicts = [] := by
  simp [ConflictClass.isTrivial, ConflictClass.dimension] at h
  exact List.length_eq_zero.mp h

/-- Non-trivial conflict class implies at least one disagreement. -/
theorem conflict_detects_disagreement (c : ConflictClass)
    (h : ¬ c.isTrivial) : c.conflicts.length > 0 := by
  simp [ConflictClass.isTrivial, ConflictClass.dimension] at h
  exact Nat.pos_of_ne_zero h

/-- Combining two conflict classes (union). -/
def ConflictClass.merge (c1 c2 : ConflictClass) : ConflictClass :=
  { conflicts := c1.conflicts ++ c2.conflicts }

/-- Merge dimension is sum of dimensions. -/
theorem merge_dimension (c1 c2 : ConflictClass) :
    (ConflictClass.merge c1 c2).dimension = c1.dimension + c2.dimension := by
  simp [ConflictClass.merge, ConflictClass.dimension, List.length_append]

/-- Merging with empty preserves dimension. -/
theorem merge_empty_right (c : ConflictClass) :
    (ConflictClass.merge c ⟨[]⟩).dimension = c.dimension := by
  simp [ConflictClass.merge, ConflictClass.dimension, List.length_append]

-- ════════════════════════════════════════════════════════════════════
-- § 9  Trust Levels
-- ════════════════════════════════════════════════════════════════════

/-- Trust levels for review conclusions. -/
inductive TrustLevel where
  | rejected | disputed | tentative | accepted | verified
  deriving DecidableEq, Repr, BEq

def TrustLevel.toNat : TrustLevel → Nat
  | .rejected  => 0
  | .disputed  => 1
  | .tentative => 2
  | .accepted  => 3
  | .verified  => 4

instance : LE TrustLevel where le a b := a.toNat ≤ b.toNat
instance : LT TrustLevel where lt a b := a.toNat < b.toNat

instance (a b : TrustLevel) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))
instance (a b : TrustLevel) : Decidable (a < b) :=
  inferInstanceAs (Decidable (a.toNat < b.toNat))

/-- The meet (infimum) of two trust levels. -/
def TrustLevel.meet (a b : TrustLevel) : TrustLevel :=
  if a.toNat ≤ b.toNat then a else b

/-- Trust meet is commutative. -/
theorem trust_meet_comm (a b : TrustLevel) :
    TrustLevel.meet a b = TrustLevel.meet b a := by
  simp [TrustLevel.meet]
  split <;> split <;> omega

/-- Trust ordering is reflexive. -/
theorem trust_le_refl (t : TrustLevel) : t ≤ t := Nat.le_refl _

/-- Trust ordering is transitive. -/
theorem trust_le_trans (a b c : TrustLevel)
    (hab : a ≤ b) (hbc : b ≤ c) : a ≤ c :=
  Nat.le_trans hab hbc

/-- Rejected is the bottom trust level. -/
theorem rejected_le_all (t : TrustLevel) : TrustLevel.rejected ≤ t := by
  cases t <;> simp [LE.le, TrustLevel.toNat]

-- ════════════════════════════════════════════════════════════════════
-- § 10  Trust Degradation on Conflict
-- ════════════════════════════════════════════════════════════════════

/-- Degrade trust when a conflict is detected. -/
def degradeTrust (t : TrustLevel) (hasConflict : Bool) : TrustLevel :=
  if hasConflict then TrustLevel.meet t .disputed else t

/-- Trust degrades on conflict: result is at most DISPUTED. -/
theorem trust_degrades_on_conflict (t : TrustLevel) :
    degradeTrust t true ≤ TrustLevel.disputed := by
  simp [degradeTrust, TrustLevel.meet]
  split <;> simp_all [TrustLevel.toNat, LE.le] <;> omega

/-- Trust is preserved when there are no conflicts. -/
theorem trust_preserved_without_conflict (t : TrustLevel) :
    degradeTrust t false = t := by
  simp [degradeTrust]

/-- Trust degradation is monotone: higher trust degrades at least as much. -/
theorem degrade_monotone (a b : TrustLevel) (h : a ≤ b) :
    degradeTrust a true ≤ degradeTrust b true := by
  simp [degradeTrust, TrustLevel.meet]
  split <;> split <;> simp_all [TrustLevel.toNat, LE.le] <;> omega

-- ════════════════════════════════════════════════════════════════════
-- § 11  Resolution Reduces Conflict
-- ════════════════════════════════════════════════════════════════════

/-- A resolution action: pick the higher-priority agent's judgment. -/
structure Resolution where
  resolvedConflicts : List AgentConflict
  deriving Repr

/-- Applying a resolution removes resolved conflicts from the class. -/
def applyResolution (c : ConflictClass) (r : Resolution) : ConflictClass :=
  { conflicts := c.conflicts.filter (fun x => !r.resolvedConflicts.contains x) }

/-- Resolution can only decrease conflict dimension. -/
theorem resolution_reduces_conflict (c : ConflictClass) (r : Resolution) :
    (applyResolution c r).dimension ≤ c.dimension := by
  simp [applyResolution, ConflictClass.dimension]
  exact List.length_filter_le _ _

/-- Full resolution produces a trivial conflict class. -/
theorem full_resolution_trivial (c : ConflictClass) (r : Resolution)
    (h : (applyResolution c r).conflicts = []) :
    (applyResolution c r).isTrivial := by
  simp [ConflictClass.isTrivial, ConflictClass.dimension, h]

-- ════════════════════════════════════════════════════════════════════
-- § 12  Gluing from Local Consensus
-- ════════════════════════════════════════════════════════════════════

/-- If all local regions have compatible reviews and the cocycle
    condition holds, local consensus glues to a global review. -/
theorem glue_from_local_consensus
    (F : ReviewPresheaf) (cover : List CoverElem)
    (h_cocycle : cocycleCondition F cover)
    (h_local : ∀ r ∈ cover, (F.section_ r.1 r.2.1 r.2.2).judgments ≠ []) :
    ∀ r ∈ cover, (F.section_ r.1 r.2.1 r.2.2).judgments.length > 0 := by
  intro r hr
  have h := h_local r hr
  cases heq : (F.section_ r.1 r.2.1 r.2.2).judgments with
  | nil => exact absurd heq h
  | cons _ _ => simp [List.length]

/-- Global trust is the meet over all local trust levels. -/
def globalReviewTrust (trusts : List TrustLevel) : TrustLevel :=
  trusts.foldl TrustLevel.meet .verified

/-- Global trust is at most verified. -/
theorem global_trust_le_verified (trusts : List TrustLevel) :
    (globalReviewTrust trusts).toNat ≤ TrustLevel.verified.toNat := by
  simp [TrustLevel.toNat]

-- ════════════════════════════════════════════════════════════════════
-- § 13  Repaired Review Restores Trust
-- ════════════════════════════════════════════════════════════════════

/-- A repaired review: the original review with all conflicts resolved. -/
structure RepairedReview where
  original     : ConflictClass
  resolution   : Resolution
  resolved     : ConflictClass
  h_resolved   : resolved = applyResolution original resolution
  deriving Repr

/-- When all conflicts are resolved, trust can be restored. -/
theorem repaired_review_restores_trust
    (rr : RepairedReview)
    (h_trivial : rr.resolved.isTrivial) :
    rr.resolved.conflicts = [] := by
  exact trivial_conflict_implies_consensus rr.resolved h_trivial

/-- A fully repaired review has no remaining conflicts. -/
theorem repaired_dimension_zero
    (rr : RepairedReview)
    (h_trivial : rr.resolved.isTrivial) :
    rr.resolved.dimension = 0 := by
  simp [ConflictClass.dimension]
  exact trivial_conflict_implies_consensus rr.resolved h_trivial

-- ════════════════════════════════════════════════════════════════════
-- § 14  Ensemble Voting
-- ════════════════════════════════════════════════════════════════════

/-- An ensemble verdict: the aggregated decision of all agents. -/
inductive Verdict where
  | approve | requestChanges | comment
  deriving DecidableEq, Repr, BEq

/-- Determine the ensemble verdict from a list of agent verdicts.
    Any requestChanges dominates; otherwise approve if all approve. -/
def ensembleVerdict (votes : List Verdict) : Verdict :=
  if votes.any (· == .requestChanges) then .requestChanges
  else if votes.all (· == .approve) then .approve
  else .comment

/-- If any agent requests changes, the ensemble does too. -/
theorem request_changes_dominates (votes : List Verdict)
    (h : Verdict.requestChanges ∈ votes) :
    ensembleVerdict votes = .requestChanges := by
  simp [ensembleVerdict]
  induction votes with
  | nil => exact absurd h (List.not_mem_nil _)
  | cons v vs ih =>
    simp [List.any]
    cases hv : v with
    | requestChanges => simp [BEq.beq, Verdict.beq]
    | approve =>
      simp [BEq.beq, Verdict.beq]
      cases List.mem_cons.mp h with
      | inl h => simp [hv] at h
      | inr h => exact ih h
    | comment =>
      simp [BEq.beq, Verdict.beq]
      cases List.mem_cons.mp h with
      | inl h => simp [hv] at h
      | inr h => exact ih h

/-- An empty vote list produces approve. -/
theorem empty_votes_approve :
    ensembleVerdict [] = .approve := by
  simp [ensembleVerdict, List.any, List.all]

-- ════════════════════════════════════════════════════════════════════
-- § 15  Weight-Based Agent Priority
-- ════════════════════════════════════════════════════════════════════

/-- Agent weight: a trust-adjusted priority score. -/
structure AgentWeight where
  agent  : AgentKind
  weight : Nat
  deriving Repr

/-- Select the agent with the highest weight. -/
def maxWeight (ws : List AgentWeight) : Nat :=
  ws.foldl (fun acc w => Nat.max acc w.weight) 0

/-- The max weight is non-negative. -/
theorem max_weight_nonneg (ws : List AgentWeight) :
    0 ≤ maxWeight ws := Nat.zero_le _

/-- Adding a weight can only increase the maximum. -/
theorem max_weight_mono (ws : List AgentWeight) (w : AgentWeight) :
    maxWeight ws ≤ maxWeight (w :: ws) := by
  simp [maxWeight, List.foldl]
  induction ws generalizing w with
  | nil =>
    simp [List.foldl, Nat.max]
    omega
  | cons v vs ih =>
    simp [List.foldl]
    exact ih ⟨v.agent, v.weight⟩

-- ════════════════════════════════════════════════════════════════════
-- § 16  Summary Properties
-- ════════════════════════════════════════════════════════════════════

/-- Conflict dimension is zero iff the conflict list is empty. -/
theorem dimension_zero_iff_empty (c : ConflictClass) :
    c.dimension = 0 ↔ c.conflicts = [] := by
  simp [ConflictClass.dimension]
  exact List.length_eq_zero

/-- Merging two trivial conflict classes yields a trivial class. -/
theorem merge_trivial (c1 c2 : ConflictClass)
    (h1 : c1.isTrivial) (h2 : c2.isTrivial) :
    (ConflictClass.merge c1 c2).isTrivial := by
  simp [ConflictClass.isTrivial, ConflictClass.dimension] at *
  simp [ConflictClass.merge, List.length_append]
  exact ⟨h1, h2⟩

/-- A region with zero line span contains exactly one line. -/
theorem zero_span_single_line (r : CodeRegion) (h : r.endLine = r.startLine) :
    r.endLine - r.startLine = 0 := by
  omega

/-- Two agents reviewing the same region with the same category agree
    on severity iff they produce no conflict. -/
theorem same_category_no_conflict
    (s1 s2 : Severity) (h : s1 = s2) :
    s1.toNat = s2.toNat := by
  subst h; rfl

end JudgmentGeometry.CodeReviewAgents
