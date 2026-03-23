/-
  Paper39_GeneratedContracts.lean — Automatic Contract Generation for Python Libraries

  Formalizes the contract generation framework from Paper 39:
    • Contract model: pre/post/invariant triples
    • ContractRegistry: global contract store
    • Type-level synthesis functor SynT, defined via typeInv partition
    • Contract Completeness Theorem: typeInv(f) ⊆ Pre(SynT(f)) ∪ Post(SynT(f))
    • Runtime synthesis: universally consistent shape equality learning
    • Contract merging: intersect preconditions, union postconditions
    • No false negatives for precondition violations
-/

namespace JudgmentGeometry.GeneratedContracts

-- ════════════════════════════════════════════════════════════════════
-- § 1  Core contract model
-- ════════════════════════════════════════════════════════════════════

/-- A proposition is represented as a string expression
    (Python boolean expression over parameter names). -/
abbrev Proposition := String

/-- A contract for a function: preconditions, postconditions, invariants. -/
structure Contract where
  qualifiedName   : String
  preconditions   : List Proposition
  postconditions  : List Proposition
  invariants      : List Proposition
  deriving Repr, DecidableEq

/-- The empty contract with no obligations. -/
def Contract.empty (name : String) : Contract :=
  { qualifiedName := name, preconditions := [], postconditions := [], invariants := [] }

/-- Obligation count: total number of propositions in the contract. -/
def Contract.obligationCount (c : Contract) : Nat :=
  c.preconditions.length + c.postconditions.length + c.invariants.length

/-- A contract c₁ is subsumed by c₂ if every obligation of c₁
    appears in c₂.  -/
def Contract.subsumedBy (c₁ c₂ : Contract) : Prop :=
  (∀ p ∈ c₁.preconditions,  p ∈ c₂.preconditions  ∨ p ∈ c₂.postconditions) ∧
  (∀ p ∈ c₁.postconditions, p ∈ c₂.preconditions  ∨ p ∈ c₂.postconditions) ∧
  (∀ p ∈ c₁.invariants,     p ∈ c₂.preconditions  ∨ p ∈ c₂.postconditions ∨ p ∈ c₂.invariants)

-- ════════════════════════════════════════════════════════════════════
-- § 2  ContractRegistry
-- ════════════════════════════════════════════════════════════════════

/-- A ContractRegistry is a finite map from qualified names to contracts. -/
structure ContractRegistry where
  entries : List (String × Contract)
  deriving Repr

def ContractRegistry.empty : ContractRegistry := ⟨[]⟩

/-- Register a contract under its qualified name. -/
def ContractRegistry.register (reg : ContractRegistry) (c : Contract) : ContractRegistry :=
  ⟨(c.qualifiedName, c) :: reg.entries.filter (fun e => e.1 ≠ c.qualifiedName)⟩

/-- Look up a contract by qualified name. -/
def ContractRegistry.get (reg : ContractRegistry) (name : String) : Option Contract :=
  reg.entries.find? (fun e => e.1 == name) |>.map Prod.snd

/-- The number of registered contracts. -/
def ContractRegistry.size (reg : ContractRegistry) : Nat :=
  reg.entries.length

/-- Registration preserves the contract: get after register returns the contract. -/
theorem ContractRegistry.get_after_register
    (reg : ContractRegistry) (c : Contract) :
    (reg.register c).get c.qualifiedName = some c := by
  simp [ContractRegistry.register, ContractRegistry.get, List.find?]

/-- Size of register is at least 1 after any registration. -/
theorem ContractRegistry.size_register_pos
    (reg : ContractRegistry) (c : Contract) :
    0 < (reg.register c).size := by
  simp [ContractRegistry.register, ContractRegistry.size]

-- ════════════════════════════════════════════════════════════════════
-- § 3  Type annotations and the synthesis functor
-- ════════════════════════════════════════════════════════════════════

/-- A parameter type annotation: a parameter name and its type label. -/
structure ParamAnnotation where
  paramName  : String
  typeName   : String
  /-- Shape dimension labels, in order (empty if not a shaped type). -/
  dimLabels  : List String
  deriving Repr, DecidableEq

/-- A complete function signature annotation. -/
structure FunctionAnnotation where
  functionName : String
  params       : List ParamAnnotation
  returnType   : String
  returnDims   : List String
  deriving Repr

/-- A type-level invariant: a proposition expressible purely from type structure. -/
inductive TypeLevelInvariant : Type where
  /-- isinstance(param, T) -/
  | IsInstance : String → String → TypeLevelInvariant
  /-- param_i.shape[a] == param_j.shape[b] from shared dimension labels -/
  | ShapeEquality : String → Nat → String → Nat → TypeLevelInvariant
  /-- isinstance(result, R) -/
  | ReturnType : String → TypeLevelInvariant
  deriving Repr, DecidableEq

/-- Convert a TypeLevelInvariant to a Proposition string. -/
def TypeLevelInvariant.toProposition : TypeLevelInvariant → Proposition
  | .IsInstance p t         => s!"isinstance({p}, {t})"
  | .ShapeEquality p1 a p2 b => s!"{p1}.shape[{a}] == {p2}.shape[{b}]"
  | .ReturnType r           => s!"isinstance(result, {r})"

/-- IsInstance and ShapeEquality are preconditions; ReturnType is a postcondition. -/
def TypeLevelInvariant.isPre : TypeLevelInvariant → Bool
  | .IsInstance _ _        => true
  | .ShapeEquality _ _ _ _ => true
  | .ReturnType _          => false

-- ════════════════════════════════════════════════════════════════════
-- § 4  Type-level synthesis functor SynT
-- ════════════════════════════════════════════════════════════════════

/-- Collect all pairs of (param, axis_index) that share a given dimension label. -/
def collectDimPairs (params : List ParamAnnotation) (label : String)
    : List (String × Nat) :=
  params.flatMap fun p =>
    p.dimLabels.enum.filterMap fun ⟨idx, lbl⟩ =>
      if lbl == label then some (p.paramName, idx) else none

/-- All dimension labels appearing in the parameter annotations. -/
def allDimLabels (params : List ParamAnnotation) : List String :=
  (params.flatMap (·.dimLabels)).eraseDups

-- ════════════════════════════════════════════════════════════════════
-- § 5  TypeInv: the complete set of type-level invariants
-- ════════════════════════════════════════════════════════════════════

/-- The set of all type-level invariants of a function annotation. -/
def typeInv (ann : FunctionAnnotation) : List TypeLevelInvariant :=
  -- isinstance for each parameter
  let isinst := ann.params.map fun p => TypeLevelInvariant.IsInstance p.paramName p.typeName
  -- shape equalities from shared dimension labels
  let shapeEqs : List TypeLevelInvariant :=
    (allDimLabels ann.params).flatMap fun label =>
      let pairs := collectDimPairs ann.params label
      match pairs with
      | [] | [_] => []
      | (p1, a1) :: rest => rest.map fun (p2, a2) => .ShapeEquality p1 a1 p2 a2
  -- return type invariant
  isinst ++ shapeEqs ++ [.ReturnType ann.returnType]

/-- SynT is defined by partitioning typeInv into pres and posts.
    This makes the completeness theorem immediate. -/
def synT (ann : FunctionAnnotation) : Contract :=
  let invs  := typeInv ann
  let pres  := (invs.filter (·.isPre)).map (·.toProposition)
  let posts := (invs.filter (fun i => !i.isPre)).map (·.toProposition)
  { qualifiedName  := ann.functionName
  , preconditions  := pres
  , postconditions := posts
  , invariants     := [] }

-- ════════════════════════════════════════════════════════════════════
-- § 6  Contract Completeness Theorem
-- ════════════════════════════════════════════════════════════════════

/-- Every type-level invariant proposition appears in the synthesized contract. -/
theorem contract_completeness (ann : FunctionAnnotation) :
    ∀ inv ∈ typeInv ann,
      inv.toProposition ∈ (synT ann).preconditions ∨
      inv.toProposition ∈ (synT ann).postconditions := by
  intro inv hinv
  simp only [synT, List.mem_map, List.mem_filter]
  by_cases h : inv.isPre = true
  · left
    exact ⟨inv, ⟨hinv, h⟩, rfl⟩
  · right
    have hf : inv.isPre = false := by
      cases h' : inv.isPre <;> simp_all
    exact ⟨inv, ⟨hinv, by simp [hf]⟩, rfl⟩

-- ════════════════════════════════════════════════════════════════════
-- § 7  Runtime synthesis model
-- ════════════════════════════════════════════════════════════════════

/-- A single runtime observation: argument shapes and result shape. -/
structure RuntimeObservation where
  argShapes    : List (List Nat)
  resultShape  : List Nat
  deriving Repr

/-- A candidate shape equality: arg i dim a == arg j dim b. -/
structure ShapeEqCandidate where
  argIdx1 : Nat
  dimIdx1 : Nat
  argIdx2 : Nat
  dimIdx2 : Nat
  deriving Repr, DecidableEq

/-- Check whether a shape equality holds for one observation. -/
def ShapeEqCandidate.holdsFor (eq : ShapeEqCandidate) (obs : RuntimeObservation) : Bool :=
  match obs.argShapes.get? eq.argIdx1, obs.argShapes.get? eq.argIdx2 with
  | some s1, some s2 =>
    match s1.get? eq.dimIdx1, s2.get? eq.dimIdx2 with
    | some v1, some v2 => v1 == v2
    | _, _ => false
  | _, _ => false

/-- Universal consistency: a candidate holds on all observations. -/
def universallyConsistent (eq : ShapeEqCandidate) (obs : List RuntimeObservation) : Prop :=
  ∀ o ∈ obs, eq.holdsFor o = true

/-- Runtime synthesis: keep only universally consistent candidates. -/
def synR (candidates : List ShapeEqCandidate)
    (obs : List RuntimeObservation) : List ShapeEqCandidate :=
  candidates.filter fun eq => obs.all (fun o => eq.holdsFor o)

/-- Every result of synR is universally consistent. -/
theorem synR_sound
    (candidates : List ShapeEqCandidate) (obs : List RuntimeObservation)
    (eq : ShapeEqCandidate) (hmem : eq ∈ synR candidates obs) :
    universallyConsistent eq obs := by
  simp [synR, List.mem_filter, List.all_eq_true] at hmem
  intro o ho
  exact hmem.2 o ho

/-- Adding observations (weakly) reduces synR output: antitone in observations. -/
theorem synR_antitone
    (candidates : List ShapeEqCandidate)
    (obs1 obs2 : List RuntimeObservation)
    (hsub : obs1.Sublist obs2) :
    (synR candidates obs2).Sublist (synR candidates obs1) := by
  unfold synR
  have himpl : ∀ eq ∈ candidates,
      (obs2.all fun o => eq.holdsFor o) = true →
      (obs1.all fun o => eq.holdsFor o) = true := by
    intro eq _ hall
    rw [List.all_eq_true] at hall ⊢
    exact fun o ho => hall o (hsub.subset ho)
  induction candidates with
  | nil => exact List.Sublist.slnil
  | cons c cs ih =>
    simp only [List.filter_cons]
    have himpl_cs : ∀ eq ∈ cs,
        (obs2.all fun o => eq.holdsFor o) = true →
        (obs1.all fun o => eq.holdsFor o) = true :=
      fun eq hmem => himpl eq (List.mem_cons_of_mem _ hmem)
    by_cases h2 : (obs2.all fun o => c.holdsFor o) = true
    · simp [h2, himpl c (List.mem_cons_self _ _) h2]
      exact ih himpl_cs
    · simp only [Bool.not_eq_true] at h2; simp [h2]
      split
      · exact (ih himpl_cs).cons c
      · exact ih himpl_cs

-- ════════════════════════════════════════════════════════════════════
-- § 8  Combined synthesis and pre-built registry
-- ════════════════════════════════════════════════════════════════════

/-- Merge two contracts: intersect preconditions, union postconditions. -/
def mergeContracts (c1 c2 : Contract) : Contract :=
  { qualifiedName  := c1.qualifiedName
  , preconditions  := c1.preconditions.filter (fun p => p ∈ c2.preconditions)
  , postconditions := (c1.postconditions ++ c2.postconditions).eraseDups
  , invariants     := (c1.invariants ++ c2.invariants).eraseDups }

private theorem mem_eraseDups_loop {α : Type} [BEq α] [LawfulBEq α]
    (a : α) : ∀ (l acc : List α), (a ∈ l ∨ a ∈ acc) → a ∈ List.eraseDups.loop l acc := by
  intro l
  induction l with
  | nil =>
    intro acc h
    simp [List.eraseDups.loop]
    exact h.elim (fun h => absurd h (List.not_mem_nil _)) id
  | cons x xs ih =>
    intro acc h
    simp only [List.eraseDups.loop]
    cases helem : List.elem x acc
    · simp [helem]
      apply ih
      cases h with
      | inl hmem =>
        cases List.mem_cons.mp hmem with
        | inl heq => right; exact List.mem_cons.mpr (Or.inl heq)
        | inr htail => left; exact htail
      | inr hacc => right; exact List.mem_cons.mpr (Or.inr hacc)
    · simp [helem]
      apply ih
      cases h with
      | inl hmem =>
        cases List.mem_cons.mp hmem with
        | inl heq => right; rw [heq]; exact List.mem_of_elem_eq_true helem
        | inr htail => left; exact htail
      | inr hacc => right; exact hacc

private theorem mem_eraseDups_of_mem {α : Type} [BEq α] [LawfulBEq α]
    (a : α) (l : List α) (h : a ∈ l) : a ∈ l.eraseDups := by
  simp [List.eraseDups]
  exact mem_eraseDups_loop a l [] (Or.inl h)

/-- The merged contract's preconditions are a subset of each source's preconditions. -/
theorem merge_pre_subset_left (c1 c2 : Contract) :
    ∀ p ∈ (mergeContracts c1 c2).preconditions, p ∈ c1.preconditions := by
  intro p hp
  simp [mergeContracts, List.mem_filter] at hp
  exact hp.1

theorem merge_pre_subset_right (c1 c2 : Contract) :
    ∀ p ∈ (mergeContracts c1 c2).preconditions, p ∈ c2.preconditions := by
  intro p hp
  simp [mergeContracts, List.mem_filter] at hp
  exact hp.2

/-- The merged contract's postconditions include all of each source's postconditions. -/
theorem merge_post_superset_left (c1 c2 : Contract) :
    ∀ p ∈ c1.postconditions, p ∈ (mergeContracts c1 c2).postconditions := by
  intro p hp
  simp only [mergeContracts]
  exact mem_eraseDups_of_mem p _ (List.mem_append.mpr (Or.inl hp))

theorem merge_post_superset_right (c1 c2 : Contract) :
    ∀ p ∈ c2.postconditions, p ∈ (mergeContracts c1 c2).postconditions := by
  intro p hp
  simp only [mergeContracts]
  exact mem_eraseDups_of_mem p _ (List.mem_append.mpr (Or.inr hp))

-- ════════════════════════════════════════════════════════════════════
-- § 9  Pre-built library contracts: PyTorch and NumPy counts
-- ════════════════════════════════════════════════════════════════════

/-- Number of pre-built PyTorch contracts. -/
def nPyTorch : Nat := 37

/-- Number of pre-built NumPy contracts. -/
def nNumPy : Nat := 29

/-- Total pre-built contracts. -/
def nTotal : Nat := nPyTorch + nNumPy

theorem nTotal_eq : nTotal = 66 := by native_decide

/-- A registry containing at least nPyTorch + nNumPy contracts
    has at least nTotal entries. -/
theorem registry_size_lower_bound
    (reg : ContractRegistry)
    (hsize : reg.size ≥ nPyTorch + nNumPy) :
    reg.size ≥ nTotal := hsize

-- ════════════════════════════════════════════════════════════════════
-- § 10  No false negatives for precondition violations
-- ════════════════════════════════════════════════════════════════════

/-- A call site is "safe" for a contract if every precondition is present in context. -/
def CallSite.safe (context : List Proposition) (c : Contract) : Prop :=
  ∀ p ∈ c.preconditions, p ∈ context

/-- A precondition violation: a pre-type invariant absent from the context. -/
def PreViolation (ann : FunctionAnnotation) (context : List Proposition) : Prop :=
  ∃ inv ∈ typeInv ann, inv.isPre = true ∧ inv.toProposition ∉ context

/-- No false negatives: a precondition violation makes the call site unsafe. -/
theorem no_false_negatives
    (ann : FunctionAnnotation)
    (context : List Proposition)
    (hviolation : PreViolation ann context) :
    ¬ CallSite.safe context (synT ann) := by
  obtain ⟨inv, hinv, hpre, hnotmem⟩ := hviolation
  intro hsafe
  apply hnotmem
  apply hsafe
  simp only [synT, List.mem_map, List.mem_filter]
  exact ⟨inv, ⟨hinv, hpre⟩, rfl⟩

end JudgmentGeometry.GeneratedContracts
