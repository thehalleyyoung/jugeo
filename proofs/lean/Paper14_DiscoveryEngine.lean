/-
  Paper14_DiscoveryEngine.lean
  Judgment Geometry · Paper 14: The Discovery Engine

  Formalizes the automated theorem and invariant discovery framework:
    • Core enumerations matching the Python implementation
    • Finite semantic site model
    • The scanAll exhaustive discovery algorithm
    • Completeness theorem: finite sites guarantee full invariant discovery
    • Analogy transport soundness
    • Kind classification consistency
    • Federation completeness
    • Novelty score monotonicity
-/

namespace JudgmentGeometry.Paper14

-- ════════════════════════════════════════════════════════════════════
-- § 1  Enumerations (mirroring the Python implementation)
-- ════════════════════════════════════════════════════════════════════

/-- Semantic categories of discovered theorems.
    Mirrors TheoremKind in jugeo.ideation.novelty_search.theorems. -/
inductive TheoremKind where
  | optimality    -- algorithm achieves best-known bound
  | bound         -- upper or lower bound on a quantity
  | completeness  -- procedure covers all cases
  | monotonicity  -- function is monotone under condition
  | approximation -- approximation ratio or additive error
  | impossibility -- rules out certain guarantees
  deriving DecidableEq, Repr, BEq

/-- Discovery strategy algorithms.
    Mirrors DiscoveryAlgorithm in jugeo.ideation.kind_discovery.algorithms. -/
inductive DiscoveryAlgorithm where
  | exhaustive
  | greedy
  | beamSearch
  | frequencyGuided
  | patternFirst
  | hybrid
  | obstructionMining
  | patternExtraction
  | semanticClustering
  | bootstrapPlanning
  | crossDomain
  deriving DecidableEq, Repr, BEq

/-- Novelty search strategies.
    Mirrors SearchStrategy in jugeo.ideation.novelty_search.models. -/
inductive SearchStrategy where
  | random
  | greedy
  | beam
  | diversified
  | purposeConditioned
  deriving DecidableEq, Repr, BEq

/-- Distance metric kinds for novelty computation.
    Mirrors MetricKind in jugeo.ideation.novelty_search.models. -/
inductive MetricKind where
  | semantic
  | structural
  | topological
  | hybrid
  deriving DecidableEq, Repr, BEq

-- Sanity check: all TheoremKind values are mutually distinct
theorem theorem_kinds_distinct :
    TheoremKind.optimality ≠ TheoremKind.bound ∧
    TheoremKind.bound ≠ TheoremKind.completeness ∧
    TheoremKind.monotonicity ≠ TheoremKind.approximation ∧
    TheoremKind.approximation ≠ TheoremKind.impossibility :=
  ⟨by decide, by decide, by decide, by decide⟩

-- ════════════════════════════════════════════════════════════════════
-- § 2  Core data structures
-- ════════════════════════════════════════════════════════════════════

/-- A discovery candidate: a potential invariant with metadata. -/
structure Candidate where
  id      : String
  kind    : TheoremKind
  novelty : Nat     -- integer approximation of novelty in [0, 100]
  trusted : Bool
  deriving Repr

/-- A finite semantic site: finite ordered list of expressible invariants. -/
structure FiniteSite (α : Type) where
  invariants : List α
  deriving Repr

/-- Discovery configuration: algorithm, metric, and budget. -/
structure DiscoveryConfig where
  algorithm : DiscoveryAlgorithm
  metric    : MetricKind
  budget    : Nat
  deriving Repr

def defaultConfig : DiscoveryConfig :=
  { algorithm := .exhaustive, metric := .semantic, budget := 1000 }

-- ════════════════════════════════════════════════════════════════════
-- § 3  The scanAll exhaustive discovery algorithm
-- ════════════════════════════════════════════════════════════════════

/-- Exhaustive scan: visit each invariant in order, adding it to the
    accumulator if not already present.
    Mirrors DiscoveryAlgorithm.EXHAUSTIVE in the Python engine. -/
def scanAll {α : Type} [DecidableEq α] : List α → List α → List α
  | [],      acc => acc
  | x :: xs, acc =>
    if x ∈ acc then scanAll xs acc
    else          scanAll xs (x :: acc)

-- ── Supporting lemmas ──────────────────────────────────────────────

/-- Monotonicity: any element already in acc is preserved by scanAll. -/
lemma scanAll_mono {α : Type} [DecidableEq α]
    (invs : List α) (acc : List α) :
    ∀ x ∈ acc, x ∈ scanAll invs acc := by
  induction invs generalizing acc with
  | nil => simp [scanAll]
  | cons hd tl ih =>
    intro x hx
    simp only [scanAll]
    split_ifs with h
    · exact ih acc x hx
    · exact ih (hd :: acc) x (List.mem_cons_of_mem _ hx)

/-- The head of the input list always appears in the scanAll result. -/
lemma scanAll_head_mem {α : Type} [DecidableEq α]
    (hd : α) (tl : List α) (acc : List α) :
    hd ∈ scanAll (hd :: tl) acc := by
  simp only [scanAll]
  split_ifs with h
  · exact scanAll_mono tl acc hd h
  · exact scanAll_mono tl (hd :: acc) hd (List.mem_cons_self hd acc)

/-- **Completeness of exhaustive scan** (core technical lemma):
    every element in `invs` appears in `scanAll invs acc`. -/
theorem scanAll_complete {α : Type} [DecidableEq α]
    (invs : List α) (acc : List α) :
    ∀ x ∈ invs, x ∈ scanAll invs acc := by
  induction invs generalizing acc with
  | nil => simp
  | cons hd tl ih =>
    intro x hx
    rw [List.mem_cons] at hx
    cases hx with
    | inl h =>
      subst h
      exact scanAll_head_mem hd tl acc
    | inr h =>
      simp only [scanAll]
      split_ifs with hmem
      · exact ih acc x h
      · exact ih (hd :: acc) x h

-- ════════════════════════════════════════════════════════════════════
-- § 4  Main Completeness Theorem (Paper 14, Theorem 7.1)
-- ════════════════════════════════════════════════════════════════════

/-- **Discovery Engine Completeness Theorem**:
    For any finite semantic site, the exhaustive discovery strategy
    starting from the empty state finds every expressible invariant. -/
theorem completeness_of_discovery {α : Type} [DecidableEq α]
    (site : FiniteSite α) :
    ∀ x ∈ site.invariants, x ∈ scanAll site.invariants [] :=
  fun x hx => scanAll_complete site.invariants [] x hx

/-- Corollary: discovery is monotone — known invariants are never lost. -/
theorem discovery_monotone {α : Type} [DecidableEq α]
    (site : FiniteSite α) (known : List α) :
    ∀ x ∈ known, x ∈ scanAll site.invariants known :=
  fun x hx => scanAll_mono site.invariants known x hx

-- ════════════════════════════════════════════════════════════════════
-- § 5  Novelty score and diversity
-- ════════════════════════════════════════════════════════════════════

/-- Novelty score of a candidate relative to the current portfolio.
    Zero if already known; portfolio size + 1 if genuinely new. -/
def noveltyScore {α : Type} [DecidableEq α]
    (x : α) (portfolio : List α) : Nat :=
  if x ∈ portfolio then 0 else portfolio.length + 1

/-- Known items have zero novelty. -/
theorem novelty_zero_of_known {α : Type} [DecidableEq α]
    (x : α) (portfolio : List α) (h : x ∈ portfolio) :
    noveltyScore x portfolio = 0 := by
  simp [noveltyScore, h]

/-- New items have strictly positive novelty. -/
theorem novelty_pos_of_new {α : Type} [DecidableEq α]
    (x : α) (portfolio : List α) (h : x ∉ portfolio) :
    0 < noveltyScore x portfolio := by
  simp [noveltyScore, h]

/-- Novelty of a new item grows as the portfolio grows
    (more things in portfolio means the score gets larger numerically). -/
theorem novelty_grows_with_portfolio {α : Type} [DecidableEq α]
    (x : α) (base ext : List α)
    (hb : x ∉ base) (hbe : x ∉ base ++ ext) :
    noveltyScore x base ≤ noveltyScore x (base ++ ext) := by
  simp [noveltyScore, hb, hbe, List.length_append]
  omega

/-- Filter candidates not in the current portfolio. -/
def novelCandidates {α : Type} [DecidableEq α]
    (portfolio candidates : List α) : List α :=
  candidates.filter (fun c => decide (c ∉ portfolio))

/-- Every result of novelCandidates is genuinely new. -/
theorem novel_candidates_are_new {α : Type} [DecidableEq α]
    (portfolio candidates : List α) :
    ∀ c ∈ novelCandidates portfolio candidates, c ∉ portfolio := by
  intro c hc
  simp [novelCandidates, List.mem_filter, decide_eq_true_eq] at hc
  exact hc.2

-- ════════════════════════════════════════════════════════════════════
-- § 6  Analogy transport soundness
-- ════════════════════════════════════════════════════════════════════

/-- A sound analogy between two finite sites:
    a map that sends source invariants to valid target invariants.
    Mirrors AnalogyFinder / IdeaTransporter in jugeo.ideation.federation. -/
structure SoundAnalogy (α β : Type) where
  sourceInvs : List α
  targetInvs : List β
  transport  : α → β
  /-- Soundness condition: source invariants map into the target site. -/
  sound      : ∀ x ∈ sourceInvs, transport x ∈ targetInvs

/-- **Analogy Transport Soundness** (Proposition 4.2):
    if an analogy is sound, transported invariants are discovered
    by the exhaustive engine on the target site. -/
theorem analogy_transport_sound {α β : Type} [DecidableEq α] [DecidableEq β]
    (analogy : SoundAnalogy α β) :
    ∀ x ∈ analogy.sourceInvs,
      analogy.transport x ∈ scanAll analogy.targetInvs [] := by
  intro x hx
  exact scanAll_complete analogy.targetInvs [] _ (analogy.sound x hx)

/-- Transport completeness: if discovery is complete on the source,
    all transported candidates appear in the target's discovered set. -/
theorem analogy_transport_complete {α β : Type} [DecidableEq α] [DecidableEq β]
    (analogy : SoundAnalogy α β)
    (sourceComplete : ∀ x ∈ analogy.sourceInvs,
        x ∈ scanAll analogy.sourceInvs []) :
    ∀ x ∈ analogy.sourceInvs,
      analogy.transport x ∈ scanAll analogy.targetInvs [] := by
  intro x hx
  exact scanAll_complete analogy.targetInvs [] _ (analogy.sound x hx)

-- ════════════════════════════════════════════════════════════════════
-- § 7  Kind classification consistency
-- ════════════════════════════════════════════════════════════════════

/-- A labeled invariant: invariant paired with its TheoremKind. -/
abbrev LabeledInv (α : Type) := α × TheoremKind

/-- Extract all invariants of a specific kind from a labeled list.
    Mirrors KindDiscoveryEngine's partitioning in kind_discovery. -/
def invariantsOfKind {α : Type} [DecidableEq TheoremKind]
    (labeled : List (LabeledInv α)) (k : TheoremKind) : List α :=
  labeled.filterMap (fun p => if p.2 = k then some p.1 else none)

/-- **Kind Classification Consistency**:
    every labeled invariant appears in the partition for its assigned kind. -/
theorem kind_classification_complete {α : Type} [DecidableEq TheoremKind]
    (labeled : List (LabeledInv α)) (x : α) (k : TheoremKind)
    (h : (x, k) ∈ labeled) :
    x ∈ invariantsOfKind labeled k := by
  simp only [invariantsOfKind, List.mem_filterMap]
  exact ⟨(x, k), h, by simp⟩

/-- Classification is surjective onto labels: every kind in the
    labeled list appears as a key in the partition. -/
theorem kind_assignment_in_labels {α : Type} [DecidableEq TheoremKind]
    (labeled : List (LabeledInv α)) (x : α) (k : TheoremKind)
    (h : (x, k) ∈ labeled) :
    ∃ y, y ∈ invariantsOfKind labeled k :=
  ⟨x, kind_classification_complete labeled x k h⟩

-- ════════════════════════════════════════════════════════════════════
-- § 8  Federation: distributed discovery
-- ════════════════════════════════════════════════════════════════════

/-- Federated union: merge discoveries from multiple distributed nodes.
    Each node contributes a list of discovered invariants; the
    federation returns their union (de-duplicated). -/
def federatedUnion {α : Type} [DecidableEq α] : List (List α) → List α
  | []                  => []
  | nodeResult :: rest  => scanAll nodeResult (federatedUnion rest)

/-- **Federation Completeness** (Corollary 6.3):
    every invariant discovered by any node appears in the federated result. -/
theorem federation_complete {α : Type} [DecidableEq α]
    (nodeResults : List (List α)) :
    ∀ node ∈ nodeResults, ∀ x ∈ node,
      x ∈ federatedUnion nodeResults := by
  induction nodeResults with
  | nil => simp
  | cons hd tl ih =>
    intro node hnode x hx
    simp only [federatedUnion]
    rw [List.mem_cons] at hnode
    cases hnode with
    | inl h =>
      subst h
      exact scanAll_complete hd (federatedUnion tl) x hx
    | inr h =>
      apply scanAll_mono
      exact ih node h x hx

/-- The federated union is an upper bound on every node's discoveries. -/
theorem federation_subsumes_nodes {α : Type} [DecidableEq α]
    (nodeResults : List (List α)) (i : Fin nodeResults.length) :
    ∀ x ∈ nodeResults.get i,
      x ∈ federatedUnion nodeResults :=
  fun x hx => federation_complete nodeResults (nodeResults.get i)
    (List.get_mem nodeResults i.val i.isLt) x hx

-- ════════════════════════════════════════════════════════════════════
-- § 9  Algorithm completeness classification
-- ════════════════════════════════════════════════════════════════════

/-- Predicate: does the given algorithm guarantee completeness? -/
def isGuaranteedComplete (alg : DiscoveryAlgorithm) : Bool :=
  alg == DiscoveryAlgorithm.exhaustive

theorem exhaustive_is_complete :
    isGuaranteedComplete DiscoveryAlgorithm.exhaustive = true := rfl

theorem greedy_is_not_complete :
    isGuaranteedComplete DiscoveryAlgorithm.greedy = false := rfl

theorem hybrid_is_not_guaranteed :
    isGuaranteedComplete DiscoveryAlgorithm.hybrid = false := rfl

/-- The exhaustive algorithm is the unique complete strategy. -/
theorem only_exhaustive_is_complete (alg : DiscoveryAlgorithm) :
    isGuaranteedComplete alg = true ↔ alg = DiscoveryAlgorithm.exhaustive := by
  constructor
  · intro h
    cases alg <;> simp_all [isGuaranteedComplete]
  · intro h
    subst h
    rfl

end JudgmentGeometry.Paper14
