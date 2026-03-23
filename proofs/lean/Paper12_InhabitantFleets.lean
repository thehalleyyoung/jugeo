/-
  Paper12_InhabitantFleets.lean — Inhabitant Fleet Convergence
  Formalizes Paper 12 of the Judgment Geometry series.

  Key results (NO sorry):
    • FleetMember / FleetState / BackpressureSignal structures
    • competitionScore = Nat.max: monotone, commutative, idempotent, associative
    • BackpressureSignal throttling: bounded by threshold when critical
    • TwoWorkerFleet converges to a stable state in exactly one step
    • Stable fleet is a fixed point; applyN n times leaves it unchanged
    • maxFleetScore upper-bounds every member's score
    • Backpressure boundedness and non-critical monotonicity
    • Pair auction: pairWinner score ≥ both inputs
    • fleet_permanently_stable: once converged, stays stable forever
-/

namespace JudgmentGeometry.Paper12

-- ════════════════════════════════════════════════════════════════════
-- § 1  Core data structures
-- ════════════════════════════════════════════════════════════════════

/-- Abstract fleet member: identifier and integral evidence score. -/
structure FleetMember where
  id    : Nat
  score : Nat       -- proportional to evidence_score × K
  deriving Repr, DecidableEq

/-- Abstract fleet state: ordered list of members and a round counter. -/
structure FleetState where
  members : List FleetMember
  round   : Nat
  deriving Repr

/-- Backpressure signal: raw instability and the critical threshold. -/
structure BackpressureSignal where
  instability : Nat   -- instability × K, integer-scaled
  threshold   : Nat   -- critical threshold × K
  deriving Repr

/-- A signal is critical when instability strictly exceeds the threshold. -/
def BackpressureSignal.isCritical (s : BackpressureSignal) : Bool :=
  s.instability > s.threshold

-- ════════════════════════════════════════════════════════════════════
-- § 2  Competition function
-- ════════════════════════════════════════════════════════════════════

/-- Competition winner score = max of the two evidence scores.
    Implements InhabitantProposal.compete_with semantics. -/
def competitionScore (a b : Nat) : Nat := Nat.max a b

/-- Competition is monotone: winner score ≥ the left argument. -/
theorem competitionScore_ge_left (a b : Nat) : a ≤ competitionScore a b :=
  Nat.le_max_left a b

/-- Competition is monotone: winner score ≥ the right argument. -/
theorem competitionScore_ge_right (a b : Nat) : b ≤ competitionScore a b :=
  Nat.le_max_right a b

/-- Competition is commutative. -/
theorem competitionScore_comm (a b : Nat) :
    competitionScore a b = competitionScore b a := by
  simp [competitionScore, Nat.max_comm]

/-- Competition is idempotent. -/
theorem competitionScore_idem (a : Nat) : competitionScore a a = a := by
  simp [competitionScore]

/-- Competition is associative. -/
theorem competitionScore_assoc (a b c : Nat) :
    competitionScore (competitionScore a b) c =
    competitionScore a (competitionScore b c) := by
  simp [competitionScore, Nat.max_assoc]

/-- The winner equals one of the two inputs. -/
theorem competitionScore_eq (a b : Nat) :
    competitionScore a b = a ∨ competitionScore a b = b := by
  simp only [competitionScore]
  rcases Nat.lt_or_ge a b with h | h
  · right; exact Nat.max_eq_right (Nat.le_of_lt h)
  · left; exact Nat.max_eq_left h

-- ════════════════════════════════════════════════════════════════════
-- § 3  Backpressure throttling
-- ════════════════════════════════════════════════════════════════════

/-- Throttle a score under a backpressure signal.
    Critical: cap at threshold.  Non-critical: pass through. -/
def throttle (sig : BackpressureSignal) (score : Nat) : Nat :=
  if sig.isCritical then Nat.min score sig.threshold else score

/-- Throttle never increases a score. -/
theorem throttle_le_score (sig : BackpressureSignal) (score : Nat) :
    throttle sig score ≤ score := by
  unfold throttle
  by_cases h : sig.isCritical
  · simp [h]; exact Nat.min_le_left score sig.threshold
  · simp [h]

/-- Under a critical signal, the throttled score is at most the threshold. -/
theorem throttle_critical_le_threshold (sig : BackpressureSignal)
    (score : Nat) (h : sig.isCritical = true) :
    throttle sig score ≤ sig.threshold := by
  unfold throttle; simp [h]; exact Nat.min_le_right score sig.threshold

/-- Under a non-critical signal (isCritical = false), throttle is the identity. -/
theorem throttle_noncritical_id (sig : BackpressureSignal)
    (score : Nat) (h : sig.isCritical = false) :
    throttle sig score = score := by
  unfold throttle; simp [h]

-- ════════════════════════════════════════════════════════════════════
-- § 4  Two-worker fleet: convergence in one step (Corollary 5.2)
-- ════════════════════════════════════════════════════════════════════

/-- Concrete two-worker fleet for the convergence corollary. -/
@[ext]
structure TwoWorkerFleet where
  score0 : Nat
  score1 : Nat
  deriving Repr, DecidableEq

/-- A two-worker fleet is stable when both member scores are equal. -/
def TwoWorkerFleet.isStable (fleet : TwoWorkerFleet) : Prop :=
  fleet.score0 = fleet.score1

/-- One competition step: each worker takes the max of both scores. -/
def twoWorkerStep (fleet : TwoWorkerFleet) : TwoWorkerFleet :=
  let s := competitionScore fleet.score0 fleet.score1
  { score0 := s, score1 := s }

/-- **Corollary 5.2**: a two-worker fleet is stable after exactly one step. -/
theorem twoWorker_stable_after_one_step (fleet : TwoWorkerFleet) :
    (twoWorkerStep fleet).isStable := by
  simp [TwoWorkerFleet.isStable, twoWorkerStep]

/-- Under a step, member 0's score does not decrease. -/
theorem twoWorker_step_ge_score0 (fleet : TwoWorkerFleet) :
    fleet.score0 ≤ (twoWorkerStep fleet).score0 :=
  competitionScore_ge_left fleet.score0 fleet.score1

/-- Under a step, member 1's score does not decrease. -/
theorem twoWorker_step_ge_score1 (fleet : TwoWorkerFleet) :
    fleet.score1 ≤ (twoWorkerStep fleet).score1 :=
  competitionScore_ge_right fleet.score0 fleet.score1

/-- A stable two-worker fleet is a fixed point of the competition step. -/
theorem twoWorker_stable_is_fixed (fleet : TwoWorkerFleet)
    (h : fleet.isStable) : twoWorkerStep fleet = fleet := by
  obtain ⟨s0, s1⟩ := fleet
  simp only [TwoWorkerFleet.isStable] at h
  subst h
  simp [twoWorkerStep, competitionScore]

-- ════════════════════════════════════════════════════════════════════
-- § 5  General fleet stability
-- ════════════════════════════════════════════════════════════════════

/-- A fleet state is stable if all members carry the same evidence score. -/
def FleetState.isStable (s : FleetState) : Prop :=
  ∀ m₁ m₂ : FleetMember,
    m₁ ∈ s.members → m₂ ∈ s.members → m₁.score = m₂.score

/-- An empty fleet is trivially stable. -/
theorem empty_fleet_stable :
    FleetState.isStable { members := [], round := 0 } := by
  intro m₁ _ h₁
  exact absurd h₁ (List.not_mem_nil m₁)

/-- A single-member fleet is trivially stable. -/
theorem singleton_fleet_stable (m : FleetMember) :
    FleetState.isStable { members := [m], round := 0 } := by
  intro m₁ m₂ h₁ h₂
  simp [List.mem_singleton] at h₁ h₂
  subst h₁; subst h₂; rfl

-- ════════════════════════════════════════════════════════════════════
-- § 6  Maximum fleet score (upper-bound invariant, Lemma 5.3)
-- ════════════════════════════════════════════════════════════════════

/-- Maximum evidence score over a fleet member list; recursively defined. -/
def maxFleetScore : List FleetMember → Nat
  | []       => 0
  | hd :: tl => Nat.max hd.score (maxFleetScore tl)

/-- **Lemma 5.3 (Max upper bound)**: every member's score ≤ maxFleetScore. -/
theorem maxFleetScore_ge (members : List FleetMember) (m : FleetMember)
    (hm : m ∈ members) : m.score ≤ maxFleetScore members := by
  induction members with
  | nil  => exact absurd hm (List.not_mem_nil m)
  | cons hd tl ih =>
    rcases List.mem_cons.mp hm with rfl | hmtl
    · exact Nat.le_max_left _ _
    · exact Nat.le_trans (ih hmtl) (Nat.le_max_right _ _)

/-- The maximum fleet score is non-negative. -/
theorem maxFleetScore_nonneg (members : List FleetMember) :
    0 ≤ maxFleetScore members :=
  Nat.zero_le _

/-- If all members have score = s and the list is non-empty,
    then maxFleetScore = s.  Witnesses Φ = 0 at a stable fleet. -/
theorem maxFleetScore_stable (members : List FleetMember) (s : Nat)
    (hall : ∀ m ∈ members, m.score = s) (hne : members ≠ []) :
    maxFleetScore members = s := by
  induction members with
  | nil  => exact absurd rfl hne
  | cons hd tl ih =>
    have hhd : hd.score = s := hall hd (List.mem_cons_self hd tl)
    by_cases htl : tl = []
    · -- single-element list: maxFleetScore [hd] = max hd.score 0 = hd.score = s
      subst htl
      have hmatch : maxFleetScore [hd] = Nat.max hd.score 0 := rfl
      rw [hmatch, hhd]
      exact Nat.max_eq_left (Nat.zero_le s)
    · -- cons case: maxFleetScore (hd :: tl) = max hd.score (maxFleetScore tl)
      have htl_eq : maxFleetScore tl = s :=
        ih (fun m hm => hall m (List.mem_cons_of_mem hd hm)) htl
      have hmatch : maxFleetScore (hd :: tl) = Nat.max hd.score (maxFleetScore tl) := rfl
      rw [hmatch, hhd, htl_eq]
      exact Nat.max_eq_left (Nat.le_refl s)

-- ════════════════════════════════════════════════════════════════════
-- § 7  Backpressure boundedness (Corollary 5.3)
-- ════════════════════════════════════════════════════════════════════

/-- **Corollary 5.3**: under a critical signal, throttled competition score
    is bounded by the signal threshold. -/
theorem backpressure_boundedness (sig : BackpressureSignal)
    (h : sig.isCritical = true) (a b : Nat) :
    throttle sig (competitionScore a b) ≤ sig.threshold :=
  throttle_critical_le_threshold sig (competitionScore a b) h

/-- Under non-critical backpressure, competition score is non-decreasing. -/
theorem noncritical_competition_nondecreasing (sig : BackpressureSignal)
    (h : sig.isCritical = false) (a b : Nat) :
    a ≤ throttle sig (competitionScore a b) := by
  rw [throttle_noncritical_id sig _ h]
  exact competitionScore_ge_left a b

/-- Both a and b are bounded after throttle under critical backpressure. -/
theorem both_bounded_after_critical_throttle (sig : BackpressureSignal)
    (h : sig.isCritical = true) (a b : Nat) :
    throttle sig (competitionScore a b) ≤ sig.threshold ∧
    throttle sig (competitionScore b a) ≤ sig.threshold :=
  ⟨backpressure_boundedness sig h a b,
   backpressure_boundedness sig h b a⟩

-- ════════════════════════════════════════════════════════════════════
-- § 8  Pair auction score upper-bound (Proposition 4.1)
-- ════════════════════════════════════════════════════════════════════

/-- Simplified bid record for auction score proofs. -/
structure BidRecord where
  bidScore           : Nat
  overlapCompat      : Nat
  backpressTolerance : Nat
  deriving Repr

/-- Auction total score = bidScore × overlapCompat × backpressTolerance. -/
def BidRecord.totalScore (b : BidRecord) : Nat :=
  b.bidScore * b.overlapCompat * b.backpressTolerance

/-- Two-bid auction winner: pick the bid with higher auction score.
    Corresponds to BidAggregator.pick_winner restricted to two bids. -/
def pairWinner (a b : BidRecord) : BidRecord :=
  if a.totalScore ≥ b.totalScore then a else b

/-- **Proposition 4.1a**: pair winner score ≥ left bid's score. -/
theorem pairWinner_ge_left (a b : BidRecord) :
    a.totalScore ≤ (pairWinner a b).totalScore := by
  unfold pairWinner
  by_cases h : a.totalScore ≥ b.totalScore
  · rw [if_pos h]; exact Nat.le_refl _
  · rw [if_neg h]; exact Nat.le_of_lt (Nat.lt_of_not_le h)

/-- **Proposition 4.1b**: pair winner score ≥ right bid's score. -/
theorem pairWinner_ge_right (a b : BidRecord) :
    b.totalScore ≤ (pairWinner a b).totalScore := by
  unfold pairWinner
  by_cases h : a.totalScore ≥ b.totalScore
  · rw [if_pos h]; exact h
  · rw [if_neg h]; exact Nat.le_refl _

/-- Winner equals the max-score bid: pairWinner is sound for two bids. -/
theorem pairWinner_ub (a b : BidRecord) (c : BidRecord)
    (hca : c.totalScore ≤ a.totalScore) (hcb : c.totalScore ≤ b.totalScore) :
    c.totalScore ≤ (pairWinner a b).totalScore := by
  unfold pairWinner
  by_cases h : a.totalScore ≥ b.totalScore
  · rw [if_pos h]; exact hca
  · rw [if_neg h]; exact hcb

-- ════════════════════════════════════════════════════════════════════
-- § 9  Summary: Fleet Convergence Theorem (Theorem 5.1)
-- ════════════════════════════════════════════════════════════════════

/-- Helper: apply a function n times to an initial value. -/
def applyN {α : Type} (f : α → α) : Nat → α → α
  | 0,     a => a
  | n + 1, a => applyN f n (f a)

/-- **Theorem 5.1 (base case)**: after one step, a two-worker fleet is stable
    and no member score has decreased. -/
theorem fleet_convergence_base_case (fleet : TwoWorkerFleet) :
    (twoWorkerStep fleet).isStable ∧
    fleet.score0 ≤ (twoWorkerStep fleet).score0 ∧
    fleet.score1 ≤ (twoWorkerStep fleet).score1 :=
  ⟨twoWorker_stable_after_one_step fleet,
   twoWorker_step_ge_score0 fleet,
   twoWorker_step_ge_score1 fleet⟩

/-- A stable fleet is unchanged by any number of competition steps.
    Proves the "Converged" phase in the fleet lifecycle is absorbing. -/
theorem stable_fleet_fixed_by_applyN (fleet : TwoWorkerFleet)
    (h : fleet.isStable) (n : Nat) :
    applyN twoWorkerStep n fleet = fleet := by
  induction n with
  | zero    => rfl
  | succ k ih =>
    show applyN twoWorkerStep k (twoWorkerStep fleet) = fleet
    rw [twoWorker_stable_is_fixed fleet h]
    exact ih

/-- **Main convergence theorem**: for any n ≥ 1, the fleet is permanently stable.
    Follows from convergence in 1 step + fixed-point property. -/
theorem fleet_permanently_stable (fleet : TwoWorkerFleet) (n : Nat)
    (hn : 1 ≤ n) :
    (applyN twoWorkerStep n fleet).isStable := by
  cases n with
  | zero  => omega
  | succ k =>
    show (applyN twoWorkerStep k (twoWorkerStep fleet)).isStable
    have hstable : (twoWorkerStep fleet).isStable :=
      twoWorker_stable_after_one_step fleet
    rw [stable_fleet_fixed_by_applyN (twoWorkerStep fleet) hstable k]
    exact hstable

end JudgmentGeometry.Paper12
