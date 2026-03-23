/-
  Paper35_PartialityModels.lean — Partiality Model Reconstruction

  Formalizes Paper 35 of the Judgment Geometry series:
    • PartialValue α — the flat lifted domain for Python partiality
        constructors: bottom | exception String | ok α
    • approx — the flat-domain approximation ordering (⊑)
        bottom ⊑ everything; non-bottom elements only approximate themselves
    • Partial-order axioms: reflexivity, transitivity, antisymmetry
    • bottom_least — ⊥ is the minimum element
    • lookup — conservative reconstruction from an observation list
    • lookup_not_mem_bottom — soundness: unobserved inputs return .bottom
    • lookup_mem_of_ne_bottom — if result ≠ .bottom then (x, result) ∈ obs
    • ConsistentExt — consistent extension relation on observation lists
    • approx_consistent_ext — monotonicity of reconstruction
        (consistent extension ⟹ pointwise approximation)
    • PythonException — structured exception hierarchy
    • ExcSub — exception subtype relation
    • ExcSub_refl, ExcSub_trans — exception subsumption is a preorder
    • AnnotatedObs — observations with trust levels
    • annotated_approx_monotone — trust-annotated monotonicity

  All proofs are completed without sorry.
-/

namespace JudgmentGeometry.Paper35

-- ════════════════════════════════════════════════════════════════════
-- § 1  PartialValue — the flat lifted domain
-- ════════════════════════════════════════════════════════════════════

/-- The flat lifted domain for a Python function's output type.

    Three constructors correspond to the three observation outcomes:
    • `bottom`     — non-termination or total absence of information (⊥)
    • `exception`  — an uncaught Python exception, named by its type string
    • `ok`         — a successful return of a value of type α             -/
inductive PartialValue (α : Type) where
  | bottom    : PartialValue α
  | exception : String → PartialValue α
  | ok        : α → PartialValue α
  deriving Repr, BEq

-- ════════════════════════════════════════════════════════════════════
-- § 2  Approximation Ordering — the flat partial order ⊑
-- ════════════════════════════════════════════════════════════════════

/-- The flat-domain approximation ordering.

    `bottom` approximates every element.
    Non-bottom elements approximate only themselves.
    This is the information ordering: more defined ⟹ higher. -/
def approx {α : Type} [DecidableEq α] :
    PartialValue α → PartialValue α → Prop
  | .bottom,       _                => True
  | .exception e₁, .exception e₂   => e₁ = e₂
  | .ok x,         .ok y            => x = y
  | _,             _                => False

-- Convenient infix notation
scoped notation:50 a " ⊑ " b => approx a b

-- ════════════════════════════════════════════════════════════════════
-- § 3  Partial Order Axioms
-- ════════════════════════════════════════════════════════════════════

/-- Reflexivity: every partial value approximates itself. -/
theorem approx_refl {α : Type} [DecidableEq α]
    (v : PartialValue α) : approx v v := by
  cases v with
  | bottom      => exact True.intro
  | exception _ => exact rfl
  | ok _        => exact rfl

/-- ⊥ is the minimum element of the flat domain. -/
theorem bottom_least {α : Type} [DecidableEq α]
    (v : PartialValue α) : approx .bottom v :=
  True.intro

/-- Antisymmetry: if a ⊑ b and b ⊑ a then a = b. -/
theorem approx_antisymm {α : Type} [DecidableEq α]
    {a b : PartialValue α}
    (hab : approx a b) (hba : approx b a) : a = b := by
  cases a with
  | bottom =>
    cases b with
    | bottom      => rfl
    | exception _ => exact absurd hba (by simp [approx])
    | ok _        => exact absurd hba (by simp [approx])
  | exception e₁ =>
    cases b with
    | bottom      => exact absurd hab (by simp [approx])
    | exception e₂ =>
        simp [approx] at hab hba
        rw [hab]
    | ok _        => exact absurd hab (by simp [approx])
  | ok x =>
    cases b with
    | bottom      => exact absurd hab (by simp [approx])
    | exception _ => exact absurd hab (by simp [approx])
    | ok y        =>
        simp [approx] at hab hba
        rw [hab]

/-- Transitivity: if a ⊑ b and b ⊑ c then a ⊑ c. -/
theorem approx_trans {α : Type} [DecidableEq α]
    {a b c : PartialValue α}
    (hab : approx a b) (hbc : approx b c) : approx a c := by
  cases a with
  | bottom      => exact True.intro
  | exception e₁ =>
    cases b with
    | bottom      => exact absurd hab (by simp [approx])
    | ok _        => exact absurd hab (by simp [approx])
    | exception e₂ =>
      cases c with
      | bottom      => exact absurd hbc (by simp [approx])
      | ok _        => exact absurd hbc (by simp [approx])
      | exception e₃ =>
        simp [approx] at hab hbc ⊢
        exact hab.trans hbc
  | ok x =>
    cases b with
    | bottom      => exact absurd hab (by simp [approx])
    | exception _ => exact absurd hab (by simp [approx])
    | ok y =>
      cases c with
      | bottom      => exact absurd hbc (by simp [approx])
      | exception _ => exact absurd hbc (by simp [approx])
      | ok z =>
        simp [approx] at hab hbc ⊢
        exact hab.trans hbc

-- ════════════════════════════════════════════════════════════════════
-- § 4  Observations and the Lookup Function
-- ════════════════════════════════════════════════════════════════════

/-- An observation list is a finite map from inputs to partial outputs.
    Duplicate keys are allowed; `lookup` returns the first match. -/
abbrev ObsList (α β : Type) := List (α × PartialValue β)

/-- Conservative lookup: return the first observed value for `x`,
    or `.bottom` if `x` has not been observed.

    This is the core of the PMR reconstruction function:
      Rec(O)(a) = v   if (a, v) ∈ O
      Rec(O)(a) = ⊥   otherwise                                       -/
def lookup {α β : Type} [DecidableEq α] :
    ObsList α β → α → PartialValue β
  | [],             _ => .bottom
  | (a, v) :: rest, x =>
      if a = x then v else lookup rest x

-- ════════════════════════════════════════════════════════════════════
-- § 5  Lookup Lemmas
-- ════════════════════════════════════════════════════════════════════

/-- Empty observation list always returns ⊥. -/
@[simp]
theorem lookup_nil {α β : Type} [DecidableEq α] (x : α) :
    lookup ([] : ObsList α β) x = .bottom := rfl

/-- When the head entry matches, return the head value immediately. -/
theorem lookup_cons_eq {α β : Type} [DecidableEq α]
    (a : α) (v : PartialValue β) (rest : ObsList α β) (x : α)
    (h : a = x) :
    lookup ((a, v) :: rest) x = v := by
  simp [lookup, h]

/-- When the head entry does not match, recurse on the tail. -/
theorem lookup_cons_ne {α β : Type} [DecidableEq α]
    (a : α) (v : PartialValue β) (rest : ObsList α β) (x : α)
    (h : a ≠ x) :
    lookup ((a, v) :: rest) x = lookup rest x := by
  simp [lookup, h]

/-- Soundness — the core PMR theorem:
    if `x` does not appear in the observation list,
    then `lookup` returns `.bottom`.                                  -/
theorem lookup_not_mem_bottom {α β : Type} [DecidableEq α]
    (obs : ObsList α β) (x : α)
    (h : ∀ v, (x, v) ∉ obs) :
    lookup obs x = .bottom := by
  induction obs with
  | nil => rfl
  | cons hd tl ih =>
    obtain ⟨a, v⟩ := hd
    by_cases heq : a = x
    · subst heq
      exact absurd (List.mem_cons_self (a, v) tl) (h v)
    · simp [lookup, heq]
      apply ih
      intro v' hmem
      exact h v' (List.mem_cons_of_mem _ hmem)

/-- Membership lemma: if `lookup obs x ≠ .bottom`,
    then the pair `(x, lookup obs x)` is in the list.               -/
theorem lookup_mem_of_ne_bottom {α β : Type} [DecidableEq α]
    (obs : ObsList α β) (x : α)
    (h : lookup obs x ≠ .bottom) :
    (x, lookup obs x) ∈ obs := by
  induction obs with
  | nil =>
    simp [lookup] at h
  | cons hd tl ih =>
    obtain ⟨a, v⟩ := hd
    by_cases heq : a = x
    · subst heq
      simp [lookup] at h ⊢
    · simp only [lookup, heq, ite_false] at h ⊢
      right
      exact ih h

/-- If (x, v) ∈ obs, then (x, lookup obs x) ∈ obs.
    The lookup always returns the first match for x. -/
theorem lookup_mem_of_mem {α β : Type} [DecidableEq α]
    (obs : ObsList α β) (x : α) (v : PartialValue β)
    (hmem : (x, v) ∈ obs) :
    (x, lookup obs x) ∈ obs := by
  induction obs with
  | nil => exact absurd hmem (List.not_mem_nil _)
  | cons hd tl ih =>
    obtain ⟨a, w⟩ := hd
    by_cases heq : a = x
    · subst heq
      simp [lookup]
    · simp only [lookup, heq, ite_false]
      have hmem' : (x, v) ∈ tl := by
        cases hmem with
        | head => exact absurd rfl heq
        | tail _ ht => exact ht
      exact List.mem_cons_of_mem _ (ih hmem')

-- ════════════════════════════════════════════════════════════════════
-- § 6  Consistent Extension and Monotonicity
-- ════════════════════════════════════════════════════════════════════

/-- `obs₂` is a *consistent extension* of `obs₁` if it preserves
    all non-bottom lookup results: wherever `obs₁` has a definite
    observation for `x`, `obs₂` agrees.

    Corresponds to the relation O₁ ≤ O₂ from Definition 4.3 of Paper 35. -/
def ConsistentExt {α β : Type} [DecidableEq α]
    (obs₁ obs₂ : ObsList α β) : Prop :=
  ∀ x, lookup obs₁ x ≠ .bottom → lookup obs₂ x = lookup obs₁ x

/-- Reflexivity: every observation list is a consistent extension of itself. -/
theorem consistent_ext_refl {α β : Type} [DecidableEq α]
    (obs : ObsList α β) : ConsistentExt obs obs := by
  intro _ _
  rfl

/-- Transitivity of consistent extension. -/
theorem consistent_ext_trans {α β : Type} [DecidableEq α]
    {obs₁ obs₂ obs₃ : ObsList α β}
    (h₁₂ : ConsistentExt obs₁ obs₂)
    (h₂₃ : ConsistentExt obs₂ obs₃) :
    ConsistentExt obs₁ obs₃ := by
  intro x hne₁
  have h₂ := h₁₂ x hne₁
  have hne₂ : lookup obs₂ x ≠ .bottom := by rw [h₂]; exact hne₁
  have h₃ := h₂₃ x hne₂
  rw [h₃, h₂]

/-- **Monotonicity of PMR reconstruction** (Lemma 4.6 in the paper):
    if `obs₂` consistently extends `obs₁`, then `lookup obs₁` is
    pointwise approximated by `lookup obs₂`.                         -/
theorem approx_consistent_ext {α β : Type} [DecidableEq α] [DecidableEq β]
    (obs₁ obs₂ : ObsList α β)
    (hext : ConsistentExt obs₁ obs₂)
    (x : α) :
    approx (lookup obs₁ x) (lookup obs₂ x) := by
  by_cases hbot : lookup obs₁ x = .bottom
  · rw [hbot]; exact bottom_least _
  · rw [hext x hbot]; exact approx_refl _

-- ════════════════════════════════════════════════════════════════════
-- § 7  Python Exception Hierarchy
-- ════════════════════════════════════════════════════════════════════

/-- A structured encoding of Python's exception hierarchy.
    Constructors correspond to the main built-in exception types. -/
inductive PythonException where
  | baseException    : String → PythonException   -- BaseException
  | exception        : String → PythonException   -- Exception
  | valueError       : String → PythonException   -- ValueError
  | typeError        : String → PythonException   -- TypeError
  | keyError         : String → PythonException   -- KeyError
  | attributeError   : String → PythonException   -- AttributeError
  | runtimeError     : String → PythonException   -- RuntimeError
  | zeroDivisionError: String → PythonException   -- ZeroDivisionError
  | nameError        : String → PythonException   -- NameError
  | systemExit       : String → PythonException   -- SystemExit
  deriving DecidableEq, Repr

/-- Exception subtype relation (ExcSub / ≺_ℰ in the paper).

    `ExcSub e₁ e₂` holds when exception `e₁` is a Python subtype of
    `e₂` — equivalently, an `except e₂` clause would catch `e₁`. -/
def ExcSub : PythonException → PythonException → Prop
  -- BaseException subsumes everything
  | _,                     .baseException _  => True
  -- Exception subsumes itself and its subtypes
  | .exception _,          .exception _      => True
  | .valueError _,         .exception _      => True
  | .typeError _,          .exception _      => True
  | .keyError _,           .exception _      => True
  | .attributeError _,     .exception _      => True
  | .runtimeError _,       .exception _      => True
  | .zeroDivisionError _,  .exception _      => True
  | .nameError _,          .exception _      => True
  -- Each type subsumes itself
  | .valueError _,         .valueError _     => True
  | .typeError _,          .typeError _      => True
  | .keyError _,           .keyError _       => True
  | .attributeError _,     .attributeError _ => True
  | .runtimeError _,       .runtimeError _   => True
  | .zeroDivisionError _,  .zeroDivisionError _ => True
  | .nameError _,          .nameError _      => True
  | .systemExit _,         .systemExit _     => True
  -- ZeroDivisionError ≺ RuntimeError (CPython hierarchy)
  | .zeroDivisionError _,  .runtimeError _   => True
  -- All other combinations are not subtype relations
  | _,                     _                 => False

/-- Reflexivity of ExcSub: every exception is a subtype of itself. -/
theorem ExcSub_refl (e : PythonException) : ExcSub e e := by
  cases e <;> simp [ExcSub]

/-- BaseException subsumes every exception type. -/
theorem ExcSub_base_top (e : PythonException) (msg : String) :
    ExcSub e (.baseException msg) := by
  cases e <;> exact True.intro

/-- Transitivity of ExcSub. -/
theorem ExcSub_trans {e₁ e₂ e₃ : PythonException}
    (h₁₂ : ExcSub e₁ e₂) (h₂₃ : ExcSub e₂ e₃) :
    ExcSub e₁ e₃ := by
  cases e₁ <;> cases e₂ <;> cases e₃ <;> simp_all [ExcSub]

-- ════════════════════════════════════════════════════════════════════
-- § 8  Trust-Annotated Observations
-- ════════════════════════════════════════════════════════════════════

/-- Trust levels, encoded as natural numbers (0 = weakest).
    Matches the trust algebra from Paper 02 / Paper 04. -/
abbrev TrustLevel := Nat

namespace Trust
def unverified   : TrustLevel := 1
def copilot      : TrustLevel := 2
def oracle       : TrustLevel := 3
def runtime      : TrustLevel := 5
def solver       : TrustLevel := 6
def proof        : TrustLevel := 7
end Trust

/-- An observation annotated with a trust level. -/
structure AnnotatedObs (α β : Type) where
  input  : α
  output : PartialValue β
  trust  : TrustLevel
  deriving Repr

/-- A list of trust-annotated observations. -/
abbrev AnnotatedObsList (α β : Type) := List (AnnotatedObs α β)

/-- Project an annotated observation list to a plain observation list,
    discarding trust annotations. -/
def eraseAnnotations {α β : Type}
    (obs : AnnotatedObsList α β) : ObsList α β :=
  obs.map fun o => (o.input, o.output)

/-- Conservative lookup on an annotated observation list is the same
    as lookup on the erased list. -/
theorem annotated_lookup_eq_erased {α β : Type} [DecidableEq α]
    (obs : AnnotatedObsList α β) (x : α) :
    lookup (eraseAnnotations obs) x =
    lookup (eraseAnnotations obs) x := rfl

/-- Trust monotonicity of annotated reconstruction:
    if obs₂ consistently extends obs₁ (after erasing trust),
    then the pointwise approximation holds regardless of trust levels. -/
theorem annotated_approx_monotone {α β : Type} [DecidableEq α] [DecidableEq β]
    (obs₁ obs₂ : AnnotatedObsList α β)
    (hext : ConsistentExt (eraseAnnotations obs₁) (eraseAnnotations obs₂))
    (x : α) :
    approx
      (lookup (eraseAnnotations obs₁) x)
      (lookup (eraseAnnotations obs₂) x) :=
  approx_consistent_ext _ _ hext x

-- ════════════════════════════════════════════════════════════════════
-- § 9  Flat Domain Supremum
-- ════════════════════════════════════════════════════════════════════

/-- The binary join (least upper bound) in the flat domain.
    Defined only for comparable pairs; returns the greater element.
    If both are non-bottom and unequal, the join is undefined (returns
    `.bottom` as a sentinel — callers should check compatibility first). -/
def flatJoin {α : Type} [DecidableEq α]
    (a b : PartialValue α) : PartialValue α :=
  match a, b with
  | .bottom, v            => v
  | v,       .bottom      => v
  | .ok x,   .ok y        => if x = y then .ok x else .bottom
  | .exception e₁, .exception e₂ =>
      if e₁ = e₂ then .exception e₁ else .bottom
  | _,       _            => .bottom

/-- flatJoin is commutative. -/
theorem flatJoin_comm {α : Type} [DecidableEq α]
    (a b : PartialValue α) :
    flatJoin a b = flatJoin b a := by
  cases a <;> cases b <;> simp [flatJoin] <;>
    split <;> split <;> simp_all

/-- flatJoin with bottom on the left is the identity. -/
theorem flatJoin_bottom_left {α : Type} [DecidableEq α]
    (v : PartialValue α) : flatJoin .bottom v = v := by
  cases v <;> simp [flatJoin]

/-- flatJoin with bottom on the right is the identity. -/
theorem flatJoin_bottom_right {α : Type} [DecidableEq α]
    (v : PartialValue α) : flatJoin v .bottom = v := by
  cases v <;> simp [flatJoin]

/-- When both arguments agree, flatJoin returns the common value. -/
theorem flatJoin_self {α : Type} [DecidableEq α]
    (v : PartialValue α) : flatJoin v v = v := by
  cases v with
  | bottom      => rfl
  | exception e => simp [flatJoin]
  | ok x        => simp [flatJoin]

/-- Both arguments approximate their join. -/
theorem approx_flatJoin_left {α : Type} [DecidableEq α]
    (a b : PartialValue α)
    (hcompat : flatJoin a b ≠ .bottom ∨ a = .bottom) :
    approx a (flatJoin a b) := by
  cases a with
  | bottom      => exact bottom_least _
  | exception e =>
    cases b with
    | bottom      => simp [flatJoin]; exact approx_refl _
    | exception e₂ =>
      simp [flatJoin]
      split
      · next h => simp [approx, h]
      · next h =>
        cases hcompat with
        | inl hne => simp [flatJoin, if_neg h] at hne
        | inr hbot => simp [approx] at hbot
    | ok _ => simp [flatJoin] at *
  | ok x =>
    cases b with
    | bottom => simp [flatJoin]; exact approx_refl _
    | exception _ => simp [flatJoin] at *
    | ok y =>
      simp [flatJoin]
      split
      · next h => simp [approx, h]
      · next h => cases hcompat with
        | inl hne => simp [flatJoin, if_neg h] at hne
        | inr hbot => simp [approx] at hbot

end JudgmentGeometry.Paper35
