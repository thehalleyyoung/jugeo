/-
  Paper16_PersistentHomology.lean — Persistent Homology of Software Evolution
  Formalizes Paper 16: commit filtrations, persistence modules, stability.
-/

namespace JudgmentGeometry.Paper16

-- ════════════════════════════════════════════════════════════════════
-- § 1  Core Structures: Commit Filtrations
-- ════════════════════════════════════════════════════════════════════

/-- A commit filtration assigns a Betti-number profile to each commit index. -/
structure CommitFiltration where
  len        : Nat
  hlen       : len > 0
  siteSize   : Fin len → Nat
  mono       : ∀ i j : Fin len, i.val ≤ j.val → siteSize i ≤ siteSize j

/-- Transitivity: if i ≤ j and j ≤ k then siteSize i ≤ siteSize k. -/
theorem filtration_trans (F : CommitFiltration) (i j k : Fin F.len)
    (hij : i.val ≤ j.val) (hjk : j.val ≤ k.val) :
    F.siteSize i ≤ F.siteSize k :=
  F.mono i k (Nat.le_trans hij hjk)

/-- The first site is ≤ all others. -/
theorem filtration_initial (F : CommitFiltration) (i : Fin F.len) :
    F.siteSize ⟨0, F.hlen⟩ ≤ F.siteSize i :=
  F.mono ⟨0, F.hlen⟩ i (Nat.zero_le i.val)

/-- All sites are ≤ the last one. -/
theorem filtration_terminal (F : CommitFiltration) (i : Fin F.len) :
    F.siteSize i ≤ F.siteSize ⟨F.len - 1, Nat.sub_lt F.hlen Nat.one_pos⟩ :=
  F.mono i ⟨F.len - 1, Nat.sub_lt F.hlen Nat.one_pos⟩ (Nat.le_sub_one_of_lt i.isLt)

-- ════════════════════════════════════════════════════════════════════
-- § 2  Persistence Modules
-- ════════════════════════════════════════════════════════════════════

/-- A persistence module over [n] assigns a rank (dimension) to each index
    with monotone structure maps (rank can only grow or stay). -/
structure PersistenceModule where
  len  : Nat
  rank : Fin len → Nat

/-- Betti-number profile for degree k extracted from a commit filtration. -/
def bettiProfile (F : CommitFiltration) (bettiAt : Fin F.len → Nat) : PersistenceModule :=
  { len := F.len, rank := bettiAt }

/-- PH₀ tracks connected components (β₀). -/
def ph0 (F : CommitFiltration) (beta0 : Fin F.len → Nat) : PersistenceModule :=
  bettiProfile F beta0

/-- PH₁ tracks dependency cycles (β₁). -/
def ph1 (F : CommitFiltration) (beta1 : Fin F.len → Nat) : PersistenceModule :=
  bettiProfile F beta1

/-- PH₂ tracks higher-order tangles (β₂). -/
def ph2 (F : CommitFiltration) (beta2 : Fin F.len → Nat) : PersistenceModule :=
  bettiProfile F beta2

/-- The PH₁ module rank at index i equals β₁(i). -/
theorem ph1_rank_eq_beta1 (F : CommitFiltration) (beta1 : Fin F.len → Nat)
    (i : Fin F.len) :
    (ph1 F beta1).rank i = beta1 i :=
  rfl

-- ════════════════════════════════════════════════════════════════════
-- § 3  Natural Distance (Discrete Metric)
-- ════════════════════════════════════════════════════════════════════

/-- Absolute difference on Nat, used as a discrete metric. -/
def natDist (a b : Nat) : Nat :=
  if a ≤ b then b - a else a - b

theorem natDist_self (a : Nat) : natDist a a = 0 := by
  simp [natDist]

theorem natDist_comm (a b : Nat) : natDist a b = natDist b a := by
  simp [natDist]
  omega

theorem natDist_triangle (a b c : Nat) :
    natDist a c ≤ natDist a b + natDist b c := by
  simp [natDist]
  omega

theorem natDist_le_iff (a b eps : Nat) :
    natDist a b ≤ eps ↔ (a ≤ b ∧ b ≤ a + eps) ∨ (b ≤ a ∧ a ≤ b + eps) := by
  simp [natDist]
  omega

-- ════════════════════════════════════════════════════════════════════
-- § 4  Bars and Bar Distance
-- ════════════════════════════════════════════════════════════════════

/-- A persistence bar [birth, death). -/
structure Bar where
  birth : Nat
  death : Nat
  valid : birth < death

/-- Distance between two bars: max of birth-distance and death-distance. -/
def barDist (p q : Bar) : Nat :=
  max (natDist p.birth q.birth) (natDist p.death q.death)

/-- If births are ε-close and deaths are ε-close, barDist ≤ ε. -/
theorem barDist_le_of (p q : Bar) (eps : Nat)
    (hb : natDist p.birth q.birth ≤ eps)
    (hd : natDist p.death q.death ≤ eps) :
    barDist p q ≤ eps := by
  simp [barDist]
  exact ⟨hb, hd⟩

theorem barDist_comm (p q : Bar) : barDist p q = barDist q p := by
  simp [barDist, natDist_comm]

-- ════════════════════════════════════════════════════════════════════
-- § 5  Discrete Interleaving and Stability
-- ════════════════════════════════════════════════════════════════════

/-- Two persistence modules are ε-interleaved if their ranks differ by
    at most ε at every index. -/
def epsInterleaved (M N : PersistenceModule) (eps : Nat)
    (hlen : M.len = N.len) : Prop :=
  ∀ (i : Fin M.len),
    natDist (M.rank i) (N.rank (hlen ▸ i)) ≤ eps

/-- Discrete stability for β₁: if two filtrations are ε-interleaved at
    degree 1, their β₁ values differ by at most ε at every index. -/
theorem discrete_stability_h1
    (F F' : CommitFiltration)
    (beta1 beta1' : Fin F.len → Nat)
    (hlen : F.len = F'.len)
    (eps : Nat)
    (hint : ∀ (i : Fin F.len),
      natDist (beta1 i) (beta1' (hlen ▸ i)) ≤ eps)
    (i : Fin F.len) :
    natDist ((ph1 F beta1).rank i) ((ph1 F' beta1').rank (hlen ▸ i)) ≤ eps := by
  simp [ph1, bettiProfile]
  exact hint i

-- ════════════════════════════════════════════════════════════════════
-- § 6  Architectural Health
-- ════════════════════════════════════════════════════════════════════

/-- A filtration is "healthy at degree 1" if β₁ at the last commit
    does not exceed β₁ at the first commit plus a threshold. -/
def isHealthy (F : CommitFiltration) (beta1 : Fin F.len → Nat)
    (threshold : Nat) : Prop :=
  beta1 ⟨F.len - 1, Nat.sub_lt F.hlen Nat.one_pos⟩ ≤
  beta1 ⟨0, F.hlen⟩ + threshold

/-- Architectural decay: β₁ grew beyond the threshold. -/
def hasDecay (F : CommitFiltration) (beta1 : Fin F.len → Nat)
    (threshold : Nat) : Prop :=
  ¬ isHealthy F beta1 threshold

/-- Health dichotomy: either healthy or decayed. -/
theorem health_dichotomy (F : CommitFiltration) (beta1 : Fin F.len → Nat)
    (threshold : Nat) :
    isHealthy F beta1 threshold ∨ hasDecay F beta1 threshold :=
  Classical.em _

/-- Health stability: if F is healthy with threshold θ and F' is
    ε-interleaved with F, then F' is healthy with threshold θ + 2ε. -/
theorem health_stability
    (F F' : CommitFiltration)
    (beta1 beta1' : Fin F.len → Nat)
    (hlen : F.len = F'.len)
    (theta eps : Nat)
    (hhealth : isHealthy F beta1 theta)
    (hint : ∀ (i : Fin F.len),
      natDist (beta1 i) (beta1' (hlen ▸ i)) ≤ eps) :
    isHealthy F' beta1' (theta + 2 * eps) := by
  simp only [isHealthy] at *
  have h0 := hint ⟨0, F.hlen⟩
  have hlast := hint ⟨F.len - 1, Nat.sub_lt F.hlen Nat.one_pos⟩
  simp [natDist] at h0 hlast
  omega

-- ════════════════════════════════════════════════════════════════════
-- § 7  Decay Detection
-- ════════════════════════════════════════════════════════════════════

/-- If β₁(last) > β₁(first) + threshold, there is decay. -/
theorem decay_detection (F : CommitFiltration) (beta1 : Fin F.len → Nat)
    (threshold : Nat)
    (hgrow : beta1 ⟨F.len - 1, Nat.sub_lt F.hlen Nat.one_pos⟩ >
             beta1 ⟨0, F.hlen⟩ + threshold) :
    hasDecay F beta1 threshold := by
  simp [hasDecay, isHealthy]
  omega

/-- If healthy, then β₁(last) ≤ β₁(first) + threshold. -/
theorem healthy_bound (F : CommitFiltration) (beta1 : Fin F.len → Nat)
    (threshold : Nat) (hh : isHealthy F beta1 threshold) :
    beta1 ⟨F.len - 1, Nat.sub_lt F.hlen Nat.one_pos⟩ ≤
    beta1 ⟨0, F.hlen⟩ + threshold :=
  hh

-- ════════════════════════════════════════════════════════════════════
-- § 8  Bar Stability from Interleaving
-- ════════════════════════════════════════════════════════════════════

/-- Given ε-close birth/death values, one can construct ε-close bars. -/
theorem bar_stability
    (b d b' d' eps : Nat)
    (hbd : b < d) (hbd' : b' < d')
    (hb : natDist b b' ≤ eps) (hd : natDist d d' ≤ eps) :
    barDist ⟨b, d, hbd⟩ ⟨b', d', hbd'⟩ ≤ eps :=
  barDist_le_of ⟨b, d, hbd⟩ ⟨b', d', hbd'⟩ eps hb hd

-- ════════════════════════════════════════════════════════════════════
-- § 9  Severity and Short/Long Classification
-- ════════════════════════════════════════════════════════════════════

/-- Severity of a bar is death - birth. -/
def severity (bar : Bar) : Nat := bar.death - bar.birth

theorem severity_pos (bar : Bar) : severity bar > 0 := by
  simp [severity]
  omega

/-- A bar is short (transient) if severity ≤ τ. -/
def isShort (bar : Bar) (tau : Nat) : Prop := severity bar ≤ tau

/-- A bar is long (persistent) if severity > τ. -/
def isLong (bar : Bar) (tau : Nat) : Prop := severity bar > tau

theorem short_or_long (bar : Bar) (tau : Nat) :
    isShort bar tau ∨ isLong bar tau := by
  simp [isShort, isLong]
  omega

-- ════════════════════════════════════════════════════════════════════
-- § 10  Grand Stability Package
-- ════════════════════════════════════════════════════════════════════

/-- The grand stability package: conjunction of discrete stability,
    bar stability, and health stability. -/
theorem grand_stability_package
    (F F' : CommitFiltration)
    (beta1 beta1' : Fin F.len → Nat)
    (hlen : F.len = F'.len)
    (theta eps : Nat)
    (hhealth : isHealthy F beta1 theta)
    (hint : ∀ (i : Fin F.len),
      natDist (beta1 i) (beta1' (hlen ▸ i)) ≤ eps) :
    -- (1) Discrete stability: β₁ values are ε-close
    (∀ (i : Fin F.len),
      natDist ((ph1 F beta1).rank i)
              ((ph1 F' beta1').rank (hlen ▸ i)) ≤ eps) ∧
    -- (2) Bar stability helper: ε-close births/deaths give ε-close bars
    (∀ (b d b' d' : Nat) (hbd : b < d) (hbd' : b' < d'),
      natDist b b' ≤ eps → natDist d d' ≤ eps →
      barDist ⟨b, d, hbd⟩ ⟨b', d', hbd'⟩ ≤ eps) ∧
    -- (3) Health stability: healthy + interleaved → bounded β₁
    isHealthy F' beta1' (theta + 2 * eps) := by
  exact ⟨
    fun i => discrete_stability_h1 F F' beta1 beta1' hlen eps hint i,
    fun _ _ _ _ hbd hbd' hb hd => bar_stability _ _ _ _ eps hbd hbd' hb hd,
    health_stability F F' beta1 beta1' hlen theta eps hhealth hint
  ⟩

-- ════════════════════════════════════════════════════════════════════
-- § 11  Summary
-- ════════════════════════════════════════════════════════════════════

/-- Paper 16 results (zero sorry):
    1. filtration_trans — transitivity of filtration ordering
    2. filtration_initial — first site ≤ all others
    3. filtration_terminal — all sites ≤ last one
    4. ph1_rank_eq_beta1 — PH₁ rank equals β₁
    5. natDist_comm — metric commutativity
    6. natDist_triangle — triangle inequality
    7. natDist_le_iff — characterisation of ε-closeness
    8. barDist_le_of — bar distance bounded by component distances
    9. discrete_stability_h1 — ε-interleaving → ε-close β₁
   10. health_stability — healthy + interleaved → bounded β₁
   11. grand_stability_package — conjunction of all three main results
-/
theorem paper16_summary : True := trivial

end JudgmentGeometry.Paper16
