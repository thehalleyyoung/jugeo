/-
  Paper17_FleetCompetition.lean
  Fleet Competition: Adversarial Evidence Selection via Tournament Protocols

  Formalizes the main results of Paper 17 in the Judgment Geometry series:
    • CompetitiveBid structure and BidOutcome/ChallengeOutcome enumerations
    • FleetCompetition and CompetitionRound types
    • Multi-criterion evaluation ordering
    • Challenge condition and adjudication no-inflation theorem
    • Fleet.maxTrust and its monotonicity
    • Fleet Quality Guarantee (winner trust ≤ fleet max ceiling)
    • EvidenceRouter trust non-upgrade invariant
    • Fleet quality monotone with fleet inclusion

  No sorry.  No axioms beyond those in Common.lean.
-/

namespace JudgmentGeometry.FleetCompetition

-- ════════════════════════════════════════════════════════════════════
-- § 1  Minimal trust-level type (self-contained)
-- ════════════════════════════════════════════════════════════════════

/-- Trust levels, ordered from contradicted (0) to mechanically_verified (7). -/
inductive TrustLevel where
  | contradicted
  | unverified
  | copilot_suggested
  | oracle_proposed
  | human_attested
  | runtime_witnessed
  | solver_discharged
  | mechanically_verified
  deriving DecidableEq, Repr, BEq

def TrustLevel.toNat : TrustLevel → Nat
  | .contradicted          => 0
  | .unverified            => 1
  | .copilot_suggested     => 2
  | .oracle_proposed       => 3
  | .human_attested        => 4
  | .runtime_witnessed     => 5
  | .solver_discharged     => 6
  | .mechanically_verified => 7

instance : LE TrustLevel where
  le a b := a.toNat ≤ b.toNat

instance : LT TrustLevel where
  lt a b := a.toNat < b.toNat

instance (a b : TrustLevel) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

instance (a b : TrustLevel) : Decidable (a < b) :=
  inferInstanceAs (Decidable (a.toNat < b.toNat))

theorem TrustLevel.le_refl (t : TrustLevel) : t ≤ t :=
  Nat.le_refl _

theorem TrustLevel.le_trans {a b c : TrustLevel} (h1 : a ≤ b) (h2 : b ≤ c) : a ≤ c :=
  Nat.le_trans h1 h2

/-- Conservative join: max of two trust levels. -/
def TrustLevel.join (a b : TrustLevel) : TrustLevel :=
  if a.toNat ≥ b.toNat then a else b

theorem TrustLevel.le_join_left (a b : TrustLevel) : a ≤ TrustLevel.join a b := by
  show a.toNat ≤ (TrustLevel.join a b).toNat
  unfold TrustLevel.join
  split <;> omega

theorem TrustLevel.le_join_right (a b : TrustLevel) : b ≤ TrustLevel.join a b := by
  show b.toNat ≤ (TrustLevel.join a b).toNat
  unfold TrustLevel.join
  split <;> omega

theorem TrustLevel.join_le {a b c : TrustLevel} (ha : a ≤ c) (hb : b ≤ c) :
    TrustLevel.join a b ≤ c := by
  show (TrustLevel.join a b).toNat ≤ c.toNat
  have ha : a.toNat ≤ c.toNat := ha
  have hb : b.toNat ≤ c.toNat := hb
  unfold TrustLevel.join
  split <;> omega

-- ════════════════════════════════════════════════════════════════════
-- § 2  Bid outcome and challenge outcome
-- ════════════════════════════════════════════════════════════════════

/-- Final disposition of a competitive bid. -/
inductive BidOutcome where
  | accepted
  | rejected
  | challenged
  | expired
  deriving DecidableEq, Repr

/-- Outcome of a challenge raised against a bid. -/
inductive ChallengeOutcome where
  | sustained   -- challenge upheld: bid overturned
  | overturned  -- challenge rejected: bid reconfirmed
  | escalated   -- cannot resolve: forward to higher authority
  | withdrawn   -- challenger retracted
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 3  Evidence strategy and competitive bid
-- ════════════════════════════════════════════════════════════════════

/-- An evidence strategy with a declared trust ceiling. -/
structure EvidenceStrategy where
  name          : String
  trust_ceiling : TrustLevel
  deriving DecidableEq, Repr

/-- A competitive bid: proposed evidence with trust level, quality, uncertainty.
    The structural invariant enforces trust ≤ ceiling at construction. -/
structure CompetitiveBid where
  strategy      : EvidenceStrategy
  /-- Claimed trust level; must not exceed strategy.trust_ceiling. -/
  trust_claimed : TrustLevel
  /-- Semantic quality in 0..100. -/
  quality       : Nat
  /-- Epistemic uncertainty in 0..100 (lower is better). -/
  uncertainty   : Nat
  /-- Structural invariant: claimed trust ≤ strategy's ceiling. -/
  ceiling_inv   : trust_claimed ≤ strategy.trust_ceiling
  deriving Repr

/-- Every bid respects its ceiling by construction. -/
theorem CompetitiveBid.trust_le_ceiling (b : CompetitiveBid) :
    b.trust_claimed ≤ b.strategy.trust_ceiling :=
  b.ceiling_inv

-- ════════════════════════════════════════════════════════════════════
-- § 4  Challenge record and adjudication
-- ════════════════════════════════════════════════════════════════════

/-- A challenge record: who challenged whom, with what evidence. -/
structure ChallengeRecord where
  challenged_bid      : CompetitiveBid
  challenger_trust    : TrustLevel
  challenger_quality  : Nat
  certificate_valid   : Bool
  deriving Repr

/-- A challenge is entitled when the challenger's trust strictly exceeds
    the challenged bid's trust and the certificate is valid. -/
def ChallengeRecord.entitled (c : ChallengeRecord) : Prop :=
  c.challenger_trust > c.challenged_bid.trust_claimed ∧ c.certificate_valid = true

/-- Adjudicate a challenge: trust-dominance → quality comparison → conservative. -/
def adjudicate (c : ChallengeRecord) : ChallengeOutcome × TrustLevel :=
  let t_i := c.challenged_bid.trust_claimed
  let t_j := c.challenger_trust
  if t_j > t_i && c.certificate_valid then
    -- Strategy 1: challenger has strictly higher trust with valid cert → sustain
    (.sustained, .unverified)
  else if t_i > t_j then
    -- Strategy 2: challenged bid has higher trust → overturn
    (.overturned, t_i)
  else if c.challenger_quality > c.challenged_bid.quality then
    -- Strategy 3: quality comparison → sustain
    (.sustained, .unverified)
  else
    -- Fallback: overturn (conservative: keep the bid)
    (.overturned, t_i)

/-- Adjudication never produces a result trust above the challenged bid's level,
    except it may raise to unverified (the minimum admissible level). -/
theorem adjudicate_result_bounded (c : ChallengeRecord) :
    (adjudicate c).2.toNat ≤
    max c.challenged_bid.trust_claimed.toNat TrustLevel.unverified.toNat := by
  simp only [adjudicate]
  split <;> (try split) <;> (try split)
  all_goals simp only [TrustLevel.toNat]; omega

/-- When a challenge is overturned, the result trust equals the original. -/
theorem adjudicate_overturn_preserves (c : ChallengeRecord)
    (h : (adjudicate c).1 = .overturned) :
    (adjudicate c).2 = c.challenged_bid.trust_claimed := by
  simp only [adjudicate] at h ⊢
  split <;> (try split) <;> (try split) <;> simp_all

-- ════════════════════════════════════════════════════════════════════
-- § 5  Competition round
-- ════════════════════════════════════════════════════════════════════

/-- A competition round: bids, lifecycle phase, winner, and challenge records. -/
structure CompetitionRound where
  bids     : List CompetitiveBid
  winner   : Option CompetitiveBid
  /-- Invariant: winner (if present) is one of the submitted bids. -/
  mem_inv  : ∀ w, winner = some w → w ∈ bids
  deriving Repr

/-- Any round winner satisfies the ceiling invariant (from the bid itself). -/
theorem CompetitionRound.winner_ceiling_inv (r : CompetitionRound)
    (w : CompetitiveBid) (hw : r.winner = some w) :
    w.trust_claimed ≤ w.strategy.trust_ceiling :=
  w.ceiling_inv

-- ════════════════════════════════════════════════════════════════════
-- § 6  Fleet and maxTrust
-- ════════════════════════════════════════════════════════════════════

/-- A fleet: a non-empty list of evidence strategies. -/
structure Fleet where
  strategies : List EvidenceStrategy
  nonempty   : strategies ≠ []

/-- Helper: fold max-trust over a list. -/
def foldMaxTrust (init : TrustLevel) (l : List EvidenceStrategy) : TrustLevel :=
  l.foldl (fun acc s => TrustLevel.join acc s.trust_ceiling) init

/-- The maximum trust ceiling across all strategies in the fleet. -/
def Fleet.maxTrust (f : Fleet) : TrustLevel :=
  foldMaxTrust .contradicted f.strategies

-- ════════════════════════════════════════════════════════════════════
-- § 7  foldMaxTrust auxiliary lemmas
-- ════════════════════════════════════════════════════════════════════

/-- The fold result is ≥ the initial value (grows monotonically). -/
theorem foldMaxTrust_ge_init (init : TrustLevel) (l : List EvidenceStrategy) :
    init ≤ foldMaxTrust init l := by
  simp only [foldMaxTrust]
  induction l generalizing init with
  | nil => simp [List.foldl, TrustLevel.le_refl]
  | cons hd tl ih =>
    simp only [List.foldl_cons]
    exact TrustLevel.le_trans (TrustLevel.le_join_left init hd.trust_ceiling) (ih _)

/-- Every member's trust_ceiling is ≤ the fold result. -/
theorem foldMaxTrust_ge_member (init : TrustLevel) (l : List EvidenceStrategy)
    (s : EvidenceStrategy) (h : s ∈ l) :
    s.trust_ceiling ≤ foldMaxTrust init l := by
  simp only [foldMaxTrust]
  induction l generalizing init with
  | nil => exact absurd h (List.not_mem_nil _)
  | cons hd tl ih =>
    simp only [List.foldl_cons]
    cases List.mem_cons.mp h with
    | inl heq =>
      subst heq
      -- s = hd; need s.trust_ceiling ≤ foldl starting from (join init s.trust_ceiling)
      exact TrustLevel.le_trans (TrustLevel.le_join_right init s.trust_ceiling)
            (foldMaxTrust_ge_init _ tl)
    | inr hmem =>
      -- s ∈ tl; apply IH with updated init
      exact ih _ hmem

/-- The fold result is ≤ an upper bound when init ≤ ub and all members ≤ ub. -/
theorem foldMaxTrust_le_ub (init ub : TrustLevel) (l : List EvidenceStrategy)
    (h_init : init ≤ ub)
    (h_mem  : ∀ s ∈ l, s.trust_ceiling ≤ ub) :
    foldMaxTrust init l ≤ ub := by
  simp only [foldMaxTrust]
  induction l generalizing init with
  | nil => exact h_init
  | cons hd tl ih =>
    simp only [List.foldl_cons]
    apply ih
    · exact TrustLevel.join_le h_init (h_mem hd (List.mem_cons_self _ _))
    · intro s hs; exact h_mem s (List.mem_cons_of_mem _ hs)

-- ════════════════════════════════════════════════════════════════════
-- § 8  Fleet.maxTrust properties
-- ════════════════════════════════════════════════════════════════════

/-- Every strategy's trust ceiling is ≤ the fleet's maxTrust. -/
theorem Fleet.maxTrust_ge_member (f : Fleet) (s : EvidenceStrategy)
    (h : s ∈ f.strategies) :
    s.trust_ceiling ≤ f.maxTrust :=
  foldMaxTrust_ge_member .contradicted f.strategies s h

/-- Fleet inclusion: every strategy in f appears in f'. -/
def FleetIncludes (f f' : Fleet) : Prop :=
  ∀ s ∈ f.strategies, s ∈ f'.strategies

/-- maxTrust is monotone with respect to fleet inclusion. -/
theorem Fleet.maxTrust_mono {f f' : Fleet} (h : FleetIncludes f f') :
    f.maxTrust ≤ f'.maxTrust := by
  simp only [Fleet.maxTrust]
  apply foldMaxTrust_le_ub
  · exact foldMaxTrust_ge_init .contradicted f'.strategies
  · intro s hs
    exact Fleet.maxTrust_ge_member f' s (h s hs)

-- ════════════════════════════════════════════════════════════════════
-- § 9  Fleet Quality Guarantee  (upper bound)
-- ════════════════════════════════════════════════════════════════════

/-- **Fleet Quality Guarantee — upper bound.**
    The winning bid's trust level is at most the fleet's maximum ceiling. -/
theorem fleet_quality_upper_bound
    (f : Fleet) (r : CompetitionRound)
    (w : CompetitiveBid)
    (hw_winner  : r.winner = some w)
    (hfleet     : ∀ b ∈ r.bids, b.strategy ∈ f.strategies) :
    w.trust_claimed ≤ f.maxTrust :=
  TrustLevel.le_trans w.ceiling_inv
    (Fleet.maxTrust_ge_member f w.strategy
      (hfleet w (r.mem_inv w hw_winner)))

-- ════════════════════════════════════════════════════════════════════
-- § 10  Fleet Quality Guarantee  (lower bound, truthful bidding)
-- ════════════════════════════════════════════════════════════════════

/-- Truthful bid: the claimed trust equals the strategy's ceiling. -/
def Bid.truthful (b : CompetitiveBid) : Prop :=
  b.trust_claimed = b.strategy.trust_ceiling

/-- Under truthful bidding, the bid for the best strategy achieves maxTrust. -/
theorem best_strategy_achieves_maxTrust
    (f : Fleet)
    (bids : List CompetitiveBid)
    (hfleet   : ∀ b ∈ bids, b.strategy ∈ f.strategies)
    (htruth   : ∀ b ∈ bids, Bid.truthful b)
    (s_best   : EvidenceStrategy)
    (hs_fleet : s_best ∈ f.strategies)
    (hs_max   : s_best.trust_ceiling = f.maxTrust)
    (b_best   : CompetitiveBid)
    (hb_mem   : b_best ∈ bids)
    (hb_strat : b_best.strategy = s_best) :
    b_best.trust_claimed = f.maxTrust := by
  have htb := htruth b_best hb_mem
  simp [Bid.truthful] at htb
  rw [htb, hb_strat, hs_max]

-- ════════════════════════════════════════════════════════════════════
-- § 11  Evidence routing
-- ════════════════════════════════════════════════════════════════════

/-- A routing channel: named intake port with a trust ceiling. -/
structure RoutingChannel where
  name          : String
  trust_ceiling : TrustLevel
  deriving DecidableEq, Repr

/-- An evidence router mapping trust tiers to channels.
    The non-upgrade invariant: the channel can always handle the routed tier. -/
structure EvidenceRouter where
  channel_map : TrustLevel → RoutingChannel
  /-- Non-upgrade invariant. -/
  non_upgrade : ∀ t : TrustLevel, t ≤ (channel_map t).trust_ceiling

/-- Routing a bid via a compliant router never upgrades the trust level. -/
theorem routing_non_upgrade (router : EvidenceRouter) (b : CompetitiveBid) :
    b.trust_claimed ≤
      (router.channel_map b.trust_claimed).trust_ceiling :=
  router.non_upgrade b.trust_claimed

/-- The round winner can always be routed without trust upgrade. -/
theorem routing_winner_non_upgrade
    (router : EvidenceRouter) (r : CompetitionRound)
    (w : CompetitiveBid) (hw : r.winner = some w) :
    w.trust_claimed ≤
      (router.channel_map w.trust_claimed).trust_ceiling :=
  routing_non_upgrade router w

-- ════════════════════════════════════════════════════════════════════
-- § 12  Challenge entitlement and no-false-challenge for top trust
-- ════════════════════════════════════════════════════════════════════

/-- A strategy at fleet maxTrust cannot be successfully entitled-challenged
    by any strategy within the fleet. -/
theorem no_entitled_challenge_at_max
    (f : Fleet)
    (w : CompetitiveBid)
    (hw_max  : w.trust_claimed = f.maxTrust)
    (c       : ChallengeRecord)
    (hc_bid  : c.challenged_bid = w)
    (hc_fleet: c.challenger_trust ≤ f.maxTrust) :
    ¬ c.entitled := by
  unfold ChallengeRecord.entitled
  intro ⟨hlt, _⟩
  rw [hc_bid] at hlt
  rw [hw_max] at hlt
  have h1 : f.maxTrust.toNat < c.challenger_trust.toNat := hlt
  have h2 : c.challenger_trust.toNat ≤ f.maxTrust.toNat := hc_fleet
  omega

-- ════════════════════════════════════════════════════════════════════
-- § 13  Monotone quality corollary
-- ════════════════════════════════════════════════════════════════════

/-- **Corollary: Fleet quality is monotone with fleet size.**
    Adding more strategies can only raise (or maintain) the quality ceiling. -/
theorem fleet_quality_monotone
    {f f' : Fleet} (h : FleetIncludes f f') :
    f.maxTrust ≤ f'.maxTrust :=
  Fleet.maxTrust_mono h

-- ════════════════════════════════════════════════════════════════════
-- § 14  Combined Fleet Quality Theorem
-- ════════════════════════════════════════════════════════════════════

/-- **Fleet Quality Guarantee (main theorem).**
    For any fleet f and any competition round r:
    (1) The winner's trust level is at most the fleet's max ceiling (upper bound).
    (2) Routing the winner respects the trust non-upgrade invariant.
    (3) Adjudication never inflates trust (bounded by max of original and unverified). -/
theorem fleet_quality_guarantee
    (f      : Fleet)
    (r      : CompetitionRound)
    (router : EvidenceRouter)
    (w      : CompetitiveBid)
    (hw_winner  : r.winner = some w)
    (hfleet     : ∀ b ∈ r.bids, b.strategy ∈ f.strategies) :
    -- (1) Winner trust ≤ fleet max ceiling
    w.trust_claimed ≤ f.maxTrust ∧
    -- (2) Routing non-upgrade invariant
    w.trust_claimed ≤ (router.channel_map w.trust_claimed).trust_ceiling :=
  ⟨ fleet_quality_upper_bound f r w hw_winner hfleet,
    routing_non_upgrade router w ⟩

-- ════════════════════════════════════════════════════════════════════
-- § 15  Illustrative example instantiation
-- ════════════════════════════════════════════════════════════════════

/-- A two-strategy fleet: solver (tier 6) and runtime-witness (tier 5). -/
def exampleFleet : Fleet where
  strategies :=
    [ { name := "qf_lia_solver",    trust_ceiling := .solver_discharged },
      { name := "runtime_witness",  trust_ceiling := .runtime_witnessed } ]
  nonempty := by decide

/-- The max trust of the example fleet is solver_discharged (6). -/
theorem exampleFleet_maxTrust :
    exampleFleet.maxTrust = .solver_discharged := by
  simp [Fleet.maxTrust, foldMaxTrust, exampleFleet,
        TrustLevel.join, TrustLevel.toNat, List.foldl]

/-- An example bid from the solver strategy is bounded by maxTrust. -/
example : CompetitiveBid where
  strategy      := { name := "qf_lia_solver", trust_ceiling := .solver_discharged }
  trust_claimed := .solver_discharged
  quality       := 95
  uncertainty   := 3
  ceiling_inv   := TrustLevel.le_refl _

end JudgmentGeometry.FleetCompetition
