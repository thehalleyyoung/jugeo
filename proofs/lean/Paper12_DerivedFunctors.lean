/-
  Paper12_DerivedFunctors.lean — Derived Functors for Judgment Sheaves
  Formalizes Paper 12: R0V, restriction, Mayer–Vietoris, H1.
-/

namespace JudgmentGeometry.Paper12

-- ════════════════════════════════════════════════════════════════════
-- § 1  Core Types
-- ════════════════════════════════════════════════════════════════════

/-- Kind of coordinate in a software project. -/
inductive CoordinateKind where
  | module | function | interface | test | region
  deriving DecidableEq, Repr, BEq

/-- A coordinate identifies a location in the project. -/
structure Coordinate where
  name : String
  kind : CoordinateKind
  deriving DecidableEq, Repr, BEq

/-- A proposition to be judged. -/
structure Proposition where
  formula : String
  deriving DecidableEq, Repr, BEq

/-- A judgment: a proposition evaluated at a coordinate. -/
structure Judgment where
  coordinate   : Coordinate
  proposition  : Proposition
  obstructions : List String
  trust        : Nat
  deriving DecidableEq, Repr, BEq

-- ════════════════════════════════════════════════════════════════════
-- § 2  Verification & R0V
-- ════════════════════════════════════════════════════════════════════

/-- A judgment is verified when it has no obstructions. -/
def Judgment.isVerified : Judgment → Bool :=
  fun j => j.obstructions.isEmpty

/-- R0V: the 0-th derived functor — verified judgments at a coordinate. -/
def R0V (js : List Judgment) (c : Coordinate) : List Judgment :=
  js.filter (fun j => (j.coordinate == c) && j.isVerified)

-- ════════════════════════════════════════════════════════════════════
-- § 3  Module Cover & Restriction
-- ════════════════════════════════════════════════════════════════════

/-- A cover of a module by two sub-coordinates with intersection. -/
structure ModuleCover where
  total : Coordinate
  subA  : Coordinate
  subB  : Coordinate
  inter : Coordinate

/-- Restrict a judgment to a different coordinate. -/
def Judgment.restrict (j : Judgment) (c : Coordinate) : Judgment :=
  { j with coordinate := c }

-- ════════════════════════════════════════════════════════════════════
-- § 4  H1 & Connecting Homomorphism
-- ════════════════════════════════════════════════════════════════════

/-- An element of H^1: a section on the intersection with lift data. -/
structure H1Class where
  interSection : Judgment
  liftsToA     : Bool
  liftsToB     : Bool

/-- An H1 class is trivial when it lifts to both sub-coordinates. -/
def H1Class.isTrivial : H1Class → Bool :=
  fun h => h.liftsToA && h.liftsToB

/-- The connecting homomorphism δ: checks if a section on the intersection
    lifts to each sub-coordinate. -/
def connectingHom (sA sB : List Judgment) (j : Judgment) : H1Class :=
  { interSection := j
  , liftsToA     := sA.any (fun k => k.proposition == j.proposition)
  , liftsToB     := sB.any (fun k => k.proposition == j.proposition) }

/-- Two judgments are proposition-compatible. -/
def PropCompatible (j k : Judgment) : Prop :=
  j.proposition = k.proposition

/-- H^1 vanishes when every section on the intersection lifts to both. -/
def H1Vanishes (sA sB inter : List Judgment) : Prop :=
  ∀ j ∈ inter, (connectingHom sA sB j).isTrivial = true

-- ════════════════════════════════════════════════════════════════════
-- § 5  Mayer–Vietoris Data
-- ════════════════════════════════════════════════════════════════════

/-- Bundle of all four R0V results for Mayer–Vietoris. -/
structure MayerVietorisData where
  cover   : ModuleCover
  r0Total : List Judgment
  r0A     : List Judgment
  r0B     : List Judgment
  r0Inter : List Judgment

-- ════════════════════════════════════════════════════════════════════
-- § 6  R0V Properties
-- ════════════════════════════════════════════════════════════════════

/-- Every element of R0V belongs to the original list. -/
theorem R0V_subset (js : List Judgment) (c : Coordinate) (j : Judgment)
    (hj : j ∈ R0V js c) : j ∈ js :=
  (List.mem_filter.mp hj).1

/-- Every element of R0V is verified and at the given coordinate. -/
theorem R0V_verified (js : List Judgment) (c : Coordinate) (j : Judgment)
    (hj : j ∈ R0V js c) : j.isVerified = true := by
  have hf := (List.mem_filter.mp hj).2
  exact (Bool.and_eq_true.mp hf).2

/-- Every element of R0V has the correct coordinate. -/
theorem R0V_at_coord (js : List Judgment) (c : Coordinate) (j : Judgment)
    (hj : j ∈ R0V js c) : (j.coordinate == c) = true := by
  have hf := (List.mem_filter.mp hj).2
  exact (Bool.and_eq_true.mp hf).1

/-- R0V of an empty list is empty. -/
theorem R0V_nil (c : Coordinate) : R0V [] c = [] := rfl

/-- Filtering R0V again yields the same list (idempotent). -/
theorem R0V_idempotent (js : List Judgment) (c : Coordinate) :
    R0V (R0V js c) c = R0V js c := by
  simp only [R0V]
  apply List.filter_eq_self.mpr
  intro j hj
  exact (List.mem_filter.mp hj).2

/-- R0V is monotone: a sublist of inputs yields a sublist of outputs. -/
theorem R0V_monotone (js js' : List Judgment) (c : Coordinate)
    (hsub : js'.Sublist js) :
    (R0V js' c).Sublist (R0V js c) := by
  exact List.Sublist.filter _ hsub

/-- R0V is a sublist of the input. -/
theorem R0V_is_sublist (js : List Judgment) (c : Coordinate) :
    (R0V js c).Sublist js :=
  List.filter_sublist _

/-- R0V never exceeds the input length. -/
theorem R0V_length_le (js : List Judgment) (c : Coordinate) :
    (R0V js c).length ≤ js.length :=
  List.Sublist.length_le (R0V_is_sublist js c)

-- ════════════════════════════════════════════════════════════════════
-- § 7  Restriction Properties
-- ════════════════════════════════════════════════════════════════════

/-- Restriction preserves verification status. -/
theorem restrict_isVerified (j : Judgment) (c : Coordinate) :
    (j.restrict c).isVerified = j.isVerified := by
  simp [Judgment.restrict, Judgment.isVerified]

/-- Restriction preserves the proposition. -/
theorem restrict_preserves_prop (j : Judgment) (c : Coordinate) :
    (j.restrict c).proposition = j.proposition := by
  simp [Judgment.restrict]

/-- Restricting twice is the same as restricting to the second coordinate. -/
theorem restrict_restrict (j : Judgment) (c1 c2 : Coordinate) :
    (j.restrict c1).restrict c2 = j.restrict c2 := by
  simp [Judgment.restrict]

-- ════════════════════════════════════════════════════════════════════
-- § 8  Connecting Homomorphism Properties
-- ════════════════════════════════════════════════════════════════════

/-- The section stored in the connecting homomorphism is the input. -/
theorem connecting_section (sA sB : List Judgment) (j : Judgment) :
    (connectingHom sA sB j).interSection = j := rfl

/-- If sA contains a judgment with the same proposition, liftsToA is true. -/
theorem connecting_lifts_A (sA sB : List Judgment) (j k : Judgment)
    (hk : k ∈ sA) (hprop : k.proposition == j.proposition = true) :
    (connectingHom sA sB j).liftsToA = true := by
  simp only [connectingHom]
  induction sA with
  | nil => exact absurd hk (List.not_mem_nil k)
  | cons x xs ih =>
    simp only [List.any_cons, Bool.or_eq_true]
    rw [List.mem_cons] at hk
    cases hk with
    | inl h => left; rw [← h]; exact hprop
    | inr h => right; exact ih h

/-- If sB contains a judgment with the same proposition, liftsToB is true. -/
theorem connecting_lifts_B (sA sB : List Judgment) (j k : Judgment)
    (hk : k ∈ sB) (hprop : k.proposition == j.proposition = true) :
    (connectingHom sA sB j).liftsToB = true := by
  simp only [connectingHom]
  induction sB with
  | nil => exact absurd hk (List.not_mem_nil k)
  | cons x xs ih =>
    simp only [List.any_cons, Bool.or_eq_true]
    rw [List.mem_cons] at hk
    cases hk with
    | inl h => left; rw [← h]; exact hprop
    | inr h => right; exact ih h

-- ════════════════════════════════════════════════════════════════════
-- § 9  Compatibility & Triviality
-- ════════════════════════════════════════════════════════════════════

/-- A global judgment restricted to two sub-coordinates is compatible. -/
theorem global_to_compatible (j : Judgment) (cA cB : Coordinate) :
    PropCompatible (j.restrict cA) (j.restrict cB) := by
  simp [PropCompatible, Judgment.restrict]

/-- A global judgment that restricts into both sA and sB makes the
    connecting homomorphism trivial. -/
theorem global_makes_connecting_trivial (cover : ModuleCover)
    (sA sB : List Judgment) (jM : Judgment)
    (hA : jM.restrict cover.subA ∈ sA)
    (hB : jM.restrict cover.subB ∈ sB) :
    (connectingHom sA sB (jM.restrict cover.inter)).isTrivial = true := by
  simp only [H1Class.isTrivial, Bool.and_eq_true]
  refine ⟨?_, ?_⟩
  · apply connecting_lifts_A sA sB _ (jM.restrict cover.subA) hA
    simp [Judgment.restrict, beq_self_eq_true]
  · apply connecting_lifts_B sA sB _ (jM.restrict cover.subB) hB
    simp [Judgment.restrict, beq_self_eq_true]

-- ════════════════════════════════════════════════════════════════════
-- § 10  H1 Vanishing
-- ════════════════════════════════════════════════════════════════════

/-- H1 vanishes on an empty intersection. -/
theorem H1_vanishes_empty (sA sB : List Judgment) :
    H1Vanishes sA sB [] := by
  intro j hj
  exact absurd hj (List.not_mem_nil j)

-- ════════════════════════════════════════════════════════════════════
-- § 11  Grand Mayer–Vietoris Theorem
-- ════════════════════════════════════════════════════════════════════

/-- **Mayer–Vietoris core properties**: a grand conjunction packaging
    the fundamental properties of R0V, restriction, and connecting
    homomorphisms. -/
theorem mayer_vietoris_core_properties :
    (∀ js c j, j ∈ R0V js c → j ∈ js) ∧
    (∀ js c j, j ∈ R0V js c → j.isVerified = true) ∧
    (∀ js c j, j ∈ R0V js c → (j.coordinate == c) = true) ∧
    (∀ j cA cB, PropCompatible (j.restrict cA) (j.restrict cB)) :=
  ⟨R0V_subset, R0V_verified, R0V_at_coord, global_to_compatible⟩

end JudgmentGeometry.Paper12
