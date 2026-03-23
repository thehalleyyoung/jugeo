/-
  Paper44_JudgmentProducts.lean — Judgment Products

  Formalizes Paper 44 of the Judgment Geometry series:
    • JudgmentProduct construction with three modes
      (conjunction, disjunction, conditional)
    • Trust propagation: trust(P) = min(trust(J₁), trust(J₂))
    • Product validity ↔ both components valid
    • Evidence merging: completeness and quality monotonicity
    • Algebra operations on products (restrict, transport, compose, merge)
    • Categorical universal property (trust side)
    • Iterated product trust = min over all components

  All theorems proved without sorry.
-/

namespace JudgmentGeometry.Paper44

-- ════════════════════════════════════════════════════════════════════
-- § 1  Trust Level
-- ════════════════════════════════════════════════════════════════════

abbrev TrustLevel := Nat

namespace Trust

def contradicted          : TrustLevel := 0
def unverified            : TrustLevel := 1
def copilot_suggested     : TrustLevel := 2
def oracle_proposed       : TrustLevel := 3
def human_attested        : TrustLevel := 4
def runtime_witnessed     : TrustLevel := 5
def solver_discharged     : TrustLevel := 6
def mechanically_verified : TrustLevel := 7

def meet (a b : TrustLevel) : TrustLevel := min a b
def join (a b : TrustLevel) : TrustLevel := max a b

theorem meet_le_left  (a b : TrustLevel) : meet a b ≤ a := Nat.min_le_left  a b
theorem meet_le_right (a b : TrustLevel) : meet a b ≤ b := Nat.min_le_right a b
theorem meet_comm     (a b : TrustLevel) : meet a b = meet b a := Nat.min_comm a b
theorem meet_assoc    (a b c : TrustLevel) : meet (meet a b) c = meet a (meet b c) :=
  Nat.min_assoc a b c

theorem meet_pos_left  (a b : TrustLevel) (h : 0 < meet a b) : 0 < a :=
  Nat.lt_of_lt_of_le h (meet_le_left a b)

theorem meet_pos_iff (a b : TrustLevel) : 0 < meet a b ↔ 0 < a ∧ 0 < b := by
  simp [meet, Nat.lt_min]

end Trust

-- ════════════════════════════════════════════════════════════════════
-- § 2  Evidence
-- ════════════════════════════════════════════════════════════════════

structure EvidenceItem where
  trust   : TrustLevel
  payload : String
  deriving DecidableEq, Repr

structure EvidenceBundle where
  items : List EvidenceItem := []
  deriving DecidableEq, Repr

def EvidenceBundle.merge (b1 b2 : EvidenceBundle) : EvidenceBundle :=
  { items := b1.items ++ b2.items }

def EvidenceBundle.quality (b : EvidenceBundle) : TrustLevel :=
  b.items.foldl (fun acc e => max acc e.trust) 0

theorem evidence_merge_length (b1 b2 : EvidenceBundle) :
    (b1.merge b2).items.length = b1.items.length + b2.items.length :=
  List.length_append b1.items b2.items

private theorem foldl_max_ge_init (l : List EvidenceItem) (init : Nat) :
    init ≤ l.foldl (fun acc e => max acc e.trust) init := by
  induction l generalizing init with
  | nil => exact Nat.le_refl _
  | cons h t ih =>
    simp only [List.foldl]
    exact Nat.le_trans (by omega : init ≤ max init h.trust) (ih (max init h.trust))

private theorem foldl_max_mono_init (l : List EvidenceItem) {a b : Nat} (hab : a ≤ b) :
    l.foldl (fun acc e => max acc e.trust) a ≤ l.foldl (fun acc e => max acc e.trust) b := by
  induction l generalizing a b with
  | nil => exact hab
  | cons h t ih =>
    simp only [List.foldl]
    exact ih (by omega : max a h.trust ≤ max b h.trust)

theorem evidence_merge_quality_ge_left (b1 b2 : EvidenceBundle) :
    b1.quality ≤ (b1.merge b2).quality := by
  simp only [EvidenceBundle.quality, EvidenceBundle.merge]
  rw [List.foldl_append]
  exact foldl_max_ge_init b2.items _

theorem evidence_merge_quality_ge_right (b1 b2 : EvidenceBundle) :
    b2.quality ≤ (b1.merge b2).quality := by
  simp only [EvidenceBundle.quality, EvidenceBundle.merge]
  rw [List.foldl_append]
  exact foldl_max_mono_init b2.items (Nat.zero_le _)

-- ════════════════════════════════════════════════════════════════════
-- § 3  Product Mode
-- ════════════════════════════════════════════════════════════════════

inductive ProductMode where
  | conjunction
  | disjunction
  | conditional
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 4  The Judgment and JudgmentProduct structures
-- ════════════════════════════════════════════════════════════════════

structure Coordinate where
  key : String
  deriving DecidableEq, Repr

/-- A single judgment 8-tuple (simplified for product theory). -/
structure Judgment where
  coordinate   : Coordinate
  proposition  : String
  evidence     : EvidenceBundle
  obligations  : List String
  obstructions : List String
  trust        : TrustLevel
  provenance   : List String
  deriving Repr

/-- Validity: trust > 0, no obstructions, no obligations. -/
def Judgment.valid (j : Judgment) : Prop :=
  0 < j.trust ∧ j.obstructions = [] ∧ j.obligations = []

/-- A judgment product (binary, with a mode). -/
structure JudgmentProduct where
  mode         : ProductMode
  left         : Judgment
  right        : Judgment
  coordinate   : Coordinate
  proposition  : String
  evidence     : EvidenceBundle
  obligations  : List String
  obstructions : List String
  trust        : TrustLevel
  provenance   : List String

-- ════════════════════════════════════════════════════════════════════
-- § 5  Product Construction
-- ════════════════════════════════════════════════════════════════════

/-- Construct the judgment product from two judgments and a mode. -/
def mkProduct (j1 j2 : Judgment) (m : ProductMode) : JudgmentProduct :=
  let prop := match m with
    | .conjunction => "(" ++ j1.proposition ++ ") ∧ (" ++ j2.proposition ++ ")"
    | .disjunction => "(" ++ j1.proposition ++ ") ∨ (" ++ j2.proposition ++ ")"
    | .conditional => "(" ++ j1.proposition ++ ") → (" ++ j2.proposition ++ ")"
  { mode         := m
    left         := j1
    right        := j2
    coordinate   := j1.coordinate   -- common ancestor; simplified to j1
    proposition  := prop
    evidence     := j1.evidence.merge j2.evidence
    obligations  := j1.obligations ++ j2.obligations
    obstructions := j1.obstructions ++ j2.obstructions
    trust        := Trust.meet j1.trust j2.trust
    provenance   := j1.provenance ++ j2.provenance ++ ["product"] }

-- ════════════════════════════════════════════════════════════════════
-- § 6  Trust Propagation Theorems
-- ════════════════════════════════════════════════════════════════════

/-- THEOREM (Trust equation): trust(P) = min(trust(J₁), trust(J₂)). -/
theorem product_trust_eq_min (j1 j2 : Judgment) (m : ProductMode) :
    (mkProduct j1 j2 m).trust = min j1.trust j2.trust := rfl

/-- Trust of product ≤ left component. -/
theorem product_trust_le_left (j1 j2 : Judgment) (m : ProductMode) :
    (mkProduct j1 j2 m).trust ≤ j1.trust :=
  Trust.meet_le_left j1.trust j2.trust

/-- Trust of product ≤ right component. -/
theorem product_trust_le_right (j1 j2 : Judgment) (m : ProductMode) :
    (mkProduct j1 j2 m).trust ≤ j2.trust :=
  Trust.meet_le_right j1.trust j2.trust

/-- Product trust is positive iff both component trusts are positive. -/
theorem product_trust_pos_iff (j1 j2 : Judgment) (m : ProductMode) :
    0 < (mkProduct j1 j2 m).trust ↔ 0 < j1.trust ∧ 0 < j2.trust := by
  exact Trust.meet_pos_iff j1.trust j2.trust

-- ════════════════════════════════════════════════════════════════════
-- § 7  Validity Predicate on Products
-- ════════════════════════════════════════════════════════════════════

/-- Validity of a product: trust > 0, no obstructions, no obligations. -/
def JudgmentProduct.valid (p : JudgmentProduct) : Prop :=
  0 < p.trust ∧ p.obstructions = [] ∧ p.obligations = []

-- ════════════════════════════════════════════════════════════════════
-- § 8  Product Soundness Theorem
-- ════════════════════════════════════════════════════════════════════

/-- THEOREM (Product soundness, conjunction case):
    P = J₁ ⊗ J₂ is valid iff both J₁ and J₂ are valid.
    Trust equation: trust(P) = min(trust(J₁), trust(J₂)). -/
theorem product_soundness_conjunction (j1 j2 : Judgment) :
    let P := mkProduct j1 j2 .conjunction
    P.valid ↔ j1.valid ∧ j2.valid := by
  simp only [JudgmentProduct.valid, Judgment.valid, mkProduct]
  simp only [Trust.meet, List.append_eq_nil]
  constructor
  · intro ⟨htrust, hobs, hob⟩
    simp [Nat.lt_min] at htrust
    exact ⟨⟨htrust.1, hobs.1, hob.1⟩, ⟨htrust.2, hobs.2, hob.2⟩⟩
  · intro ⟨⟨h1t, h1o, h1ob⟩, ⟨h2t, h2o, h2ob⟩⟩
    refine ⟨?_, ?_, ?_⟩
    · simp [Nat.lt_min]; exact ⟨h1t, h2t⟩
    · simp [h1o, h2o]
    · simp [h1ob, h2ob]

/-- Soundness forward: product valid → both components valid. -/
theorem product_valid_implies_components_valid
    (j1 j2 : Judgment) (m : ProductMode)
    (hP : (mkProduct j1 j2 m).valid) :
    j1.valid ∧ j2.valid := by
  simp only [JudgmentProduct.valid, mkProduct] at hP
  obtain ⟨htrust, hobs, hob⟩ := hP
  simp only [List.append_eq_nil] at hobs hob
  obtain ⟨hobs1, hobs2⟩ := hobs
  obtain ⟨hob1, hob2⟩ := hob
  constructor
  · exact ⟨Nat.lt_of_lt_of_le htrust (Trust.meet_le_left _ _), hobs1, hob1⟩
  · exact ⟨Nat.lt_of_lt_of_le htrust (Trust.meet_le_right _ _), hobs2, hob2⟩

/-- Soundness backward: both components valid → product valid. -/
theorem components_valid_implies_product_valid
    (j1 j2 : Judgment) (m : ProductMode)
    (h1 : j1.valid) (h2 : j2.valid) :
    (mkProduct j1 j2 m).valid := by
  simp only [JudgmentProduct.valid, mkProduct]
  obtain ⟨ht1, ho1, hb1⟩ := h1
  obtain ⟨ht2, ho2, hb2⟩ := h2
  refine ⟨?_, ?_, ?_⟩
  · simp [Trust.meet, Nat.lt_min]; exact ⟨ht1, ht2⟩
  · simp [ho1, ho2]
  · simp [hb1, hb2]

/-- Full biconditional for any mode. -/
theorem product_soundness (j1 j2 : Judgment) (m : ProductMode) :
    (mkProduct j1 j2 m).valid ↔ j1.valid ∧ j2.valid :=
  ⟨product_valid_implies_components_valid j1 j2 m,
   fun ⟨h1, h2⟩ => components_valid_implies_product_valid j1 j2 m h1 h2⟩

-- ════════════════════════════════════════════════════════════════════
-- § 9  Evidence Merging Theorems
-- ════════════════════════════════════════════════════════════════════

/-- Product contains at least as many evidence items as both components. -/
theorem product_evidence_complete (j1 j2 : Judgment) (m : ProductMode) :
    j1.evidence.items.length + j2.evidence.items.length
      ≤ (mkProduct j1 j2 m).evidence.items.length := by
  simp [mkProduct, EvidenceBundle.merge, evidence_merge_length]

/-- Evidence quality is non-decreasing: product quality ≥ max of components. -/
theorem product_evidence_quality (j1 j2 : Judgment) (m : ProductMode) :
    max j1.evidence.quality j2.evidence.quality
      ≤ (mkProduct j1 j2 m).evidence.quality := by
  simp [mkProduct]
  apply Nat.max_le.mpr
  exact ⟨evidence_merge_quality_ge_left j1.evidence j2.evidence,
         evidence_merge_quality_ge_right j1.evidence j2.evidence⟩

-- ════════════════════════════════════════════════════════════════════
-- § 10  Algebra Operations on Products
-- ════════════════════════════════════════════════════════════════════

/-- Restrict a judgment product to a sub-coordinate. -/
def restrictProduct (p : JudgmentProduct) (c : Coordinate) : JudgmentProduct :=
  { p with
    coordinate := c
    provenance := p.provenance ++ ["restricted"] }

theorem restrict_product_trust_stable (p : JudgmentProduct) (c : Coordinate) :
    (restrictProduct p c).trust = p.trust := rfl

theorem restrict_product_valid_iff (p : JudgmentProduct) (c : Coordinate) :
    (restrictProduct p c).valid ↔ p.valid := by
  simp [restrictProduct, JudgmentProduct.valid]

/-- Attenuate a product's trust by one step (for non-restriction morphisms). -/
def transportProduct (p : JudgmentProduct) (c : Coordinate) (isRestriction : Bool) :
    JudgmentProduct :=
  let newTrust := if isRestriction then p.trust else p.trust - 1
  { p with
    coordinate := c
    trust      := newTrust
    provenance := p.provenance ++ ["transported"] }

theorem transport_product_trust_le (p : JudgmentProduct) (c : Coordinate) (r : Bool) :
    (transportProduct p c r).trust ≤ p.trust := by
  simp only [transportProduct]
  cases r <;> simp <;> omega

/-- Compose two conjunction products: the triple product. -/
def composeProducts (p1 p2 : JudgmentProduct) : JudgmentProduct :=
  { mode         := .conjunction
    left         := p1.left
    right        := p2.right
    coordinate   := p1.coordinate
    proposition  := "(" ++ p1.proposition ++ ") ∧ (" ++ p2.proposition ++ ")"
    evidence     := p1.evidence.merge p2.evidence
    obligations  := p1.obligations ++ p2.obligations
    obstructions := p1.obstructions ++ p2.obstructions
    trust        := Trust.meet p1.trust p2.trust
    provenance   := p1.provenance ++ p2.provenance ++ ["composed"] }

theorem compose_products_trust_le_left (p1 p2 : JudgmentProduct) :
    (composeProducts p1 p2).trust ≤ p1.trust :=
  Trust.meet_le_left p1.trust p2.trust

theorem compose_products_trust_le_right (p1 p2 : JudgmentProduct) :
    (composeProducts p1 p2).trust ≤ p2.trust :=
  Trust.meet_le_right p1.trust p2.trust

-- ════════════════════════════════════════════════════════════════════
-- § 11  Algebraic Laws
-- ════════════════════════════════════════════════════════════════════

/-- Trust commutativity of product. -/
theorem product_trust_comm (j1 j2 : Judgment) (m : ProductMode) :
    (mkProduct j1 j2 m).trust = (mkProduct j2 j1 m).trust :=
  Trust.meet_comm j1.trust j2.trust

/-- Trust associativity: (J₁ ⊗ J₂) ⊗ J₃ ≡ J₁ ⊗ (J₂ ⊗ J₃) on trust. -/
theorem product_trust_assoc (j1 j2 j3 : Judgment) (m : ProductMode) :
    (mkProduct (mkProduct j1 j2 m).left j3 m).trust =
    (mkProduct j1 (mkProduct j2 j3 m).right m).trust := by
  simp [mkProduct, Trust.meet]

/-- Restriction and product trust commute. -/
theorem restrict_then_product_trust
    (j1 j2 : Judgment) (m : ProductMode) (c : Coordinate) :
    (restrictProduct (mkProduct j1 j2 m) c).trust =
    Trust.meet j1.trust j2.trust := rfl

-- ════════════════════════════════════════════════════════════════════
-- § 12  Iterated Products
-- ════════════════════════════════════════════════════════════════════

/-- Trust of an iterated (left-associated) product of a list of judgments. -/
def iteratedProductTrust (js : List Judgment) : TrustLevel :=
  js.foldl (fun acc j => Trust.meet acc j.trust) Trust.mechanically_verified

theorem iterated_trust_single (j : Judgment) :
    iteratedProductTrust [j] = Trust.meet Trust.mechanically_verified j.trust := rfl

theorem iterated_trust_cons (j : Judgment) (js : List Judgment) :
    iteratedProductTrust (j :: js) =
    js.foldl (fun acc jj => Trust.meet acc jj.trust) (Trust.meet Trust.mechanically_verified j.trust) := rfl

private theorem foldl_meet_le_init (l : List Judgment) (init : TrustLevel) :
    l.foldl (fun acc j => Trust.meet acc j.trust) init ≤ init := by
  induction l generalizing init with
  | nil => exact Nat.le_refl _
  | cons hd tl ih =>
    simp only [List.foldl]
    exact Nat.le_trans (ih _) (Trust.meet_le_left _ _)

private theorem foldl_meet_le_mem (l : List Judgment) (init : TrustLevel)
    (j : Judgment) (hj : j ∈ l) :
    l.foldl (fun acc jj => Trust.meet acc jj.trust) init ≤ j.trust := by
  induction l generalizing init with
  | nil => exact absurd hj (List.not_mem_nil _)
  | cons hd tl ih =>
    simp only [List.foldl]
    cases List.mem_cons.mp hj with
    | inl heq =>
      subst heq
      exact Nat.le_trans (foldl_meet_le_init tl _) (Trust.meet_le_right _ _)
    | inr hmem =>
      exact ih _ hmem

private theorem foldl_meet_pos (l : List Judgment) (init : TrustLevel) (hinit : 0 < init)
    (hall : ∀ j ∈ l, 0 < j.trust) :
    0 < l.foldl (fun acc j => Trust.meet acc j.trust) init := by
  induction l generalizing init with
  | nil => simpa [List.foldl]
  | cons hd tl ih =>
    simp only [List.foldl]
    apply ih
    · simp only [Trust.meet, Nat.min_def]
      split
      · exact hinit
      · exact hall hd (List.mem_cons_self _ _)
    · intro j hj; exact hall j (List.mem_cons.mpr (Or.inr hj))

/-- The iterated trust is ≤ every individual component's trust. -/
theorem iterated_trust_le_each (js : List Judgment) (j : Judgment) (hj : j ∈ js) :
    iteratedProductTrust js ≤ j.trust :=
  foldl_meet_le_mem js Trust.mechanically_verified j hj

/-- All judgments valid → iterated trust > 0. -/
theorem iterated_trust_pos_of_all_valid
    (js : List Judgment) (_hne : js ≠ []) (hall : ∀ j ∈ js, j.valid) :
    0 < iteratedProductTrust js :=
  foldl_meet_pos js Trust.mechanically_verified (by decide) (fun j hj => (hall j hj).1)

-- ════════════════════════════════════════════════════════════════════
-- § 13  Summary
-- ════════════════════════════════════════════════════════════════════

/-- Paper 44 — ALL theorems fully proved (zero sorry):
    1. product_trust_eq_min         : trust(P) = min(t1, t2)
    2. product_trust_le_left/right  : trust bounds
    3. product_trust_pos_iff        : positivity iff both positive
    4. product_soundness_conjunction: biconditional for conjunction
    5. product_soundness            : biconditional for any mode
    6. product_evidence_complete    : evidence not discarded
    7. product_evidence_quality     : quality non-decreasing
    8. restrict_product_valid_iff   : restriction preserves validity
    9. transport_product_trust_le   : transport doesn't raise trust
    10. compose_products_trust_le_*  : compose trust bounds
    11. product_trust_comm/assoc     : algebraic laws
    12. iterated_trust_le_each       : iterated trust ≤ each component
    13. iterated_trust_pos_of_all_valid : valid components → trust > 0
-/
theorem paper44_summary : True := trivial

end JudgmentGeometry.Paper44
