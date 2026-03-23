/-
  Paper05_FragmentRouting.lean — Fragment-Aware VC Routing

  Formalizes Paper 05 of the Judgment Geometry series:
    • SMT-LIB fragment classification
    • Fragment decidability
    • Fragment partial order (sub-fragment relationships)
    • Backend with jurisdiction and trust ceiling
    • Routing algorithm with soundness proof
    • Trust ceiling theorem
    • Classification completeness (UNKNOWN is catch-all)
    • Decomposition correctness
-/

namespace JudgmentGeometry.Paper05

-- ════════════════════════════════════════════════════════════════════
-- § 1  Trust Level (Nat-based, self-contained)
-- ════════════════════════════════════════════════════════════════════

abbrev TrustLevel := Nat
def solver_discharged : TrustLevel := 6

-- ════════════════════════════════════════════════════════════════════
-- § 2  SMT-LIB Fragments
-- ════════════════════════════════════════════════════════════════════

/-- SMT-LIB logic fragments. -/
inductive Fragment where
  | QF_LIA     -- quantifier-free linear integer arithmetic
  | QF_LRA     -- quantifier-free linear real arithmetic
  | QF_BV      -- quantifier-free bitvectors
  | QF_UF      -- quantifier-free uninterpreted functions
  | QF_AUFLIA  -- quantifier-free arrays + UF + LIA
  | QF_ABV     -- quantifier-free arrays + bitvectors
  | STRINGS    -- string constraints
  | SEQUENCES  -- sequence constraints
  | ARRAYS     -- array theory
  | DATATYPES  -- algebraic datatypes
  | NONLINEAR  -- nonlinear arithmetic
  | QUANTIFIED -- quantified formulas
  | MIXED      -- combination of multiple fragments
  | UNKNOWN    -- unclassifiable formula
  deriving DecidableEq, Repr, BEq

-- ════════════════════════════════════════════════════════════════════
-- § 3  Fragment Decidability
-- ════════════════════════════════════════════════════════════════════

def Fragment.isDecidable : Fragment → Bool
  | .QF_LIA    => true
  | .QF_LRA    => true
  | .QF_BV     => true
  | .QF_UF     => true
  | .QF_AUFLIA => true
  | .QF_ABV    => true
  | .STRINGS   => true
  | .SEQUENCES => true
  | .ARRAYS    => true
  | .DATATYPES => true
  | .NONLINEAR => false
  | .QUANTIFIED => false
  | .MIXED     => false
  | .UNKNOWN   => false

theorem qf_lia_decidable : Fragment.isDecidable .QF_LIA = true := rfl
theorem qf_lra_decidable : Fragment.isDecidable .QF_LRA = true := rfl
theorem qf_bv_decidable  : Fragment.isDecidable .QF_BV = true := rfl
theorem qf_uf_decidable  : Fragment.isDecidable .QF_UF = true := rfl

theorem nonlinear_undecidable : Fragment.isDecidable .NONLINEAR = false := rfl
theorem quantified_undecidable : Fragment.isDecidable .QUANTIFIED = false := rfl

-- ════════════════════════════════════════════════════════════════════
-- § 4  Fragment Partial Order
-- ════════════════════════════════════════════════════════════════════

def Fragment.isSubfragment : Fragment → Fragment → Bool
  | .QF_LIA,  .QF_AUFLIA => true
  | .QF_UF,   .QF_AUFLIA => true
  | .QF_BV,   .QF_ABV    => true
  | .ARRAYS,  .QF_AUFLIA => true
  | .ARRAYS,  .QF_ABV    => true
  | _,        .MIXED     => true
  | a,        b          => a == b

/-- isSubfragment is reflexive. -/
theorem Fragment.isSubfragment_refl (f : Fragment) :
    f.isSubfragment f = true := by
  cases f <;> native_decide

/-- Every fragment is a subfragment of MIXED. -/
theorem Fragment.subfragment_mixed (f : Fragment) :
    f.isSubfragment .MIXED = true := by
  cases f <;> rfl

-- ════════════════════════════════════════════════════════════════════
-- § 5  Backend Definition
-- ════════════════════════════════════════════════════════════════════

structure Backend where
  name          : String
  jurisdiction  : List Fragment
  trustCeiling  : TrustLevel
  cost          : Nat
  latencyMs     : Nat
  deriving Repr

def Backend.handles (b : Backend) (f : Fragment) : Bool :=
  b.jurisdiction.any (fun j => j == f || f.isSubfragment j)

-- ════════════════════════════════════════════════════════════════════
-- § 6  Routing Algorithm
-- ════════════════════════════════════════════════════════════════════

def route (f : Fragment) (backends : List Backend) : Option Backend :=
  backends.find? (·.handles f)

def routeCheapest (f : Fragment) (backends : List Backend) : Option Backend :=
  let capable := backends.filter (·.handles f)
  capable.foldl (fun best b =>
    match best with
    | none => some b
    | some prev => if b.cost < prev.cost then some b else some prev
  ) none

-- ════════════════════════════════════════════════════════════════════
-- § 7  Routing Soundness
-- ════════════════════════════════════════════════════════════════════

/-- Helper: if find? returns some, the predicate holds. -/
private theorem find_some_pred {α : Type} {p : α → Bool} {l : List α} {x : α}
    (h : l.find? p = some x) : p x = true := by
  induction l with
  | nil => simp [List.find?] at h
  | cons a as ih =>
    simp [List.find?] at h
    split at h
    · injection h with h; subst h; assumption
    · exact ih h

/-- Helper: if find? returns some, the element is in the list. -/
private theorem find_some_mem {α : Type} {p : α → Bool} {l : List α} {x : α}
    (h : l.find? p = some x) : x ∈ l := by
  induction l with
  | nil => simp [List.find?] at h
  | cons a as ih =>
    simp [List.find?] at h
    split at h
    · injection h with h; subst h; exact List.Mem.head _
    · exact List.Mem.tail _ (ih h)

/-- THEOREM (Routing Soundness): The router only dispatches to backends
    that cover the requested fragment. -/
theorem routing_soundness (f : Fragment) (backends : List Backend) (b : Backend)
    (hroute : route f backends = some b) :
    b.handles f = true := by
  unfold route at hroute
  exact @find_some_pred Backend (fun x => x.handles f) backends b hroute

/-- The routed backend is in the original list. -/
theorem routing_in_list (f : Fragment) (backends : List Backend) (b : Backend)
    (hroute : route f backends = some b) :
    b ∈ backends := by
  unfold route at hroute
  exact @find_some_mem Backend (fun x => x.handles f) backends b hroute

-- ════════════════════════════════════════════════════════════════════
-- § 8  Trust Ceiling
-- ════════════════════════════════════════════════════════════════════

def routedTrust (queryTrust : TrustLevel) (b : Backend) : TrustLevel :=
  Nat.min queryTrust b.trustCeiling

/-- THEOREM (Trust Ceiling): The result trust ≤ the backend's trust ceiling. -/
theorem trust_ceiling (queryTrust : TrustLevel) (b : Backend) :
    routedTrust queryTrust b ≤ b.trustCeiling :=
  Nat.min_le_right queryTrust b.trustCeiling

/-- The result trust ≤ the query's original trust. -/
theorem trust_no_inflate (queryTrust : TrustLevel) (b : Backend) :
    routedTrust queryTrust b ≤ queryTrust :=
  Nat.min_le_left queryTrust b.trustCeiling

/-- Combined: routed trust is bounded by both. -/
theorem routed_trust_bounded (queryTrust : TrustLevel) (b : Backend) :
    routedTrust queryTrust b ≤ queryTrust ∧
    routedTrust queryTrust b ≤ b.trustCeiling :=
  ⟨trust_no_inflate queryTrust b, trust_ceiling queryTrust b⟩

-- ════════════════════════════════════════════════════════════════════
-- § 9  Classification Completeness
-- ════════════════════════════════════════════════════════════════════

def classifyFormula (hasQuantifiers hasNonlinear hasBV hasArrays : Bool)
    (hasStrings : Bool) : Fragment :=
  if hasQuantifiers then .QUANTIFIED
  else if hasNonlinear then .NONLINEAR
  else if hasBV && hasArrays then .QF_ABV
  else if hasBV then .QF_BV
  else if hasArrays then .QF_AUFLIA
  else if hasStrings then .STRINGS
  else .QF_LIA

def classifyWithFallback (maybeFragment : Option Fragment) : Fragment :=
  maybeFragment.getD .UNKNOWN

theorem classification_completeness (mf : Option Fragment) :
    ∃ f : Fragment, classifyWithFallback mf = f :=
  ⟨classifyWithFallback mf, rfl⟩

theorem unknown_catch_all : classifyWithFallback none = .UNKNOWN := rfl

theorem classification_preserves (f : Fragment) :
    classifyWithFallback (some f) = f := rfl

theorem classifyFormula_not_unknown (q n bv arr str : Bool) :
    classifyFormula q n bv arr str ≠ Fragment.UNKNOWN := by
  simp [classifyFormula]
  split <;> (try split) <;> (try split) <;> (try split) <;> (try split) <;>
    (try split) <;> exact Fragment.noConfusion

-- ════════════════════════════════════════════════════════════════════
-- § 10  Decomposition
-- ════════════════════════════════════════════════════════════════════

structure Decomposition where
  original   : String
  subProblems : List (Fragment × String)
  nonempty   : subProblems ≠ []

def Decomposition.fragments (d : Decomposition) : List Fragment :=
  d.subProblems.map (·.1)

def routeDecomposition (d : Decomposition) (backends : List Backend) :
    List (Option Backend) :=
  d.subProblems.map (fun sp => route sp.1 backends)

/-- THEOREM (Decomposition Routing): If every sub-problem gets routed,
    then every routed backend handles its assigned fragment. -/
theorem decomposition_routing_sound
    (d : Decomposition) (backends : List Backend)
    (hall : ∀ sp ∈ d.subProblems,
      ∃ b, route sp.1 backends = some b) :
    ∀ sp ∈ d.subProblems,
      ∃ b, route sp.1 backends = some b ∧ b.handles sp.1 = true := by
  intro sp hsp
  obtain ⟨b, hb⟩ := hall sp hsp
  exact ⟨b, hb, routing_soundness sp.1 backends b hb⟩

-- ════════════════════════════════════════════════════════════════════
-- § 11  Backend Registry
-- ════════════════════════════════════════════════════════════════════

structure BackendRegistry where
  backends : List Backend
  nonempty : backends ≠ []

def BackendRegistry.covers (reg : BackendRegistry) (f : Fragment) : Prop :=
  ∃ b ∈ reg.backends, b.handles f = true

def BackendRegistry.isComplete (reg : BackendRegistry) : Prop :=
  ∀ f : Fragment, f.isDecidable = true → reg.covers f

/-- Helper: if an element satisfies p, find? succeeds. -/
private theorem find_succeeds {α : Type} {p : α → Bool} {l : List α}
    (x : α) (hx : x ∈ l) (hp : p x = true) :
    ∃ y, l.find? p = some y := by
  induction l with
  | nil => exact absurd hx (List.not_mem_nil _)
  | cons a as ih =>
    simp [List.find?]
    split
    · exact ⟨a, rfl⟩
    · next hn =>
      cases hx with
      | head => simp_all
      | tail _ htl => exact ih htl

theorem route_succeeds_if_covered (reg : BackendRegistry) (f : Fragment)
    (hcov : reg.covers f) :
    ∃ b, route f reg.backends = some b := by
  obtain ⟨b, hmem, hhandles⟩ := hcov
  exact find_succeeds b hmem hhandles

-- ════════════════════════════════════════════════════════════════════
-- § 12  Universal Backend
-- ════════════════════════════════════════════════════════════════════

def universalBackend : Backend :=
  { name := "universal"
    jurisdiction := [.QF_LIA, .QF_LRA, .QF_BV, .QF_UF, .QF_AUFLIA, .QF_ABV,
                     .STRINGS, .SEQUENCES, .ARRAYS, .DATATYPES,
                     .NONLINEAR, .QUANTIFIED, .MIXED, .UNKNOWN]
    trustCeiling := solver_discharged
    cost := 100
    latencyMs := 5000 }

/-- The universal backend handles every fragment. -/
theorem universal_handles_all (f : Fragment) :
    universalBackend.handles f = true := by
  cases f <;> native_decide

/-- With the universal backend, every fragment can be routed. -/
theorem universal_routing_complete (f : Fragment) :
    route f [universalBackend] = some universalBackend := by
  simp [route, List.find?, universal_handles_all f]

-- ════════════════════════════════════════════════════════════════════
-- § 13  Trust Interaction with Routing
-- ════════════════════════════════════════════════════════════════════

def endToEndTrust (f : Fragment) (queryTrust : TrustLevel)
    (backends : List Backend) : Option TrustLevel :=
  (route f backends).map (routedTrust queryTrust)

/-- If routing succeeds, the end-to-end trust ≤ query trust. -/
theorem endToEnd_bounded (f : Fragment) (queryTrust : TrustLevel)
    (backends : List Backend) (t : TrustLevel)
    (h : endToEndTrust f queryTrust backends = some t) :
    t ≤ queryTrust := by
  unfold endToEndTrust at h
  cases hr : route f backends with
  | none => simp [hr, Option.map] at h
  | some b =>
    simp [hr, Option.map] at h
    rw [← h]
    exact trust_no_inflate queryTrust b

-- ════════════════════════════════════════════════════════════════════
-- § 14  Summary
-- ════════════════════════════════════════════════════════════════════

/-- Paper 05 — ALL theorems fully proved:
    1. routing_soundness — router only dispatches to capable backends
    2. trust_ceiling — result trust ≤ backend ceiling
    3. trust_no_inflate — result trust ≤ query trust
    4. classification_completeness — every formula gets a fragment
    5. classifyFormula_not_unknown — classifier never returns UNKNOWN
    6. decomposition_routing_sound — routed sub-problems are sound
    7. universal_routing_complete — universal backend routes everything
    8. endToEnd_bounded — end-to-end trust is bounded
-/
theorem paper05_summary : True := trivial

end JudgmentGeometry.Paper05
