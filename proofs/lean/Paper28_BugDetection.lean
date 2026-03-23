/-
  Paper28_BugDetection.lean — Sheaf-Theoretic Bug Detection and Classification

  Formalizes Paper 28 of the Judgment Geometry series:
    • BugKind: the eight canonical obstruction families
    • Coordinate: sheaf-site identifier (file, line, column, node type)
    • CheckOutcome: pass | fail | inconclusive
    • LocalCheck: a single proposition check at a coordinate
    • SemanticSite: transparent alias for List LocalCheck (the Čech site)
    • hasViolation: ∃ lc ∈ site, lc.coord = c ∧ lc.outcome = .fail
    • extractReports: canonical detector (collects all failing checks)
    • extractReports_sound: every produced report has a failing witness
    • localization_guarantee: main theorem — zero false positives
    • Additional: severity bounds, trust ordering, BugRepository count
        lemmas, BugClassifier injectivity, atlas soundness

  All theorems proved without sorry.
-/

namespace JudgmentGeometry.BugDetection

-- ════════════════════════════════════════════════════════════════════
-- § 1  Bug Kinds
-- ════════════════════════════════════════════════════════════════════

/-- The eight canonical obstruction families, corresponding bijectively
    to the eight generators of H¹(𝒮_code, ℱ). -/
inductive BugKind where
  | typeError              -- σ_type  : type-mismatch obstruction
  | logicError             -- σ_logic : logic-consistency obstruction
  | scopeViolation         -- σ_scope : scope-containment obstruction
  | protocolViolation      -- σ_proto : protocol-sequence obstruction
  | trustViolation         -- σ_trust : trust-tier demotion obstruction
  | resourceLeak           -- σ_res   : resource-boundedness obstruction
  | concurrencyHazard      -- σ_conc  : data-race obstruction
  | specificationDeviation -- σ_spec  : contract-violation obstruction
  deriving DecidableEq, Repr, Inhabited

/-- Severity bucket in {3, 4, 5}. -/
def BugKind.severity : BugKind → Nat
  | .typeError              => 3
  | .logicError             => 4
  | .scopeViolation         => 4
  | .protocolViolation      => 4
  | .trustViolation         => 5
  | .resourceLeak           => 3
  | .concurrencyHazard      => 5
  | .specificationDeviation => 3

/-- Every severity is in the valid range [1, 5]. -/
theorem severity_valid (k : BugKind) : 1 ≤ k.severity ∧ k.severity ≤ 5 := by
  cases k <;> decide

/-- `isLocal k` is true iff the bug is detectable at a single coordinate. -/
def BugKind.isLocal : BugKind → Bool
  | .typeError | .logicError | .scopeViolation
  | .trustViolation | .resourceLeak         => true
  | .protocolViolation | .concurrencyHazard
  | .specificationDeviation                 => false

/-- Exactly five of the eight kinds are local. -/
theorem five_local_kinds :
    (List.filter BugKind.isLocal
      [BugKind.typeError, .logicError, .scopeViolation, .trustViolation,
       .resourceLeak, .protocolViolation, .concurrencyHazard,
       .specificationDeviation]).length = 5 := by decide

-- ════════════════════════════════════════════════════════════════════
-- § 2  Coordinates
-- ════════════════════════════════════════════════════════════════════

/-- A sheaf-site coordinate uniquely identifies an AST node. -/
structure Coordinate where
  file     : String
  lineno   : Nat
  col      : Nat
  nodeType : String
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 3  Local Checks and the Semantic Site
-- ════════════════════════════════════════════════════════════════════

/-- The outcome of evaluating a proposition at a coordinate. -/
inductive CheckOutcome where
  | pass
  | fail
  | inconclusive
  deriving DecidableEq, Repr

/-- A local check: one proposition kind tested at one coordinate. -/
structure LocalCheck where
  coord   : Coordinate
  kind    : BugKind
  outcome : CheckOutcome
  deriving Repr

/-- A semantic site is a transparent alias for a list of local checks.
    `abbrev` ensures Lean unfolds it for type-class resolution. -/
abbrev SemanticSite := List LocalCheck

-- ════════════════════════════════════════════════════════════════════
-- § 4  Violations
-- ════════════════════════════════════════════════════════════════════

/-- A violation exists at coordinate `c` if some local check at `c` failed. -/
def hasViolation (site : SemanticSite) (c : Coordinate) : Prop :=
  ∃ lc ∈ site, lc.coord = c ∧ lc.outcome = .fail

/-- Adding a failing check at `c` immediately creates a violation there. -/
theorem violation_of_fail (lc : LocalCheck) (rest : SemanticSite)
    (h : lc.outcome = .fail) :
    hasViolation (lc :: rest) lc.coord :=
  ⟨lc, List.mem_cons_self _ _, rfl, h⟩

/-- A violation in the tail persists after prepending any head. -/
theorem violation_cons_tail (head : LocalCheck) (rest : SemanticSite)
    (c : Coordinate) (hv : hasViolation rest c) :
    hasViolation (head :: rest) c := by
  obtain ⟨lc, hmem, hcoord, hout⟩ := hv
  exact ⟨lc, List.mem_cons_of_mem head hmem, hcoord, hout⟩

-- ════════════════════════════════════════════════════════════════════
-- § 5  Bug Reports
-- ════════════════════════════════════════════════════════════════════

/-- A bug report: obstruction kind, localized coordinate, severity score. -/
structure BugReport where
  kind     : BugKind
  coord    : Coordinate
  severity : Nat
  deriving Repr

-- ════════════════════════════════════════════════════════════════════
-- § 6  The Canonical Detector
-- ════════════════════════════════════════════════════════════════════

/-- `extractReports` emits one `BugReport` per failing `LocalCheck`. -/
def extractReports : SemanticSite → List BugReport
  | []         => []
  | lc :: rest =>
    if lc.outcome == .fail
    then { kind := lc.kind, coord := lc.coord, severity := lc.kind.severity }
         :: extractReports rest
    else extractReports rest

@[simp] theorem extractReports_nil : extractReports [] = [] := rfl

-- ════════════════════════════════════════════════════════════════════
-- § 7  The Localization Guarantee
-- ════════════════════════════════════════════════════════════════════

/-- Every report in `extractReports site` comes from a failing check. -/
theorem extractReports_sound (site : SemanticSite) (r : BugReport)
    (h : r ∈ extractReports site) :
    ∃ lc ∈ site, lc.coord = r.coord ∧ lc.outcome = .fail := by
  induction site with
  | nil => simp at h
  | cons lc rest ih =>
    simp only [extractReports] at h
    cases hout : lc.outcome with
    | pass =>
      -- .pass ≠ .fail, so the detector skips this check.
      simp only [hout,
                 show (CheckOutcome.pass == CheckOutcome.fail) = false from rfl,
                 if_false] at h
      obtain ⟨lc', hmem', hcoord, hfail⟩ := ih h
      exact ⟨lc', List.mem_cons_of_mem lc hmem', hcoord, hfail⟩
    | fail =>
      -- .fail == .fail, so r is the head check or from the tail.
      simp only [hout,
                 show (CheckOutcome.fail == CheckOutcome.fail) = true from rfl,
                 if_true] at h
      rw [List.mem_cons] at h
      cases h with
      | inl heq =>
        -- r was created directly from lc.
        subst heq
        exact ⟨lc, List.mem_cons_self _ _, rfl, hout⟩
      | inr hmem =>
        obtain ⟨lc', hmem', hcoord, hfail⟩ := ih hmem
        exact ⟨lc', List.mem_cons_of_mem lc hmem', hcoord, hfail⟩
    | inconclusive =>
      -- .inconclusive ≠ .fail, so the detector skips this check.
      simp only [hout,
                 show (CheckOutcome.inconclusive == CheckOutcome.fail) = false from rfl,
                 if_false] at h
      obtain ⟨lc', hmem', hcoord, hfail⟩ := ih h
      exact ⟨lc', List.mem_cons_of_mem lc hmem', hcoord, hfail⟩

/-- **Localization Guarantee** (Theorem 7.1 of the paper).
    Every report produced by `extractReports` witnesses a genuine
    proposition violation at the reported coordinate.
    Equivalently: the canonical detector has **zero false positives**. -/
theorem localization_guarantee (site : SemanticSite) (r : BugReport)
    (hr : r ∈ extractReports site) :
    hasViolation site r.coord := by
  obtain ⟨lc, hmem, hcoord, hout⟩ := extractReports_sound site r hr
  exact ⟨lc, hmem, hcoord, hout⟩

/-- **Corollary:** a site with no failing checks produces no reports. -/
theorem no_reports_if_no_fail (site : SemanticSite)
    (hno : ∀ lc ∈ site, lc.outcome ≠ .fail) :
    extractReports site = [] := by
  induction site with
  | nil => rfl
  | cons lc rest ih =>
    simp only [extractReports]
    have hlc : lc.outcome ≠ .fail := hno lc (List.mem_cons_self _ _)
    have hrest : ∀ lc' ∈ rest, lc'.outcome ≠ .fail :=
      fun lc' hm => hno lc' (List.mem_cons_of_mem lc hm)
    cases hout : lc.outcome with
    | pass        => simp [hout, ih hrest]
    | fail        => exact absurd hout hlc
    | inconclusive => simp [hout, ih hrest]

-- ════════════════════════════════════════════════════════════════════
-- § 8  Trust Level and Severity
-- ════════════════════════════════════════════════════════════════════

/-- Minimum trust level required to admit a bug at severity `sev`.
    Clamps `sev` to [2, 5] (ORACLE_PROPOSED through VERIFIED_PROOF). -/
def minTrustForSeverity (sev : Nat) : Nat :=
  if sev ≤ 2 then 2
  else if sev ≥ 5 then 5
  else sev  -- sev ∈ {3, 4}

/-- Every BugKind's severity meets the minimum threshold at level 3. -/
theorem kind_severity_ge_oracle (k : BugKind) :
    minTrustForSeverity k.severity ≥ 3 := by
  cases k <;> decide

/-- Severity clamp is bounded above by 5. -/
theorem trust_le_five (sev : Nat) : minTrustForSeverity sev ≤ 5 := by
  unfold minTrustForSeverity
  by_cases h1 : sev ≤ 2
  · rw [if_pos h1]; omega
  · rw [if_neg h1]
    by_cases h2 : sev ≥ 5
    · rw [if_pos h2]; omega
    · rw [if_neg h2]; omega

/-- `minTrustForSeverity` is monotone: higher severity → higher trust. -/
theorem trust_monotone (s₁ s₂ : Nat) (h : s₁ ≤ s₂) :
    minTrustForSeverity s₁ ≤ minTrustForSeverity s₂ := by
  unfold minTrustForSeverity
  by_cases h1 : s₁ ≤ 2
  · rw [if_pos h1]
    by_cases h2 : s₂ ≤ 2
    · rw [if_pos h2]; omega
    · rw [if_neg h2]
      by_cases h3 : s₂ ≥ 5
      · rw [if_pos h3]; omega
      · rw [if_neg h3]; omega
  · rw [if_neg h1]
    by_cases h2 : s₁ ≥ 5
    · have h3 : s₂ ≥ 5 := Nat.le_trans h2 h
      rw [if_pos h2]
      by_cases h4 : s₂ ≤ 2
      · omega
      · rw [if_neg h4]
        by_cases h5 : s₂ ≥ 5
        · rw [if_pos h5]; omega
        · omega
    · rw [if_neg h2]
      by_cases h3 : s₂ ≤ 2
      · omega
      · rw [if_neg h3]
        by_cases h4 : s₂ ≥ 5
        · rw [if_pos h4]; omega
        · rw [if_neg h4]; omega

-- ════════════════════════════════════════════════════════════════════
-- § 9  Bug Classifier
-- ════════════════════════════════════════════════════════════════════

/-- The proposition kind corresponding to each BugKind. -/
inductive PropKind where
  | typeCorrect | logicConsistent | scopeValid | protocolConform
  | trustPreserved | resourceBounded | concurrencySafe | specCompliant
  deriving DecidableEq, Repr

/-- The BugClassifier bijection: BugKind → PropKind. -/
def BugKind.toPropKind : BugKind → PropKind
  | .typeError              => .typeCorrect
  | .logicError             => .logicConsistent
  | .scopeViolation         => .scopeValid
  | .protocolViolation      => .protocolConform
  | .trustViolation         => .trustPreserved
  | .resourceLeak           => .resourceBounded
  | .concurrencyHazard      => .concurrencySafe
  | .specificationDeviation => .specCompliant

/-- `toPropKind` is injective: distinct bug kinds map to distinct kinds. -/
theorem toPropKind_injective (a b : BugKind)
    (h : a.toPropKind = b.toPropKind) : a = b := by
  cases a <;> cases b <;> simp_all [BugKind.toPropKind]

-- ════════════════════════════════════════════════════════════════════
-- § 10  Bug Repository
-- ════════════════════════════════════════════════════════════════════

/-- An append-only store of BugReports. -/
structure BugRepository where
  reports : List BugReport

def BugRepository.empty : BugRepository := ⟨[]⟩

def BugRepository.add (repo : BugRepository) (r : BugReport) : BugRepository :=
  ⟨r :: repo.reports⟩

def BugRepository.count (repo : BugRepository) : Nat :=
  repo.reports.length

theorem count_empty : BugRepository.empty.count = 0 := rfl

theorem count_add (repo : BugRepository) (r : BugReport) :
    (repo.add r).count = repo.count + 1 := by
  simp [BugRepository.add, BugRepository.count, List.length_cons]

/-- An added report is immediately retrievable. -/
theorem add_mem (repo : BugRepository) (r : BugReport) :
    r ∈ (repo.add r).reports :=
  List.mem_cons_self _ _

/-- Adding never shrinks the repository. -/
theorem count_monotone (repo : BugRepository) (r : BugReport) :
    repo.count ≤ (repo.add r).count := by
  rw [count_add]; omega

-- ════════════════════════════════════════════════════════════════════
-- § 11  Problem Atlas
-- ════════════════════════════════════════════════════════════════════

/-- One entry in the ProblemAtlas: a named bug pattern. -/
structure AtlasEntry where
  name    : String
  bugKind : BugKind
  pattern : String
  deriving Repr

abbrev ProblemAtlas := List AtlasEntry

/-- Fast-path matcher: first atlas entry whose kind matches `lc`. -/
def atlasMatch (atlas : ProblemAtlas) (lc : LocalCheck) : Option AtlasEntry :=
  atlas.find? (fun e => e.bugKind == lc.kind)

/-- **Atlas Soundness.** A failing local check always provides a genuine
    violation at its coordinate, independently of any atlas match. -/
theorem atlas_match_sound (site : SemanticSite) (lc : LocalCheck)
    (hmem : lc ∈ site) (hout : lc.outcome = .fail) :
    hasViolation site lc.coord :=
  ⟨lc, hmem, rfl, hout⟩

-- ════════════════════════════════════════════════════════════════════
-- § 12  Summary
-- ════════════════════════════════════════════════════════════════════

/-- Master theorem packaging the principal results of Paper 28. -/
theorem bugDetectionSoundness :
    -- (a) Every BugKind has severity in [1, 5].
    (∀ k : BugKind, 1 ≤ k.severity ∧ k.severity ≤ 5) ∧
    -- (b) The canonical detector has zero false positives.
    (∀ (site : SemanticSite) (r : BugReport),
        r ∈ extractReports site → hasViolation site r.coord) ∧
    -- (c) An empty site yields no reports.
    extractReports [] = [] ∧
    -- (d) The empty repository has count 0.
    BugRepository.empty.count = 0 := by
  refine ⟨severity_valid, localization_guarantee, rfl, rfl⟩

end JudgmentGeometry.BugDetection
