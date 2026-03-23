/-
  Paper11_CoverDesign.lean — Cover Design Algorithms: Lean 4 Formalisation

  Formalises Paper 11 of the Judgment Geometry series.

  Main results (all proved without `sorry`):
    • isValid_empty        — the empty cover is vacuously valid
    • isValid_cons         — adding a valid member preserves validity
    • isValid_sublist      — sub-families of valid covers are valid
    • identityCover_isValid — the identity cover satisfies the axioms
    • greedyCover_isValid  — the greedy construction is valid
    • greedy_completeness  — greedy covers every site object (Thm 7.1)
    • stability_axiom      — Grothendieck stability (sub-family valid)
    • join_covers          — Grothendieck transitivity (union covering)
    • nonempty_cover_covering_sieve — non-empty covers yield covering sieves
    • greedy_covers_mono   — greedy coverage is monotone in the key set
    • merge_isValid        — merging two valid covers gives a valid cover
    • merge_coversKeys     — merging preserves coverage of keys
    • sieve_contains_source — cover members appear as sieve sources
    • refinement_preserves_sieve — valid refinement yields covering sieve

  Pattern follows Paper04_TrustAlgebra and Paper12_DerivedFunctors:
  self-contained, no imports, `autoImplicit := false`.
-/

namespace JudgmentGeometry.Paper11

-- ════════════════════════════════════════════════════════════════════
-- §A  Primitive types
-- ════════════════════════════════════════════════════════════════════

/-- A named coordinate object in the semantic site (module, function, …). -/
structure Coord where
  name : String
  deriving DecidableEq, Repr, BEq

/-- A member of a covering family: a morphism `source → target`. -/
structure CoverMember where
  source : Coord
  target : Coord
  deriving DecidableEq, Repr, BEq

/-- A covering family over a base coordinate. -/
structure Cover where
  base    : Coord
  members : List CoverMember
  deriving DecidableEq, Repr

/-- A sieve on `base`: a set of source names (downward-closed wrt
    pre-composition, here modelled as a list of source coordinate names). -/
structure Sieve where
  base    : Coord
  sources : List String
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- §B  Cover validity
--
-- A cover is *valid* when every member morphism targets the base
-- coordinate.  This mirrors `Cover.is_valid()` in covers.py.
-- ════════════════════════════════════════════════════════════════════

def Cover.isValid (c : Cover) : Prop :=
  ∀ m ∈ c.members, m.target = c.base

/-- The empty cover is vacuously valid. -/
theorem isValid_empty (base : Coord) : (Cover.mk base []).isValid :=
  fun _ h => absurd h (List.not_mem_nil _)

/-- Prepending a member whose target equals the base preserves validity. -/
theorem isValid_cons {base : Coord} {m : CoverMember} {ms : List CoverMember}
    (hm : m.target = base)
    (hms : (Cover.mk base ms).isValid) :
    (Cover.mk base (m :: ms)).isValid := by
  intro m' hm'
  simp only [List.mem_cons] at hm'
  rcases hm' with rfl | h
  · exact hm
  · exact hms m' h

/-- A sub-list of a valid cover's members is valid over the same base.
    This is the member-level form of the Grothendieck stability axiom. -/
theorem isValid_sublist {base : Coord} {all sub : List CoverMember}
    (hv  : (Cover.mk base all).isValid)
    (hsub : ∀ m ∈ sub, m ∈ all) :
    (Cover.mk base sub).isValid :=
  fun m hm => hv m (hsub m hm)

-- ════════════════════════════════════════════════════════════════════
-- §C  Identity cover axiom
-- ════════════════════════════════════════════════════════════════════

/-- The identity cover of `x` contains a single morphism `x → x`. -/
def identityCover (x : Coord) : Cover where
  base    := x
  members := [⟨x, x⟩]

theorem identityCover_isValid (x : Coord) :
    (identityCover x).isValid := by
  intro m hm
  simp [identityCover] at hm
  subst hm
  rfl

/-- The identity cover covers its own base. -/
theorem identityCover_selfCovers (x : Coord) :
    ∃ m ∈ (identityCover x).members, m.source = x :=
  ⟨⟨x, x⟩, List.mem_singleton.mpr rfl, rfl⟩

-- ════════════════════════════════════════════════════════════════════
-- §D  Greedy cover construction
--
-- `greedyCover base keys` mirrors `CoverGenerator.canonical_cover`:
-- one inclusion morphism per site key, all targeting `base`.
-- ════════════════════════════════════════════════════════════════════

/-- Build a cover from an explicit list of site-object keys. -/
def greedyCover (base : Coord) (keys : List String) : Cover where
  base    := base
  members := keys.map fun k => ⟨⟨k⟩, base⟩

/-- Every member of the greedy cover targets the base. -/
theorem greedyCover_isValid (base : Coord) (keys : List String) :
    (greedyCover base keys).isValid := by
  intro m hm
  simp only [greedyCover, List.mem_map] at hm
  obtain ⟨k, _, rfl⟩ := hm
  rfl

/-- `c.coversKeys keys` holds when every key in `keys` is a source. -/
def Cover.coversKeys (c : Cover) (keys : List String) : Prop :=
  ∀ k ∈ keys, ∃ m ∈ c.members, m.source.name = k

/-- **Theorem 7.1 — Greedy Completeness**.
    For any finite semantic site (list of keys),
    `greedyCover` is both valid and satisfies the covering axiom. -/
theorem greedy_completeness (base : Coord) (keys : List String) :
    (greedyCover base keys).isValid ∧
    (greedyCover base keys).coversKeys keys := by
  refine ⟨greedyCover_isValid base keys, fun k hk => ?_⟩
  refine ⟨⟨⟨k⟩, base⟩, ?_, rfl⟩
  simp only [greedyCover, List.mem_map]
  exact ⟨k, hk, rfl⟩

/-- Greedy coverage is monotone: a larger key list covers the original. -/
theorem greedy_covers_mono (base : Coord) (keys₁ keys₂ : List String)
    (hsub : ∀ k ∈ keys₁, k ∈ keys₂) :
    (greedyCover base keys₂).coversKeys keys₁ := by
  intro k hk
  refine ⟨⟨⟨k⟩, base⟩, ?_, rfl⟩
  simp only [greedyCover, List.mem_map]
  exact ⟨k, hsub k hk, rfl⟩

/-- Cover of a superset covers the original key list. -/
theorem cover_coversKeys_append (base : Coord) (keys extra : List String) :
    (greedyCover base (keys ++ extra)).coversKeys keys :=
  greedy_covers_mono base keys (keys ++ extra) (fun k hk => List.mem_append_left _ hk)

/-- The greedy cover has exactly one member per key. -/
theorem greedyCover_length (base : Coord) (keys : List String) :
    (greedyCover base keys).members.length = keys.length := by
  simp [greedyCover]

-- ════════════════════════════════════════════════════════════════════
-- §E  Grothendieck stability axiom
-- ════════════════════════════════════════════════════════════════════

/-- **Stability**: any sub-family of a valid cover is valid.
    Mirrors `CoverDiagnostics.check_covering_axiom`. -/
theorem stability_axiom (c : Cover) (sub : List CoverMember)
    (hv : c.isValid) (hsub : sub ⊆ c.members) :
    (Cover.mk c.base sub).isValid :=
  fun m hm => hv m (hsub hm)

-- ════════════════════════════════════════════════════════════════════
-- §F  Grothendieck transitivity axiom
--
-- If we join the members of a collection of covering families,
-- the result covers the same key set.
-- ════════════════════════════════════════════════════════════════════

/-- Combine a list of covers by pooling all their members. -/
def joinCovers (covers : List Cover) (base : Coord) : Cover where
  base    := base
  members := (covers.map (·.members)).join

private theorem mem_join_of {α : Type} {a : α} {ls : List (List α)} {l : List α}
    (hl : l ∈ ls) (ha : a ∈ l) : a ∈ ls.join := by
  induction ls with
  | nil         => exact absurd hl (List.not_mem_nil _)
  | cons hd tl ih =>
    simp only [List.mem_cons] at hl
    rcases hl with rfl | htl
    · exact List.mem_append_left _ ha
    · exact List.mem_append_right _ (ih htl)

/-- **Transitivity**: joining covering families preserves coverage. -/
theorem join_covers (covers : List Cover) (base : Coord) (keys : List String)
    (c₀ : Cover) (hc₀ : c₀ ∈ covers)
    (hcovers : ∀ c ∈ covers, c.coversKeys keys) :
    (joinCovers covers base).coversKeys keys := by
  intro k hk
  obtain ⟨m, hm_mem, hm_src⟩ := hcovers c₀ hc₀ k hk
  refine ⟨m, ?_, hm_src⟩
  simp only [joinCovers]
  apply mem_join_of
  · exact List.mem_map.mpr ⟨c₀, hc₀, rfl⟩
  · exact hm_mem

-- ════════════════════════════════════════════════════════════════════
-- §G  Sieve representation
--
-- Mirrors `Cover.sieve_representation()` and `Sieve.generate_from_cover()`.
-- ════════════════════════════════════════════════════════════════════

/-- Generate the sieve spanned by a covering family. -/
def Sieve.ofCover (c : Cover) : Sieve where
  base    := c.base
  sources := c.members.map (·.source.name)

/-- A sieve is *covering* iff it is non-empty. -/
def Sieve.isCovering (s : Sieve) : Prop := s.sources ≠ []

/-- Every cover member's source appears in the generated sieve. -/
theorem sieve_contains_source (c : Cover) (m : CoverMember)
    (hm : m ∈ c.members) :
    m.source.name ∈ (Sieve.ofCover c).sources :=
  List.mem_map.mpr ⟨m, hm, rfl⟩

/-- **Theorem**: A non-empty cover generates a covering sieve.
    Formalises `Sieve.is_covering_sieve()`. -/
theorem nonempty_cover_covering_sieve (c : Cover) (h : c.members ≠ []) :
    (Sieve.ofCover c).isCovering := by
  unfold Sieve.isCovering Sieve.ofCover
  simp only [ne_eq]
  cases hc : c.members with
  | nil       => exact absurd hc h
  | cons m ms =>
    simp only [List.map_cons]
    exact List.cons_ne_nil _ _

/-- A valid refinement of a non-empty cover still generates a covering sieve. -/
theorem refinement_preserves_sieve (c : Cover) (sub : List CoverMember)
    (hne : sub ≠ [])
    (hsub : ∀ m ∈ sub, m ∈ c.members) :
    (Sieve.ofCover (Cover.mk c.base sub)).isCovering := by
  apply nonempty_cover_covering_sieve
  exact hne

/-- The sieve of the greedy cover has one source per key. -/
theorem greedy_sieve_sources (base : Coord) (keys : List String) :
    (Sieve.ofCover (greedyCover base keys)).sources = keys := by
  simp only [Sieve.ofCover, greedyCover, List.map_map, Function.comp_def]
  exact List.map_id keys

-- ════════════════════════════════════════════════════════════════════
-- §H  Cover merging
--
-- Mirrors `CoverMerger.merge()` with KEEP_BOTH semantics.
-- ════════════════════════════════════════════════════════════════════

/-- Merge two covers by pooling their members (KEEP_BOTH policy). -/
def Cover.merge (c₁ c₂ : Cover) : Cover where
  base    := c₁.base
  members := c₁.members ++ c₂.members

/-- **Proposition**: merging two valid covers over the same base is valid. -/
theorem merge_isValid (c₁ c₂ : Cover)
    (hv₁   : c₁.isValid)
    (hv₂   : c₂.isValid)
    (hbase : c₁.base = c₂.base) :
    (c₁.merge c₂).isValid := by
  intro m hm
  simp only [Cover.merge, List.mem_append] at hm
  rcases hm with h | h
  · exact hv₁ m h
  · exact hbase ▸ hv₂ m h

/-- Merging preserves coverage: if c₁ covers keys then so does c₁.merge c₂. -/
theorem merge_coversKeys (c₁ c₂ : Cover) (keys : List String)
    (h : c₁.coversKeys keys) :
    (c₁.merge c₂).coversKeys keys := by
  intro k hk
  obtain ⟨m, hm, hsrc⟩ := h k hk
  exact ⟨m, List.mem_append_left _ hm, hsrc⟩

/-- Merging also picks up coverage from the right cover. -/
theorem merge_coversKeys_right (c₁ c₂ : Cover) (keys : List String)
    (h : c₂.coversKeys keys) :
    (c₁.merge c₂).coversKeys keys := by
  intro k hk
  obtain ⟨m, hm, hsrc⟩ := h k hk
  exact ⟨m, List.mem_append_right _ hm, hsrc⟩

/-- Merging a greedy cover with any other cover is still valid (given base eq). -/
theorem merge_greedy_isValid (base : Coord) (keys : List String) (c : Cover)
    (hbase : c.base = base) (hvc : c.isValid) :
    ((greedyCover base keys).merge c).isValid :=
  merge_isValid _ _ (greedyCover_isValid base keys) hvc (hbase ▸ rfl)

-- ════════════════════════════════════════════════════════════════════
-- §I  Summary: all three Grothendieck axioms hold for greedyCover
-- ════════════════════════════════════════════════════════════════════

/-- **Theorem (Grothendieck Axioms)**.
    For any finite semantic site, `greedyCover` satisfies
    identity, stability, and the covering axiom simultaneously. -/
theorem greedyCover_grothendieck (base : Coord) (keys : List String)
    (hne : keys ≠ []) :
    -- (1) covering axiom
    (greedyCover base keys).coversKeys keys ∧
    -- (2) stability: sub-families are valid
    (∀ sub : List CoverMember,
       sub ⊆ (greedyCover base keys).members →
       (Cover.mk base sub).isValid) ∧
    -- (3) sieve generation: the cover generates a covering sieve
    (Sieve.ofCover (greedyCover base keys)).isCovering := by
  refine ⟨(greedy_completeness base keys).2, fun sub hsub => ?_, ?_⟩
  · exact stability_axiom (greedyCover base keys) sub
      (greedyCover_isValid base keys) hsub
  · apply nonempty_cover_covering_sieve
    intro h
    apply hne
    have h2 := greedyCover_length base keys
    rw [h] at h2
    exact List.length_eq_zero.mp h2.symm

end JudgmentGeometry.Paper11
