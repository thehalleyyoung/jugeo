/-
  Paper87_CopilotDeduction.lean — Copilot-Assisted Deduction

  Formalizes the core structures from Paper 87:
    • Rule schemas and deduction rules
    • Suggestion outcomes and scoring
    • Cache operations and proof-state monotonicity
    • Theorems: soundness_preservation, suggestion_relevance,
      cache_consistency, feedback_convergence
-/

namespace JudgmentGeometry.CopilotDeduction

-- ════════════════════════════════════════════════════════════════════
-- § 1  Core Types
-- ════════════════════════════════════════════════════════════════════

/-- Judgment identifiers are natural numbers for simplicity. -/
abbrev JudgmentId := Nat

/-- A rule schema: a named template with a premise count and conclusion. -/
structure RuleSchema where
  name         : String
  premiseCount : Nat
  conclusionId : JudgmentId
  deriving Repr, BEq, DecidableEq

/-- A validated deduction rule carries a schema and a priority for ranking. -/
structure DeductionRule where
  schema   : RuleSchema
  priority : Nat
  deriving Repr

/-- Outcome of a Copilot rule suggestion. -/
inductive SuggestionOutcome where
  | accepted
  | rejected
  | timeout
  deriving DecidableEq, Repr, BEq

-- ════════════════════════════════════════════════════════════════════
-- § 2  Proof State and Rule Application
-- ════════════════════════════════════════════════════════════════════

/-- A proof state: established judgment IDs and the current goal. -/
structure ProofState where
  established : List JudgmentId
  goal        : JudgmentId
  deriving Repr

/-- A proof is complete when the goal appears among established judgments. -/
def ProofState.isComplete (ps : ProofState) : Prop :=
  ps.goal ∈ ps.established

/-- Predicate: a rule can be applied when the supplied premise IDs match
    the schema's arity and every premise is already established. -/
def canApply (ps : ProofState) (rule : RuleSchema)
    (premises : List JudgmentId) : Prop :=
  premises.length = rule.premiseCount ∧ ∀ p ∈ premises, p ∈ ps.established

/-- Apply a rule: extend the established set with the rule's conclusion.
    Requires a proof that the rule is applicable. -/
def applyRule (ps : ProofState) (rule : RuleSchema)
    (premises : List JudgmentId) (_ : canApply ps rule premises) : ProofState :=
  { ps with established := rule.conclusionId :: ps.established }

-- ════════════════════════════════════════════════════════════════════
-- § 3  Suggestion Scoring
-- ════════════════════════════════════════════════════════════════════

/-- Relevance score: 100 when the conclusion matches the goal, else 0. -/
def relevanceScore (rule : RuleSchema) (goalId : JudgmentId) : Nat :=
  if rule.conclusionId = goalId then 100 else 0

/-- Maximum relevance constant, for normalization. -/
def maxRelevance : Nat := 100

/-- Combined score: relevance plus priority, capped at maxRelevance. -/
def combinedScore (rule : DeductionRule) (goalId : JudgmentId) : Nat :=
  min (relevanceScore rule.schema goalId + rule.priority) maxRelevance

/-- Update a running score based on user feedback. -/
def updateScore (score : Nat) (outcome : SuggestionOutcome) : Nat :=
  match outcome with
  | .accepted => score + 10
  | .rejected => score - min score 5
  | .timeout  => score

-- ════════════════════════════════════════════════════════════════════
-- § 4  Cache Model
-- ════════════════════════════════════════════════════════════════════

/-- Association-list cache parameterised by key and value types. -/
def Cache (κ α : Type) := List (κ × α)

instance : EmptyCollection (Cache κ α) := ⟨[]⟩

/-- Look up the first entry matching *key*. -/
def Cache.lookup [DecidableEq κ] : Cache κ α → κ → Option α
  | [],            _   => none
  | (k, v) :: rest, key => if k = key then some v else Cache.lookup rest key

/-- Insert a new entry at the front of the cache. -/
def Cache.insert (c : Cache κ α) (key : κ) (val : α) : Cache κ α :=
  (key, val) :: c

/-- Number of entries in the cache. -/
def Cache.size (c : Cache κ α) : Nat := c.length

-- ════════════════════════════════════════════════════════════════════
-- § 5  Interaction Log
-- ════════════════════════════════════════════════════════════════════

/-- An interaction record for the audit log. -/
structure InteractionRecord where
  kind      : String
  ruleUsed  : Option RuleSchema
  outcome   : SuggestionOutcome
  deriving Repr

/-- The interaction log is simply a list of records. -/
def InteractionLog := List InteractionRecord

/-- Append a record to the log. -/
def InteractionLog.record (log : InteractionLog)
    (rec : InteractionRecord) : InteractionLog :=
  log ++ [rec]

/-- Count records of a given kind. -/
def InteractionLog.countKind (log : InteractionLog) (k : String) : Nat :=
  log.filter (fun r => r.kind == k) |>.length

-- ════════════════════════════════════════════════════════════════════
-- § 6  Theorems
-- ════════════════════════════════════════════════════════════════════

/-- **Soundness preservation**: applying a rule never removes any
    previously established judgment. -/
theorem soundness_preservation (ps : ProofState) (rule : RuleSchema)
    (premises : List JudgmentId) (h : canApply ps rule premises) :
    ∀ j, j ∈ ps.established → j ∈ (applyRule ps rule premises h).established := by
  intro j hj
  simp only [applyRule]
  exact List.mem_cons_of_mem _ hj

/-- **Suggestion relevance**: when a rule's conclusion matches the goal,
    its relevance score equals the maximum. -/
theorem suggestion_relevance (rule : RuleSchema) (goalId : JudgmentId)
    (h : rule.conclusionId = goalId) :
    relevanceScore rule goalId = maxRelevance := by
  simp only [relevanceScore, maxRelevance, h, ite_true]

/-- **Cache consistency**: looking up a key immediately after inserting it
    always returns the inserted value. -/
theorem cache_consistency [DecidableEq κ] (c : Cache κ α) (key : κ) (val : α) :
    Cache.lookup (Cache.insert c key val) key = some val := by
  simp only [Cache.insert, Cache.lookup, ite_true]

/-- **Feedback convergence** (accepted): accepting a suggestion always
    increases the running score. -/
theorem feedback_convergence (score : Nat) :
    updateScore score .accepted ≥ score := by
  simp only [updateScore]
  omega

/-- Applying a relevant rule with valid premises completes the proof. -/
theorem relevant_application_completes (ps : ProofState) (rule : RuleSchema)
    (premises : List JudgmentId) (h : canApply ps rule premises)
    (hrel : rule.conclusionId = ps.goal) :
    (applyRule ps rule premises h).isComplete := by
  simp only [applyRule, ProofState.isComplete]
  rw [hrel]
  exact List.mem_cons_self _ _

/-- Recording an interaction grows the log by exactly one entry. -/
theorem log_grows (log : InteractionLog) (rec : InteractionRecord) :
    (log.record rec).length = log.length + 1 := by
  simp [InteractionLog.record, List.length_append]

/-- The cache grows monotonically on insert. -/
theorem cache_grows [DecidableEq κ] (c : Cache κ α) (key : κ) (val : α) :
    Cache.size (Cache.insert c key val) = Cache.size c + 1 := by
  simp [Cache.insert, Cache.size, List.length]

/-- The combined score is bounded by maxRelevance. -/
theorem combined_score_bounded (rule : DeductionRule) (goalId : JudgmentId) :
    combinedScore rule goalId ≤ maxRelevance := by
  simp only [combinedScore, maxRelevance]
  exact Nat.min_le_right _ _

/-- Rejecting never increases the score. -/
theorem rejection_nonincreasing (score : Nat) :
    updateScore score .rejected ≤ score := by
  simp only [updateScore]
  omega

/-- Timeout leaves the score unchanged. -/
theorem timeout_stable (score : Nat) :
    updateScore score .timeout = score := by
  simp [updateScore]

/-- An empty cache has no entries. -/
theorem empty_cache_lookup [DecidableEq κ] (key : κ) :
    Cache.lookup (∅ : Cache κ α) key = none := by
  simp [Cache.lookup, EmptyCollection.emptyCollection]

end JudgmentGeometry.CopilotDeduction
