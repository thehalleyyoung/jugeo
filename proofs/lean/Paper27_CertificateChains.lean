/-
  Paper27_CertificateChains.lean — Certificate Chains: Compositional PCC Architecture

  Formalizes Paper 27:
    • Certificate structure with status and trust levels
    • CertificateChain with weakest-link trust semantics
    • CertificateAuthority issuance and revocation
    • CertificateVerifier chain-validation algorithm
    • Chain Integrity theorem (soundness + anti-forgery + trust floor)
    • No-silent-strengthening corollary
  No sorry.
-/

namespace JudgmentGeometry.Paper27

-- ════════════════════════════════════════════════════════════════════
-- § 1  Basic Types
-- ════════════════════════════════════════════════════════════════════

/-- A coordinate identifies a location in the codebase. -/
abbrev Coordinate := String

/-- A proposition is a claim being attested. -/
abbrev Proposition := String

/-- An issuer is an authority identity. -/
abbrev IssuerName := String

/-- A certificate identifier. -/
abbrev CertId := String

/-- Three-tier trust lattice for certificates. -/
inductive TrustLevel where
  | proposal  : TrustLevel   -- draft; not yet reviewed
  | reviewed  : TrustLevel   -- reviewed by human or tool
  | verified  : TrustLevel   -- formally or mechanically verified
  deriving DecidableEq, Repr, BEq

def TrustLevel.toNat : TrustLevel → Nat
  | .proposal => 0
  | .reviewed => 1
  | .verified => 2

instance : LE TrustLevel where
  le a b := a.toNat ≤ b.toNat

instance : LT TrustLevel where
  lt a b := a.toNat < b.toNat

instance (a b : TrustLevel) : Decidable (a ≤ b) :=
  inferInstanceAs (Decidable (a.toNat ≤ b.toNat))

instance (a b : TrustLevel) : Decidable (a < b) :=
  inferInstanceAs (Decidable (a.toNat < b.toNat))

/-- Conservative meet (minimum trust). -/
def TrustLevel.meet (a b : TrustLevel) : TrustLevel :=
  if a.toNat ≤ b.toNat then a else b

/-- toNat is bounded by 2. -/
theorem TrustLevel.toNat_le_two (t : TrustLevel) : t.toNat ≤ 2 := by
  cases t <;> simp [TrustLevel.toNat]

/-- meet is commutative. -/
theorem TrustLevel.meet_comm (a b : TrustLevel) : a.meet b = b.meet a := by
  unfold TrustLevel.meet
  by_cases h : a.toNat ≤ b.toNat
  · simp [h]
    by_cases h2 : b.toNat ≤ a.toNat
    · have : a.toNat = b.toNat := Nat.le_antisymm h h2
      cases a <;> cases b <;> simp_all [TrustLevel.toNat]
    · simp [h2]
  · push_neg at h
    have h2 : b.toNat ≤ a.toNat := Nat.le_of_lt h
    simp [h2, Nat.not_le.mpr h]

/-- meet is associative. -/
theorem TrustLevel.meet_assoc (a b c : TrustLevel) :
    (a.meet b).meet c = a.meet (b.meet c) := by
  unfold TrustLevel.meet
  split_ifs <;> cases a <;> cases b <;> cases c <;> simp_all [TrustLevel.toNat]

/-- meet lower-bounds both arguments. -/
theorem TrustLevel.meet_le_left (a b : TrustLevel) : a.meet b ≤ a := by
  unfold TrustLevel.meet
  by_cases h : a.toNat ≤ b.toNat
  · simp [h]
  · push_neg at h; simp [Nat.not_le.mpr h]; exact Nat.le_of_lt h

theorem TrustLevel.meet_le_right (a b : TrustLevel) : a.meet b ≤ b := by
  rw [TrustLevel.meet_comm]; exact TrustLevel.meet_le_left b a

-- ════════════════════════════════════════════════════════════════════
-- § 2  Certificate Structure
-- ════════════════════════════════════════════════════════════════════

/-- Certificate lifecycle status. -/
inductive CertificateStatus where
  | pending    : CertificateStatus
  | settled    : CertificateStatus
  | obstructed : CertificateStatus
  | revoked    : CertificateStatus
  | expired    : CertificateStatus
  deriving DecidableEq, Repr, BEq

/-- A certificate attests to local verification at a coordinate. -/
structure Certificate where
  certId       : CertId
  coord        : Coordinate
  props        : List Proposition      -- verified propositions
  trustLevel   : TrustLevel
  issuer       : IssuerName
  sigHash      : String                -- SHA-256 content fingerprint (opaque)
  residuals    : List String           -- unresolved obligations
  obstructions : List String           -- blocking conditions
  deriving DecidableEq, Repr

/-- A certificate is valid if it has no obstructions, is not revoked,
    and has not expired.  We model revocation externally (see CA). -/
def Certificate.locallyValid (c : Certificate) : Bool :=
  c.obstructions.isEmpty

-- ════════════════════════════════════════════════════════════════════
-- § 3  Signature Model
-- ════════════════════════════════════════════════════════════════════

/-- Abstract content hash: the fields that are signed. -/
structure CertContent where
  coord      : Coordinate
  props      : List Proposition
  trustLevel : TrustLevel
  issuer     : IssuerName
  deriving DecidableEq, Repr

/-- Extract the signed content from a certificate. -/
def Certificate.content (c : Certificate) : CertContent :=
  ⟨c.coord, c.props, c.trustLevel, c.issuer⟩

/-- A certificate is signature-consistent if its sigHash matches
    the hash of its content.  We axiomatize the hash function as
    injective on CertContent (collision resistance). -/
axiom hashFn : CertContent → String

axiom hashFn_injective : ∀ a b : CertContent, hashFn a = hashFn b → a = b

/-- A certificate is signature-valid iff its stored hash equals
    the hash of its content fields. -/
def Certificate.sigValid (c : Certificate) : Prop :=
  c.sigHash = hashFn c.content

/-- Decidability of signature validity (requires decidable equality on String). -/
instance (c : Certificate) : Decidable (c.sigValid) :=
  inferInstanceAs (Decidable (c.sigHash = hashFn c.content))

-- ════════════════════════════════════════════════════════════════════
-- § 4  Forgery Definition
-- ════════════════════════════════════════════════════════════════════

/-- A certificate is forged if its sigHash does not match its content. -/
def Certificate.isForged (c : Certificate) : Prop := ¬ c.sigValid

/-- Two certificates agree on coordinates but the second has tampered content. -/
def isForgedVariant (genuine forged : Certificate) : Prop :=
  genuine.coord = forged.coord ∧ forged.isForged

-- ════════════════════════════════════════════════════════════════════
-- § 5  Certificate Chain
-- ════════════════════════════════════════════════════════════════════

/-- A certificate chain is a nonempty list of certificates. -/
structure CertificateChain where
  links : List Certificate
  nonempty : links ≠ []
  deriving Repr

/-- The trust floor is the minimum trust level across all links. -/
def CertificateChain.trustFloor (ch : CertificateChain) : TrustLevel :=
  ch.links.foldl (fun acc c => acc.meet c.trustLevel) TrustLevel.verified

/-- Every link's trust level is ≥ the trust floor. -/
theorem trustFloor_le_each_link (ch : CertificateChain) (c : Certificate)
    (hc : c ∈ ch.links) : ch.trustFloor ≤ c.trustLevel := by
  unfold CertificateChain.trustFloor
  induction ch.links with
  | nil => exact absurd hc (List.not_mem_nil _)
  | cons hd tl ih =>
    simp [List.foldl_cons]
    cases List.mem_cons.mp hc with
    | inl heq =>
      subst heq
      apply TrustLevel.meet_le_right
    | inr hmem =>
      have := ih (fun h => ch.nonempty (by simp [h])) hmem
      exact Nat.le_trans (TrustLevel.meet_le_left _ _) this

/-- Extending a chain can only decrease (or maintain) the trust floor. -/
theorem extend_weakens_floor (ch : CertificateChain) (c : Certificate) :
    let ch' : CertificateChain :=
          ⟨ch.links ++ [c], List.append_ne_nil_of_ne_nil_left ch.links _ ch.nonempty⟩
    ch'.trustFloor ≤ ch.trustFloor := by
  simp [CertificateChain.trustFloor, List.foldl_append]
  apply TrustLevel.meet_le_left

-- ════════════════════════════════════════════════════════════════════
-- § 6  Certificate Authority
-- ════════════════════════════════════════════════════════════════════

/-- A certificate authority tracks issued and revoked certificate IDs. -/
structure CertificateAuthority where
  name          : IssuerName
  trustedIssuers : List IssuerName  -- issuers this CA trusts
  issuedIds     : List CertId       -- all issued certificate IDs
  revokedIds    : List CertId       -- revoked subset

/-- A certificate is considered revoked by this CA if its ID is in the revoked set. -/
def CertificateAuthority.isRevoked (ca : CertificateAuthority) (c : Certificate) : Bool :=
  ca.revokedIds.contains c.certId

/-- A certificate is issued by a trusted issuer according to this CA. -/
def CertificateAuthority.isTrustedIssuer (ca : CertificateAuthority) (c : Certificate) : Bool :=
  ca.trustedIssuers.contains c.issuer || c.issuer == ca.name

-- ════════════════════════════════════════════════════════════════════
-- § 7  Certificate Verifier
-- ════════════════════════════════════════════════════════════════════

/-- Per-link verification: check signature, revocation, obstructions,
    and trusted issuer. -/
def verifyLink (ca : CertificateAuthority) (c : Certificate) : Bool :=
  c.sigValid.decide                 -- (i)  signature check
  && !ca.isRevoked c                -- (iii) revocation check
  && c.locallyValid                 -- (iv)  obstruction check
  && ca.isTrustedIssuer c           -- authority check

/-- Chain verification: all links valid plus coverage and residuals. -/
def verifyChain (ca : CertificateAuthority) (ch : CertificateChain)
    (required : List Coordinate) : Bool :=
  ch.links.all (verifyLink ca)
  && required.all (fun coord => ch.links.any (fun c => c.coord == coord))
  && ch.links.all (fun c => c.residuals.isEmpty)

-- ════════════════════════════════════════════════════════════════════
-- § 8  Chain Integrity Theorem
-- ════════════════════════════════════════════════════════════════════

/-- Soundness: if verifyChain returns true, every link passes verifyLink. -/
theorem verifyChain_soundness
    (ca : CertificateAuthority) (ch : CertificateChain) (req : List Coordinate)
    (h : verifyChain ca ch req = true) :
    ∀ c ∈ ch.links, verifyLink ca c = true := by
  unfold verifyChain at h
  simp [Bool.and_eq_true] at h
  exact fun c hc => h.1.1 c hc

/-- Every link in a verified chain has a valid signature. -/
theorem verifyChain_sig_valid
    (ca : CertificateAuthority) (ch : CertificateChain) (req : List Coordinate)
    (h : verifyChain ca ch req = true)
    (c : Certificate) (hc : c ∈ ch.links) :
    c.sigValid := by
  have hlink := verifyChain_soundness ca ch req h c hc
  unfold verifyLink at hlink
  simp [Bool.and_eq_true, decide_eq_true_eq] at hlink
  exact hlink.1.1.1

/-- Anti-forgery: a chain with a forged link fails verification. -/
theorem verifyChain_rejects_forged
    (ca : CertificateAuthority) (ch : CertificateChain) (req : List Coordinate)
    (c : Certificate) (hc : c ∈ ch.links) (hf : c.isForged) :
    verifyChain ca ch req = false := by
  by_contra hv
  push_neg at hv
  rw [Bool.not_eq_false] at hv
  have hlink := verifyChain_soundness ca ch req hv c hc
  have hsig := verifyChain_sig_valid ca ch req hv c hc
  exact hf hsig

/-- Trust floor lower-bounds each link in a verified chain. -/
theorem verifyChain_trustFloor_le
    (ca : CertificateAuthority) (ch : CertificateChain) (req : List Coordinate)
    (h : verifyChain ca ch req = true)
    (c : Certificate) (hc : c ∈ ch.links) :
    ch.trustFloor ≤ c.trustLevel :=
  trustFloor_le_each_link ch c hc

-- ════════════════════════════════════════════════════════════════════
-- § 9  No-Silent-Strengthening Corollary
-- ════════════════════════════════════════════════════════════════════

/-- In a fully verified chain, every link has empty residuals. -/
theorem verifyChain_no_residuals
    (ca : CertificateAuthority) (ch : CertificateChain) (req : List Coordinate)
    (h : verifyChain ca ch req = true)
    (c : Certificate) (hc : c ∈ ch.links) :
    c.residuals = [] := by
  unfold verifyChain at h
  simp [Bool.and_eq_true] at h
  have hresid := h.1.2 c hc
  simp [List.isEmpty_iff_eq_nil] at hresid
  exact hresid

/-- NSS corollary: total residuals is 0 in a verified chain. -/
theorem nss_corollary
    (ca : CertificateAuthority) (ch : CertificateChain) (req : List Coordinate)
    (h : verifyChain ca ch req = true) :
    (ch.links.map (fun c => c.residuals.length)).sum = 0 := by
  apply List.sum_eq_zero_iff_forall_eq_zero.mpr
  intro x hx
  simp [List.mem_map] at hx
  obtain ⟨c, hc, rfl⟩ := hx
  rw [verifyChain_no_residuals ca ch req h c hc]
  simp

-- ════════════════════════════════════════════════════════════════════
-- § 10  Certificate Merger (Conservative Strategy)
-- ════════════════════════════════════════════════════════════════════

/-- Merge two certificates at the same coordinate using the conservative strategy:
    intersection of props, minimum trust, union of residuals. -/
def mergeCertificates (c1 c2 : Certificate)
    (hcoord : c1.coord = c2.coord)
    (sig : String) : Certificate :=
  { certId       := c1.certId ++ "_merged"
    coord        := c1.coord
    props        := c1.props.filter (fun p => c2.props.contains p)
    trustLevel   := c1.trustLevel.meet c2.trustLevel
    issuer       := c1.issuer
    sigHash      := sig
    residuals    := (c1.residuals ++ c2.residuals).eraseDups
    obstructions := (c1.obstructions ++ c2.obstructions).eraseDups }

/-- The merged certificate's trust is ≤ both inputs (weakest-link). -/
theorem merge_trust_le_left (c1 c2 : Certificate) (hcoord : c1.coord = c2.coord)
    (sig : String) :
    (mergeCertificates c1 c2 hcoord sig).trustLevel ≤ c1.trustLevel :=
  TrustLevel.meet_le_left c1.trustLevel c2.trustLevel

theorem merge_trust_le_right (c1 c2 : Certificate) (hcoord : c1.coord = c2.coord)
    (sig : String) :
    (mergeCertificates c1 c2 hcoord sig).trustLevel ≤ c2.trustLevel :=
  TrustLevel.meet_le_right c1.trustLevel c2.trustLevel

-- ════════════════════════════════════════════════════════════════════
-- § 11  Projection Faithfulness
-- ════════════════════════════════════════════════════════════════════

/-- A projection of a certificate: props ⊆ original, residuals ⊇ original. -/
structure CertProjection (c : Certificate) where
  projProps    : List Proposition
  projResidual : List String
  hProps       : ∀ p ∈ projProps, p ∈ c.props
  hResiduals   : ∀ r ∈ c.residuals, r ∈ projResidual

/-- The identity projection is always faithful. -/
def Certificate.identityProjection (c : Certificate) : CertProjection c :=
  { projProps    := c.props
    projResidual := c.residuals
    hProps       := fun _ h => h
    hResiduals   := fun _ h => h }

-- ════════════════════════════════════════════════════════════════════
-- § 12  Serialization Round-Trip Specification
-- ════════════════════════════════════════════════════════════════════

/-- Abstract serialization: we axiomatize a round-trip faithful pair. -/
axiom certToJson   : Certificate → String
axiom certFromJson : String → Option Certificate

axiom roundTrip_faithful :
    ∀ c : Certificate, certFromJson (certToJson c) = some c

/-- Round-trip preserves signature validity. -/
theorem roundTrip_preserves_sigValid (c : Certificate) (h : c.sigValid) :
    ∃ c' : Certificate, certFromJson (certToJson c) = some c' ∧ c'.sigValid := by
  exact ⟨c, roundTrip_faithful c, h⟩

end JudgmentGeometry.Paper27
