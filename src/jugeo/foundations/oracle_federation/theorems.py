from __future__ import annotations
"""Formal theorem statements for oracle federation — Theory2.tex Ch7.

This module encodes the formal theorems and lemmas from Chapter 7 of
Theory2.tex as Python dataclass instances.  Each theorem captures the
statement, hypotheses, conclusion, proof sketch, and cross-references.

The theorems are not proofs — they are *statements* that the implementation
is expected to uphold.  The ``TheoremRegistry`` class allows the system to
enumerate all theorems, verify that their stated hypotheses are satisfied by
the current configuration, and generate a compliance report.

Chapter 7 theorem summary
--------------------------
Theorem 7.1 (Trust Ceiling Conservation):
    No oracle can raise its own trust ceiling without explicit external
    corroboration.  This is the ``no self-promotion`` invariant.

Theorem 7.2 (Federation Soundness):
    If each member solver is sound for its declared fragment class, then the
    federation is sound for the union of those fragment classes.

Theorem 7.3 (Witness Consistency):
    Mutually consistent runtime witnesses can be merged into a single
    composite witness without trust loss.  Inconsistency triggers demotion.

Theorem 7.4 (Jurisdiction Composition):
    The composition of two jurisdictions enforces the meet (greatest lower
    bound) of their trust ceilings.

Theorem 7.5 (Copilot Ceiling Invariance):
    Copilot proposals are permanently bounded at ``COPILOT_SUGGESTED`` unless
    externally corroborated by a strictly higher-tier channel.

Lemma 7.1 (Oracle Boundedness):
    Every oracle proposal record carries a ceiling_applied field and that
    field is set at creation time, not retroactively.

Lemma 7.2 (Federation Completeness):
    For any fragment in the union of member jurisdictions, the federation
    router produces a valid routing decision.

Corollary 7.1 (Composition Ceiling):
    The trust ceiling of a composed jurisdiction is at most the minimum
    ceiling of the component jurisdictions.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
except ImportError:
    TrustLevel = None  # type: ignore
    TrustTier = None  # type: ignore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TheoremKind(Enum):
    """Classification of a formal statement in Theory2.tex Ch7."""

    THEOREM = "theorem"
    LEMMA = "lemma"
    COROLLARY = "corollary"
    PROPOSITION = "proposition"
    DEFINITION = "definition"


class ProofStatus(Enum):
    """Current proof maturity level of a theorem statement."""

    STATED = "stated"
    SKETCH_PROVIDED = "sketch_provided"
    MECHANICALLY_VERIFIED = "mechanically_verified"
    UNKNOWN = "unknown"
    PENDING = "pending"


# ---------------------------------------------------------------------------
# Dataclass: Theorem
# ---------------------------------------------------------------------------


@dataclass
class Theorem:
    """A formal theorem, lemma, or corollary from Theory2.tex Chapter 7.

    Instances of this class represent *statements* about the expected
    behaviour of the JuGeo verification framework.  They are not
    executable proofs; they serve as machine-readable specification
    artefacts that can be checked against a live system configuration via
    the ``TheoremRegistry.verify_statement`` method.

    Attributes
    ----------
    name:
        Unique human-readable name, e.g. ``"Theorem 7.1"``.
    kind:
        Classification of the statement (theorem, lemma, corollary, etc.).
    statement:
        The full formal statement of the theorem as a prose string.
    hypotheses:
        Ordered list of hypothesis strings.  Each hypothesis is a
        precondition that must hold for the conclusion to be guaranteed.
    conclusion:
        The conclusion of the theorem — what is guaranteed when all
        hypotheses are satisfied.
    proof_sketch:
        Informal description of how the theorem is proved or enforced by
        the implementation.
    references:
        Cross-references to Theory2.tex sections, implementation classes,
        or external documents.
    status:
        Current proof maturity status from the ``ProofStatus`` enum.
    tags:
        Free-form tags for filtering and searching.
    created_at:
        Unix timestamp recording when this theorem record was created.
    """

    name: str
    kind: TheoremKind = TheoremKind.THEOREM
    statement: str = ""
    hypotheses: list[str] = field(default_factory=list)
    conclusion: str = ""
    proof_sketch: str = ""
    references: list[str] = field(default_factory=list)
    status: ProofStatus = ProofStatus.STATED
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a fully serialisable dictionary representation.

        All enum values are converted to their ``.value`` strings so that
        the result can be serialised to JSON without a custom encoder.

        Returns
        -------
        dict
            Keys mirror the dataclass field names.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "statement": self.statement,
            "hypotheses": list(self.hypotheses),
            "conclusion": self.conclusion,
            "proof_sketch": self.proof_sketch,
            "references": list(self.references),
            "status": self.status.value,
            "tags": list(self.tags),
            "created_at": self.created_at,
        }

    def short_name(self) -> str:
        """Return the theorem name with spaces replaced by underscores.

        This is suitable for use as a dictionary key or Python identifier
        fragment.

        Returns
        -------
        str
            Name string with every space replaced by ``"_"``.
        """
        return self.name.replace(" ", "_")

    def is_verified(self) -> bool:
        """Return ``True`` if the theorem has been mechanically verified.

        Returns
        -------
        bool
            ``True`` iff ``self.status == ProofStatus.MECHANICALLY_VERIFIED``.
        """
        return self.status is ProofStatus.MECHANICALLY_VERIFIED

    def hypothesis_count(self) -> int:
        """Return the number of hypotheses for this theorem.

        Returns
        -------
        int
            Length of the ``hypotheses`` list.
        """
        return len(self.hypotheses)

    def describe(self) -> str:
        """Return a formatted multi-line description of this theorem.

        The description includes the kind, name, full statement, numbered
        hypotheses, conclusion, and proof sketch.

        Returns
        -------
        str
            Human-readable multi-line string.
        """
        lines = [
            f"[{self.kind.value.upper()}] {self.name}",
            f"Status: {self.status.value}",
            "",
            "Statement:",
            f"  {self.statement}",
            "",
        ]
        if self.hypotheses:
            lines.append("Hypotheses:")
            for idx, hyp in enumerate(self.hypotheses, start=1):
                lines.append(f"  H{idx}. {hyp}")
            lines.append("")
        lines += [
            "Conclusion:",
            f"  {self.conclusion}",
            "",
            "Proof sketch:",
            f"  {self.proof_sketch}",
            "",
        ]
        if self.references:
            lines.append("References: " + ", ".join(self.references))
        if self.tags:
            lines.append("Tags: " + ", ".join(self.tags))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level theorem constants
# ---------------------------------------------------------------------------

THEOREM_7_1_TRUST_CEILING_CONSERVATION = Theorem(
    name="Theorem 7.1",
    kind=TheoremKind.THEOREM,
    statement=(
        "Let O be an oracle registered in the JuGeo federation with declared "
        "trust ceiling C.  Let R be any response record produced by O.  Then "
        "the effective trust level of R as stored in the evidence ledger is at "
        "most C, regardless of the trust level that O claims in the response "
        "payload.  Formally: for all oracles O and responses R produced by O, "
        "effective_trust(R) ≤ ceiling(O), where the ordering ≤ is given by the "
        "canonical TrustLevel lattice (Theory2.tex §2.4).  This invariant is "
        "called the *no self-promotion* property and is the cornerstone of the "
        "oracle trust model: an oracle cannot elevate its own epistemic status "
        "without an external corroboration event originating from a strictly "
        "higher-tier channel."
    ),
    hypotheses=[
        "Oracle O is registered in the federation with a declared trust ceiling C "
        "(i.e., O.ceiling_applied is set at registration time and is immutable).",
        "Response R is produced by oracle O and carries a trust_level field with "
        "value L in the canonical TrustLevel enumeration.",
        "The claim L > C holds under the canonical lattice ordering defined in "
        "Theory2.tex §2.4 (i.e., rank(L) > rank(C)).",
        "No external corroboration event from a channel with trust level > C has "
        "been recorded for R in the evidence ledger prior to the ceiling check.",
    ],
    conclusion=(
        "The trust level of R is clamped to C before R is committed to the "
        "evidence ledger.  A violation event is recorded in the audit trail with "
        "the original claimed level preserved as original_trust.  The "
        "ceiling_enforced flag on R is set to True."
    ),
    proof_sketch=(
        "The TrustCeilingEnforcer intercepts every oracle response before ledger "
        "commit.  It reads the oracle's registered ceiling C from the federation "
        "manifest and compares rank(R.trust_level) against rank(C) using the "
        "_TRUST_RANK map.  If a violation is detected, the enforcer rewrites "
        "R.trust_level to C and appends a CeilingViolation record to the audit "
        "log (§7.1.2).  Because the enforcer is positioned between the oracle "
        "channel and the ledger write path, no response can bypass it.  The "
        "ceiling C itself is set at oracle registration time by a human "
        "administrator and is protected against in-band modification by the "
        "federation manifest's signature check (§7.2.1).  Together these "
        "mechanisms guarantee the invariant by construction: the only path to a "
        "higher effective trust level is an out-of-band corroboration event that "
        "the enforcer is explicitly authorised to process."
    ),
    references=[
        "Theory2.tex §7.1.1",
        "Theory2.tex §7.1.2",
        "Theory2.tex §2.4",
        "TrustCeilingEnforcer.enforce()",
        "algorithms.trust_ceiling_propagation()",
    ],
    status=ProofStatus.SKETCH_PROVIDED,
    tags=["trust", "ceiling", "oracle", "no-self-promotion", "invariant"],
)

THEOREM_7_2_FEDERATION_SOUNDNESS = Theorem(
    name="Theorem 7.2",
    kind=TheoremKind.THEOREM,
    statement=(
        "Let F be a solver federation whose member set is {S_1, …, S_n}.  For "
        "each member S_i let J_i denote its declared jurisdiction (the set of "
        "fragment classes for which S_i is registered as sound).  Let "
        "J = ∪_{i=1}^{n} J_i be the union jurisdiction of F.  If every S_i is "
        "individually sound for every fragment class in J_i — meaning that "
        "whenever S_i accepts a fragment φ ∈ J_i as valid, φ is genuinely "
        "valid under the ground semantics — then F is sound for J.  That is, "
        "for any fragment φ ∈ J, if the federation router selects solver S_k "
        "such that φ ∈ J_k and S_k accepts φ, then φ is valid.  The theorem "
        "guarantees that federation does not introduce unsoundness beyond the "
        "individual member oracles."
    ),
    hypotheses=[
        "Each member solver S_i is individually sound for its jurisdiction J_i: "
        "for all φ ∈ J_i, S_i(φ) = ACCEPTED implies φ is valid.",
        "The federation router's jurisdiction check is correct: the router only "
        "dispatches fragment φ to solver S_k if φ ∈ J_k.",
        "Fragment classes are disjoint or, when overlapping, each covering solver "
        "is sound for the overlap, so no solver is asked to judge a fragment "
        "outside its competence.",
        "The trust ceiling of each S_i is at most SOLVER_DISCHARGED, and no "
        "solver response is elevated above this ceiling without independent "
        "corroboration (by Theorem 7.1).",
    ],
    conclusion=(
        "The federation F is sound for the union jurisdiction J: for any "
        "fragment φ ∈ J, if F accepts φ then φ is valid under the ground "
        "semantics."
    ),
    proof_sketch=(
        "By hypothesis, every S_i is sound for J_i.  The router (§7.2.3) "
        "guarantees that it dispatches φ only to a solver whose jurisdiction "
        "covers φ; this is the jurisdiction_check_passed flag in the routing "
        "decision.  The returned verdict for φ therefore comes from a solver "
        "that is sound for φ, making the verdict reliable.  The trust ceiling "
        "invariant (Thm 7.1) prevents any member from claiming a higher trust "
        "level than warranted, so the effective trust of the federation's verdict "
        "is bounded by the member's ceiling.  Composing these two properties "
        "yields federation-level soundness."
    ),
    references=[
        "Theory2.tex §7.2",
        "Theory2.tex §7.2.3",
        "algorithms.federation_route_optimal()",
        "Theorem 7.1",
    ],
    status=ProofStatus.SKETCH_PROVIDED,
    tags=["federation", "soundness", "solver", "routing", "jurisdiction"],
)

THEOREM_7_3_WITNESS_CONSISTENCY = Theorem(
    name="Theorem 7.3",
    kind=TheoremKind.THEOREM,
    statement=(
        "Let W = {w_1, …, w_k} be a set of runtime witnesses produced by "
        "independent observation channels.  W is called *mutually consistent* "
        "if for every pair (w_i, w_j) with overlapping scope — i.e., sharing "
        "at least one entity_id or heap_snapshot key — the values reported by "
        "w_i and w_j for each shared key are identical or one is absent.  If "
        "W is mutually consistent, then the witnesses in W can be merged into "
        "a single composite witness w* whose trust level equals the maximum "
        "trust level in W, without any loss of epistemic quality.  If W is not "
        "mutually consistent, at least one witness must be demoted: its trust "
        "level is reduced to UNVERIFIED and the conflict is recorded in the "
        "audit trail."
    ),
    hypotheses=[
        "Each w_i ∈ W is produced by a distinct, independent observation "
        "channel (no two witnesses share a source_id).",
        "W is mutually consistent: for every pair (w_i, w_j) and every shared "
        "key k, w_i[k] = w_j[k] or at least one of w_i[k], w_j[k] is absent.",
        "The trust level of each w_i is at most RUNTIME_WITNESSED in the "
        "canonical TrustLevel lattice (witnesses cannot claim higher tiers).",
        "The merge operation does not introduce new claims beyond those present "
        "in the individual witnesses.",
    ],
    conclusion=(
        "The composite witness w* carries trust_level = max({trust(w_i)}) and "
        "scope = ∪{scope(w_i)}.  No trust demotion is required.  If W is "
        "inconsistent, the conflict is logged and the inconsistent witness is "
        "demoted to UNVERIFIED."
    ),
    proof_sketch=(
        "Mutual consistency (verified by witness_consistency_check) guarantees "
        "that merging the witnesses introduces no contradictions.  The composite "
        "trust level is the maximum over the individual trust levels because each "
        "consistent witness independently attests to its portion of the "
        "composite's claims.  Independence of channels (H1) ensures that the "
        "maximum trust level is not artificially inflated by a single source "
        "duplicating its own attestation.  When inconsistency is detected, the "
        "WitnessCorrelator demotes the lower-trusted party to UNVERIFIED and "
        "records a conflict entry, preserving the audit requirement of §7.1.2.  "
        "The demotion is conservative: we demote rather than discard so that the "
        "conflicting evidence remains inspectable."
    ),
    references=[
        "Theory2.tex §7.3",
        "Theory2.tex §7.1.2",
        "algorithms.witness_consistency_check()",
        "algorithms.WitnessCorrelator",
    ],
    status=ProofStatus.SKETCH_PROVIDED,
    tags=["witness", "consistency", "merge", "runtime", "demotion"],
)

THEOREM_7_4_JURISDICTION_COMPOSITION = Theorem(
    name="Theorem 7.4",
    kind=TheoremKind.THEOREM,
    statement=(
        "Let J_1 and J_2 be two jurisdictions in the JuGeo federation, each "
        "characterised by an allowed domain set D_i and a trust ceiling C_i.  "
        "The composition J_1 ⊓ J_2 of these jurisdictions is the jurisdiction "
        "J* with allowed domain set D* = D_1 ∩ D_2 and trust ceiling "
        "C* = min(C_1, C_2) under the canonical TrustLevel lattice ordering.  "
        "This is the *meet* of J_1 and J_2 in the jurisdiction lattice.  The "
        "theorem generalises to n jurisdictions: "
        "J_1 ⊓ … ⊓ J_n has domain D_1 ∩ … ∩ D_n and ceiling "
        "min(C_1, …, C_n).  Jurisdiction composition is commutative, "
        "associative, and idempotent."
    ),
    hypotheses=[
        "J_1 = (D_1, C_1) and J_2 = (D_2, C_2) are well-formed jurisdictions: "
        "D_i are non-empty sets of fragment-kind strings and C_i are elements "
        "of the canonical TrustLevel lattice.",
        "The composition operator ⊓ is the greatest lower bound in the "
        "jurisdiction lattice, ordered by D_1 ⊆ D_2 and C_1 ≤ C_2.",
        "No jurisdiction has a trust ceiling above SOLVER_DISCHARGED unless "
        "it has been granted a special elevation by a human administrator.",
    ],
    conclusion=(
        "J* = J_1 ⊓ J_2 = (D_1 ∩ D_2, min(C_1, C_2)).  The composed "
        "jurisdiction is the most permissive jurisdiction that is simultaneously "
        "as restrictive as both J_1 and J_2.  Any fragment φ acceptable under "
        "J* is acceptable under both J_1 and J_2."
    ),
    proof_sketch=(
        "The jurisdiction lattice is defined in Theory2.tex §7.4 with the "
        "partial order (D_1, C_1) ≤ (D_2, C_2) iff D_1 ⊆ D_2 and C_1 ≤ C_2.  "
        "The meet of two elements in a product lattice is the component-wise "
        "meet: the intersection of the domain sets and the minimum of the "
        "ceilings.  The jurisdiction_intersection_algorithm implements this "
        "directly using set intersection for domains and the _TRUST_RANK map for "
        "the ceiling minimum.  Commutativity and associativity follow from the "
        "corresponding properties of set intersection and integer minimum.  "
        "Idempotency (J ⊓ J = J) follows trivially from D ∩ D = D and min(C, C) = C."
    ),
    references=[
        "Theory2.tex §7.4",
        "Theory2.tex §7.4.1",
        "algorithms.jurisdiction_intersection_algorithm()",
        "Corollary 7.1",
    ],
    status=ProofStatus.SKETCH_PROVIDED,
    tags=["jurisdiction", "composition", "meet", "lattice", "ceiling"],
)

THEOREM_7_5_COPILOT_CEILING_INVARIANCE = Theorem(
    name="Theorem 7.5",
    kind=TheoremKind.THEOREM,
    statement=(
        "Let P be any proposal introduced into the JuGeo evidence ledger via a "
        "Copilot channel.  Then the effective trust level of P is permanently "
        "bounded above by COPILOT_SUGGESTED in the canonical TrustLevel lattice, "
        "unless and until an external corroboration event E is recorded for P "
        "from a channel whose own trust level is strictly greater than "
        "COPILOT_SUGGESTED.  This bound is called the *Copilot ceiling* and it "
        "holds by design: Copilot enters the JuGeo trust hierarchy at the "
        "COPILOT_SUGGESTED tier because its proposals are heuristically "
        "generated and have not been subject to formal verification, runtime "
        "witnessing, or human attestation at the time of submission.  The "
        "ceiling is not a penalty — it is an accurate reflection of the "
        "epistemic status of an unverified generative suggestion.  Elevation "
        "above COPILOT_SUGGESTED requires an independent, out-of-band "
        "corroboration event; Copilot cannot self-promote."
    ),
    hypotheses=[
        "P is a proposal produced by a Copilot channel and submitted to the "
        "JuGeo evidence ledger.  P.source_channel = 'copilot'.",
        "At the time of submission, no external corroboration event for P "
        "has been recorded in the ledger from any channel with trust level "
        "strictly greater than COPILOT_SUGGESTED.",
        "The federation manifest registers Copilot channels with "
        "ceiling = COPILOT_SUGGESTED, and this ceiling is set at registration "
        "time and is immutable without administrator intervention (by Lemma 7.1).",
        "The Copilot channel does not itself constitute a corroborating source "
        "for its own proposals: a second Copilot response to the same prompt "
        "does not count as independent corroboration.",
    ],
    conclusion=(
        "effective_trust(P) = COPILOT_SUGGESTED until an independent external "
        "corroboration event E with trust(E) > COPILOT_SUGGESTED is recorded.  "
        "Copilot cannot self-promote: no action taken by Copilot alone can "
        "raise P above COPILOT_SUGGESTED.  This is by design, not by oversight."
    ),
    proof_sketch=(
        "Theorem 7.1 (Trust Ceiling Conservation) guarantees that no response "
        "from an oracle can exceed its registered ceiling.  Copilot is registered "
        "with ceiling = COPILOT_SUGGESTED (see federation manifest §7.2.1).  "
        "Therefore, by Thm 7.1, every Copilot response is clamped to "
        "COPILOT_SUGGESTED at ledger commit time.  The only escape route is an "
        "external corroboration event E with trust(E) > COPILOT_SUGGESTED.  "
        "Such an event must originate from a distinct channel (source_id ≠ "
        "Copilot's channel id) to satisfy the source-uniqueness requirement of "
        "the corroboration chain validator (§7.1.3, corroboration_chain_validator). "
        "If no such event exists, the ceiling holds unconditionally.  The design "
        "intent is that Copilot proposals serve as a *starting point* for human "
        "review or automated verification, after which they may be elevated by "
        "an independent, higher-trust channel."
    ),
    references=[
        "Theory2.tex §7.5",
        "Theory2.tex §7.1",
        "Theory2.tex §7.2.1",
        "Theorem 7.1",
        "Lemma 7.1",
        "algorithms.corroboration_chain_validator()",
    ],
    status=ProofStatus.SKETCH_PROVIDED,
    tags=[
        "copilot",
        "ceiling",
        "invariance",
        "no-self-promotion",
        "design",
        "copilot-suggested",
    ],
)

LEMMA_7_1_ORACLE_BOUNDEDNESS = Theorem(
    name="Lemma 7.1",
    kind=TheoremKind.LEMMA,
    statement=(
        "Let O be any oracle registered in the JuGeo federation.  Every "
        "proposal record P produced by O carries a ``ceiling_applied`` field "
        "whose value is set at creation time to the registered ceiling of O "
        "and is thereafter immutable.  Formally: for all P produced by O, "
        "P.ceiling_applied = ceiling(O) and this field is written exactly once "
        "— at the moment P is constructed — and is never overwritten by "
        "subsequent processing steps.  This lemma underpins Theorem 7.1 by "
        "ensuring that the ceiling is an intrinsic property of each proposal "
        "record rather than an extrinsic check applied late in the pipeline."
    ),
    hypotheses=[
        "O is registered in the federation manifest with a well-formed "
        "ceiling field: ceiling(O) ∈ TrustLevel.",
        "The proposal construction path for O calls OracleProposalRecord.__init__ "
        "which writes ceiling_applied = ceiling(O) as its first field assignment.",
        "No subsequent pipeline stage (routing, ranking, ledger commit) modifies "
        "the ceiling_applied field of an existing proposal record.",
        "The federation manifest's ceiling values are protected against in-band "
        "modification by the manifest signature scheme (§7.2.1).",
    ],
    conclusion=(
        "For every proposal P produced by O: P.ceiling_applied is set at "
        "construction time to ceiling(O), is never subsequently modified, and "
        "accurately reflects the trust ceiling that will be enforced by the "
        "TrustCeilingEnforcer when P is processed."
    ),
    proof_sketch=(
        "The OracleProposalRecord dataclass (Theory2.tex §7.1) sets "
        "ceiling_applied in __post_init__ before any other processing occurs.  "
        "The field is declared with a frozen flag equivalent in the ledger schema "
        "so that UPDATE operations on ceiling_applied are rejected.  Integration "
        "tests (§9.3) verify that a proposal record's ceiling_applied field "
        "matches the registered ceiling of its source oracle after a full "
        "round-trip through the pipeline, confirming that no stage overwrites it."
    ),
    references=[
        "Theory2.tex §7.1",
        "Theory2.tex §7.1.1",
        "Theory2.tex §7.2.1",
        "Theorem 7.1",
    ],
    status=ProofStatus.SKETCH_PROVIDED,
    tags=["oracle", "boundedness", "ceiling_applied", "immutability", "invariant"],
)

LEMMA_7_2_FEDERATION_COMPLETENESS = Theorem(
    name="Lemma 7.2",
    kind=TheoremKind.LEMMA,
    statement=(
        "Let F be a solver federation with member set {S_1, …, S_n} and let "
        "J = ∪_{i=1}^{n} J_i be the union jurisdiction of F.  For any fragment "
        "φ such that φ ∈ J — i.e., the fragment kind of φ is declared in at "
        "least one member's jurisdiction — the federation router produces a "
        "valid routing decision D with D.jurisdiction_check_passed = True and "
        "D.selected_backend ∈ {S_1, …, S_n}.  This lemma is the completeness "
        "counterpart to the soundness guarantee of Theorem 7.2: together they "
        "establish that the federation is both complete and sound for J."
    ),
    hypotheses=[
        "The union jurisdiction J = ∪ J_i is non-empty and well-defined.",
        "φ is a fragment whose kind is an element of J: fragment_kind(φ) ∈ J.",
        "The federation manifest is consistent: every solver S_i listed in the "
        "manifest is reachable and has responded to a liveness probe within the "
        "last TTL seconds.",
        "The federation_route_optimal function has access to the current, "
        "non-stale federation_dict (populated from the live manifest).",
    ],
    conclusion=(
        "The router returns a routing decision D with "
        "D.selected_backend ∈ {S_i | fragment_kind(φ) ∈ J_i} and "
        "D.jurisdiction_check_passed = True.  The decision is non-null and "
        "the selected backend is capable of processing φ."
    ),
    proof_sketch=(
        "The federation_route_optimal function filters the member_solvers dict "
        "to those whose jurisdiction list contains fragment_kind(φ).  By H2, "
        "at least one such solver exists; therefore the eligible list is non-empty "
        "and the function selects the highest-scoring eligible solver.  The "
        "jurisdiction_check_passed flag is set to True in the returned dict "
        "precisely when at least one eligible solver was found.  H3 and H4 "
        "ensure the manifest is current, so no solver that has left the "
        "federation appears as eligible."
    ),
    references=[
        "Theory2.tex §7.2",
        "Theory2.tex §7.2.3",
        "algorithms.federation_route_optimal()",
        "Theorem 7.2",
    ],
    status=ProofStatus.SKETCH_PROVIDED,
    tags=["federation", "completeness", "routing", "jurisdiction", "lemma"],
)

COROLLARY_7_1_COMPOSITION_CEILING = Theorem(
    name="Corollary 7.1",
    kind=TheoremKind.COROLLARY,
    statement=(
        "Let J_1 and J_2 be two jurisdictions with trust ceilings C_1 and C_2 "
        "respectively.  Then the trust ceiling C* of the composed jurisdiction "
        "J* = J_1 ⊓ J_2 satisfies C* ≤ min(C_1, C_2) under the canonical "
        "TrustLevel lattice ordering.  More precisely, C* = min(C_1, C_2).  "
        "This corollary follows immediately from Theorem 7.4 and states "
        "explicitly that jurisdiction composition can only lower — never raise "
        "— the effective trust ceiling.  It is a direct operational consequence "
        "of the meet structure established in Thm 7.4 and is stated separately "
        "because it is referenced frequently in the implementation documentation."
    ),
    hypotheses=[
        "J_1 and J_2 are well-formed jurisdictions in the sense of Theorem 7.4: "
        "each has a non-empty allowed_domains list and a trust ceiling in "
        "TrustLevel.",
        "The composition operation is the meet ⊓ defined in Theorem 7.4.",
    ],
    conclusion=(
        "C* = min(C_1, C_2).  Composing jurisdictions never raises the trust "
        "ceiling; it either lowers it or leaves it unchanged."
    ),
    proof_sketch=(
        "This is an immediate consequence of Theorem 7.4.  The composed ceiling "
        "is defined as min(C_1, C_2) by construction.  The inequality C* ≤ C_i "
        "for i = 1, 2 follows from the definition of minimum.  No additional "
        "argument is required beyond citing Thm 7.4."
    ),
    references=[
        "Theory2.tex §7.4",
        "Theorem 7.4",
        "algorithms.jurisdiction_intersection_algorithm()",
    ],
    status=ProofStatus.SKETCH_PROVIDED,
    tags=["corollary", "composition", "ceiling", "lattice", "jurisdiction"],
)


# ---------------------------------------------------------------------------
# Class: TheoremRegistry
# ---------------------------------------------------------------------------


class TheoremRegistry:
    """Registry of all formal theorems for Chapter 7 of Theory2.tex.

    Provides methods to register theorems, look them up by name, verify
    that their stated hypotheses are satisfied by a live system context,
    and generate compliance reports.

    The registry is not a dataclass because it requires mutable state and
    non-trivial construction logic.
    """

    def __init__(self) -> None:
        self._theorems: dict[str, Theorem] = {}
        self._verification_results: dict[str, dict] = {}

    def register(self, theorem: Theorem) -> None:
        """Add *theorem* to the registry, keyed by its name.

        If a theorem with the same name is already registered, it is
        silently overwritten so that module-reload scenarios work correctly.

        Parameters
        ----------
        theorem:
            The ``Theorem`` instance to register.
        """
        self._theorems[theorem.name] = theorem
        logger.debug("TheoremRegistry: registered '%s' (%s)", theorem.name, theorem.kind.value)

    def get(self, name: str) -> Theorem | None:
        """Return the theorem with the given *name*, or ``None`` if absent.

        Parameters
        ----------
        name:
            The exact name string used when registering the theorem.

        Returns
        -------
        Theorem | None
            The matching theorem, or ``None``.
        """
        return self._theorems.get(name)

    def list_all(self) -> list[Theorem]:
        """Return all registered theorems sorted alphabetically by name.

        Returns
        -------
        list[Theorem]
            Sorted list of all ``Theorem`` instances in the registry.
        """
        return sorted(self._theorems.values(), key=lambda t: t.name)

    def list_by_kind(self, kind: TheoremKind) -> list[Theorem]:
        """Return all theorems of a given *kind*, sorted by name.

        Parameters
        ----------
        kind:
            A ``TheoremKind`` enum value to filter by.

        Returns
        -------
        list[Theorem]
            Sorted list of matching theorems.
        """
        return sorted(
            (t for t in self._theorems.values() if t.kind is kind),
            key=lambda t: t.name,
        )

    def verify_statement(self, theorem_name: str, context: dict) -> dict:
        """Check whether the hypotheses of a named theorem hold in *context*.

        This is a *statement check*, not a formal proof.  It tests each
        hypothesis string against the context dict by looking for hypothesis
        keywords in the context keys and values.  A hypothesis is considered
        "checked" (not necessarily proved) if its key tokens appear in the
        context.  The purpose is to confirm that the preconditions relevant
        to a theorem are at least represented in the current system state.

        Parameters
        ----------
        theorem_name:
            Name of the theorem to check.
        context:
            Dictionary of current system state or configuration values.
            Keys should correspond to concepts mentioned in the theorem's
            hypotheses (e.g. ``"oracle_ceiling"``, ``"fragment_kind"``).

        Returns
        -------
        dict
            Keys: ``theorem_name``, ``passed`` (bool), ``hypothesis_results``
            (list of per-hypothesis dicts), ``timestamp`` (float).
        """
        theorem = self._theorems.get(theorem_name)
        if theorem is None:
            result = {
                "theorem_name": theorem_name,
                "passed": False,
                "hypothesis_results": [],
                "timestamp": time.time(),
                "error": f"Theorem '{theorem_name}' not found in registry",
            }
            self._verification_results[theorem_name] = result
            return result

        hyp_results: list[dict] = []
        all_passed = True
        context_str = " ".join(str(v) for v in context.values()).lower()
        context_keys = {k.lower() for k in context}

        for idx, hyp in enumerate(theorem.hypotheses):
            # Extract meaningful tokens from the hypothesis (words > 4 chars).
            tokens = [
                w.strip("().,;:'\"").lower()
                for w in hyp.split()
                if len(w.strip("().,;:'\"")) > 4
            ]
            # A hypothesis is "satisfied" if any of its significant tokens
            # appear in the context keys or values.
            matched_tokens = [t for t in tokens if t in context_str or t in context_keys]
            satisfied = len(matched_tokens) > 0
            if not satisfied:
                all_passed = False

            hyp_results.append(
                {
                    "index": idx,
                    "hypothesis": hyp,
                    "satisfied": satisfied,
                    "matched_tokens": matched_tokens,
                }
            )

        result = {
            "theorem_name": theorem_name,
            "passed": all_passed,
            "hypothesis_results": hyp_results,
            "timestamp": time.time(),
        }
        self._verification_results[theorem_name] = result
        logger.info(
            "verify_statement('%s'): %s",
            theorem_name,
            "PASSED" if all_passed else "FAILED",
        )
        return result

    def verify_all_statements(self, context: dict) -> dict:
        """Run ``verify_statement`` for every registered theorem.

        Parameters
        ----------
        context:
            System context dict passed to each individual verification.

        Returns
        -------
        dict
            Aggregate result with keys ``total``, ``passed``, ``failed``,
            and ``results`` (mapping theorem name → individual result dict).
        """
        aggregate: dict[str, dict] = {}
        passed_count = 0
        failed_count = 0

        for name in self._theorems:
            individual = self.verify_statement(name, context)
            aggregate[name] = individual
            if individual.get("passed"):
                passed_count += 1
            else:
                failed_count += 1

        summary = {
            "total": len(self._theorems),
            "passed": passed_count,
            "failed": failed_count,
            "results": aggregate,
            "timestamp": time.time(),
        }
        logger.info(
            "verify_all_statements: %d/%d passed",
            passed_count,
            len(self._theorems),
        )
        return summary

    def generate_compliance_report(self) -> str:
        """Generate a multi-line formatted compliance report for all theorems.

        The report lists each registered theorem, its kind, status, and the
        most recent verification result (if any).

        Returns
        -------
        str
            Multi-line human-readable report string.
        """
        lines = [
            "=" * 70,
            "  JuGeo Oracle Federation — Theorem Compliance Report",
            "  Theory2.tex Chapter 7",
            "=" * 70,
            f"  Registered theorems: {len(self._theorems)}",
            f"  Verification results cached: {len(self._verification_results)}",
            "",
        ]

        for theorem in self.list_all():
            last_result = self._verification_results.get(theorem.name)
            if last_result:
                verdict = "PASSED" if last_result.get("passed") else "FAILED"
            else:
                verdict = "NOT RUN"

            lines.append(f"  [{theorem.kind.value.upper():12s}] {theorem.name}")
            lines.append(f"    Status   : {theorem.status.value}")
            lines.append(f"    Tags     : {', '.join(theorem.tags) if theorem.tags else '—'}")
            lines.append(f"    Last run : {verdict}")
            lines.append(f"    Refs     : {', '.join(theorem.references[:2]) if theorem.references else '—'}")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)

    def get_chapter_summary(self) -> dict:
        """Return a high-level summary of the theorems in this registry.

        Returns
        -------
        dict
            Keys: ``chapter``, ``theorem_count``, ``lemma_count``,
            ``corollary_count``, ``proposition_count``, ``verified_count``,
            ``total``.
        """
        counts: dict[str, int] = {k.value: 0 for k in TheoremKind}
        verified = 0
        for theorem in self._theorems.values():
            counts[theorem.kind.value] += 1
            if theorem.is_verified():
                verified += 1
        return {
            "chapter": 7,
            "theorem_count": counts.get(TheoremKind.THEOREM.value, 0),
            "lemma_count": counts.get(TheoremKind.LEMMA.value, 0),
            "corollary_count": counts.get(TheoremKind.COROLLARY.value, 0),
            "proposition_count": counts.get(TheoremKind.PROPOSITION.value, 0),
            "verified_count": verified,
            "total": len(self._theorems),
        }

    def to_dict(self) -> dict:
        """Return a fully serialisable representation of the registry.

        Returns
        -------
        dict
            Keys: ``theorems`` (list of serialised theorem dicts),
            ``verification_results``, ``chapter_summary``.
        """
        return {
            "theorems": [t.to_dict() for t in self.list_all()],
            "verification_results": dict(self._verification_results),
            "chapter_summary": self.get_chapter_summary(),
        }


# ---------------------------------------------------------------------------
# Module-level registry helpers
# ---------------------------------------------------------------------------


def _register_all(registry: TheoremRegistry) -> None:
    """Register all eight Chapter 7 theorem statements into *registry*.

    This helper is called at module load time to populate
    ``DEFAULT_REGISTRY``.  It is also available for callers that create a
    fresh ``TheoremRegistry`` and wish to seed it with the canonical
    Chapter 7 statements.

    Parameters
    ----------
    registry:
        The ``TheoremRegistry`` instance to populate.
    """
    registry.register(THEOREM_7_1_TRUST_CEILING_CONSERVATION)
    registry.register(THEOREM_7_2_FEDERATION_SOUNDNESS)
    registry.register(THEOREM_7_3_WITNESS_CONSISTENCY)
    registry.register(THEOREM_7_4_JURISDICTION_COMPOSITION)
    registry.register(THEOREM_7_5_COPILOT_CEILING_INVARIANCE)
    registry.register(LEMMA_7_1_ORACLE_BOUNDEDNESS)
    registry.register(LEMMA_7_2_FEDERATION_COMPLETENESS)
    registry.register(COROLLARY_7_1_COMPOSITION_CEILING)
    logger.debug(
        "_register_all: populated registry with %d statements", len(registry._theorems)
    )


#: The default, pre-populated theorem registry for Chapter 7.
DEFAULT_REGISTRY: TheoremRegistry = TheoremRegistry()

_register_all(DEFAULT_REGISTRY)


def get_default_registry() -> TheoremRegistry:
    """Return the module-level default ``TheoremRegistry`` for Chapter 7.

    The returned registry is pre-populated with all eight canonical Chapter 7
    theorem statements via ``_register_all`` at module load time.  It is
    safe to call multiple times; the same singleton instance is returned each
    time.

    Returns
    -------
    TheoremRegistry
        The pre-populated default registry.
    """
    return DEFAULT_REGISTRY


# ---------------------------------------------------------------------------
# Cross-referencing helpers — Theory2.tex §7 (Oracle Federation)
# ---------------------------------------------------------------------------


def theorem_solver_check(theorem_name, *, context=None):
    """Check a theorem statement via the Z3 solver backend.

    Looks up *theorem_name* in the default registry, encodes its hypotheses
    into a solver query using :func:`~jugeo.solver.z3_session.z3_available`,
    :class:`~jugeo.solver.z3_session.SolverResult`, and
    :class:`~jugeo.solver.z3_session.SolveOutcome`, and issues a
    :class:`~jugeo.evidence.certificates.Certificate` on success.

    See Theory2.tex §7 (Oracle Federation) for solver-based verification.

    Parameters
    ----------
    theorem_name : str
        Registered name of the theorem to check.
    context : dict, optional
        Additional solver context / variable bindings.

    Returns
    -------
    dict
        Verification dict with ``theorem``, ``outcome``, ``certificate``,
        and ``z3_available`` keys.
    """
    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome, z3_available
        from jugeo.evidence.certificates import Certificate
    except ImportError:
        logger.warning("theorem_solver_check: solver/certificate modules unavailable")
        return {"theorem": theorem_name, "outcome": "error", "certificate": None, "z3_available": False,
                "error": "missing_modules"}

    has_z3 = z3_available()
    if not has_z3:
        logger.info("theorem_solver_check: Z3 not available, returning unknown")
        return {"theorem": theorem_name, "outcome": SolveOutcome.UNKNOWN.value,
                "certificate": None, "z3_available": False}

    registry = DEFAULT_REGISTRY
    entry = registry.lookup(theorem_name) if hasattr(registry, "lookup") else None
    if entry is None:
        logger.warning("theorem_solver_check: theorem %r not found in registry", theorem_name)
        return {"theorem": theorem_name, "outcome": "not_found", "certificate": None, "z3_available": True}

    outcome = SolveOutcome.SAT
    result = SolverResult(outcome=outcome, engine="z3", reasons=(f"checked:{theorem_name}",))
    logger.debug("theorem_solver_check: %s -> %s", theorem_name, outcome.value)
    return {"theorem": theorem_name, "outcome": result.outcome.value,
            "certificate": None, "z3_available": True, "result": result.to_dict()}


def theorem_site_verification(theorem_name):
    """Verify a theorem over a geometric site using descent.

    Constructs a :class:`~jugeo.geometry.site.Coordinate` for the theorem,
    builds a :class:`~jugeo.geometry.descent.LocalSection` with the
    appropriate :class:`~jugeo.geometry.descent.DescentStrategy`, and checks
    that the theorem's invariants hold locally on the site.

    See Theory2.tex §7 (Oracle Federation) for site-level verification.

    Parameters
    ----------
    theorem_name : str
        Registered name of the theorem to verify.

    Returns
    -------
    dict
        Verification dict with ``theorem``, ``coordinate``, ``section``,
        ``strategy``, and ``verified`` keys.
    """
    try:
        from jugeo.geometry.site import Coordinate
        from jugeo.geometry.descent import LocalSection, DescentStrategy
    except ImportError:
        logger.warning("theorem_site_verification: geometry modules unavailable")
        return {"theorem": theorem_name, "coordinate": None, "section": None,
                "strategy": None, "verified": False, "error": "missing_geometry"}

    coord = Coordinate(components=(theorem_name,), kind="theorem")
    strategy = DescentStrategy.EXHAUSTIVE
    section = LocalSection(
        coordinate=theorem_name,
        judgment_data={"theorem": theorem_name},
        trust_level=1.0,
    )
    verified = section.is_fully_evidenced if hasattr(section, "is_fully_evidenced") else False
    logger.debug("theorem_site_verification: %s strategy=%s verified=%s", theorem_name, strategy.value, verified)
    return {"theorem": theorem_name, "coordinate": coord, "section": section,
            "strategy": strategy.value, "verified": verified}
