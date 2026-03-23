/-
  Paper16_PersistentHomology.lean — Persistent Homology of Software Evolution
  Formalizes Paper 16 of the Judgment Geometry series.

  Key results (all proved without sorry):
    • CommitFiltration: monotone sequence of semantic sites
    • Filtration transitivity and boundary lemmas
    • PersistenceModule induced by a filtration in degrees 0, 1, 2
    • natDist: a discrete metric (symmetry, triangle inequality)
    • ε-interleaving predicate between two commit filtrations
    • Discrete stability: ε-interleaving implies ε-close Betti numbers
    • Bar / barcode types; barDist between bars
    • ObstructionClass: birth–death pairs for H₁ generators
    • Architectural health predicate and its stability under interleaving
    • Grand stability package collecting the main results
-/

namespace JudgmentGeometry.Paper16

-- ════════════════════════════════════════════════════════════════════
-- § 1  Semantic sites (simplified)
-- ════════════════════════════════════════════════════════════════════

/-- A simplified semantic site: records Betti numbers β₀, β₁, β₂.
    In the full theory (Paper 01) a site is a Grothendieck site;
    here we abstract to its homological invariants. -/
structure Site where
  beta0 : Nat   -- β₀: number of connected components
  beta1 : Nat   -- β₁: number of independent 1-cycles (dependency loops)
  beta2 : Nat   -- β₂: number of 2-dimensional voids
  deriving Repr, DecidableEq

/-- Site inclusion: every Betti number of the smaller site is ≤ that of the larger. -/
def Site.le (s t : Site) : Prop :=
  s.beta0 ≤ t.beta0 ∧ s.beta1 ≤ t.beta1 ∧ s.beta2 ≤ t.beta2

theorem Site.le_refl (s : Site) : s.le s :=
  ⟨Nat.le_refl _, Nat.le_refl _, Nat.le_refl _⟩

theorem Site.le_trans (r s t : Site) (h1 : r.le s) (h2 : s.le t) : r.le t :=
  ⟨Nat.le_trans h1.1 h2.1,
   Nat.le_trans h1.2.1 h2.2.1,
   Nat.le_trans h1.2.2 h2.2.2⟩

-- ════════════════════════════════════════════════════════════════════
-- § 2  Commit filtrations
-- ════════════════════════════════════════════════════════════════════

/-- A commit filtration: n commits whose semantic sites form an increasing chain.
    sites i is the semantic site at commit i; mono says inclusions hold. -/
structure CommitFiltration (n : Nat) where
  sites : Fin n → Site
  mono  : ∀ (i j : Fin n), i.val ≤ j.val → (sites i).le (sites j)

-- ════════════════════════════════════════════════════════════════════
-- § 3  Filtration monotonicity theorems
-- ════════════════════════════════════════════════════════════════════

/-- THEOREM 3.1 (Filtration Transitivity):
    The filtration ordering is transitive along arbitrary index jumps. -/
theorem filtration_trans {n : Nat} (F : CommitFiltration n)
    (i j k : Fin n) (hij : i.val ≤ j.val) (hjk : j.val ≤ k.val) :
    (F.sites i).le (F.sites k) :=
  Site.le_trans _ _ _ (F.mono i j hij) (F.mono j k hjk)

/-- THEOREM 3.2 (Initial Dominance):
    The first commit's site is dominated by all later sites. -/
theorem filtration_initial {n : Nat} (F : CommitFiltration (n + 1))
    (i : Fin (n + 1)) :
    (F.sites ⟨0, by omega⟩).le (F.sites i) :=
  F.mono ⟨0, by omega⟩ i (Nat.zero_le _)

/-- THEOREM 3.3 (Terminal Dominance):
    The last commit's site dominates all earlier sites. -/
theorem filtration_terminal {n : Nat} (F : CommitFiltration (n + 1))
    (i : Fin (n + 1)) :
    (F.sites i).le (F.sites ⟨n, Nat.lt_succ_self _⟩) := by
  apply F.mono
  have := i.isLt
  omega

/-- THEOREM 3.4 (Consecutive steps suffice):
    A filtration is determined by knowing sites at consecutive indices. -/
theorem filtration_of_steps {n : Nat}
    (sites : Fin (n + 1) → Site)
    (steps : ∀ i : Fin n, (sites ⟨i.val, by omega⟩).le
                           (sites ⟨i.val + 1, by omega⟩)) :
    CommitFiltration (n + 1) := by
  refine ⟨sites, ?_⟩
  intro i j hij
  induction h : j.val - i.val generalizing i j with
  | zero =>
    have heq : i.val = j.val := by omega
    have : i = j := Fin.ext heq
    rw [this]
    exact Site.le_refl _
  | succ d ih =>
    have hd : i.val + d + 1 = j.val := by omega
    have hmid : i.val + d < n + 1 := by omega
    have hstep := steps ⟨i.val + d, by omega⟩
    simp only at hstep
    have hprev := ih i ⟨i.val + d, by omega⟩ (by omega) (by omega)
    exact Site.le_trans _ _ _ hprev hstep

-- ════════════════════════════════════════════════════════════════════
-- § 4  Persistence modules
-- ════════════════════════════════════════════════════════════════════

/-- A persistence module assigns a rank (dimension) to each commit index.
    The rank models dim H_k(sites i). -/
structure PersistenceModule (n : Nat) where
  rank : Fin n → Nat

/-- The degree-0 persistence module (connected components). -/
def CommitFiltration.ph0 {n : Nat} (F : CommitFiltration n) : PersistenceModule n :=
  { rank := fun i => (F.sites i).beta0 }

/-- The degree-1 persistence module (dependency cycles). -/
def CommitFiltration.ph1 {n : Nat} (F : CommitFiltration n) : PersistenceModule n :=
  { rank := fun i => (F.sites i).beta1 }

/-- The degree-2 persistence module (higher-order tangles). -/
def CommitFiltration.ph2 {n : Nat} (F : CommitFiltration n) : PersistenceModule n :=
  { rank := fun i => (F.sites i).beta2 }

/-- THEOREM 4.1 (PH₁ rank correctness):
    The rank of the degree-1 module equals the first Betti number. -/
theorem ph1_rank_eq_beta1 {n : Nat} (F : CommitFiltration n) (i : Fin n) :
    F.ph1.rank i = (F.sites i).beta1 := rfl

/-- THEOREM 4.2 (Persistence module monotone in β₀):
    The β₀ (components) rank is monotone along the filtration. -/
theorem ph0_rank_mono {n : Nat} (F : CommitFiltration n)
    (i j : Fin n) (hij : i.val ≤ j.val) :
    F.ph0.rank i ≤ F.ph0.rank j :=
  (F.mono i j hij).1

-- ════════════════════════════════════════════════════════════════════
-- § 5  Bars and the barcode
-- ════════════════════════════════════════════════════════════════════

/-- A bar [birth, death) in the persistence barcode.
    birth < death ensures non-degenerate bars. -/
structure Bar where
  birth : Nat
  death : Nat
  lt    : birth < death
  deriving Repr

/-- Length of a bar (number of commits it spans). -/
def Bar.length (b : Bar) : Nat := b.death - b.birth

theorem bar_length_pos (b : Bar) : 0 < b.length := by
  simp [Bar.length]; omega

/-- The symmetric distance between two natural numbers (discrete absolute value). -/
def natDist (a b : Nat) : Nat := if a ≥ b then a - b else b - a

theorem natDist_comm (a b : Nat) : natDist a b = natDist b a := by
  simp only [natDist]
  split_ifs <;> omega

theorem natDist_self (a : Nat) : natDist a a = 0 := by
  simp [natDist]

theorem natDist_triangle (a b c : Nat) :
    natDist a c ≤ natDist a b + natDist b c := by
  simp only [natDist]
  split_ifs <;> omega

theorem natDist_le_iff (a b eps : Nat) :
    natDist a b ≤ eps ↔ (b ≤ a + eps ∧ a ≤ b + eps) := by
  simp only [natDist]
  split_ifs with h
  · constructor
    · intro hle; exact ⟨by omega, by omega⟩
    · intro ⟨_, h2⟩; omega
  · constructor
    · intro hle; exact ⟨by omega, by omega⟩
    · intro ⟨h1, _⟩; omega

/-- Distance between two bars: the max of the birth-endpoint distance
    and the death-endpoint distance.  Defined directly to avoid `max`. -/
def barDist (b1 b2 : Bar) : Nat :=
  if natDist b1.birth b2.birth ≤ natDist b1.death b2.death
  then natDist b1.death b2.death
  else natDist b1.birth b2.birth

theorem barDist_comm (b1 b2 : Bar) : barDist b1 b2 = barDist b2 b1 := by
  simp only [barDist]
  rw [natDist_comm b1.birth b2.birth, natDist_comm b1.death b2.death]

theorem barDist_le_of (b1 b2 : Bar) (eps : Nat)
    (hb : natDist b1.birth b2.birth ≤ eps)
    (hd : natDist b1.death b2.death ≤ eps) :
    barDist b1 b2 ≤ eps := by
  simp only [barDist]
  split_ifs <;> [exact hd; exact hb]

-- ════════════════════════════════════════════════════════════════════
-- § 6  ε-interleaving and stability
-- ════════════════════════════════════════════════════════════════════

/-- Two filtrations are ε-interleaved if their Betti numbers agree within ε
    at every commit index.  This is the discrete approximation to the
    categorical ε-interleaving of persistence modules. -/
def interleavedBy {n : Nat} (F G : CommitFiltration n) (eps : Nat) : Prop :=
  ∀ i : Fin n,
    natDist (F.sites i).beta0 (G.sites i).beta0 ≤ eps ∧
    natDist (F.sites i).beta1 (G.sites i).beta1 ≤ eps ∧
    natDist (F.sites i).beta2 (G.sites i).beta2 ≤ eps

/-- ε-interleaving is symmetric. -/
theorem interleaving_symm {n : Nat} (F G : CommitFiltration n) (eps : Nat)
    (h : interleavedBy F G eps) : interleavedBy G F eps := by
  intro i
  obtain ⟨h0, h1, h2⟩ := h i
  exact ⟨by rwa [natDist_comm], by rwa [natDist_comm], by rwa [natDist_comm]⟩

/-- 0-interleaved filtrations have identical Betti numbers. -/
theorem interleaving_zero {n : Nat} (F G : CommitFiltration n)
    (h : interleavedBy F G 0) (i : Fin n) :
    F.sites i = G.sites i := by
  have ⟨h0, h1, h2⟩ := h i
  simp [natDist] at h0 h1 h2
  split_ifs at h0 h1 h2 <;>
  { cases F.sites i; cases G.sites i; simp_all; omega }

/-- THEOREM 6.1 (Bar Stability):
    If two bars have ε-close endpoints, their barDist is ≤ ε. -/
theorem bar_stability (b d b' d' eps : Nat)
    (hlt : b < d) (hlt' : b' < d')
    (hb : natDist b b' ≤ eps) (hd : natDist d d' ≤ eps) :
    barDist ⟨b, d, hlt⟩ ⟨b', d', hlt'⟩ ≤ eps :=
  barDist_le_of _ _ _ hb hd

/-- THEOREM 6.2 (Discrete Stability for H₁):
    ε-interleaved filtrations have ε-close degree-1 Betti numbers. -/
theorem discrete_stability_h1 {n : Nat} (F G : CommitFiltration n) (eps : Nat)
    (h : interleavedBy F G eps) (i : Fin n) :
    natDist (F.sites i).beta1 (G.sites i).beta1 ≤ eps :=
  (h i).2.1

/-- THEOREM 6.3 (Stability for all degrees):
    ε-interleaved filtrations have ε-close Betti numbers in all degrees. -/
theorem discrete_stability_all {n : Nat} (F G : CommitFiltration n) (eps : Nat)
    (h : interleavedBy F G eps) (i : Fin n) :
    natDist (F.sites i).beta0 (G.sites i).beta0 ≤ eps ∧
    natDist (F.sites i).beta1 (G.sites i).beta1 ≤ eps ∧
    natDist (F.sites i).beta2 (G.sites i).beta2 ≤ eps :=
  h i

/-- THEOREM 6.4 (Triangle inequality for interleaving):
    If F is ε₁-interleaved with G and G is ε₂-interleaved with H,
    then F is (ε₁ + ε₂)-interleaved with H. -/
theorem interleaving_triangle {n : Nat} (F G H : CommitFiltration n) (e1 e2 : Nat)
    (h1 : interleavedBy F G e1) (h2 : interleavedBy G H e2) :
    interleavedBy F H (e1 + e2) := by
  intro i
  obtain ⟨fg0, fg1, fg2⟩ := h1 i
  obtain ⟨gh0, gh1, gh2⟩ := h2 i
  exact ⟨Nat.le_trans (natDist_triangle _ (G.sites i).beta0 _) (Nat.add_le_add fg0 gh0),
         Nat.le_trans (natDist_triangle _ (G.sites i).beta1 _) (Nat.add_le_add fg1 gh1),
         Nat.le_trans (natDist_triangle _ (G.sites i).beta2 _) (Nat.add_le_add fg2 gh2)⟩

-- ════════════════════════════════════════════════════════════════════
-- § 7  Obstruction classes (birth–death pairs)
-- ════════════════════════════════════════════════════════════════════

/-- An obstruction class in H₁: a cycle born at some commit,
    possibly dying later when the circular dependency is resolved. -/
structure ObstructionClass where
  bornAt  : Nat          -- commit index where this cycle appeared
  diedAt  : Option Nat   -- Some j = resolved at commit j; None = still open
  deriving Repr

/-- A transient obstruction: born and resolved within a threshold window. -/
def ObstructionClass.isTransient (o : ObstructionClass) (threshold : Nat) : Prop :=
  ∃ j, o.diedAt = some j ∧ j - o.bornAt ≤ threshold

/-- A persistent obstruction: never resolved (infinite bar). -/
def ObstructionClass.isPersistent (o : ObstructionClass) : Prop :=
  o.diedAt = none

/-- THEOREM 7.1: Transient obstructions are resolved (cannot be persistent). -/
theorem transient_implies_resolved (o : ObstructionClass) (t : Nat)
    (h : o.isTransient t) : ¬ o.isPersistent := by
  obtain ⟨j, hj, _⟩ := h
  simp [ObstructionClass.isPersistent, hj]

/-- A bug-fix pair: convert an obstruction with known death to a bar. -/
def ObstructionClass.toBar (o : ObstructionClass) (d : Nat) (hlt : o.bornAt < d)
    (hdie : o.diedAt = some d) : Bar :=
  ⟨o.bornAt, d, hlt⟩

/-- THEOREM 7.2: The bar derived from an obstruction class has the same birth. -/
theorem obstruction_bar_birth (o : ObstructionClass) (d : Nat)
    (hlt : o.bornAt < d) (hdie : o.diedAt = some d) :
    (o.toBar d hlt hdie).birth = o.bornAt := rfl

-- ════════════════════════════════════════════════════════════════════
-- § 8  Architectural health
-- ════════════════════════════════════════════════════════════════════

/-- Architectural health: β₁ does not grow by more than 1 over the full history
    (allowing one new tolerated cycle per release). -/
def isHealthy {n : Nat} (F : CommitFiltration (n + 1)) : Prop :=
  (F.sites ⟨n, Nat.lt_succ_self _⟩).beta1 ≤
  (F.sites ⟨0, by omega⟩).beta1 + 1

/-- Architectural decay with explicit threshold: β₁ grew beyond the threshold. -/
def isDecaying {n : Nat} (F : CommitFiltration (n + 1)) (threshold : Nat) : Prop :=
  (F.sites ⟨0, by omega⟩).beta1 + threshold <
  (F.sites ⟨n, Nat.lt_succ_self _⟩).beta1

/-- A healthy filtration is not decaying at threshold 1. -/
theorem healthy_not_decaying {n : Nat} (F : CommitFiltration (n + 1))
    (h : isHealthy F) : ¬ isDecaying F 2 := by
  simp [isHealthy, isDecaying] at *
  omega

/-- THEOREM 8.1 (Health Stability):
    If F is healthy and G is ε-interleaved with F,
    then G's final β₁ is bounded by F's initial β₁ + 1 + 2ε. -/
theorem health_stability {n : Nat} (F G : CommitFiltration (n + 1)) (eps : Nat)
    (h : interleavedBy F G eps) (hF : isHealthy F) :
    (G.sites ⟨n, Nat.lt_succ_self _⟩).beta1 ≤
    (F.sites ⟨0, by omega⟩).beta1 + 1 + 2 * eps := by
  have h0 := (h ⟨0, by omega⟩).2.1
  have hn := (h ⟨n, Nat.lt_succ_self _⟩).2.1
  rw [natDist_le_iff] at h0 hn
  obtain ⟨_, h0r⟩ := h0
  obtain ⟨hnl, _⟩ := hn
  simp only [isHealthy] at hF
  omega

/-- THEOREM 8.2 (Decay persists under perturbation):
    If F is healthy and threshold exceeds 1 + 2ε, then G is not decaying
    at that threshold when G is ε-interleaved with F. -/
theorem no_decay_under_interleaving {n : Nat} (F G : CommitFiltration (n + 1))
    (eps threshold : Nat) (h : interleavedBy F G eps)
    (hF : isHealthy F) (hthresh : 1 + 2 * eps < threshold) :
    ¬ isDecaying G threshold := by
  have hbound := health_stability F G eps h hF
  simp only [isDecaying]
  omega

-- ════════════════════════════════════════════════════════════════════
-- § 9  Total Betti count
-- ════════════════════════════════════════════════════════════════════

/-- Total Betti count: the sum β₀ + β₁ + β₂ is a rough complexity measure. -/
def Site.totalBetti (s : Site) : Nat := s.beta0 + s.beta1 + s.beta2

/-- THEOREM 9.1 (Betti count monotone along filtration):
    Betti counts increase monotonically in a valid filtration. -/
theorem total_betti_mono (s t : Site) (h : s.le t) :
    s.totalBetti ≤ t.totalBetti := by
  simp [Site.totalBetti, Site.le] at *
  obtain ⟨h0, h1, h2⟩ := h
  omega

theorem filtration_betti_growth {n : Nat} (F : CommitFiltration (n + 1))
    (i j : Fin (n + 1)) (hij : i.val ≤ j.val) :
    (F.sites i).totalBetti ≤ (F.sites j).totalBetti :=
  total_betti_mono _ _ (F.mono i j hij)

-- ════════════════════════════════════════════════════════════════════
-- § 10  Grand stability package
-- ════════════════════════════════════════════════════════════════════

/-- **THEOREM 10.1 (Grand Stability Package)** for Paper 16.
    Collects the three main results:
      (i)   Filtration transitivity (monotonicity along commit history)
      (ii)  PH₁ rank equals β₁ (functorial correctness)
      (iii) Discrete stability: ε-interleaving implies ε-close H₁ ranks
      (iv)  Triangle inequality for natDist (metric property) -/
theorem grand_stability_package :
    (∀ n (F : CommitFiltration n) (i j k : Fin n),
        i.val ≤ j.val → j.val ≤ k.val → (F.sites i).le (F.sites k)) ∧
    (∀ n (F : CommitFiltration n) (i : Fin n),
        F.ph1.rank i = (F.sites i).beta1) ∧
    (∀ n (F G : CommitFiltration n) (eps : Nat) (i : Fin n),
        interleavedBy F G eps →
        natDist (F.sites i).beta1 (G.sites i).beta1 ≤ eps) ∧
    (∀ (a b c : Nat), natDist a c ≤ natDist a b + natDist b c) :=
  ⟨fun _ F i j k hij hjk => filtration_trans F i j k hij hjk,
   fun _ F i => ph1_rank_eq_beta1 F i,
   fun _ F G eps i h => discrete_stability_h1 F G eps h i,
   natDist_triangle⟩

end JudgmentGeometry.Paper16
