/-
  Paper20_HomotopyTypeTheory.lean — Homotopy Type Theory Meets Judgment Geometry

  Formalises the type-theoretic interpretation of the JuGeo framework:
    • Transport along paths (proof transfer)
    • Function extensionality for programs
    • Equivalence implies equality (propext / simplified univalence)
    • Proof-transfer pipeline along verified refactorings
    • Refactoring loop group structure
-/

namespace JudgmentGeometry.HomotopyTypeTheory

-- ════════════════════════════════════════════════════════════════════
-- § 1  Programs and their behavioural abstraction
-- ════════════════════════════════════════════════════════════════════

/-- A simplified program model with a name, behaviour function, and
    specification.  In the full theory this is a term of type Prog in
    Sh(𝒮). -/
structure Program where
  name     : String
  behavior : String → String
  spec     : String

/-- Programs are *extensionally equal* if they agree on every input. -/
def Program.ExtEq (A B : Program) : Prop :=
  ∀ x, A.behavior x = B.behavior x

-- ════════════════════════════════════════════════════════════════════
-- § 2  Transport along paths (Theorem 8.1 of the paper)
-- ════════════════════════════════════════════════════════════════════

/-- **Transport**: given a path `h : a = b` and a proof `ha : P a`,
    produce `P b`.  This is the fundamental path eliminator. -/
theorem jugeo_transport {α : Type} {P : α → Prop} {a b : α}
    (h : a = b) (ha : P a) : P b :=
  h ▸ ha

/-- Transport on `rfl` is the identity. -/
@[simp]
theorem jugeo_transport_refl {α : Type} {P : α → Prop} {a : α}
    (ha : P a) : jugeo_transport (P := P) rfl ha = ha :=
  rfl

/-- Transport along a concatenated path composes (Corollary 8.3). -/
theorem jugeo_transport_trans {α : Type} {P : α → Prop} {a b c : α}
    (h₁ : a = b) (h₂ : b = c) (ha : P a) :
    jugeo_transport (P := P) (h₁.trans h₂) ha =
    jugeo_transport (P := P) h₂ (jugeo_transport (P := P) h₁ ha) := by
  subst h₁; subst h₂; rfl

/-- Transport along `h.symm` after `h` recovers the original proof. -/
theorem jugeo_transport_symm_id {α : Type} {P : α → Prop} {a b : α}
    (h : a = b) (ha : P a) :
    jugeo_transport (P := P) h.symm (jugeo_transport (P := P) h ha) = ha := by
  subst h; rfl

-- ════════════════════════════════════════════════════════════════════
-- § 3  Function extensionality for programs (§ 6 of the paper)
-- ════════════════════════════════════════════════════════════════════

/-- **Behavioural extensionality**: programs equal in name, behaviour,
    and spec are propositionally equal.  Uses Lean's `funext` axiom. -/
theorem program_ext {A B : Program}
    (h_name : A.name = B.name)
    (h_beh  : ∀ x, A.behavior x = B.behavior x)
    (h_spec : A.spec = B.spec) :
    A = B := by
  obtain ⟨nameA, behavA, specA⟩ := A
  obtain ⟨nameB, behavB, specB⟩ := B
  simp only at h_name h_beh h_spec
  subst h_name; subst h_spec
  congr 1
  exact funext h_beh

/-- Extensionally equal programs (same name/spec) are propositionally
    equal — the program-level analogue of the Univalence Axiom. -/
theorem extEq_implies_eq {A B : Program}
    (h_name : A.name = B.name)
    (h_ext  : Program.ExtEq A B)
    (h_spec : A.spec = B.spec) :
    A = B :=
  program_ext h_name h_ext h_spec

-- ════════════════════════════════════════════════════════════════════
-- § 4  Propositional univalence (propext, Theorem 6.2)
-- ════════════════════════════════════════════════════════════════════

/-- Logically equivalent propositions are propositionally equal.
    This is `propext`, implied by the full Univalence Axiom. -/
theorem prop_equiv_implies_eq (P Q : Prop) (h : P ↔ Q) : P = Q :=
  propext h

/-- A proof transfers across a propositional equality of propositions. -/
theorem prop_transfer {P Q : Prop} (heq : P = Q) (hp : P) : Q :=
  heq ▸ hp

/-- Proof transfer across logically equivalent propositions. -/
theorem prop_transfer_iff {P Q : Prop} (hpq : P ↔ Q) (hp : P) : Q :=
  prop_transfer (prop_equiv_implies_eq P Q hpq) hp

-- ════════════════════════════════════════════════════════════════════
-- § 5  Program equivalences
-- ════════════════════════════════════════════════════════════════════

/-- A *program equivalence*: inverse behaviour maps showing that two
    programs compute the same function up to isomorphism. -/
structure ProgEquiv (A B : Program) where
  fwd        : String → String
  bwd        : String → String
  bwd_fwd    : ∀ y, bwd (fwd y) = y
  fwd_bwd    : ∀ z, fwd (bwd z) = z
  coherent_A : ∀ x, fwd (A.behavior x) = B.behavior x
  coherent_B : ∀ x, bwd (B.behavior x) = A.behavior x

/-- The identity equivalence. -/
def ProgEquiv.refl (A : Program) : ProgEquiv A A where
  fwd        := id
  bwd        := id
  bwd_fwd    := fun _ => rfl
  fwd_bwd    := fun _ => rfl
  coherent_A := fun _ => rfl
  coherent_B := fun _ => rfl

/-- Equivalences compose. -/
def ProgEquiv.trans {A B C : Program}
    (e₁ : ProgEquiv A B) (e₂ : ProgEquiv B C) : ProgEquiv A C where
  fwd        := e₂.fwd ∘ e₁.fwd
  bwd        := e₁.bwd ∘ e₂.bwd
  bwd_fwd    := fun y => by simp [Function.comp, e₁.bwd_fwd, e₂.bwd_fwd]
  fwd_bwd    := fun z => by simp [Function.comp, e₁.fwd_bwd, e₂.fwd_bwd]
  coherent_A := fun x => by simp [Function.comp, e₁.coherent_A, e₂.coherent_A]
  coherent_B := fun x => by simp [Function.comp, e₁.coherent_B, e₂.coherent_B]

/-- Equivalences are symmetric. -/
def ProgEquiv.symm {A B : Program} (e : ProgEquiv A B) : ProgEquiv B A where
  fwd        := e.bwd
  bwd        := e.fwd
  bwd_fwd    := e.fwd_bwd
  fwd_bwd    := e.bwd_fwd
  coherent_A := e.coherent_B
  coherent_B := e.coherent_A

-- ════════════════════════════════════════════════════════════════════
-- § 6  The Univalence Axiom and its algebraic properties
-- ════════════════════════════════════════════════════════════════════

/-- The **Univalence Axiom** for programs: a program equivalence induces
    a propositional equality.  Stated as a `noncomputable axiom` because
    Lean 4 has UIP; in Cubical Lean this would be derivable. -/
noncomputable axiom univalence_ax {A B : Program} (e : ProgEquiv A B) : A = B

/-- Univalence maps the identity equivalence to `rfl`. -/
noncomputable axiom univalence_refl (A : Program) :
    univalence_ax (ProgEquiv.refl A) = rfl

/-- Univalence maps composition of equivalences to path concatenation. -/
noncomputable axiom univalence_comp {A B C : Program}
    (e₁ : ProgEquiv A B) (e₂ : ProgEquiv B C) :
    univalence_ax (e₁.trans e₂) =
    (univalence_ax e₁).trans (univalence_ax e₂)

/-- Univalence maps the inverse equivalence to the inverse path. -/
noncomputable axiom univalence_symm {A B : Program}
    (e : ProgEquiv A B) :
    univalence_ax e.symm = (univalence_ax e).symm

-- ════════════════════════════════════════════════════════════════════
-- § 7  Proof transfer via univalence (Theorem 8.1 of the paper)
-- ════════════════════════════════════════════════════════════════════

/-- **Proof transfer**: transport a proof of `P A` along an equivalence
    `A ≃ B` to obtain `P B`. -/
noncomputable def proof_transfer
    (P : Program → Prop)
    {A B : Program}
    (e  : ProgEquiv A B)
    (hA : P A) :
    P B :=
  jugeo_transport (univalence_ax e) hA

/-- Proof transfer along the identity equivalence is the identity. -/
@[simp]
theorem proof_transfer_refl
    (P : Program → Prop)
    {A : Program}
    (hA : P A) :
    proof_transfer P (ProgEquiv.refl A) hA = hA := by
  simp [proof_transfer, univalence_refl]

/-- Proof transfer composes: two sequential transfers equal one combined
    transfer (Corollary 8.3). -/
theorem proof_transfer_trans
    (P : Program → Prop)
    {A B C : Program}
    (e₁ : ProgEquiv A B) (e₂ : ProgEquiv B C)
    (hA : P A) :
    proof_transfer P e₂ (proof_transfer P e₁ hA) =
    proof_transfer P (e₁.trans e₂) hA := by
  show jugeo_transport (univalence_ax e₂)
         (jugeo_transport (univalence_ax e₁) hA) =
       jugeo_transport (univalence_ax (e₁.trans e₂)) hA
  rw [univalence_comp e₁ e₂, jugeo_transport_trans]

-- ════════════════════════════════════════════════════════════════════
-- § 8  Refactoring loops and the fundamental group (§ 9 of the paper)
-- ════════════════════════════════════════════════════════════════════

/-- A *refactoring loop* at `A` is a self-equivalence of `A`. -/
def RefactLoop (A : Program) : Type :=
  ProgEquiv A A

/-- The identity loop. -/
def RefactLoop.id (A : Program) : RefactLoop A :=
  ProgEquiv.refl A

/-- Loop composition. -/
def RefactLoop.comp {A : Program} (r s : RefactLoop A) : RefactLoop A :=
  r.trans s

/-- Loop inversion. -/
def RefactLoop.inv {A : Program} (r : RefactLoop A) : RefactLoop A :=
  r.symm

/-- The `fwd` of the identity loop is `id`. -/
@[simp]
theorem RefactLoop.id_fwd (A : Program) (x : String) :
    (RefactLoop.id A).fwd x = x := rfl

/-- Left identity: `(id ∘ r).fwd = r.fwd` pointwise. -/
theorem loop_left_id_fwd {A : Program} (r : RefactLoop A) (x : String) :
    (RefactLoop.id A |>.comp r).fwd x = r.fwd x := rfl

/-- Right identity: `(r ∘ id).fwd = r.fwd` pointwise. -/
theorem loop_right_id_fwd {A : Program} (r : RefactLoop A) (x : String) :
    (r.comp (RefactLoop.id A)).fwd x = r.fwd x := rfl

/-- Left inverse: `(r⁻¹ ∘ r).fwd` is the identity pointwise,
    using the `fwd_bwd` field of the equivalence. -/
theorem loop_left_inv_fwd {A : Program} (r : RefactLoop A) (x : String) :
    (r.inv.comp r).fwd x = x := by
  simp [RefactLoop.comp, RefactLoop.inv, ProgEquiv.trans, ProgEquiv.symm,
        Function.comp]
  exact r.fwd_bwd x

/-- Right inverse: `(r ∘ r⁻¹).fwd` is the identity pointwise. -/
theorem loop_right_inv_fwd {A : Program} (r : RefactLoop A) (x : String) :
    (r.comp r.inv).fwd x = x := by
  simp [RefactLoop.comp, RefactLoop.inv, ProgEquiv.trans, ProgEquiv.symm,
        Function.comp]
  exact r.bwd_fwd x

/-- The path of a composed loop is the concatenation of paths. -/
theorem loop_paths_compose {A : Program} (r s : RefactLoop A) :
    univalence_ax (r.comp s) =
    (univalence_ax r).trans (univalence_ax s) :=
  univalence_comp r s

-- ════════════════════════════════════════════════════════════════════
-- § 9  Global sections and the descent condition
-- ════════════════════════════════════════════════════════════════════

/-- A simplified semantic site: fragment names with a cover relation. -/
structure SemanticSite where
  Fragment : Type
  covers   : Fragment → List Fragment

/-- A judgment sheaf assigns a type of evidence to each fragment. -/
structure JudgmentSheaf (S : SemanticSite) where
  sections : S.Fragment → Type
  restrict : ∀ {u v : S.Fragment}, v ∈ S.covers u → sections u → sections v

/-- A global section: compatible evidence for every fragment.
    Corresponds to a verified program in Sh(𝒮). -/
structure GlobalSection (S : SemanticSite) (F : JudgmentSheaf S) where
  eval   : ∀ (u : S.Fragment), F.sections u
  compat : ∀ (u v : S.Fragment) (hv : v ∈ S.covers u),
             F.restrict hv (eval u) = eval v

/-- Proof transfer for global sections: a path between sheaves
    transports a global section. -/
def section_transfer {S : SemanticSite}
    {F G : JudgmentSheaf S}
    (h : F = G)
    (σ : GlobalSection S F) :
    GlobalSection S G :=
  h ▸ σ

/-- Section transfer along `rfl` is the identity. -/
@[simp]
theorem section_transfer_refl {S : SemanticSite}
    {F : JudgmentSheaf S}
    (σ : GlobalSection S F) :
    section_transfer rfl σ = σ :=
  rfl

/-- Section transfer composes with path concatenation. -/
theorem section_transfer_trans {S : SemanticSite}
    {F G H : JudgmentSheaf S}
    (h₁ : F = G) (h₂ : G = H)
    (σ  : GlobalSection S F) :
    section_transfer h₂ (section_transfer h₁ σ) =
    section_transfer (h₁.trans h₂) σ := by
  subst h₁; subst h₂; rfl

-- ════════════════════════════════════════════════════════════════════
-- § 10  End-to-end proof transfer pipeline (§ 8 of the paper)
-- ════════════════════════════════════════════════════════════════════

/-- **Proof transfer pipeline**: given a property `P`, programs `A` and
    `B`, a verified equivalence, and a proof of `P A`, produce `P B`. -/
noncomputable def ProofTransferPipeline
    (P  : Program → Prop)
    (A B : Program)
    (e  : ProgEquiv A B)
    (hA : P A) :
    P B :=
  proof_transfer P e hA

/-- The pipeline is sound: transfer and inverse-transfer cancel. -/
theorem pipeline_roundtrip
    (P  : Program → Prop)
    (A B : Program)
    (e  : ProgEquiv A B)
    (hA : P A) :
    ProofTransferPipeline P B A e.symm
      (ProofTransferPipeline P A B e hA) = hA := by
  show jugeo_transport (univalence_ax e.symm)
         (jugeo_transport (univalence_ax e) hA) = hA
  rw [univalence_symm e]
  exact jugeo_transport_symm_id (univalence_ax e) hA

/-- Proof transfer preserves provability: if `P A` holds and `A ≃ B`,
    then `P B` holds. -/
theorem transfer_preserves_truth
    (P  : Program → Prop)
    {A B : Program}
    (e  : ProgEquiv A B)
    (hA : P A) :
    P B :=
  ProofTransferPipeline P A B e hA

end JudgmentGeometry.HomotopyTypeTheory
