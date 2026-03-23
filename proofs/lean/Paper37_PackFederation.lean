/-
  Paper37_PackFederation.lean — Pack Federation: Distributed Verification
  via Bridge Discovery

  Formalises the core results from Paper 37:
    • Trust tier ordering and conservative join
    • Pack model and local verification predicate
    • Bridge model, validity, and applicability
    • Evidence combining via conservative join
    • Pack authority validity
    • Bridge reachability
    • Federated system and global consistency
    • Theorem 8.1 — Federation Consistency
    • Corollary 8.2 — Evidence Combining Soundness
    • Lemma 8.3 — Trust Dominance
    • Theorem 8.4 — Pack Authority Soundness
    • Theorem 8.5 — Two-Hop Bridge Composition
    • Corollary 8.6 — All Packs Verified in Consistent Federation

  No `sorry` axioms are used.
-/

namespace JudgmentGeometry.PackFederation

-- ════════════════════════════════════════════════════════════════════
-- § 2  Trust Tiers
-- ════════════════════════════════════════════════════════════════════

/-- Trust tiers, ordered from lowest (Contradicted) to highest (Proof).
    Matches the `TrustTier` type used throughout the JuGeo framework. -/
inductive TrustTier : Type where
  | contradicted : TrustTier
  | unverified   : TrustTier
  | copilot      : TrustTier
  | oracle       : TrustTier
  | runtime      : TrustTier
  | solver       : TrustTier
  | proof        : TrustTier
  deriving DecidableEq, Repr

/-- Numeric rank: used to reduce ordering proofs to Nat arithmetic. -/
def TrustTier.rank : TrustTier → Nat
  | .contradicted => 0
  | .unverified   => 1
  | .copilot      => 2
  | .oracle       => 3
  | .runtime      => 4
  | .solver       => 5
  | .proof        => 6

/-- The trust ordering: t₁ ≤ t₂ iff t₁.rank ≤ t₂.rank. -/
def TrustTier.le (t₁ t₂ : TrustTier) : Prop :=
  t₁.rank ≤ t₂.rank

/-- Conservative (minimum-trust) join: the result is no stronger than
    either argument.  Used by EvidenceCombiner. -/
def TrustTier.join (t₁ t₂ : TrustTier) : TrustTier :=
  if t₁.rank ≤ t₂.rank then t₁ else t₂

/-- The join is dominated by its left argument (it computes the min). -/
theorem join_le_left (t₁ t₂ : TrustTier) :
    (TrustTier.join t₁ t₂).rank ≤ t₁.rank := by
  unfold TrustTier.join; split <;> omega

/-- The join is dominated by its right argument. -/
theorem join_le_right (t₁ t₂ : TrustTier) :
    (TrustTier.join t₁ t₂).rank ≤ t₂.rank := by
  unfold TrustTier.join; split <;> omega

/-- The join of two tiers with rank ≥ k also has rank ≥ k. -/
theorem join_ge_of_both_ge (t₁ t₂ : TrustTier) (k : Nat)
    (h₁ : t₁.rank ≥ k) (h₂ : t₂.rank ≥ k) :
    (TrustTier.join t₁ t₂).rank ≥ k := by
  unfold TrustTier.join; split <;> omega

/-- Contradicted is the bottom element: it is dominated by every tier. -/
theorem contradicted_minimal (t : TrustTier) :
    TrustTier.contradicted.rank ≤ t.rank := by
  cases t <;> decide

/-- Proof is the top element: every tier is dominated by it. -/
theorem proof_dominates (t : TrustTier) :
    t.rank ≤ TrustTier.proof.rank := by
  cases t <;> decide

-- ════════════════════════════════════════════════════════════════════
-- § 2  Pack Lifecycle Status
-- ════════════════════════════════════════════════════════════════════

/-- Lifecycle status of a pack (mirrors Python's `PackStatus` enum). -/
inductive PackStatus : Type where
  | pending    : PackStatus
  | active     : PackStatus
  | verified   : PackStatus
  | deprecated : PackStatus
  deriving DecidableEq, Repr

/-- Status of the federated system as a whole. -/
inductive FederationStatus : Type where
  | pending  : FederationStatus
  | active   : FederationStatus
  | degraded : FederationStatus
  | failed   : FederationStatus
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 7  Conflict Resolution Types
-- ════════════════════════════════════════════════════════════════════

/-- Four kinds of inter-pack conflict detected during federation. -/
inductive ConflictKind : Type where
  | specificationMismatch : ConflictKind
  | trustLevelConflict    : ConflictKind
  | authorityConflict     : ConflictKind
  | circularDependency    : ConflictKind
  deriving DecidableEq, Repr

/-- Resolution strategies for inter-pack conflicts. -/
inductive ResolutionStrategy : Type where
  | preferHigherTrust : ResolutionStrategy
  | preferLocal       : ResolutionStrategy
  | preferRemote      : ResolutionStrategy
  | requireManual     : ResolutionStrategy
  deriving DecidableEq, Repr

-- ════════════════════════════════════════════════════════════════════
-- § 2  Pack Model
-- ════════════════════════════════════════════════════════════════════

/-- A pack: an independently-verified code unit with its own semantic site,
    a trust annotation, and a lifecycle status. -/
structure Pack : Type where
  id         : Nat
  name       : String
  status     : PackStatus
  trustLevel : TrustTier
  deriving Repr

/-- **Local verification**: a pack is locally verified when its lifecycle
    status is `verified` and its trust level is at least `solver` (rank ≥ 5).
    Mirrors `Definition 2.3` in the paper. -/
def LocallyVerified (p : Pack) : Prop :=
  p.status = PackStatus.verified ∧ p.trustLevel.rank ≥ 5

/-- A locally verified pack has `verified` status. -/
theorem locally_verified_status (p : Pack) (h : LocallyVerified p) :
    p.status = PackStatus.verified :=
  h.1

/-- A locally verified pack has trust rank ≥ 5 (at solver level or above). -/
theorem locally_verified_trust_rank (p : Pack) (h : LocallyVerified p) :
    p.trustLevel.rank ≥ 5 :=
  h.2

-- ════════════════════════════════════════════════════════════════════
-- § 3–5  Bridge Model
-- ════════════════════════════════════════════════════════════════════

/-- A bridge relates the specifications of two packs.
    `valid` is set by `BridgeVerifier` after all three validity checks pass.
    `bidirectional` enables reverse traversal.
    `minTrust` is the minimum trust tier required to use this bridge. -/
structure Bridge : Type where
  id            : Nat
  sourceId      : Nat
  targetId      : Nat
  valid         : Bool
  bidirectional : Bool
  minTrust      : TrustTier
  deriving Repr

/-- **Bridge validity** (`Definition 5.1` in the paper):
    the verified flag is set *and* the bridge connects distinct packs. -/
def BridgeValid (b : Bridge) : Prop :=
  b.valid = true ∧ b.sourceId ≠ b.targetId

/-- A bridge is **applicable** at trust level `t` when `t` dominates the
    bridge's minimum trust requirement. -/
def BridgeApplicable (b : Bridge) (t : TrustTier) : Prop :=
  b.minTrust.rank ≤ t.rank

/-- A valid bridge always connects two distinct packs. -/
theorem bridge_valid_distinct (b : Bridge) (h : BridgeValid b) :
    b.sourceId ≠ b.targetId :=
  h.2

-- ════════════════════════════════════════════════════════════════════
-- § 6  Evidence Combining
-- ════════════════════════════════════════════════════════════════════

/-- **Combined trust** of two packs: the conservative (minimum) join of their
    trust levels.  Mirrors `EvidenceCombiner.combine_packs` in Python. -/
def combineTrust (p₁ p₂ : Pack) : TrustTier :=
  TrustTier.join p₁.trustLevel p₂.trustLevel

/-- **Lemma 6.1** — Evidence combining preserves the verification threshold:
    if both packs are locally verified, their combined trust rank is ≥ 5. -/
theorem combine_preserves_verified (p₁ p₂ : Pack)
    (h₁ : LocallyVerified p₁) (h₂ : LocallyVerified p₂) :
    (combineTrust p₁ p₂).rank ≥ 5 := by
  unfold combineTrust
  exact join_ge_of_both_ge _ _ 5 h₁.2 h₂.2

-- ════════════════════════════════════════════════════════════════════
-- § 2  Pack Authority
-- ════════════════════════════════════════════════════════════════════

/-- A pack authority: a `PackAuthorityRegistry` entry with a set of
    managed pack identifiers. -/
structure PackAuthority : Type where
  id           : Nat
  name         : String
  managedPacks : List Nat
  deriving Repr

/-- An authority is **valid** w.r.t. a pack list when every managed id
    names an actual pack in that list. -/
def AuthorityValid (a : PackAuthority) (packs : List Pack) : Prop :=
  ∀ pid ∈ a.managedPacks, ∃ p ∈ packs, p.id = pid

-- ════════════════════════════════════════════════════════════════════
-- § 3  Bridge Reachability
-- ════════════════════════════════════════════════════════════════════

/-- **Direct reachability**: `src` directly reaches `tgt` if there is a valid
    bridge whose source/target match (or, for bidirectional bridges, reversed). -/
def DirectlyReachable (bridges : List Bridge) (src tgt : Nat) : Prop :=
  ∃ b ∈ bridges, BridgeValid b ∧
    ((b.sourceId = src ∧ b.targetId = tgt) ∨
     (b.bidirectional = true ∧ b.targetId = src ∧ b.sourceId = tgt))

/-- Any direct-reachability witness exposes a valid bridge. -/
theorem direct_reach_bridge (bridges : List Bridge) (src tgt : Nat)
    (h : DirectlyReachable bridges src tgt) :
    ∃ b ∈ bridges, BridgeValid b := by
  obtain ⟨b, hmem, hval, _⟩ := h
  exact ⟨b, hmem, hval⟩

-- ════════════════════════════════════════════════════════════════════
-- § 8  Federated System and Global Consistency
-- ════════════════════════════════════════════════════════════════════

/-- A federated system: a collection of packs and bridges, plus an overall
    federation lifecycle status. -/
structure FederatedSystem : Type where
  packs     : List Pack
  bridges   : List Bridge
  fedStatus : FederationStatus
  deriving Repr

/-- **Global consistency** (`Definition 8.3` in the paper): every pack is
    locally verified *and* every bridge is structurally valid. -/
def GloballyConsistent (fs : FederatedSystem) : Prop :=
  (∀ p ∈ fs.packs, LocallyVerified p) ∧
  (∀ b ∈ fs.bridges, BridgeValid b)

-- ════════════════════════════════════════════════════════════════════
-- § 8  Main Results
-- ════════════════════════════════════════════════════════════════════

/-- **Theorem 8.1 — Federation Consistency.**
    If every pack in the federated system is locally verified and every
    bridge is structurally valid, the system is globally consistent. -/
theorem federation_consistency
    (fs         : FederatedSystem)
    (h_packs    : ∀ p ∈ fs.packs, LocallyVerified p)
    (h_bridges  : ∀ b ∈ fs.bridges, BridgeValid b) :
    GloballyConsistent fs :=
  ⟨h_packs, h_bridges⟩

/-- **Corollary 8.2 — Evidence Combining Soundness.**
    Any two locally-verified packs connected by a valid bridge have combined
    trust rank ≥ 5 (solver level or above). -/
theorem evidence_combining_soundness
    (p₁ p₂  : Pack)
    (b      : Bridge)
    (h₁     : LocallyVerified p₁)
    (h₂     : LocallyVerified p₂)
    (hb     : BridgeValid b)
    (h_src  : b.sourceId = p₁.id)
    (h_tgt  : b.targetId = p₂.id) :
    (combineTrust p₁ p₂).rank ≥ 5 :=
  combine_preserves_verified p₁ p₂ h₁ h₂

/-- **Lemma 8.3 — Trust Dominance.**
    If a locally-verified pack's trust level dominates a bridge's minimum
    trust requirement, that bridge is applicable for this pack. -/
theorem trust_dominance_applicable
    (p       : Pack)
    (b       : Bridge)
    (h_ver   : LocallyVerified p)
    (h_dom   : b.minTrust.rank ≤ p.trustLevel.rank) :
    BridgeApplicable b p.trustLevel :=
  h_dom

/-- **Theorem 8.4 — Pack Authority Soundness.**
    In a globally consistent system, every pack managed by a valid authority
    is locally verified. -/
theorem authority_soundness
    (a      : PackAuthority)
    (fs     : FederatedSystem)
    (h_auth : AuthorityValid a fs.packs)
    (h_cons : GloballyConsistent fs) :
    ∀ pid ∈ a.managedPacks,
      ∃ p ∈ fs.packs, p.id = pid ∧ LocallyVerified p := by
  intro pid hpid
  obtain ⟨p, hp_mem, hp_id⟩ := h_auth pid hpid
  exact ⟨p, hp_mem, hp_id, h_cons.1 p hp_mem⟩

/-- **Theorem 8.5 — Two-Hop Bridge Composition.**
    Given two valid bridges b₁ : A → B and b₂ : B → C (with
    b₁.targetId = b₂.sourceId), both individual hops are directly reachable. -/
theorem bridge_composition_two_hop
    (b₁ b₂   : Bridge)
    (bridges  : List Bridge)
    (h₁       : BridgeValid b₁)
    (h₂       : BridgeValid b₂)
    (h_chain  : b₁.targetId = b₂.sourceId)
    (hmem₁    : b₁ ∈ bridges)
    (hmem₂    : b₂ ∈ bridges) :
    DirectlyReachable bridges b₁.sourceId b₁.targetId ∧
    DirectlyReachable bridges b₂.sourceId b₂.targetId :=
  ⟨⟨b₁, hmem₁, h₁, Or.inl ⟨rfl, rfl⟩⟩,
   ⟨b₂, hmem₂, h₂, Or.inl ⟨rfl, rfl⟩⟩⟩

/-- **Corollary 8.6 — Global Consistency Implies All Packs Verified.**
    Under global consistency, every pack in the system has `verified` status. -/
theorem globally_consistent_all_verified
    (fs : FederatedSystem)
    (h  : GloballyConsistent fs) :
    ∀ p ∈ fs.packs, p.status = PackStatus.verified := by
  intro p hp
  exact locally_verified_status p (h.1 p hp)

end JudgmentGeometry.PackFederation
