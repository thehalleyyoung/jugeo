/-
  Paper20_CountermodelExtraction.lean — Countermodel Extraction and Diagnostic Synthesis

  Formalizes Paper 20 of the Judgment Geometry series:
    • Partial variable assignments as lists of active variable indices
    • Upward-monotone failure predicates (the key structural assumption)
    • The greedy minimizer: process each element, remove if still failing
    • Minimality theorem: the greedy minimizer produces locally minimal witnesses
        – Subset theorem: the result is a sub-assignment of the original
        – Preservation theorem: the result is still a countermodel
        – Local minimality: no single element removal maintains failure
    • Normalization: idempotency and semantic preservation
    • Failure classification and obstruction records

  All theorems proved without sorry.
  Uses only core Lean 4 (no Mathlib, no Std required).
-/

namespace JudgmentGeometry.CountermodelExtraction

-- ════════════════════════════════════════════════════════════════════
-- § 1  Variable Assignments and Core Types
-- ════════════════════════════════════════════════════════════════════

/-- A variable identifier.  We use natural numbers for simplicity;
    the implementation maps string variable names to indices before
    passing them to the minimizer. -/
abbrev VarId := Nat

/-- A partial assignment is a list of variable identifiers that are
    "active" (included in the current countermodel candidate).
    The associated values are fixed by the check function; what varies
    is which variables appear. -/
abbrev PartAsgn := List VarId

/-- A check function returns true iff the given partial assignment
    constitutes a countermodel (i.e., the assignment falsifies the
    target proposition). -/
abbrev CheckFn := PartAsgn → Bool

-- ════════════════════════════════════════════════════════════════════
-- § 2  Subassignment Relation
-- ════════════════════════════════════════════════════════════════════

/-- S is a subassignment of T if every active variable in S also
    appears in T.  This is the subset relation on the underlying
    sets of variable identifiers. -/
def subasgn (S T : PartAsgn) : Prop :=
  ∀ x, x ∈ S → x ∈ T

/-- Subassignment is reflexive: every assignment is a subassignment
    of itself. -/
theorem subasgn_refl (M : PartAsgn) : subasgn M M :=
  fun _ h => h

/-- Subassignment is transitive. -/
theorem subasgn_trans {R S T : PartAsgn} (h1 : subasgn R S) (h2 : subasgn S T) :
    subasgn R T :=
  fun x hx => h2 x (h1 x hx)

-- ════════════════════════════════════════════════════════════════════
-- § 3  Upward Monotone Failure Predicates
-- ════════════════════════════════════════════════════════════════════

/-- A check function is upward monotone if adding more variable
    assignments preserves the failure property.

    Intuition: if a partial assignment S already witnesses that
    proposition φ fails (the critical variables have the wrong values),
    then any extension T ⊇ S still witnesses the failure, because
    the relevant variables remain wrongly valued. -/
def UpwardMonotone (check : CheckFn) : Prop :=
  ∀ S T, subasgn S T → check S = true → check T = true

/-- Contrapositive of upward monotonicity.
    This is the key lemma for the minimality proof: if check T = false
    and S ⊆ T, then check S = false.
    Proof: if check S = true, then by upward monotonicity check T = true,
    contradicting the hypothesis. -/
theorem upward_mono_contra (check : CheckFn) (hup : UpwardMonotone check)
    {S T : PartAsgn} (hST : subasgn S T) (hT : check T = false) :
    check S = false := by
  cases hcS : check S with
  | false => rfl
  | true  => exact absurd (hup S T hST hcS) (by simp [hT])

-- ════════════════════════════════════════════════════════════════════
-- § 4  Variable Removal
-- ════════════════════════════════════════════════════════════════════

/-- Remove all occurrences of variable x from assignment M.
    Implemented via List.filter with an explicit if-then-else to avoid
    any Decidable instance issues. -/
def removeVar (x : VarId) (M : PartAsgn) : PartAsgn :=
  M.filter (fun v => if v = x then false else true)

/-- Membership characterisation for removeVar:
    y ∈ removeVar x M iff y ∈ M and y ≠ x. -/
theorem mem_removeVar_iff (x y : VarId) (M : PartAsgn) :
    y ∈ removeVar x M ↔ y ∈ M ∧ y ≠ x := by
  simp only [removeVar, List.mem_filter]
  constructor
  · intro ⟨hm, hb⟩
    exact ⟨hm, fun heq => by subst heq; simp at hb⟩
  · intro ⟨hm, hne⟩
    exact ⟨hm, if_neg hne⟩

/-- x never appears in removeVar x M: the element being removed is
    absent from the result. -/
theorem not_mem_removeVar_self (x : VarId) (M : PartAsgn) :
    x ∉ removeVar x M := by
  intro h
  rw [mem_removeVar_iff] at h
  exact h.2 rfl

/-- Membership in removeVar x M implies membership in M. -/
theorem mem_of_mem_removeVar (x y : VarId) (M : PartAsgn) :
    y ∈ removeVar x M → y ∈ M :=
  fun h => ((mem_removeVar_iff x y M).mp h).1

/-- removeVar is monotone with respect to subasgn:
    if S ⊆ T then removeVar x S ⊆ removeVar x T. -/
theorem removeVar_subasgn (x : VarId) {S T : PartAsgn} (h : subasgn S T) :
    subasgn (removeVar x S) (removeVar x T) := by
  intro y hy
  rw [mem_removeVar_iff] at hy ⊢
  exact ⟨h y hy.1, hy.2⟩

-- ════════════════════════════════════════════════════════════════════
-- § 5  The Greedy Minimizer
-- ════════════════════════════════════════════════════════════════════

/-- Greedy minimization auxiliary function: process elements from xs
    one at a time, maintaining an accumulator acc.

    For each element x in xs:
      – If check(removeVar x acc) = true: x can be removed while
        maintaining the countermodel property.  Drop x from acc.
      – Otherwise: x is needed.  Keep it in acc.

    The final accumulator is the minimized assignment. -/
def minimizeAux (check : CheckFn) : List VarId → PartAsgn → PartAsgn
  | [],       acc => acc
  | x :: xs,  acc =>
    if check (removeVar x acc) then minimizeAux check xs (removeVar x acc)
    else minimizeAux check xs acc

/-- The public minimizer: process all variables of M in list order. -/
def minimize (check : CheckFn) (M : PartAsgn) : PartAsgn :=
  minimizeAux check M M

-- ════════════════════════════════════════════════════════════════════
-- § 6  Unfolding Equations for minimizeAux
-- ════════════════════════════════════════════════════════════════════

/-- When check(removeVar x acc) = true, minimizeAux drops x
    and continues with the smaller accumulator removeVar x acc. -/
theorem minimizeAux_cons_pos (check : CheckFn) (x : VarId) (xs : List VarId)
    (acc : PartAsgn) (h : check (removeVar x acc) = true) :
    minimizeAux check (x :: xs) acc = minimizeAux check xs (removeVar x acc) := by
  simp [minimizeAux, h]

/-- When check(removeVar x acc) = false, minimizeAux keeps x
    and continues with the unchanged accumulator. -/
theorem minimizeAux_cons_neg (check : CheckFn) (x : VarId) (xs : List VarId)
    (acc : PartAsgn) (h : check (removeVar x acc) = false) :
    minimizeAux check (x :: xs) acc = minimizeAux check xs acc := by
  simp [minimizeAux, h]

-- ════════════════════════════════════════════════════════════════════
-- § 7  Subset Preservation
-- ════════════════════════════════════════════════════════════════════

/-- The greedy minimizer only removes elements: the result is always a
    subassignment of the accumulator.

    Proof by structural induction on xs.
    – Base case: minimizeAux [] acc = acc, so subasgn acc acc. ✓
    – Step case (cons y rest):
        ▸ If check(removeVar y acc) = true: result = minimizeAux rest (removeVar y acc).
          By IH, result ⊆ removeVar y acc ⊆ acc. ✓
        ▸ If check(removeVar y acc) = false: result = minimizeAux rest acc.
          By IH, result ⊆ acc. ✓ -/
theorem minimizeAux_subasgn (check : CheckFn) (xs : List VarId) (acc : PartAsgn) :
    subasgn (minimizeAux check xs acc) acc := by
  induction xs generalizing acc with
  | nil  => intro x hx; exact hx
  | cons y rest ih =>
    intro x hx
    cases h : check (removeVar y acc) with
    | true =>
      rw [minimizeAux_cons_pos check y rest acc h] at hx
      exact mem_of_mem_removeVar y x acc (ih (removeVar y acc) x hx)
    | false =>
      rw [minimizeAux_cons_neg check y rest acc h] at hx
      exact ih acc x hx

/-- The minimizer output is a subassignment of the input. -/
theorem minimize_subasgn (check : CheckFn) (M : PartAsgn) :
    subasgn (minimize check M) M :=
  minimizeAux_subasgn check M M

-- ════════════════════════════════════════════════════════════════════
-- § 8  Countermodel Preservation
-- ════════════════════════════════════════════════════════════════════

/-- minimizeAux preserves the check property: if check acc = true,
    then check (minimizeAux check xs acc) = true.

    Elements are removed only when check is still satisfied by the
    smaller set; otherwise they are retained unchanged.

    Proof by induction on xs:
    – Base: check (minimizeAux [] acc) = check acc = true. ✓
    – Step (cons y rest):
        ▸ If check(removeVar y acc) = true: continue with removeVar y acc.
          By IH on rest, check(minimizeAux rest (removeVar y acc)) = true. ✓
        ▸ If check(removeVar y acc) = false: continue with acc (unchanged).
          By IH on rest, check(minimizeAux rest acc) = true. ✓ -/
theorem minimizeAux_preserves (check : CheckFn) (xs : List VarId)
    (acc : PartAsgn) (h : check acc = true) :
    check (minimizeAux check xs acc) = true := by
  induction xs generalizing acc with
  | nil  => simpa [minimizeAux]
  | cons y rest ih =>
    cases hc : check (removeVar y acc) with
    | true =>
      rw [minimizeAux_cons_pos check y rest acc hc]
      exact ih (removeVar y acc) hc
    | false =>
      rw [minimizeAux_cons_neg check y rest acc hc]
      exact ih acc h

/-- The minimizer preserves the countermodel property. -/
theorem minimize_preserves (check : CheckFn) (M : PartAsgn)
    (h : check M = true) : check (minimize check M) = true :=
  minimizeAux_preserves check M M h

-- ════════════════════════════════════════════════════════════════════
-- § 9  Main Minimality Theorem
-- ════════════════════════════════════════════════════════════════════

/-- **Core Lemma (Local Minimality of minimizeAux)**:
    Under an upward-monotone check function, every element of xs that
    is RETAINED in the output of minimizeAux cannot be singly removed
    while maintaining the countermodel property.

    Proof by induction on xs.

    Step (cons y rest) — two sub-cases:

    Case A (y removed, check(removeVar y acc) = true):
      The output is minimizeAux rest (removeVar y acc).
      – If x = y: y ∉ removeVar y acc (not_mem_removeVar_self), but
        minimizeAux_subasgn says y ∈ output ⊆ removeVar y acc.
        Contradiction; goal is vacuous.
      – If x ∈ rest: apply IH with accumulator removeVar y acc. ✓

    Case B (y kept, check(removeVar y acc) = false):
      The output is minimizeAux rest acc.
      – If x = y: we need check(removeVar y (minimizeAux rest acc)) = false.
          · minimizeAux rest acc ⊆ acc (minimizeAux_subasgn)
          · removeVar y (minimizeAux rest acc) ⊆ removeVar y acc
            (removeVar_subasgn)
          · check(removeVar y acc) = false (hypothesis h)
          · By upward_mono_contra: check(removeVar y (minimizeAux rest acc)) = false. ✓
      – If x ∈ rest: apply IH with accumulator acc. ✓ -/
theorem minimizeAux_locally_minimal
    (check : CheckFn) (hup : UpwardMonotone check)
    (xs : List VarId) (acc : PartAsgn) (hacc : check acc = true) :
    ∀ x ∈ xs, x ∈ minimizeAux check xs acc →
      check (removeVar x (minimizeAux check xs acc)) = false := by
  induction xs generalizing acc with
  | nil  =>
    intro x hx
    exact absurd hx (List.not_mem_nil x)
  | cons y rest ih =>
    intro x hx_in_xs hx_in_min
    simp only [List.mem_cons] at hx_in_xs
    cases h : check (removeVar y acc) with
    | true =>
      -- y was removed; result = minimizeAux check rest (removeVar y acc)
      rw [minimizeAux_cons_pos check y rest acc h] at hx_in_min ⊢
      cases hx_in_xs with
      | inl hxy =>
        -- x = y, but y ∉ removeVar y acc ⊇ result — contradiction
        subst hxy
        exact absurd
          (minimizeAux_subasgn check rest (removeVar x acc) x hx_in_min)
          (not_mem_removeVar_self x acc)
      | inr hx_rest =>
        -- x ∈ rest: apply IH with accumulator removeVar y acc
        exact ih (removeVar y acc) h x hx_rest hx_in_min
    | false =>
      -- y was kept; result = minimizeAux check rest acc
      rw [minimizeAux_cons_neg check y rest acc h] at hx_in_min ⊢
      cases hx_in_xs with
      | inl hxy =>
        -- x = y was retained; prove check(removeVar y (final)) = false
        subst hxy
        -- (1) final = minimizeAux check rest acc ⊆ acc
        -- (2) removeVar x (final) ⊆ removeVar x acc
        -- (3) check(removeVar x acc) = false  [this is h]
        -- (4) by upward_mono_contra: check(removeVar x (final)) = false
        exact upward_mono_contra check hup
          (removeVar_subasgn x (minimizeAux_subasgn check rest acc)) h
      | inr hx_rest =>
        -- x ∈ rest: apply IH with same accumulator acc
        exact ih acc hacc x hx_rest hx_in_min

/-- **Main Theorem (Minimality)**:
    Under an upward-monotone failure predicate, the greedy minimizer
    produces a locally minimal failing witness:

    (i)   subasgn (minimize M) M           (sub-assignment)
    (ii)  check (minimize M) = true        (still a countermodel)
    (iii) ∀ x ∈ minimize M,
            check (removeVar x (minimize M)) = false  (locally minimal)

    The proof of (iii) applies minimizeAux_locally_minimal with
    xs = M = acc.  Every retained element was tested during the greedy
    pass; upward monotonicity ensures the "failed removal" decision
    remains valid for the final (potentially smaller) accumulator. -/
theorem minimizer_produces_locally_minimal
    (check : CheckFn) (hup : UpwardMonotone check)
    (M : PartAsgn) (hM : check M = true) :
    subasgn (minimize check M) M ∧
    check (minimize check M) = true ∧
    ∀ x ∈ minimize check M,
      check (removeVar x (minimize check M)) = false := by
  refine ⟨minimize_subasgn check M, minimize_preserves check M hM, ?_⟩
  intro x hx_in_min
  -- x ∈ minimize M ⊆ M (via minimize_subasgn), so x ∈ M = xs in the call
  have hx_in_M : x ∈ M := minimize_subasgn check M x hx_in_min
  simp only [minimize] at hx_in_min ⊢
  exact minimizeAux_locally_minimal check hup M M hM x hx_in_M hx_in_min

-- ════════════════════════════════════════════════════════════════════
-- § 10  Size Monotonicity
-- ════════════════════════════════════════════════════════════════════

/-- Minimization never increases the size of the assignment.
    The minimized countermodel has at most as many variables as the
    original. -/
theorem minimizeAux_length_le (check : CheckFn) (xs : List VarId) (acc : PartAsgn) :
    (minimizeAux check xs acc).length ≤ acc.length := by
  induction xs generalizing acc with
  | nil  => simp [minimizeAux]
  | cons y rest ih =>
    cases h : check (removeVar y acc) with
    | true  =>
      rw [minimizeAux_cons_pos check y rest acc h]
      exact Nat.le_trans (ih (removeVar y acc)) (List.length_filter_le _ acc)
    | false =>
      rw [minimizeAux_cons_neg check y rest acc h]
      exact ih acc

theorem minimize_length_le (check : CheckFn) (M : PartAsgn) :
    (minimize check M).length ≤ M.length :=
  minimizeAux_length_le check M M

-- ════════════════════════════════════════════════════════════════════
-- § 11  Normalization
-- ════════════════════════════════════════════════════════════════════

/-- A normalization function n is idempotent: applying it twice gives
    the same result as applying it once.  The CountermodelNormalizer
    satisfies this by construction (sort-renaming is stable under
    re-application). -/
def isIdempotent (n : PartAsgn → PartAsgn) : Prop :=
  ∀ M, n (n M) = n M

/-- A normalization is semantics-preserving if it does not change the
    result of any check function. -/
def isSemPreserving (n : PartAsgn → PartAsgn) (check : CheckFn) : Prop :=
  ∀ M, check (n M) = check M

/-- Under semantics preservation, if M is a countermodel then so is n M. -/
theorem norm_preserves_countermodel (n : PartAsgn → PartAsgn) (check : CheckFn)
    (hn : isSemPreserving n check) (M : PartAsgn) (hM : check M = true) :
    check (n M) = true := by
  rw [hn]; exact hM

-- ════════════════════════════════════════════════════════════════════
-- § 12  Failure Classification and Obstruction Records
-- ════════════════════════════════════════════════════════════════════

/-- High-level classification of failure kinds, matching the FailureClass
    enum in countermodels.py. -/
inductive FailureClass where
  | assignmentConflict  -- two variables assigned inconsistent values
  | sortViolation       -- element outside declared sort domain
  | functionMismatch    -- f(a) ≠ expected value
  | arrayOutOfBounds    -- index outside array bounds
  | quantifierWitness   -- Skolem witness satisfies ¬∀
  | unknown             -- none of the above
  deriving DecidableEq, Repr, Inhabited

/-- An obstruction record bundles a minimized assignment with its
    failure class and the coordinate where failure was witnessed.
    Corresponds to ObstructionRecord in countermodels.py. -/
structure ObstructionRecord where
  coordinate   : String
  proposition  : String
  failureClass : FailureClass
  assignment   : PartAsgn
  modelId      : String
  deriving Repr

/-- An obstruction record is well-formed if its assignment is a
    countermodel for the check function. -/
def ObstructionRecord.wellFormed (r : ObstructionRecord) (check : CheckFn) : Prop :=
  check r.assignment = true

/-- An obstruction record is locally minimal if no single variable can
    be removed from its assignment while maintaining failure. -/
def ObstructionRecord.isMinimal (r : ObstructionRecord) (check : CheckFn) : Prop :=
  check r.assignment = true ∧
  ∀ x ∈ r.assignment, check (removeVar x r.assignment) = false

/-- **Corollary**: The ObstructionConverter in countermodels.py produces
    minimal obstruction records.  Converting a countermodel via the
    greedy minimizer and then wrapping it in an ObstructionRecord yields
    a record whose assignment is locally minimal. -/
theorem obstruction_record_from_minimized
    (check : CheckFn) (hup : UpwardMonotone check)
    (M : PartAsgn) (hM : check M = true)
    (coord prop id : String) :
    let M'  := minimize check M
    let r : ObstructionRecord :=
      { coordinate   := coord
        proposition  := prop
        failureClass := .unknown
        assignment   := M'
        modelId      := id }
    r.isMinimal check := by
  simp only [ObstructionRecord.isMinimal, minimize]
  refine ⟨minimizeAux_preserves check M M hM, ?_⟩
  intro x hx_in_min
  exact minimizeAux_locally_minimal check hup M M hM x
    (minimizeAux_subasgn check M M x hx_in_min)
    hx_in_min

end JudgmentGeometry.CountermodelExtraction
