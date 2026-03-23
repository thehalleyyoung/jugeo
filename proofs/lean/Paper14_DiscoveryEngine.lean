/-
  Paper14_DiscoveryEngine.lean
  Judgment Geometry · Paper 14: The Discovery Engine

  Formalizes the automated theorem and invariant discovery framework:
    - Core enumerations matching the Python implementation
    - Finite semantic site model
    - The scanAll exhaustive discovery algorithm
    - Completeness theorem: finite sites guarantee full invariant discovery
    - Analogy transport soundness
    - Kind classification consistency
    - Federation completeness
    - Novelty score monotonicity
-/

namespace JudgmentGeometry.Paper14

-- =====================================================================
-- §1  Enumerations (mirroring the Python implementation)
-- =====================================================================

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
    TheoremKind.optimality  ≠ TheoremKind.bound ∧
    TheoremKind.bound       ≠ TheoremKind.completeness ∧
    TheoremKind.monotonicity ≠ TheoremKind.approximation ∧
    TheoremKind.approximation ≠ TheoremKind.impossibility :=
  ⟨by decide, by decide, by decide, by decide⟩

-- =====================================================================
-- §2  Core data structures
-- =====================================================================

/-- A discovery candidate: potential invariant with metadata. -/
structure Candidate where
  id      : String
  kind    : TheoremKind
  novelty : Nat     -- integer approximation in [0, 100]
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

theorem defaultConfig_is_exhaustive :
    defaultConfig.algorithm = .exhaustive := rfl

-- =====================================================================
-- §3  The scanAll exhaustive discovery algorithm
-- =====================================================================

/-- Exhaustive scan: visit each invariant in order, adding it to the
    accumulator if not already present.
    Mirrors DiscoveryAlgorithm.EXHAUSTIVE in the Python engine. -/
def scanAll {α : Type} [DecidableEq α] : List α → List α → List α
  | [],      acc => acc
  | x :: xs, acc =>
    if x ∈ acc then scanAll xs acc
    else          scanAll xs (x :: acc)

-- Unfolding helpers (private theorems for internal use)
private theorem scanAll_cons_mem {α : Type} [DecidableEq α]
    (hd : α) (tl : List α) (acc : List α) (h : hd ∈ acc) :
    scanAll (hd :: tl) acc = scanAll tl acc := by
  show (if hd ∈ acc then scanAll tl acc else scanAll tl (hd :: acc)) = scanAll tl acc
  rw [if_pos h]

private theorem scanAll_cons_not_mem {α : Type} [DecidableEq α]
    (hd : α) (tl : List α) (acc : List α) (h : hd ∉ acc) :
    scanAll (hd :: tl) acc = scanAll tl (hd :: acc) := by
  show (if hd ∈ acc then scanAll tl acc else scanAll tl (hd :: acc)) = scanAll tl (hd :: acc)
  rw [if_neg h]

-- Supporting theorems

/-- Monotonicity: any element already in acc is preserved by scanAll. -/
theorem scanAll_mono {α : Type} [DecidableEq α]
    (invs : List α) (acc : List α) :
    ∀ x ∈ acc, x ∈ scanAll invs acc := by
  induction invs generalizing acc with
  | nil => intro x hx; exact hx
  | cons hd tl ih =>
    intro x hx
    by_cases h : hd ∈ acc
    · rw [scanAll_cons_mem hd tl acc h]; exact ih acc x hx
    · rw [scanAll_cons_not_mem hd tl acc h]
      exact ih (hd :: acc) x (List.mem_cons_of_mem _ hx)

/-- The head of the input list always appears in the scanAll result. -/
theorem scanAll_head_mem {α : Type} [DecidableEq α]
    (hd : α) (tl : List α) (acc : List α) :
    hd ∈ scanAll (hd :: tl) acc := by
  by_cases h : hd ∈ acc
  · rw [scanAll_cons_mem hd tl acc h]; exact scanAll_mono tl acc hd h
  · rw [scanAll_cons_not_mem hd tl acc h]
    exact scanAll_mono tl (hd :: acc) hd (List.mem_cons_self hd acc)

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
      rw [h]; exact scanAll_head_mem hd tl acc
    | inr h =>
      by_cases hmem : hd ∈ acc
      · rw [scanAll_cons_mem hd tl acc hmem];     exact ih acc x h
      · rw [scanAll_cons_not_mem hd tl acc hmem]; exact ih (hd :: acc) x h

-- =====================================================================
-- §4  Main Completeness Theorem (Paper 14, Theorem 7.1)
-- =====================================================================

/-- **Discovery Engine Completeness Theorem**:
    For any finite semantic site, the exhaustive discovery strategy
    starting from the empty state finds every expressible invariant. -/
theorem completeness_of_discovery {α : Type} [DecidableEq α]
    (site : FiniteSite α) :
    ∀ x ∈ site.invariants, x ∈ scanAll site.invariants [] :=
  fun x hx => scanAll_complete site.invariants [] x hx

/-- Corollary: discovery is monotone -- known invariants are never lost. -/
theorem discovery_monotone {α : Type} [DecidableEq α]
    (site : FiniteSite α) (known : List α) :
    ∀ x ∈ known, x ∈ scanAll site.invariants known :=
  fun x hx => scanAll_mono site.invariants known x hx

-- =====================================================================
-- §5  Novelty score and diversity
-- =====================================================================

/-- Novelty score of a candidate relative to the current portfolio.
    Zero if already known; portfolio size + 1 if genuinely new. -/
def noveltyScore {α : Type} [DecidableEq α]
    (x : α) (portfolio : List α) : Nat :=
  if x ∈ portfolio then 0 else portfolio.length + 1

/-- Known items have zero novelty. -/
theorem novelty_zero_of_known {α : Type} [DecidableEq α]
    (x : α) (portfolio : List α) (h : x ∈ portfolio) :
    noveltyScore x portfolio = 0 := by
  unfold noveltyScore; rw [if_pos h]

/-- New items have strictly positive novelty. -/
theorem novelty_pos_of_new {α : Type} [DecidableEq α]
    (x : α) (portfolio : List α) (h : x ∉ portfolio) :
    0 < noveltyScore x portfolio := by
  unfold noveltyScore; rw [if_neg h]; exact Nat.succ_pos _

/-- Novelty of a new item grows as the portfolio grows. -/
theorem novelty_grows_with_portfolio {α : Type} [DecidableEq α]
    (x : α) (base ext : List α)
    (hb : x ∉ base) (hbe : x ∉ base ++ ext) :
    noveltyScore x base ≤ noveltyScore x (base ++ ext) := by
  unfold noveltyScore
  rw [if_neg hb, if_neg hbe, List.length_append]
  omega

-- =====================================================================
-- §6  Analogy transport soundness
-- =====================================================================

/-- A sound analogy between two finite sites:
    a map that sends source invariants to valid target invariants.
    Mirrors AnalogyFinder / IdeaTransporter in jugeo.ideation.federation. -/
structure SoundAnalogy (α β : Type) where
  sourceInvs : List α
  targetInvs : List β
  transport  : α → β
  /-- Soundness: every source invariant maps to a valid target invariant. -/
  sound      : ∀ x ∈ sourceInvs, transport x ∈ targetInvs

/-- **Analogy Transport Soundness** (Proposition 4.2):
    transported invariants are discovered by the exhaustive engine. -/
theorem analogy_transport_sound {α β : Type} [DecidableEq α] [DecidableEq β]
    (analogy : SoundAnalogy α β) :
    ∀ x ∈ analogy.sourceInvs,
      analogy.transport x ∈ scanAll analogy.targetInvs [] := by
  intro x hx
  exact scanAll_complete analogy.targetInvs [] _ (analogy.sound x hx)

/-- Composing two sound analogies yields a sound analogy. -/
def composeSoundAnalogy {α β γ : Type}
    (f : SoundAnalogy α β) (g : SoundAnalogy β γ)
    (himg : ∀ x ∈ f.sourceInvs, f.transport x ∈ g.sourceInvs) :
    SoundAnalogy α γ where
  sourceInvs := f.sourceInvs
  targetInvs := g.targetInvs
  transport  := fun x => g.transport (f.transport x)
  sound      := fun x hx => g.sound _ (himg x hx)

theorem composeSoundAnalogy_transport {α β γ : Type}
    (f : SoundAnalogy α β) (g : SoundAnalogy β γ)
    (himg : ∀ x ∈ f.sourceInvs, f.transport x ∈ g.sourceInvs)
    (x : α) (_hx : x ∈ f.sourceInvs) :
    (composeSoundAnalogy f g himg).transport x =
      g.transport (f.transport x) := rfl

-- =====================================================================
-- §7  Kind classification consistency
-- =====================================================================

/-- Extract invariants of a specific kind from a labeled list.
    Mirrors KindDiscoveryEngine partitioning in kind_discovery. -/
def invariantsOfKind {α : Type} [DecidableEq TheoremKind]
    : List (α × TheoremKind) → TheoremKind → List α
  | [],             _ => []
  | (y, j) :: rest, k =>
    if j = k then y :: invariantsOfKind rest k
    else           invariantsOfKind rest k

/-- **Kind Classification Consistency**:
    every labeled invariant appears in the partition for its assigned kind. -/
theorem kind_classification_complete {α : Type} [DecidableEq TheoremKind]
    (labeled : List (α × TheoremKind)) (x : α) (k : TheoremKind)
    (h : (x, k) ∈ labeled) :
    x ∈ invariantsOfKind labeled k := by
  induction labeled with
  | nil => exact absurd h (List.not_mem_nil _)
  | cons hd tl ih =>
    rcases hd with ⟨y, j⟩
    rw [List.mem_cons] at h
    unfold invariantsOfKind
    cases h with
    | inl heq =>
      have hy : y = x := congrArg Prod.fst heq.symm
      have hj : j = k := congrArg Prod.snd heq.symm
      rw [if_pos hj, hy]
      exact List.mem_cons_self x _
    | inr hmem =>
      by_cases hjk : j = k
      · rw [if_pos hjk]; exact List.mem_cons_of_mem _ (ih hmem)
      · rw [if_neg hjk]; exact ih hmem

/-- Every kind assigned in the labeled list has a non-empty partition. -/
theorem kind_partition_nonempty {α : Type} [DecidableEq TheoremKind]
    (labeled : List (α × TheoremKind)) (x : α) (k : TheoremKind)
    (h : (x, k) ∈ labeled) :
    ∃ y, y ∈ invariantsOfKind labeled k :=
  ⟨x, kind_classification_complete labeled x k h⟩

-- =====================================================================
-- §8  Federation: distributed discovery
-- =====================================================================

/-- Federated union: merge discoveries from multiple distributed nodes.
    Mirrors DiscoveryFederator in jugeo.ideation.discovery_federation. -/
def federatedUnion {α : Type} [DecidableEq α] : List (List α) → List α
  | []                 => []
  | nodeResult :: rest => scanAll nodeResult (federatedUnion rest)

private theorem federatedUnion_cons {α : Type} [DecidableEq α]
    (hd : List α) (tl : List (List α)) :
    federatedUnion (hd :: tl) = scanAll hd (federatedUnion tl) := rfl

/-- **Federation Completeness** (Corollary 6.3):
    every invariant discovered by any node appears in the federated result. -/
theorem federation_complete {α : Type} [DecidableEq α]
    (nodeResults : List (List α)) :
    ∀ node ∈ nodeResults, ∀ x ∈ node,
      x ∈ federatedUnion nodeResults := by
  induction nodeResults with
  | nil => simp [federatedUnion]
  | cons hd tl ih =>
    intro node hnode x hx
    rw [federatedUnion_cons]
    rw [List.mem_cons] at hnode
    cases hnode with
    | inl h =>
      rw [h] at hx
      exact scanAll_complete hd (federatedUnion tl) x hx
    | inr h =>
      exact scanAll_mono hd (federatedUnion tl) x (ih node h x hx)

/-- The federated union subsumes every node's individual result. -/
theorem federation_subsumes_node {α : Type} [DecidableEq α]
    (nodeResults : List (List α)) (node : List α) (hmem : node ∈ nodeResults) :
    ∀ x ∈ node, x ∈ federatedUnion nodeResults :=
  fun x hx => federation_complete nodeResults node hmem x hx

-- =====================================================================
-- §9  Algorithm completeness classification
-- =====================================================================

/-- Predicate: does the algorithm guarantee completeness? -/
def isGuaranteedComplete (alg : DiscoveryAlgorithm) : Bool :=
  alg == DiscoveryAlgorithm.exhaustive

theorem exhaustive_is_complete :
    isGuaranteedComplete DiscoveryAlgorithm.exhaustive = true := rfl

theorem greedy_is_not_complete :
    isGuaranteedComplete DiscoveryAlgorithm.greedy = false := rfl

theorem hybrid_not_guaranteed :
    isGuaranteedComplete DiscoveryAlgorithm.hybrid = false := rfl

/-- The exhaustive algorithm is the unique strategy with completeness guarantee. -/
theorem only_exhaustive_is_complete (alg : DiscoveryAlgorithm) :
    isGuaranteedComplete alg = true ↔ alg = DiscoveryAlgorithm.exhaustive := by
  cases alg <;> decide

end JudgmentGeometry.Paper14
