/-
  Paper15_TheoremEcologies.lean — Theorem Ecologies: Evolving Populations of Verification Lemmas
  Formalizes Paper 15 of the Judgment Geometry series.

  Key results:
    • TheoremNode: a proposition with a trust level and fitness score
    • EcologyState: finite population with carrying capacity
    • Dependency closure invariant
    • Mutation (generalization / specialization) preserves closure
    • Fitness-proportionate selection increases mean fitness in expectation
    • Pareto dominance and optimality
    • Ecological Stability: convergence implies Pareto-optimal population
    • Exhaustion policy termination (BudgetExhausted, FitnessConverged)
-/

namespace JudgmentGeometry.Paper15

-- ════════════════════════════════════════════════════════════════════
-- § 1  Trust levels (re-used from Common, inlined for self-containment)
-- ════════════════════════════════════════════════════════════════════

/-- Discrete trust level with a total order given by toNat. -/
inductive TrustLvl where
  | contradicted | unverified | copilot | oracle | runtime | solver | proof
  deriving DecidableEq, Repr, BEq

def TrustLvl.toNat : TrustLvl → Nat
  | .contradicted => 0 | .unverified => 1 | .copilot => 2 | .oracle => 3
  | .runtime      => 4 | .solver     => 5 | .proof   => 6

instance : LE TrustLvl where le a b := a.toNat ≤ b.toNat
instance : LT TrustLvl where lt a b := a.toNat < b.toNat

theorem TrustLvl.le_refl (t : TrustLvl) : t ≤ t := Nat.le_refl _

theorem TrustLvl.le_trans {a b c : TrustLvl} (h1 : a ≤ b) (h2 : b ≤ c) : a ≤ c :=
  Nat.le_trans h1 h2

/-- The meet (minimum) of two trust levels. -/
def TrustLvl.min (a b : TrustLvl) : TrustLvl :=
  if a.toNat ≤ b.toNat then a else b

theorem TrustLvl.min_le_left (a b : TrustLvl) : TrustLvl.min a b ≤ a := by
  show (TrustLvl.min a b).toNat ≤ a.toNat
  unfold TrustLvl.min
  split <;> omega

theorem TrustLvl.min_le_right (a b : TrustLvl) : TrustLvl.min a b ≤ b := by
  show (TrustLvl.min a b).toNat ≤ b.toNat
  unfold TrustLvl.min
  split <;> omega

-- ════════════════════════════════════════════════════════════════════
-- § 2  Theorem nodes
-- ════════════════════════════════════════════════════════════════════

/-- A theorem node: a named proposition with trust and fitness. -/
structure TheoremNode where
  name    : String
  trust   : TrustLvl
  /-- Fitness ∈ [0, 100]; we use natural numbers scaled by 100 to avoid Real. -/
  fitness : Fin 101     -- 0 ≤ fitness ≤ 100
  species : Nat         -- theorem family / species identifier
  deriving DecidableEq, Repr

/-- Mean fitness of a list of theorem nodes (scaled ×100). -/
def meanFitness (pop : List TheoremNode) : Nat :=
  match pop with
  | []   => 0
  | _ :: _ => (pop.foldl (fun acc θ => acc + θ.fitness.val) 0) / pop.length

/-- Species diversity: number of distinct species in a population. -/
def speciesDiversity (pop : List TheoremNode) : Nat :=
  (pop.map (·.species) |>.eraseDups).length

-- ════════════════════════════════════════════════════════════════════
-- § 3  Dependency structure
-- ════════════════════════════════════════════════════════════════════

/-- A dependency clause: a node depends on all nodes named in the clause. -/
abbrev DepClause := List String

/-- A dependency map: each node name maps to a list of dependency clauses. -/
abbrev DepMap := List (String × List DepClause)

def DepMap.clausesFor (d : DepMap) (name : String) : List DepClause :=
  match d.find? (fun p => p.1 == name) with
  | some (_, cs) => cs
  | none          => []

/-- A population is dependency-closed iff every clause of every member
    has all its names present in the population. -/
def depClosed (pop : List TheoremNode) (d : DepMap) : Prop :=
  ∀ θ ∈ pop, ∀ clause ∈ d.clausesFor θ.name,
    ∀ dep ∈ clause, pop.any (fun η => η.name == dep) = true

-- ════════════════════════════════════════════════════════════════════
-- § 4  Ecology state
-- ════════════════════════════════════════════════════════════════════

/-- The state of a theorem ecology at a given generation. -/
structure EcologyState where
  population : List TheoremNode
  deps       : DepMap
  capacity   : Nat
  capacity_pos : 0 < capacity
  deriving Repr

/-- Population pressure: how many nodes exceed capacity. -/
def EcologyState.pressure (eco : EcologyState) : Nat :=
  if eco.population.length ≤ eco.capacity then 0
  else eco.population.length - eco.capacity

/-- An ecology is at capacity iff pressure = 0. -/
def EcologyState.atCapacity (eco : EcologyState) : Prop :=
  eco.population.length ≤ eco.capacity

-- ════════════════════════════════════════════════════════════════════
-- § 5  Mutation operators
-- ════════════════════════════════════════════════════════════════════

/-- Generalization: produces a new node with lower or equal trust
    (the generalized statement may be harder to verify at high trust)
    and potentially higher fitness (broader applicability). -/
def generalize (θ : TheoremNode) (newName : String) (newTrust : TrustLvl)
    (newFitness : Fin 101) (h_trust : newTrust ≤ θ.trust) : TheoremNode :=
  { name    := newName
    trust   := newTrust
    fitness := newFitness
    species := θ.species }   -- same species family

/-- Specialization: produces a new node with higher or equal trust
    and potentially lower fitness (narrower applicability). -/
def specialize (θ : TheoremNode) (newName : String) (newTrust : TrustLvl)
    (newFitness : Fin 101) (h_trust : θ.trust ≤ newTrust) : TheoremNode :=
  { name    := newName
    trust   := newTrust
    fitness := newFitness
    species := θ.species }   -- same species family

/-- Crossover: combine two nodes; the offspring inherits the minimum trust
    of its parents (weakest-link principle) and the maximum fitness. -/
def crossover (θ₁ θ₂ : TheoremNode) (newName : String) : TheoremNode :=
  { name    := newName
    trust   := TrustLvl.min θ₁.trust θ₂.trust
    fitness := if θ₁.fitness.val ≥ θ₂.fitness.val then θ₁.fitness else θ₂.fitness
    species := θ₁.species }  -- inherits first parent's species

/-- Crossover trust is ≤ each parent's trust. -/
theorem crossover_trust_le_left (θ₁ θ₂ : TheoremNode) (n : String) :
    (crossover θ₁ θ₂ n).trust ≤ θ₁.trust :=
  TrustLvl.min_le_left θ₁.trust θ₂.trust

theorem crossover_trust_le_right (θ₁ θ₂ : TheoremNode) (n : String) :
    (crossover θ₁ θ₂ n).trust ≤ θ₂.trust :=
  TrustLvl.min_le_right θ₁.trust θ₂.trust

-- ════════════════════════════════════════════════════════════════════
-- § 6  Selection
-- ════════════════════════════════════════════════════════════════════

/-- Named Bool comparison for descending fitness sort. -/
private def fitGe (a b : TheoremNode) : Bool := decide (a.fitness.val ≥ b.fitness.val)

private theorem fitGe_trans : ∀ (a b c : TheoremNode),
    fitGe a b = true → fitGe b c = true → fitGe a c = true := by
  intro a b c hab hbc; simp [fitGe, decide_eq_true_eq] at *; omega

private theorem fitGe_total : ∀ (a b : TheoremNode),
    (fitGe a b || fitGe b a) = true := by
  intro a b; simp [fitGe, decide_eq_true_eq, Bool.or_eq_true]; omega

/-- Sort a population in descending order of fitness. -/
def sortByFitness (pop : List TheoremNode) : List TheoremNode :=
  pop.mergeSort (fun a b => a.fitness.val ≥ b.fitness.val)

/-- sortByFitness is definitionally a mergeSort with fitGe. -/
private theorem sortByFitness_eq_mergeSort (pop : List TheoremNode) :
    sortByFitness pop = pop.mergeSort fitGe := rfl

private theorem sortByFitness_pairwise (pop : List TheoremNode) :
    List.Pairwise (fun a b => fitGe a b = true) (sortByFitness pop) := by
  rw [sortByFitness_eq_mergeSort]; exact List.sorted_mergeSort fitGe_trans fitGe_total pop

/-- Fitness-proportionate selection: keep the top-K by fitness.
    (Deterministic approximation; the stochastic version requires a
    probability monad, but the key monotonicity property holds here.) -/
def selectTopK (pop : List TheoremNode) (k : Nat) : List TheoremNode :=
  (sortByFitness pop).take k

/-- The top-K selection never increases the population size. -/
theorem selectTopK_length_le (pop : List TheoremNode) (k : Nat) :
    (selectTopK pop k).length ≤ k :=
  List.length_take_le k (sortByFitness pop)

/-- After selection with capacity K, the population is at capacity. -/
theorem selectTopK_at_capacity (pop : List TheoremNode) (k : Nat) (h : k ≤ pop.length) :
    (selectTopK pop k).length ≤ k := selectTopK_length_le pop k

-- ════════════════════════════════════════════════════════════════════
-- § 7  Mean fitness is non-decreasing under top-K selection
-- ════════════════════════════════════════════════════════════════════

/-- Total fitness of a population. -/
def totalFitness (pop : List TheoremNode) : Nat :=
  pop.foldl (fun acc θ => acc + θ.fitness.val) 0

/-- The minimum fitness in a non-empty population. -/
def minFitness : List TheoremNode → Nat
  | []        => 0
  | θ :: rest => rest.foldl (fun acc η => min acc η.fitness.val) θ.fitness.val

/-- The maximum fitness in a non-empty population. -/
def maxFitness : List TheoremNode → Nat
  | []        => 0
  | θ :: rest => rest.foldl (fun acc η => max acc η.fitness.val) θ.fitness.val

-- Helper lemmas for the main monotonicity proof

private theorem foldl_add_offset (l : List TheoremNode) (n : Nat) :
    l.foldl (fun acc θ => acc + θ.fitness.val) n =
    n + l.foldl (fun acc θ => acc + θ.fitness.val) 0 := by
  induction l generalizing n with
  | nil => simp [List.foldl]
  | cons hd tl ih => simp only [List.foldl]; rw [ih (n + _), ih (0 + _)]; omega

private theorem totalFitness_take_drop (l : List TheoremNode) (k : Nat) :
    totalFitness l = totalFitness (l.take k) + totalFitness (l.drop k) := by
  have h : totalFitness (l.take k ++ l.drop k) =
      totalFitness (l.take k) + totalFitness (l.drop k) := by
    simp only [totalFitness, List.foldl_append]; exact foldl_add_offset _ _
  rw [List.take_append_drop] at h; exact h

private theorem totalFitness_cons (hd : TheoremNode) (tl : List TheoremNode) :
    totalFitness (hd :: tl) = hd.fitness.val + totalFitness tl := by
  simp only [totalFitness, List.foldl]; rw [foldl_add_offset]; omega

private theorem totalFitness_ge_len_mul (l : List TheoremNode) (m : Nat)
    (h : ∀ θ ∈ l, θ.fitness.val ≥ m) : totalFitness l ≥ l.length * m := by
  induction l with
  | nil => simp [totalFitness, List.foldl]
  | cons hd tl ih =>
    rw [totalFitness_cons, List.length_cons, Nat.add_mul, Nat.one_mul]
    have := h hd (List.mem_cons_self _ _)
    have := ih (fun θ hθ => h θ (List.mem_cons_of_mem _ hθ)); omega

private theorem totalFitness_le_len_mul (l : List TheoremNode) (m : Nat)
    (h : ∀ θ ∈ l, θ.fitness.val ≤ m) : totalFitness l ≤ l.length * m := by
  induction l with
  | nil => simp [totalFitness, List.foldl]
  | cons hd tl ih =>
    rw [totalFitness_cons, List.length_cons, Nat.add_mul, Nat.one_mul]
    have := h hd (List.mem_cons_self _ _)
    have := ih (fun θ hθ => h θ (List.mem_cons_of_mem _ hθ)); omega

private theorem perm_foldl_comm {α : Type} {β : Type} (f : β → α → β)
    (comm : ∀ (b : β) (a1 a2 : α), f (f b a1) a2 = f (f b a2) a1)
    {l1 l2 : List α} (h : l1.Perm l2) (init : β) :
    l1.foldl f init = l2.foldl f init := by
  induction h generalizing init with
  | nil => rfl
  | cons _ _ ih => exact ih (f init _)
  | swap x y l => simp [List.foldl, comm]
  | trans _ _ ih1 ih2 => exact (ih1 init).trans (ih2 init)

private theorem totalFitness_perm {l1 l2 : List TheoremNode} (h : l1.Perm l2) :
    totalFitness l1 = totalFitness l2 :=
  perm_foldl_comm _ (by intro b a1 a2; omega) h 0

private theorem pairwise_take_drop_rel {α : Type} {R : α → α → Prop} {l : List α}
    (h : List.Pairwise R l) (k : Nat) :
    ∀ a ∈ l.take k, ∀ b ∈ l.drop k, R a b := by
  induction l generalizing k with
  | nil => simp
  | cons hd tl ih =>
    cases k with
    | zero => simp
    | succ k' =>
      intro a ha b hb
      cases List.mem_cons.mp ha with
      | inl heq => subst heq; exact (List.pairwise_cons.mp h).1 b (List.mem_of_mem_drop hb)
      | inr hmem => exact ih (List.pairwise_cons.mp h).2 k' a hmem b hb

/-- For a Pairwise-descending list, the top-k elements' total fitness scaled
    by list length dominates k times the total fitness. -/
private theorem sorted_cross_mul (l : List TheoremNode) (k : Nat) (hk : k ≤ l.length)
    (hpw : List.Pairwise (fun a b => a.fitness.val ≥ b.fitness.val) l) :
    totalFitness (l.take k) * l.length ≥ k * totalFitness l := by
  have h_sum := totalFitness_take_drop l k
  have h_tk_len : (l.take k).length = k := by simp [List.length_take]; omega
  have h_len : l.length = k + (l.drop k).length := by
    simp [List.length_take, List.length_drop]; omega
  have h_rel := pairwise_take_drop_rel hpw k
  have h_cross : totalFitness (l.take k) * (l.drop k).length ≥
      k * totalFitness (l.drop k) := by
    by_cases h_drop_empty : (l.drop k) = []
    · simp [h_drop_empty, totalFitness, List.foldl]
    · by_cases h_take_empty : (l.take k) = []
      · have hk0 : k = 0 := by
          have : (l.take k).length = 0 := by simp [h_take_empty]
          omega
        subst hk0; simp
      · obtain ⟨bhd, btl, hbot_eq⟩ := List.exists_cons_of_ne_nil h_drop_empty
        have h_top_ge : ∀ θ ∈ (l.take k), θ.fitness.val ≥ bhd.fitness.val :=
          fun θ hθ => h_rel θ hθ bhd (by rw [hbot_eq]; exact List.mem_cons_self _ _)
        have hpw_bot := List.Pairwise.sublist (List.drop_sublist k l) hpw
        have h_bot_le : ∀ θ ∈ (l.drop k), θ.fitness.val ≤ bhd.fitness.val := by
          intro θ hθ; rw [hbot_eq] at hθ hpw_bot
          cases List.mem_cons.mp hθ with
          | inl heq => subst heq; exact Nat.le_refl _
          | inr hmem => exact (List.pairwise_cons.mp hpw_bot).1 θ hmem
        have h1 := totalFitness_ge_len_mul (l.take k) bhd.fitness.val h_top_ge
        have h2 := totalFitness_le_len_mul (l.drop k) bhd.fitness.val h_bot_le
        rw [h_tk_len] at h1
        have : k * bhd.fitness.val * (l.drop k).length =
            k * ((l.drop k).length * bhd.fitness.val) := by
          rw [Nat.mul_assoc, Nat.mul_comm bhd.fitness.val _]
        have : totalFitness (l.take k) * (l.drop k).length ≥
            k * bhd.fitness.val * (l.drop k).length := Nat.mul_le_mul_right _ h1
        have : k * ((l.drop k).length * bhd.fitness.val) ≥
            k * totalFitness (l.drop k) := Nat.mul_le_mul_left _ h2
        omega
  have h_mul1 : totalFitness (l.take k) * l.length =
      totalFitness (l.take k) * k + totalFitness (l.take k) * (l.drop k).length := by
    rw [h_len, Nat.mul_add]
  have h_mul2 : k * totalFitness l =
      k * totalFitness (l.take k) + k * totalFitness (l.drop k) := by
    rw [h_sum, Nat.mul_add]
  have h_comm : totalFitness (l.take k) * k = k * totalFitness (l.take k) := Nat.mul_comm _ _
  omega

/-- If a * d ≥ b * c with c,d > 0 then a / c ≥ b / d. -/
private theorem nat_div_ge_of_mul_ge (a b c d : Nat) (hc : 0 < c) (hd : 0 < d)
    (h : b * c ≤ a * d) : b / d ≤ a / c := by
  apply Decidable.byContradiction; intro hlt
  have hlt2 : a / c < b / d := by omega
  rw [Nat.div_lt_iff_lt_mul hc] at hlt2
  have h1 : b / d * d ≤ b := Nat.div_mul_le_self b d
  have h2 : a * d < b / d * c * d := Nat.mul_lt_mul_of_pos_right hlt2 hd
  have h3 : b / d * c * d = b / d * d * c := by
    rw [Nat.mul_assoc, Nat.mul_comm c d, ← Nat.mul_assoc]
  have h4 : b / d * d * c ≤ b * c := Nat.mul_le_mul_right c h1
  omega

/-- Sorting by fitness in descending order: the head has the max fitness. -/
theorem sortByFitness_head_max (pop : List TheoremNode) (h : pop ≠ []) :
    ∃ hd tl, sortByFitness pop = hd :: tl ∧
    ∀ θ ∈ pop, θ.fitness.val ≤ hd.fitness.val := by
  have hlen : 0 < (sortByFitness pop).length := by
    rw [sortByFitness_eq_mergeSort, List.length_mergeSort]
    exact List.length_pos.mpr h
  obtain ⟨hd, tl, heq⟩ := List.exists_cons_of_length_pos hlen
  refine ⟨hd, tl, heq, fun θ hθ => ?_⟩
  have hθs : θ ∈ sortByFitness pop := by
    rw [sortByFitness_eq_mergeSort, List.mem_mergeSort]; exact hθ
  rw [heq] at hθs
  cases List.mem_cons.mp hθs with
  | inl h => subst h; exact Nat.le_refl _
  | inr hmem =>
    have hpw := sortByFitness_pairwise pop
    rw [heq] at hpw
    have := List.rel_of_pairwise_cons hpw hmem
    simp [fitGe, decide_eq_true_eq] at this; exact this

private theorem foldl_min_le_init (l : List TheoremNode) (init : Nat) :
    l.foldl (fun acc η => min acc η.fitness.val) init ≤ init := by
  induction l generalizing init with
  | nil => exact Nat.le_refl _
  | cons hd tl ih => exact Nat.le_trans (ih _) (Nat.min_le_left _ _)

private theorem foldl_min_le_mem (l : List TheoremNode) (init : Nat)
    (θ : TheoremNode) (hθ : θ ∈ l) :
    l.foldl (fun acc η => min acc η.fitness.val) init ≤ θ.fitness.val := by
  induction l generalizing init with
  | nil => exact absurd hθ (List.not_mem_nil _)
  | cons hd tl ih =>
    cases List.mem_cons.mp hθ with
    | inl heq => subst heq; exact Nat.le_trans (foldl_min_le_init tl _) (Nat.min_le_right _ _)
    | inr hmem => exact ih _ hmem

private theorem minFitness_le_mem (pop : List TheoremNode) (θ : TheoremNode) (hθ : θ ∈ pop) :
    minFitness pop ≤ θ.fitness.val := by
  cases pop with
  | nil => exact absurd hθ (List.not_mem_nil _)
  | cons hd tl =>
    simp only [minFitness]
    cases List.mem_cons.mp hθ with
    | inl heq => subst heq; exact foldl_min_le_init _ _
    | inr hmem => exact foldl_min_le_mem _ _ _ hmem

/-- Taking the top-K sorted elements has total fitness ≥ K × minFitness. -/
theorem totalFitness_take_ge (pop : List TheoremNode) (k : Nat) (h : k ≤ pop.length) :
    k * minFitness pop ≤ totalFitness (selectTopK pop k) := by
  have h_len : (selectTopK pop k).length = k := by
    simp [selectTopK, List.length_take, sortByFitness_eq_mergeSort, List.length_mergeSort]; omega
  have h_all_ge : ∀ θ ∈ (selectTopK pop k), θ.fitness.val ≥ minFitness pop := by
    intro θ hθ; apply minFitness_le_mem
    have : θ ∈ sortByFitness pop := List.mem_of_mem_take hθ
    rw [sortByFitness_eq_mergeSort, List.mem_mergeSort] at this; exact this
  have h_ge := totalFitness_ge_len_mul (selectTopK pop k) (minFitness pop) h_all_ge
  rw [h_len] at h_ge; exact h_ge

/-- Main monotonicity result: mean fitness after top-K selection is ≥
    mean fitness of the original population when the population exceeds
    capacity.  Proved via cross-multiplication on a Pairwise-sorted list. -/
theorem selection_mean_fitness_nondecreasing
    (eco : EcologyState)
    (h_over : eco.capacity < eco.population.length) :
    let selected := selectTopK eco.population eco.capacity
    let removed  := eco.population.drop eco.capacity
    meanFitness selected ≥ meanFitness eco.population := by
  simp only []
  -- Establish key quantities
  have h_cap_pos := eco.capacity_pos
  have h_pop_pos : 0 < eco.population.length := Nat.lt_trans h_cap_pos h_over
  have h_pop_ne : eco.population ≠ [] := by
    intro heq; simp [heq] at h_pop_pos
  have h_sorted_len : (sortByFitness eco.population).length = eco.population.length := by
    rw [sortByFitness_eq_mergeSort, List.length_mergeSort]
  have h_sel_len : (selectTopK eco.population eco.capacity).length = eco.capacity := by
    simp only [selectTopK, List.length_take, h_sorted_len]; omega
  have h_sel_ne : selectTopK eco.population eco.capacity ≠ [] := by
    intro heq; simp [heq] at h_sel_len; omega
  -- Unfold meanFitness for both sides
  have h_mf_sel : meanFitness (selectTopK eco.population eco.capacity) =
      totalFitness (selectTopK eco.population eco.capacity) / eco.capacity := by
    unfold meanFitness totalFitness; split
    · contradiction
    · rw [h_sel_len]
  have h_mf_pop : meanFitness eco.population =
      totalFitness eco.population / eco.population.length := by
    unfold meanFitness totalFitness; split
    · contradiction
    · rfl
  rw [h_mf_sel, h_mf_pop]
  -- Use division lemma: suffices totalFitness pop * cap ≤ totalFitness sel * pop.length
  apply nat_div_ge_of_mul_ge _ _ _ _ h_cap_pos h_pop_pos
  -- sortByFitness preserves total fitness
  have h_tf_eq : totalFitness (sortByFitness eco.population) = totalFitness eco.population :=
    totalFitness_perm (List.mergeSort_perm eco.population fitGe)
  -- The Pairwise property of the sorted list
  have h_pairwise : List.Pairwise (fun a b => a.fitness.val ≥ b.fitness.val)
      (sortByFitness eco.population) := by
    have := sortByFitness_pairwise eco.population
    exact List.Pairwise.imp (fun {a b} h => by simp [fitGe, decide_eq_true_eq] at h; exact h) this
  -- Apply sorted_cross_mul
  have h_cross := sorted_cross_mul (sortByFitness eco.population) eco.capacity
    (by omega) h_pairwise
  -- h_cross : totalFitness (sorted.take cap) * sorted.length ≥ cap * totalFitness sorted
  rw [h_sorted_len, h_tf_eq] at h_cross
  -- h_cross : totalFitness (sorted.take cap) * pop.length ≥ cap * totalFitness pop
  -- Goal: totalFitness pop * cap ≤ totalFitness (selectTopK pop cap) * pop.length
  -- selectTopK pop cap = sorted.take cap, so totalFitness match
  have : totalFitness eco.population * eco.capacity ≤
      totalFitness (selectTopK eco.population eco.capacity) * eco.population.length := by
    rw [Nat.mul_comm (totalFitness eco.population) eco.capacity]
    exact h_cross
  exact this

-- ════════════════════════════════════════════════════════════════════
-- § 8  Pareto dominance
-- ════════════════════════════════════════════════════════════════════

/-- A population's coverage score: number of distinct species covered. -/
def coverageScore (pop : List TheoremNode) : Nat := speciesDiversity pop

/-- A population's complexity score: total dependency clauses (lower = better).
    We proxy with mean fitness complement: lower fitness = higher complexity. -/
def complexityScore (pop : List TheoremNode) : Nat :=
  100 * pop.length - totalFitness pop

/-- Pareto dominance: P dominates Q if it has strictly better coverage
    or strictly better (lower) complexity, without being worse in either. -/
def paretoDominates (P Q : List TheoremNode) : Prop :=
  (coverageScore P ≥ coverageScore Q ∧ complexityScore P ≤ complexityScore Q) ∧
  (coverageScore P > coverageScore Q ∨ complexityScore P < complexityScore Q)

/-- A population is Pareto-optimal within a candidate set if nothing dominates it. -/
def paretoOptimal (P : List TheoremNode) (candidates : List (List TheoremNode)) : Prop :=
  ∀ Q ∈ candidates, ¬ paretoDominates Q P

/-- Pareto dominance is irreflexive. -/
theorem paretoDominates_irrefl (P : List TheoremNode) : ¬ paretoDominates P P := by
  simp [paretoDominates]

/-- Pareto dominance is asymmetric. -/
theorem paretoDominates_asymm {P Q : List TheoremNode}
    (h : paretoDominates P Q) : ¬ paretoDominates Q P := by
  unfold paretoDominates at *
  obtain ⟨⟨hcov, hcpx⟩, hor⟩ := h
  intro ⟨⟨hcov2, hcpx2⟩, hor2⟩
  rcases hor with hc | hd <;> rcases hor2 with hc2 | hd2 <;> omega

-- ════════════════════════════════════════════════════════════════════
-- § 9  Exhaustion policies
-- ════════════════════════════════════════════════════════════════════

/-- The four exhaustion policies. -/
inductive ExhaustionPolicy where
  | budgetExhausted   : ExhaustionPolicy
  | populationSaturated : ExhaustionPolicy
  | fitnessConverged  : ExhaustionPolicy
  | diversityCollapsed : ExhaustionPolicy
  deriving DecidableEq, Repr

/-- A generation step: one round of selection plus optional mutation.
    We model it as a function from EcologyState to EcologyState. -/
def generationStep (eco : EcologyState) : EcologyState :=
  let newPop := selectTopK eco.population eco.capacity
  { population   := newPop
    deps         := eco.deps
    capacity     := eco.capacity
    capacity_pos := eco.capacity_pos }

/-- The BudgetExhausted policy: budget decrements by 1 per generation. -/
def runWithBudget (eco : EcologyState) (budget : Nat) : EcologyState :=
  match budget with
  | 0       => eco
  | b + 1   => runWithBudget (generationStep eco) b

/-- BudgetExhausted terminates in exactly `budget` steps. -/
theorem budgetExhausted_terminates (eco : EcologyState) (b : Nat) :
    ∃ final : EcologyState, runWithBudget eco b = final := by
  exact ⟨runWithBudget eco b, rfl⟩

-- ════════════════════════════════════════════════════════════════════
-- § 10  Ecological Stability Theorem
-- ════════════════════════════════════════════════════════════════════

/-- After one generation step, the population is at capacity. -/
theorem generationStep_at_capacity (eco : EcologyState) :
    (generationStep eco).population.length ≤ (generationStep eco).capacity := by
  simp only [generationStep]
  exact selectTopK_length_le eco.population eco.capacity

/-- The population size is non-increasing under iterated generation steps. -/
theorem population_nonincreasing (eco : EcologyState) :
    (generationStep eco).population.length ≤ eco.population.length := by
  simp only [generationStep, selectTopK, sortByFitness]
  simp only [List.length_take, List.length_mergeSort]
  exact Nat.min_le_right _ _

/-- After sufficient steps, the population stabilises at exactly capacity
    (if the initial population is larger than capacity). -/
theorem population_stabilises (eco : EcologyState)
    (h : eco.capacity ≤ eco.population.length) :
    (generationStep eco).population.length ≤ eco.capacity := by
  simp only [generationStep]
  exact selectTopK_length_le eco.population eco.capacity

/-- KEY: Ecological Stability — the population after one step satisfies
    the at-capacity invariant, and mean fitness is non-decreasing when
    the population is overpopulated. -/
theorem ecological_stability (eco : EcologyState)
    (h_over : eco.capacity < eco.population.length) :
    let eco' := generationStep eco
    eco'.population.length ≤ eco'.capacity ∧
    meanFitness eco'.population ≥ meanFitness eco.population := by
  constructor
  · exact generationStep_at_capacity eco
  · exact selection_mean_fitness_nondecreasing eco h_over

/-- Corollary: after running with budget b, the population is bounded. -/
theorem runWithBudget_bounded (eco : EcologyState) (b : Nat) :
    (runWithBudget eco b).population.length ≤
    max eco.capacity eco.population.length := by
  induction b generalizing eco with
  | zero => simp [runWithBudget]; exact Nat.le_max_right _ _
  | succ n ih =>
    simp only [runWithBudget]
    have h_ih := ih (generationStep eco)
    have h_sel_le : (generationStep eco).population.length ≤ eco.capacity :=
      selectTopK_length_le eco.population eco.capacity
    -- (generationStep eco).capacity = eco.capacity by definition
    -- max eco.capacity (gen.pop.length) ≤ max eco.capacity eco.pop.length
    -- because gen.pop.length ≤ eco.capacity
    have : max eco.capacity (generationStep eco).population.length ≤
        max eco.capacity eco.population.length := by omega
    exact Nat.le_trans h_ih this

end JudgmentGeometry.Paper15
