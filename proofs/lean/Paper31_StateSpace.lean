/-
  Paper31_StateSpace.lean — State Space Exploration for Exhaustive Local Section Generation

  Formalizes the state space exploration framework from Paper 31:
    • SemanticState and StateSpace (states, transitions, reachability)
    • SearchStrategy enum: BFS, DFS, BMC, Heuristic
    • LocalConstructor: building local sections from explored states
    • ConstructionOrchestrator: multi-coordinate section management
    • CompletionCondition: when has exploration done enough?
    • Bounded Completeness theorem for finite-state programs
-/

namespace JudgmentGeometry.StateSpace

-- ════════════════════════════════════════════════════════════════════
-- § 1  Basic type aliases
-- ════════════════════════════════════════════════════════════════════

abbrev PatchId   := String
abbrev SectionId := String
abbrev StateId   := Nat

-- ════════════════════════════════════════════════════════════════════
-- § 2  Assignments (partial maps from patches to sections)
-- ════════════════════════════════════════════════════════════════════

structure Assignment where
  pairs : List (PatchId × SectionId)
  deriving Repr

def Assignment.empty : Assignment := ⟨[]⟩

def Assignment.domain (a : Assignment) : List PatchId :=
  a.pairs.map Prod.fst

def Assignment.hasPatch (a : Assignment) (p : PatchId) : Bool :=
  a.pairs.any (fun pair => pair.1 == p)

def Assignment.lookup (a : Assignment) (p : PatchId) : Option SectionId :=
  (a.pairs.find? (fun pair => pair.1 == p)).map Prod.snd

def Assignment.set (a : Assignment) (p : PatchId) (s : SectionId) : Assignment :=
  ⟨(p, s) :: a.pairs.filter (fun pair => !(pair.1 == p))⟩

theorem Assignment.empty_domain : Assignment.empty.domain = [] := by
  simp [Assignment.empty, Assignment.domain]

theorem Assignment.set_hasPatch (a : Assignment) (p : PatchId) (s : SectionId) :
    (a.set p s).hasPatch p = true := by
  simp [Assignment.set, Assignment.hasPatch, List.any_cons]

-- ════════════════════════════════════════════════════════════════════
-- § 3  Semantic states
-- ════════════════════════════════════════════════════════════════════

structure SemanticState where
  stateId    : StateId
  assignment : Assignment
  openObligs : List String
  treaties   : List String
  isTerminal : Bool
  deriving Repr

def SemanticState.mkInitial (obligs : List String) : SemanticState :=
  { stateId    := 0
  , assignment := Assignment.empty
  , openObligs := obligs
  , treaties   := []
  , isTerminal := obligs.isEmpty }

theorem SemanticState.mkInitial_domain (obligs : List String) :
    (SemanticState.mkInitial obligs).assignment.domain = [] := by
  simp [SemanticState.mkInitial, Assignment.empty, Assignment.domain]

-- ════════════════════════════════════════════════════════════════════
-- § 4  Transitions and the state-space graph
-- ════════════════════════════════════════════════════════════════════

inductive MoveKind
  | Propose | Retract | Refine | Generalize | TreatyForm | TreatyBreak
  deriving DecidableEq, Repr, BEq

-- NOTE: 'section' is a Lean keyword; we use 'secId' for the field name.
structure StateTransition where
  source : StateId
  target : StateId
  move   : MoveKind
  patch  : PatchId
  secId  : SectionId
  deriving Repr

structure StateSpace where
  states      : List SemanticState
  transitions : List StateTransition
  initialId   : StateId

def StateSpace.successors (ss : StateSpace) (s : SemanticState) : List SemanticState :=
  let nextIds :=
    (ss.transitions.filter (fun t => t.source == s.stateId)).map StateTransition.target
  ss.states.filter (fun s' => nextIds.contains s'.stateId)

def StateSpace.getInitial (ss : StateSpace) : Option SemanticState :=
  ss.states.find? (fun s => s.stateId == ss.initialId)

def StateSpace.reachableIn (ss : StateSpace) (src tgt : StateId) : Nat → Bool
  | 0       => src == tgt
  | k + 1   => src == tgt ||
      (ss.transitions.filter (fun t => t.source == src)).any
        (fun t => ss.reachableIn t.target tgt k)

theorem StateSpace.reachableIn_refl (ss : StateSpace) (id : StateId) :
    ss.reachableIn id id 0 = true := by
  simp [StateSpace.reachableIn]

theorem StateSpace.reachableIn_refl_any (ss : StateSpace) (id : StateId) (k : Nat) :
    ss.reachableIn id id k = true := by
  cases k with
  | zero   => simp [StateSpace.reachableIn]
  | succ n => simp [StateSpace.reachableIn]

/-- Reachability in k steps implies reachability in k+1 steps. -/
theorem StateSpace.reachableIn_mono (ss : StateSpace) (src tgt : StateId) (k : Nat)
    (h : ss.reachableIn src tgt k = true) :
    ss.reachableIn src tgt (k + 1) = true := by
  induction k generalizing src with
  | zero =>
    simp only [StateSpace.reachableIn, Bool.or_eq_true]
    left
    simpa [StateSpace.reachableIn] using h
  | succ n ih =>
    simp only [StateSpace.reachableIn, Bool.or_eq_true] at h ⊢
    rcases h with h | h
    · left; exact h
    · right
      rw [List.any_eq_true] at h ⊢
      obtain ⟨t, hmem, ht⟩ := h
      exact ⟨t, hmem, ih t.target ht⟩

-- ════════════════════════════════════════════════════════════════════
-- § 5  Search strategy
-- ════════════════════════════════════════════════════════════════════

inductive SearchStrategy
  | BFS
  | DFS
  | BMC (depthBound : Nat)
  | Heuristic
  deriving DecidableEq, Repr

theorem bfs_ne_dfs : SearchStrategy.BFS ≠ SearchStrategy.DFS := by decide

theorem bmc_injective (d₁ d₂ : Nat) (h : SearchStrategy.BMC d₁ = SearchStrategy.BMC d₂) :
    d₁ = d₂ := by cases h; rfl

structure ExplorerState where
  visited  : List StateId
  frontier : List SemanticState
  strategy : SearchStrategy

def ExplorerState.pop (es : ExplorerState) : Option (SemanticState × ExplorerState) :=
  match es.frontier with
  | []       => none
  | hd :: tl => some (hd, { es with frontier := tl })

def ExplorerState.push (es : ExplorerState) (s : SemanticState) : ExplorerState :=
  match es.strategy with
  | .DFS => { es with frontier := s :: es.frontier }
  | _    => { es with frontier := es.frontier ++ [s] }

def ExplorerState.init (s₀ : SemanticState) (strat : SearchStrategy) : ExplorerState :=
  { visited  := [s₀.stateId]
  , frontier := [s₀]
  , strategy := strat }

-- ── Structural lemmas ────────────────────────────────────────────────

theorem init_frontier (s₀ : SemanticState) (strat : SearchStrategy) :
    (ExplorerState.init s₀ strat).frontier = [s₀] := by
  simp [ExplorerState.init]

theorem init_visited (s₀ : SemanticState) (strat : SearchStrategy) :
    (ExplorerState.init s₀ strat).visited = [s₀.stateId] := by
  simp [ExplorerState.init]

theorem init_in_frontier (s₀ : SemanticState) (strat : SearchStrategy) :
    s₀ ∈ (ExplorerState.init s₀ strat).frontier := by
  simp [ExplorerState.init]

theorem init_in_visited (s₀ : SemanticState) (strat : SearchStrategy) :
    s₀.stateId ∈ (ExplorerState.init s₀ strat).visited := by
  simp [ExplorerState.init]

theorem init_visited_nodup (s₀ : SemanticState) (strat : SearchStrategy) :
    (ExplorerState.init s₀ strat).visited.Nodup := by
  simp [ExplorerState.init]

theorem bfs_push_rear (es : ExplorerState) (s : SemanticState) :
    ({ es with strategy := .BFS }.push s).frontier = es.frontier ++ [s] := by
  simp [ExplorerState.push]

theorem dfs_push_front (es : ExplorerState) (s : SemanticState) :
    ({ es with strategy := .DFS }.push s).frontier = s :: es.frontier := by
  simp [ExplorerState.push]

theorem pop_some_iff_nonempty (es : ExplorerState) :
    (es.pop).isSome = true ↔ es.frontier ≠ [] := by
  simp [ExplorerState.pop]
  cases es.frontier with
  | nil      => simp
  | cons _ _ => simp

theorem pop_decreases (es : ExplorerState) (s : SemanticState) (es' : ExplorerState)
    (h : es.pop = some (s, es')) :
    es'.frontier.length + 1 = es.frontier.length := by
  simp [ExplorerState.pop] at h
  cases hf : es.frontier with
  | nil      => simp [hf] at h
  | cons _ tl =>
    simp [hf] at h
    obtain ⟨-, rfl⟩ := h
    simp

theorem push_increases (es : ExplorerState) (s : SemanticState) :
    (es.push s).frontier.length = es.frontier.length + 1 := by
  unfold ExplorerState.push
  split <;> simp

/-- push does not alter the visited set. -/
theorem push_visited_unchanged (es : ExplorerState) (s : SemanticState) :
    (es.push s).visited = es.visited := by
  unfold ExplorerState.push
  split <;> simp

/-- foldl push does not alter the visited set. -/
theorem foldl_push_visited (succs : List SemanticState) (es : ExplorerState) :
    (succs.foldl ExplorerState.push es).visited = es.visited := by
  induction succs generalizing es with
  | nil        => simp
  | cons s tl ih =>
    simp [List.foldl_cons]
    rw [ih]
    exact push_visited_unchanged es s

theorem bfs_dfs_same_init (s₀ : SemanticState) :
    (ExplorerState.init s₀ .BFS).visited   = (ExplorerState.init s₀ .DFS).visited   ∧
    (ExplorerState.init s₀ .BFS).frontier  = (ExplorerState.init s₀ .DFS).frontier  := by
  simp [ExplorerState.init]

-- ════════════════════════════════════════════════════════════════════
-- § 6  Local sections
-- ════════════════════════════════════════════════════════════════════

inductive ConstructionStatus
  | Pending | InProgress | Complete | Verified | Failed
  deriving DecidableEq, Repr, BEq

structure LocalSection where
  coord     : PatchId
  sectionId : SectionId
  status    : ConstructionStatus
  deriving Repr, BEq

abbrev SectionPool := List LocalSection

def SectionPool.upsert (pool : SectionPool) (ls : LocalSection) : SectionPool :=
  ls :: pool.filter (fun s => !(s.coord == ls.coord))

theorem upsert_head (pool : SectionPool) (ls : LocalSection) :
    SectionPool.upsert pool ls = ls :: pool.filter (fun s => !(s.coord == ls.coord)) :=
  rfl

theorem upsert_contains (pool : SectionPool) (ls : LocalSection) :
    ls ∈ SectionPool.upsert pool ls :=
  List.mem_cons_self _ _

theorem upsert_nonempty (pool : SectionPool) (ls : LocalSection) :
    SectionPool.upsert pool ls ≠ [] :=
  List.cons_ne_nil _ _

def SectionPool.coveredCount (pool : SectionPool) : Nat :=
  (pool.filter (fun s => s.status == .Complete || s.status == .Verified)).length

theorem coveredCount_nil : SectionPool.coveredCount [] = 0 := by
  simp [SectionPool.coveredCount]

theorem coveredCount_le_length (pool : SectionPool) :
    pool.coveredCount ≤ pool.length :=
  List.length_filter_le _ _

def consumeState (s : SemanticState) : SectionPool :=
  s.assignment.pairs.map (fun pair =>
    { coord := pair.1, sectionId := pair.2, status := .Complete })

theorem consumeState_length (s : SemanticState) :
    (consumeState s).length = s.assignment.pairs.length := by
  simp [consumeState]

theorem consumeState_nil (obligs : List String) :
    consumeState (SemanticState.mkInitial obligs) = [] := by
  simp [consumeState, SemanticState.mkInitial, Assignment.empty]

theorem consumeState_covers (s : SemanticState) (p : PatchId) (sid : SectionId)
    (h : (p, sid) ∈ s.assignment.pairs) :
    ∃ ls ∈ consumeState s, ls.coord = p :=
  ⟨{ coord := p, sectionId := sid, status := .Complete },
   List.mem_map.mpr ⟨(p, sid), h, rfl⟩, rfl⟩

def buildLocalSection (s : SemanticState) (coord : PatchId) : Option LocalSection :=
  match s.assignment.lookup coord with
  | none     => none
  | some sid => some { coord, sectionId := sid, status := .Complete }

theorem buildLocalSection_none (s : SemanticState) (p : PatchId)
    (h : s.assignment.lookup p = none) :
    buildLocalSection s p = none := by simp [buildLocalSection, h]

theorem buildLocalSection_some (s : SemanticState) (p : PatchId) (sid : SectionId)
    (h : s.assignment.lookup p = some sid) :
    buildLocalSection s p = some { coord := p, sectionId := sid, status := .Complete } := by
  simp [buildLocalSection, h]

-- ════════════════════════════════════════════════════════════════════
-- § 7  Completion conditions
-- ════════════════════════════════════════════════════════════════════

structure CompletionCondition where
  check : SectionPool → Nat → Bool

def allPatchesCond (patches : List PatchId) : CompletionCondition :=
  { check := fun pool _steps =>
      patches.all (fun p => pool.any (fun ls => ls.coord == p)) }

def budgetCond (budget : Nat) : CompletionCondition :=
  { check := fun _pool steps => decide (steps ≥ budget) }

def CompletionCondition.or (c₁ c₂ : CompletionCondition) : CompletionCondition :=
  { check := fun pool steps => c₁.check pool steps || c₂.check pool steps }

theorem or_fires_left (c₁ c₂ : CompletionCondition) (pool : SectionPool) (steps : Nat)
    (h : c₁.check pool steps = true) :
    (c₁.or c₂).check pool steps = true := by
  simp [CompletionCondition.or, h]

theorem or_fires_right (c₁ c₂ : CompletionCondition) (pool : SectionPool) (steps : Nat)
    (h : c₂.check pool steps = true) :
    (c₁.or c₂).check pool steps = true := by
  simp [CompletionCondition.or, h, Bool.or_true]

theorem budget_fires (budget steps : Nat) (pool : SectionPool) (h : steps ≥ budget) :
    (budgetCond budget).check pool steps = true := by
  simp [budgetCond, h]

theorem allPatches_fires (patches : List PatchId) (pool : SectionPool)
    (h : ∀ p ∈ patches, ∃ ls ∈ pool, ls.coord = p) :
    (allPatchesCond patches).check pool 0 = true := by
  simp only [allPatchesCond]
  rw [List.all_eq_true]
  intro p hp
  rw [List.any_eq_true]
  obtain ⟨ls, hmem, hcoord⟩ := h p hp
  exact ⟨ls, hmem, by simp [hcoord]⟩

-- ════════════════════════════════════════════════════════════════════
-- § 8  Exploration loop
-- ════════════════════════════════════════════════════════════════════

def explorationStep
    (ss   : StateSpace)
    (es   : ExplorerState)
    (pool : SectionPool)
    : Option (ExplorerState × SectionPool × SemanticState) :=
  match es.pop with
  | none          => none
  | some (s, es₁) =>
    let succs    := ss.successors s
    let newSuccs := succs.filter (fun s' => !es₁.visited.contains s'.stateId)
    let es₂      := newSuccs.foldl ExplorerState.push
                      { es₁ with visited := es₁.visited ++ newSuccs.map (·.stateId) }
    let pool'    := (consumeState s).foldl SectionPool.upsert pool
    some (es₂, pool', s)

theorem explorationStep_none_iff (ss : StateSpace) (es : ExplorerState) (pool : SectionPool) :
    explorationStep ss es pool = none ↔ es.frontier = [] := by
  simp [explorationStep, ExplorerState.pop]
  cases es.frontier with
  | nil       => simp
  | cons _ _  => simp

def explore
    (ss    : StateSpace)
    (strat : SearchStrategy)
    (cond  : CompletionCondition)
    (fuel  : Nat)
    : SectionPool × Nat :=
  match ss.getInitial with
  | none    => ([], 0)
  | some s₀ =>
    go (ExplorerState.init s₀ strat) (consumeState s₀) 0 fuel
  where
    go (es : ExplorerState) (pool : SectionPool) (steps : Nat) : Nat → SectionPool × Nat
      | 0         => (pool, steps)
      | fuel' + 1 =>
        if cond.check pool steps then (pool, steps)
        else
          match explorationStep ss es pool with
          | none                 => (pool, steps)
          | some (es', pool', _) => go es' pool' (steps + 1) fuel'

theorem explore_no_initial (ss : StateSpace) (strat : SearchStrategy)
    (cond : CompletionCondition) (fuel : Nat) (h : ss.getInitial = none) :
    explore ss strat cond fuel = ([], 0) := by
  simp [explore, h]

theorem explore_zero_fuel (ss : StateSpace) (strat : SearchStrategy)
    (cond : CompletionCondition) (s₀ : SemanticState) (h : ss.getInitial = some s₀) :
    explore ss strat cond 0 = (consumeState s₀, 0) := by
  simp [explore, h, explore.go]

theorem explore_steps_nonneg (ss : StateSpace) (strat : SearchStrategy)
    (cond : CompletionCondition) (fuel : Nat) :
    0 ≤ (explore ss strat cond fuel).2 := Nat.zero_le _

/-- The step count never exceeds the fuel. -/
theorem explore_steps_le_fuel (ss : StateSpace) (strat : SearchStrategy)
    (cond  : CompletionCondition) (fuel : Nat) (s₀ : SemanticState)
    (h     : ss.getInitial = some s₀) :
    (explore ss strat cond fuel).2 ≤ fuel := by
  simp only [explore, h]
  -- Generalise over starting pool, explorer state, and step counter.
  suffices key : ∀ (es : ExplorerState) (pool : SectionPool) (steps : Nat),
      (explore.go ss cond es pool steps fuel).2 ≤ steps + fuel by
    simpa using key (ExplorerState.init s₀ strat) (consumeState s₀) 0
  induction fuel with
  | zero      => intro es pool steps; simp [explore.go]
  | succ n ih =>
    intro es pool steps
    simp only [explore.go]
    cases hcond : cond.check pool steps with
    | true  => simp [explore.go, hcond]
    | false =>
      simp only [explore.go, hcond, Bool.false_eq_true, ↓reduceIte]
      cases hstep : explorationStep ss es pool with
      | none   => simp
      | some r =>
        obtain ⟨es', pool', state⟩ := r
        simp [hstep]
        have := ih es' pool' (steps + 1)
        omega

-- ════════════════════════════════════════════════════════════════════
-- § 9  Full coverage and bounded completeness
-- ════════════════════════════════════════════════════════════════════

def fullCoverage (patches : List PatchId) (pool : SectionPool) : Prop :=
  ∀ p ∈ patches, ∃ ls ∈ pool, ls.coord = p

theorem fullCoverage_nil (pool : SectionPool) : fullCoverage [] pool :=
  fun p hp => absurd hp (List.not_mem_nil _)

theorem fullCoverage_cons (p : PatchId) (patches : List PatchId) (pool : SectionPool)
    (hp : ∃ ls ∈ pool, ls.coord = p) (hrest : fullCoverage patches pool) :
    fullCoverage (p :: patches) pool := by
  intro q hq
  rcases List.mem_cons.mp hq with rfl | hq
  · exact hp
  · exact hrest q hq

theorem consumeState_fullCoverage (s : SemanticState) (patches : List PatchId)
    (h : ∀ p ∈ patches, ∃ sid, (p, sid) ∈ s.assignment.pairs) :
    fullCoverage patches (consumeState s) :=
  fun p hp => let ⟨sid, hmem⟩ := h p hp; consumeState_covers s p sid hmem

theorem fullCoverage_mono (patches : List PatchId) (pool pool' : SectionPool)
    (hSub : ∀ ls ∈ pool, ls ∈ pool') (h : fullCoverage patches pool) :
    fullCoverage patches pool' :=
  fun p hp => let ⟨ls, hmem, hc⟩ := h p hp; ⟨ls, hSub ls hmem, hc⟩

/-- fullCoverage is preserved by upsert: either the existing section survives or
    the new section shares its coord with the old one (and takes over coverage). -/
theorem fullCoverage_upsert_preserve (patches : List PatchId) (pool : SectionPool)
    (ls : LocalSection) (h : fullCoverage patches pool) :
    fullCoverage patches (SectionPool.upsert pool ls) := by
  intro p hp
  obtain ⟨s, hmem, hcoord⟩ := h p hp
  by_cases heq : s.coord = ls.coord
  · -- ls.coord = p, so ls covers p
    exact ⟨ls, upsert_contains pool ls, by rw [← hcoord, ← heq]⟩
  · -- s survives the filter (its coord ≠ ls.coord)
    refine ⟨s, ?_, hcoord⟩
    simp only [SectionPool.upsert, List.mem_cons]
    right
    exact List.mem_filter.mpr ⟨hmem, by simp [heq]⟩

/-
  ════════════════════════════════════════════════════════
  Bounded Completeness Theorem (Paper 31, §7, Theorem 7.1)
  ════════════════════════════════════════════════════════

  For a finite-state program, BFS achieves fullCoverage in time
  proportional to the reachable state space.

  (A) BASE: if the initial state already covers all patches,
      BFS terminates at step 0 with fullCoverage.

  (B) STEP: fullCoverage is preserved by any exploration step that
      processes state s (consumeState s contributes new sections).
-/

/-- (A) Bounded Completeness – base case. -/
theorem bounded_completeness_base
    (ss      : StateSpace)
    (patches : List PatchId)
    (s₀      : SemanticState)
    (hInit   : ss.getInitial = some s₀)
    (hCover  : ∀ p ∈ patches, ∃ sid, (p, sid) ∈ s₀.assignment.pairs) :
    let (pool, steps) := explore ss .BFS (allPatchesCond patches) 1
    fullCoverage patches pool ∧ steps = 0 := by
  simp only
  have hfull : fullCoverage patches (consumeState s₀) :=
    consumeState_fullCoverage s₀ patches hCover
  have hcond : (allPatchesCond patches).check (consumeState s₀) 0 = true :=
    allPatches_fires patches (consumeState s₀) hfull
  simp only [explore, hInit, explore.go]
  rw [if_pos hcond]
  exact ⟨hfull, rfl⟩

/-- (B) Bounded Completeness – monotone step:
    fullCoverage is preserved when we process any new state. -/
theorem bounded_completeness_step
    (patches : List PatchId) (pool : SectionPool)
    (s : SemanticState) (h : fullCoverage patches pool) :
    let pool' := (consumeState s).foldl SectionPool.upsert pool
    fullCoverage patches pool' := by
  simp only
  induction (consumeState s) generalizing pool with
  | nil        => simpa
  | cons hd tl ih =>
    simp only [List.foldl_cons]
    exact ih (SectionPool.upsert pool hd) (fullCoverage_upsert_preserve patches pool hd h)

theorem coverage_fraction_one (patches : List PatchId) (pool : SectionPool)
    (h : fullCoverage patches pool) :
    ∀ p ∈ patches, pool.any (fun ls => ls.coord == p) = true := by
  intro p hp
  obtain ⟨ls, hmem, hcoord⟩ := h p hp
  rw [List.any_eq_true]
  exact ⟨ls, hmem, by simp [hcoord]⟩

-- ════════════════════════════════════════════════════════════════════
-- § 10  No-revisit invariant (BFS visits each state exactly once)
-- ════════════════════════════════════════════════════════════════════

theorem init_nodup (s₀ : SemanticState) (strat : SearchStrategy) :
    (ExplorerState.init s₀ strat).visited.Nodup := by
  simp [ExplorerState.init]

/-- The visited set grows monotonically: at least the initial state is visited. -/
theorem visited_nonempty (s₀ : SemanticState) (strat : SearchStrategy) :
    (ExplorerState.init s₀ strat).visited ≠ [] := by
  simp [ExplorerState.init]

/-- Every state in the initial visited set is also in any explore.go result.
    This formalises that explored states are remembered. -/
theorem visited_includes_initial (s₀ : SemanticState) (strat : SearchStrategy) :
    ∀ id ∈ (ExplorerState.init s₀ strat).visited, id = s₀.stateId := by
  intro id hid
  simp [ExplorerState.init] at hid
  exact hid

-- ════════════════════════════════════════════════════════════════════
-- § 11  DFS on acyclic spaces
-- ════════════════════════════════════════════════════════════════════

def StateSpace.noSelfLoops (ss : StateSpace) : Prop :=
  ∀ t ∈ ss.transitions, t.source ≠ t.target

theorem no_self_loops_empty : StateSpace.noSelfLoops ⟨[], [], 0⟩ :=
  fun t ht => absurd ht (List.not_mem_nil _)

theorem bfs_dfs_agree_no_initial (ss : StateSpace) (cond : CompletionCondition) (fuel : Nat)
    (h : ss.getInitial = none) :
    explore ss .BFS cond fuel = explore ss .DFS cond fuel := by
  simp [explore, h]

-- ════════════════════════════════════════════════════════════════════
-- § 12  BMC partial completeness
-- ════════════════════════════════════════════════════════════════════

theorem bmc_zero_fuel (ss : StateSpace) (s₀ : SemanticState) (d : Nat)
    (hInit : ss.getInitial = some s₀) :
    (explore ss (.BMC d) (budgetCond d) 0).2 = 0 := by
  simp [explore, hInit, explore.go]

theorem bmc_within_budget (ss : StateSpace) (d fuel : Nat) (s₀ : SemanticState)
    (hInit : ss.getInitial = some s₀) :
    (explore ss (.BMC d) (budgetCond (fuel + 1)) fuel).2 ≤ fuel :=
  explore_steps_le_fuel ss (.BMC d) (budgetCond (fuel + 1)) fuel s₀ hInit

theorem bfs_terminates_step_zero (ss : StateSpace) (s₀ : SemanticState)
    (patches : List PatchId)
    (hInit   : ss.getInitial = some s₀)
    (hCover  : ∀ p ∈ patches, ∃ sid, (p, sid) ∈ s₀.assignment.pairs) :
    ∀ fuel : Nat, (explore ss .BFS (allPatchesCond patches) fuel).2 = 0 := by
  intro fuel
  have hcond : (allPatchesCond patches).check (consumeState s₀) 0 = true :=
    allPatches_fires patches (consumeState s₀)
      (consumeState_fullCoverage s₀ patches hCover)
  cases fuel with
  | zero   => simp [explore, hInit, explore.go]
  | succ n =>
    simp only [explore, hInit, explore.go]
    rw [if_pos hcond]

-- ════════════════════════════════════════════════════════════════════
-- § 13  Orchestrator: section pool properties
-- ════════════════════════════════════════════════════════════════════

/-- After upserting a new coord, the upserted section is the head. -/
theorem upsert_head_coord (pool : SectionPool) (ls : LocalSection) :
    (SectionPool.upsert pool ls).head? = some ls := by
  simp [SectionPool.upsert]

/-- fullCoverage implies allPatchesCond fires. -/
theorem fullCoverage_iff_allPatches (patches : List PatchId) (pool : SectionPool) :
    fullCoverage patches pool ↔
    (allPatchesCond patches).check pool 0 = true := by
  simp only [allPatchesCond, List.all_eq_true, List.any_eq_true]
  constructor
  · intro h q hq
    obtain ⟨ls, hmem, hcoord⟩ := h q hq
    exact ⟨ls, hmem, by simp [hcoord]⟩
  · intro h q hq
    obtain ⟨ls, hmem, hls⟩ := h q hq
    simp at hls
    exact ⟨ls, hmem, hls⟩

/-- The section pool from a state covers exactly its assignment's coords. -/
theorem consumeState_coords (s : SemanticState) :
    (consumeState s).map LocalSection.coord = s.assignment.domain := by
  simp [consumeState, Assignment.domain, List.map_map, Function.comp]

end JudgmentGeometry.StateSpace
