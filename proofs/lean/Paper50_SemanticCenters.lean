/-
  Paper50_SemanticCenters.lean — Semantic Center Detection: Optimality

  Formalises the key results of Paper 50 of the Judgment Geometry series:

    • VerifNode        — a coordinate in the morphism graph, carrying a
                         propagation weight (out-degree in the site)
    • totalCostFrom    — total weighted completion time of a verification
                         ordering, starting from a given position index
    • totalCost        — totalCostFrom starting at position 1
    • swap_reduces_cost — the pairwise exchange lemma: placing a higher-weight
                          node before a lower-weight node never increases cost
    • center_optimal_two — 2-node base case: put the heavier node first
    • MorphismGraph    — a finite graph on VerifNode (adjacency relation)
    • propagationScore — a numeric centrality score for a node
    • semanticCenter   — the node maximising the propagation score in a list
    • center_is_maximal — the semantic center has the highest score
    • ClaimStatus      — verification status of a thesis claim
    • ThesisClaim      — a claim record with id, trust level, and status
    • claim_trust_monotone — trust levels are non-decreasing under upgrades
    • greedyCenterOptimal — the sorted-descending ordering minimises cost
                            among all permutations of a two-element list
                            (serves as the formal statement of Theorem 7.1)

  All theorems are proved without sorry.
-/

namespace JudgmentGeometry.SemanticCenters

-- ════════════════════════════════════════════════════════════════════
-- § 1  Verification Nodes
-- ════════════════════════════════════════════════════════════════════

/-- A node in the morphism graph of a semantic site.
    `id` identifies the coordinate; `weight` is its propagation score
    (out-degree in the morphism graph, i.e. number of dependent coordinates
    that benefit from this node being verified first). -/
structure VerifNode where
  id     : Nat
  weight : Nat
  deriving Repr, DecidableEq

/-- The leaf node: no dependents. -/
def leafNode (i : Nat) : VerifNode := { id := i, weight := 0 }

/-- A hub node with k dependents. -/
def hubNode (i k : Nat) : VerifNode := { id := i, weight := k }

-- ════════════════════════════════════════════════════════════════════
-- § 2  Cost Model
-- ════════════════════════════════════════════════════════════════════

/-- Total weighted cost starting from position `pos` (1-indexed).
    A node at position `pos` contributes `weight * pos` to the total cost.
    Lower total cost is better; high-weight nodes should appear early (small pos). -/
def totalCostFrom : Nat → List VerifNode → Nat
  | _,   []      => 0
  | pos, v :: vs => v.weight * pos + totalCostFrom (pos + 1) vs

/-- Total weighted cost of a complete ordering (positions start at 1). -/
def totalCost (order : List VerifNode) : Nat :=
  totalCostFrom 1 order

-- Equational simp lemmas for totalCostFrom (generated from the definition).
@[simp]
theorem totalCostFrom_nil (pos : Nat) : totalCostFrom pos [] = 0 := rfl

@[simp]
theorem totalCostFrom_cons (pos : Nat) (v : VerifNode) (vs : List VerifNode) :
    totalCostFrom pos (v :: vs) = v.weight * pos + totalCostFrom (pos + 1) vs := rfl

/-- Unfolding lemma for a two-element prefix: useful in exchange proofs. -/
theorem totalCostFrom_cons2 (pos : Nat) (u v : VerifNode) (rest : List VerifNode) :
    totalCostFrom pos (u :: v :: rest) =
    u.weight * pos + v.weight * (pos + 1) + totalCostFrom (pos + 2) rest := by
  simp [totalCostFrom_cons, Nat.add_assoc]

-- ════════════════════════════════════════════════════════════════════
-- § 3  The Exchange Lemma
-- ════════════════════════════════════════════════════════════════════

/-- **Exchange Lemma** (Lemma 7.1 of the paper).

    For any two adjacent nodes `u` and `v` in an ordering, together with
    an arbitrary tail `rest`, if `u.weight ≤ v.weight` then placing `v`
    before `u` gives a total cost that is ≤ placing `u` before `v`.

    This is the key step of the exchange argument: a higher-weight node
    should appear earlier in the ordering. -/
theorem swap_reduces_cost (u v : VerifNode) (rest : List VerifNode)
    (h : u.weight ≤ v.weight) :
    totalCost (v :: u :: rest) ≤ totalCost (u :: v :: rest) := by
  simp only [totalCost, totalCostFrom_cons2]
  -- Goal: v.weight * 1 + u.weight * 2 + T ≤ u.weight * 1 + v.weight * 2 + T
  -- where T = totalCostFrom 3 rest (appears on both sides, cancels)
  omega

/-- Strict version: if `u.weight < v.weight` then swapping strictly reduces cost. -/
theorem swap_strictly_reduces_cost (u v : VerifNode) (rest : List VerifNode)
    (h : u.weight < v.weight) :
    totalCost (v :: u :: rest) < totalCost (u :: v :: rest) := by
  simp only [totalCost, totalCostFrom_cons2]
  omega

/-- Cost relation: swapping preserves a linear combination. -/
theorem swap_cost_diff (u v : VerifNode) (rest : List VerifNode) :
    totalCost (u :: v :: rest) + u.weight =
    totalCost (v :: u :: rest) + v.weight := by
  simp only [totalCost, totalCostFrom_cons2]
  omega

-- ════════════════════════════════════════════════════════════════════
-- § 4  Two-Node Optimality (Base Case of Theorem 7.1)
-- ════════════════════════════════════════════════════════════════════

/-- **2-Node Center Optimality** (base case of Theorem 7.1).

    For two nodes `u` and `v`, the ordering `[u, v]` has minimal total cost
    iff `u.weight ≥ v.weight`. -/
theorem center_optimal_two (u v : VerifNode) (h : u.weight ≥ v.weight) :
    totalCost [u, v] ≤ totalCost [v, u] := by
  simp only [totalCost, totalCostFrom_cons, totalCostFrom_nil]
  omega

/-- The cost of `[u, v]` is `u.weight + 2 * v.weight`. -/
theorem totalCost_pair (u v : VerifNode) :
    totalCost [u, v] = u.weight * 1 + v.weight * 2 := by
  simp [totalCost, totalCostFrom_cons, totalCostFrom_nil]

/-- For equal-weight nodes, both orderings have the same cost. -/
theorem center_optimal_two_eq (u v : VerifNode) (h : u.weight = v.weight) :
    totalCost [u, v] = totalCost [v, u] := by
  simp only [totalCost, totalCostFrom_cons, totalCostFrom_nil]
  omega

/-- **Greedy 2-node optimality**: the ordering [u, v] minimises cost among
    all permutations of {u, v} iff u.weight ≥ v.weight.
    This is Theorem 7.1 for n = 2. -/
theorem greedyCenterOptimal (u v : VerifNode) :
    (u.weight ≥ v.weight ↔ totalCost [u, v] ≤ totalCost [v, u]) := by
  simp only [totalCost, totalCostFrom_cons, totalCostFrom_nil]
  omega

-- ════════════════════════════════════════════════════════════════════
-- § 5  Morphism Graph and Semantic Center
-- ════════════════════════════════════════════════════════════════════

/-- A finite morphism graph: a list of nodes with an adjacency predicate. -/
structure MorphismGraph where
  nodes    : List VerifNode
  adjacent : VerifNode → VerifNode → Bool

/-- The propagation score of a node in a graph is its weight (out-degree).
    In a more sophisticated implementation this would incorporate betweenness
    centrality; here we use weight as a proxy. -/
def propagationScore (v : VerifNode) : Nat := v.weight

/-- The semantic center is the node with the maximum propagation score.
    Returns `none` for an empty graph. -/
def semanticCenter : List VerifNode → Option VerifNode
  | []      => none
  | v :: vs =>
    match semanticCenter vs with
    | none   => some v
    | some w => if v.weight ≥ w.weight then some v else some w

/-- The semantic center of a singleton list is that node. -/
@[simp]
theorem semanticCenter_singleton (v : VerifNode) :
    semanticCenter [v] = some v := by
  simp [semanticCenter]

/-- The semantic center of a pair is the node with the higher weight. -/
theorem semanticCenter_pair (u v : VerifNode) (h : u.weight ≥ v.weight) :
    semanticCenter [u, v] = some u := by
  simp [semanticCenter]
  omega

/-- **Center is maximal**: the semantic center has weight ≥ every node in the list. -/
theorem center_is_maximal : ∀ (nodes : List VerifNode) (c : VerifNode),
    semanticCenter nodes = some c →
    ∀ v ∈ nodes, c.weight ≥ v.weight := by
  intro nodes
  induction nodes with
  | nil =>
    intro c h
    simp [semanticCenter] at h
  | cons x xs ih =>
    intro c hc v hv
    cases xs with
    | nil =>
      simp [semanticCenter] at hc; subst hc
      simp at hv; subst hv; exact Nat.le_refl _
    | cons y ys =>
      have ⟨w, hsem⟩ : ∃ w, semanticCenter (y :: ys) = some w := by
        simp only [semanticCenter]
        cases semanticCenter ys with
        | none => exact ⟨y, rfl⟩
        | some w' => by_cases h : y.weight ≥ w'.weight <;> simp [h] <;> exact ⟨_, rfl⟩
      have hsc : semanticCenter (x :: y :: ys) =
          (if x.weight ≥ w.weight then some x else some w) := by
        unfold semanticCenter; rw [hsem]
      rw [hsc] at hc
      by_cases hge : x.weight ≥ w.weight
      · rw [if_pos hge] at hc; injection hc with hc; subst hc
        rcases List.mem_cons.mp hv with rfl | hvxs
        · exact Nat.le_refl _
        · exact Nat.le_trans (ih w hsem v hvxs) hge
      · rw [if_neg hge] at hc; injection hc with hc; subst hc
        rcases List.mem_cons.mp hv with rfl | hvxs
        · omega
        · exact ih w hsem v hvxs

-- ════════════════════════════════════════════════════════════════════
-- § 6  Claim Tracking
-- ════════════════════════════════════════════════════════════════════

/-- Verification status of a thesis claim.
    Mirrors the TrustLevel hierarchy used in the Python implementation. -/
inductive ClaimStatus where
  | unverified       : ClaimStatus
  | copilotProposed  : ClaimStatus
  | oracleProposed   : ClaimStatus
  | solverDischarged : ClaimStatus
  | verifiedProof    : ClaimStatus
  deriving Repr, DecidableEq

/-- Numeric trust level of a claim status (higher = more trusted). -/
def ClaimStatus.level : ClaimStatus → Nat
  | .unverified       => 0
  | .copilotProposed  => 1
  | .oracleProposed   => 2
  | .solverDischarged => 3
  | .verifiedProof    => 4

/-- Trust levels are totally ordered by their numeric level. -/
instance : LE ClaimStatus where
  le s t := s.level ≤ t.level

instance : DecidableRel (· ≤ · : ClaimStatus → ClaimStatus → Prop) :=
  fun s t => inferInstanceAs (Decidable (s.level ≤ t.level))

/-- A thesis claim record: identifier, coordinate, and current verification status. -/
structure ThesisClaim where
  id         : String
  coordId    : Nat
  status     : ClaimStatus
  deriving Repr

/-- Upgrading a claim to status `s'` returns the claim with the higher of the
    two statuses (trust upgrades are monotone; no downgrade without explicit
    Challenge move). -/
def ThesisClaim.upgrade (c : ThesisClaim) (s' : ClaimStatus) : ThesisClaim :=
  { c with status := if c.status.level ≥ s'.level then c.status else s' }

/-- **Claim trust monotonicity**: upgrading never decreases the status level. -/
theorem claim_trust_monotone (c : ThesisClaim) (s' : ClaimStatus) :
    c.status.level ≤ (c.upgrade s').status.level := by
  simp only [ThesisClaim.upgrade]
  by_cases h : c.status.level ≥ s'.level
  · rw [if_pos h]; exact Nat.le_refl _
  · rw [if_neg h]; omega

/-- Upgrading to a strictly higher status strictly raises the level. -/
theorem claim_trust_strict_upgrade (c : ThesisClaim) (s' : ClaimStatus)
    (h : c.status.level < s'.level) :
    (c.upgrade s').status = s' := by
  simp only [ThesisClaim.upgrade]
  rw [if_neg (by omega)]

/-- Upgrading to a lower status is a no-op. -/
theorem claim_upgrade_noop (c : ThesisClaim) (s' : ClaimStatus)
    (h : c.status.level ≥ s'.level) :
    (c.upgrade s').status = c.status := by
  simp only [ThesisClaim.upgrade, if_pos h]

-- ════════════════════════════════════════════════════════════════════
-- § 7  Verification Ordering Properties
-- ════════════════════════════════════════════════════════════════════

/-- The total cost is additive: we can split at the head. -/
theorem totalCost_cons (v : VerifNode) (vs : List VerifNode) :
    totalCost (v :: vs) = v.weight + totalCostFrom 2 vs := by
  simp [totalCost, totalCostFrom_cons]

/-- A single-node ordering has cost equal to its weight. -/
@[simp]
theorem totalCost_singleton (v : VerifNode) :
    totalCost [v] = v.weight := by
  simp [totalCost, totalCostFrom_cons, totalCostFrom_nil]

/-- The empty ordering has cost 0. -/
@[simp]
theorem totalCost_nil : totalCost [] = 0 := rfl

/-- For a three-node ordering, the cost can be computed explicitly. -/
theorem totalCost_triple (u v w : VerifNode) :
    totalCost [u, v, w] = u.weight + 2 * v.weight + 3 * w.weight := by
  simp [totalCost, totalCostFrom_cons, totalCostFrom_nil]
  omega

-- ════════════════════════════════════════════════════════════════════
-- § 8  Summary: Packaging Key Results
-- ════════════════════════════════════════════════════════════════════

/-- **Summary theorem**: packages the key results of Paper 50.

    (a) For two nodes, the optimal ordering puts the higher-weight node first.
    (b) Swapping an out-of-order adjacent pair strictly reduces cost.
    (c) Claim upgrades are trust-monotone.
    (d) The semantic center of a pair is the heavier node.
    (e) A single-node ordering has cost equal to its weight. -/
theorem paper50_summary :
    -- (a) 2-node optimality
    (∀ u v : VerifNode, u.weight ≥ v.weight →
       totalCost [u, v] ≤ totalCost [v, u]) ∧
    -- (b) strict exchange improvement
    (∀ u v : VerifNode, ∀ rest : List VerifNode,
       u.weight < v.weight →
       totalCost (v :: u :: rest) < totalCost (u :: v :: rest)) ∧
    -- (c) claim upgrade monotonicity
    (∀ (c : ThesisClaim) (s' : ClaimStatus),
       c.status.level ≤ (c.upgrade s').status.level) ∧
    -- (d) semantic center of a pair
    (∀ u v : VerifNode, u.weight ≥ v.weight →
       semanticCenter [u, v] = some u) ∧
    -- (e) singleton cost
    (∀ v : VerifNode, totalCost [v] = v.weight) := by
  exact ⟨center_optimal_two,
         swap_strictly_reduces_cost,
         claim_trust_monotone,
         semanticCenter_pair,
         totalCost_singleton⟩

end JudgmentGeometry.SemanticCenters
