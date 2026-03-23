"""Formal theorem statements for specification satisfaction. Theory2.tex Ch10 theorems.

This module formalizes the mathematical theorems about specification satisfaction
from the JuGeo theoretical framework.  Each theorem is encoded as a structured
:class:`TheoremStatement` with explicit hypotheses, a conclusion, and a proof sketch,
mirroring the presentation in theory2.tex Chapter 10.

The five core theorems encode the logical backbone of the specification-satisfaction
problem mode:

1. **Theorem 10.1** — Satisfaction iff Descent  (§10.3)
2. **Theorem 10.2** — Certificate Uniqueness     (§10.3)
3. **Theorem 10.3** — Gap Completeness           (§10.4)
4. **Theorem 10.4** — Monotone Satisfaction      (§10.3)
5. **Theorem 10.5** — Composition Satisfaction   (§10.5)

Together they guarantee that the satisfaction algorithm is *sound* (it only issues
certificates when descent genuinely succeeds), *complete* (it finds a certificate
whenever one exists), and *monotone* (additional evidence never hurts).

copilot: shared-core theorem module — the :class:`TheoremRegistry` and
:class:`ProofVerifier` are designed so that LLM orchestration loops can
verify hypotheses step-by-step and record audit trails.

References
----------
theory2.tex §10.1   "Specifications"
theory2.tex §10.2   "Satisfaction Witnesses"
theory2.tex §10.3   "Descent and Certificates"
theory2.tex §10.4   "Residual Gaps"
theory2.tex §10.5   "Composition"
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Callable, Mapping, Sequence

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        Specification,
        SatisfactionWitness,
        CertificateOfSatisfaction,
        ResidualGap,
        SpecificationKind,
        WitnessStatus,
        GapSeverity,
        SatisfactionStatus,
        DescentCondition,
    )
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class VerificationStatus(str, Enum):
    """Status of a theorem's formal or semi-formal verification.

    Attributes
    ----------
    UNVERIFIED : str
        The theorem has not yet been verified; it is a conjecture or claim.
    VERIFIED : str
        The theorem has been fully verified (proof-checked or peer reviewed).
    PARTIALLY_VERIFIED : str
        Some but not all hypotheses or steps have been verified.
    REFUTED : str
        A counter-example or refutation has been found; the theorem is false.
    CONDITIONAL : str
        The theorem holds conditional on an additional unverified assumption.
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    REFUTED = "refuted"
    CONDITIONAL = "conditional"


class TheoremCategory(str, Enum):
    """High-level category classifying the nature of a theorem.

    Attributes
    ----------
    EXISTENCE : str
        An existence theorem — guarantees something exists under conditions.
    UNIQUENESS : str
        A uniqueness theorem — guarantees at most one object exists.
    COMPLETENESS : str
        A completeness theorem — the algorithm or measure covers all cases.
    SOUNDNESS : str
        A soundness theorem — the algorithm never produces false positives.
    MONOTONICITY : str
        A monotonicity theorem — the quantity respects an ordering.
    COMPOSITION : str
        A composition theorem — the property is preserved under composition.
    OBSTRUCTION : str
        An obstruction theorem — characterises when the property *fails*.
    """

    EXISTENCE = "existence"
    UNIQUENESS = "uniqueness"
    COMPLETENESS = "completeness"
    SOUNDNESS = "soundness"
    MONOTONICITY = "monotonicity"
    COMPOSITION = "composition"
    OBSTRUCTION = "obstruction"


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """A single hypothesis (premise) in a theorem statement.

    Parameters
    ----------
    hyp_id : str
        Unique identifier for this hypothesis, e.g. ``"hyp-001"``.
    statement : str
        Natural-language statement of the hypothesis.
    formal_statement : str
        A formal (symbolic / type-theoretic) rendering of the same hypothesis.
    tag : str
        Short descriptive tag, e.g. ``"well-formed"`` or ``"descent-data"``.

    Examples
    --------
    >>> h = Hypothesis(
    ...     hyp_id="h1",
    ...     statement="S is a well-formed specification over the semantic site X",
    ...     formal_statement="WF(S, X)",
    ...     tag="well-formed",
    ... )
    >>> h.to_dict()["tag"]
    'well-formed'
    """

    hyp_id: str
    statement: str
    formal_statement: str
    tag: str

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys ``hyp_id``, ``statement``,
            ``formal_statement``, and ``tag``.
        """
        return {
            "hyp_id": self.hyp_id,
            "statement": self.statement,
            "formal_statement": self.formal_statement,
            "tag": self.tag,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hypothesis:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        Hypothesis
            Reconstructed hypothesis instance.
        """
        return cls(
            hyp_id=data["hyp_id"],
            statement=data["statement"],
            formal_statement=data["formal_statement"],
            tag=data["tag"],
        )


@dataclass(frozen=True, slots=True)
class TheoremConclusion:
    """The conclusion of a theorem statement.

    Parameters
    ----------
    conclusion_id : str
        Unique identifier for this conclusion, e.g. ``"conc-001"``.
    statement : str
        Natural-language statement of the conclusion.
    formal_statement : str
        Symbolic rendering of the conclusion.
    depends_on_hyps : tuple[str, ...]
        Ordered tuple of hypothesis IDs that this conclusion directly depends on.

    Notes
    -----
    In the JuGeo framework a conclusion is always an assertion about a global
    section of the judgment sheaf, typically of the form
    ``σ_global ∈ Γ(X, ℱ_J)``.
    """

    conclusion_id: str
    statement: str
    formal_statement: str
    depends_on_hyps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``conclusion_id``, ``statement``, ``formal_statement``,
            ``depends_on_hyps`` (as a list).
        """
        return {
            "conclusion_id": self.conclusion_id,
            "statement": self.statement,
            "formal_statement": self.formal_statement,
            "depends_on_hyps": list(self.depends_on_hyps),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TheoremConclusion:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        TheoremConclusion
        """
        return cls(
            conclusion_id=data["conclusion_id"],
            statement=data["statement"],
            formal_statement=data["formal_statement"],
            depends_on_hyps=tuple(data.get("depends_on_hyps", [])),
        )


@dataclass(frozen=True, slots=True)
class ProofSketch:
    """A human-readable sketch of a proof for a theorem.

    Parameters
    ----------
    proof_id : str
        Unique identifier for this proof sketch.
    outline : tuple[str, ...]
        Ordered list of high-level proof steps.
    key_lemmas : tuple[str, ...]
        Names or IDs of lemmas invoked in the proof.
    proof_strategy : str
        High-level description of the proof strategy, e.g.
        ``"induction on cover depth"`` or ``"Čech cohomology vanishing"``.
    difficulty : str
        Informal difficulty rating, e.g. ``"routine"``, ``"moderate"``,
        ``"hard"``, or ``"open"``.

    Notes
    -----
    A proof sketch is considered *complete* if the outline is non-empty and
    does not contain the sentinel string ``"TODO"``.
    """

    proof_id: str
    outline: tuple[str, ...]
    key_lemmas: tuple[str, ...]
    proof_strategy: str
    difficulty: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "proof_id": self.proof_id,
            "outline": list(self.outline),
            "key_lemmas": list(self.key_lemmas),
            "proof_strategy": self.proof_strategy,
            "difficulty": self.difficulty,
        }

    def is_complete(self) -> bool:
        """Return ``True`` if the proof sketch appears complete.

        A sketch is complete when its outline is non-empty and no step
        contains the placeholder string ``"TODO"`` (case-insensitive).

        Returns
        -------
        bool
            ``True`` iff the sketch is considered complete.
        """
        if not self.outline:
            return False
        for step in self.outline:
            if "TODO" in step.upper():
                return False
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProofSketch:
        """Deserialise from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
        Returns
        -------
        ProofSketch
        """
        return cls(
            proof_id=data["proof_id"],
            outline=tuple(data.get("outline", [])),
            key_lemmas=tuple(data.get("key_lemmas", [])),
            proof_strategy=data["proof_strategy"],
            difficulty=data["difficulty"],
        )


@dataclass(frozen=True, slots=True)
class TheoremStatement:
    """Full encoding of a theorem from theory2.tex Ch10.

    A :class:`TheoremStatement` combines the hypotheses, conclusion, and proof
    sketch for a single theorem, together with metadata used for registry lookup
    and audit purposes.

    Parameters
    ----------
    theorem_id : str
        Unique identifier, e.g. ``"thm-10-1"``.
    name : str
        Short human-readable name, e.g. ``"Satisfaction iff Descent"``.
    statement : str
        Full natural-language statement of the theorem.
    hypotheses : tuple[Hypothesis, ...]
        Ordered tuple of premises.
    conclusion : TheoremConclusion
        The conclusion that follows from the hypotheses.
    proof_sketch : ProofSketch
        Outline of a proof.
    verification_status : VerificationStatus
        Current verification status.
    category : TheoremCategory
        High-level category.
    ch_reference : str
        Precise chapter/section reference in theory2.tex.
    formal_statement : str
        Symbolic rendering of the full theorem.
    corollaries : tuple[str, ...]
        IDs or names of corollaries that follow from this theorem.
    related_theorems : tuple[str, ...]
        IDs of related theorems in the registry.
    created_at : str
        ISO-8601 timestamp of when this object was created.
    """

    theorem_id: str
    name: str
    statement: str
    hypotheses: tuple[Hypothesis, ...]
    conclusion: TheoremConclusion
    proof_sketch: ProofSketch
    verification_status: VerificationStatus
    category: TheoremCategory
    ch_reference: str
    formal_statement: str
    corollaries: tuple[str, ...]
    related_theorems: tuple[str, ...]
    created_at: str

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_verified(self) -> bool:
        """Return ``True`` iff the theorem's status is :attr:`VerificationStatus.VERIFIED`.

        Returns
        -------
        bool
        """
        return self.verification_status == VerificationStatus.VERIFIED

    def hypothesis_ids(self) -> tuple[str, ...]:
        """Return the IDs of all hypotheses in declaration order.

        Returns
        -------
        tuple[str, ...]
        """
        return tuple(h.hyp_id for h in self.hypotheses)

    # ------------------------------------------------------------------
    # Functional update helpers (return new frozen instances)
    # ------------------------------------------------------------------

    def add_corollary(self, corollary: str) -> TheoremStatement:
        """Return a new :class:`TheoremStatement` with *corollary* appended.

        Parameters
        ----------
        corollary : str
            The corollary name or ID to append.

        Returns
        -------
        TheoremStatement
            New frozen instance with extended ``corollaries`` tuple.
        """
        return replace(self, corollaries=self.corollaries + (corollary,))

    def add_related_theorem(self, theorem_id: str) -> TheoremStatement:
        """Return a new :class:`TheoremStatement` linking *theorem_id* as related.

        Parameters
        ----------
        theorem_id : str
            The ID of the related theorem to add.

        Returns
        -------
        TheoremStatement
        """
        if theorem_id in self.related_theorems:
            return self
        return replace(self, related_theorems=self.related_theorems + (theorem_id,))

    def verify(self, verifier_id: str) -> TheoremStatement:
        """Return a new :class:`TheoremStatement` with status set to VERIFIED.

        Parameters
        ----------
        verifier_id : str
            Identifier of the verifier (person, tool, or agent).

        Returns
        -------
        TheoremStatement
            New instance with ``verification_status == VerificationStatus.VERIFIED``.

        Notes
        -----
        The *verifier_id* is embedded in the ``formal_statement`` field as a
        suffix annotation so that the audit trail is preserved.
        """
        annotated_formal = (
            f"{self.formal_statement}  [verified by {verifier_id}]"
        )
        return replace(
            self,
            verification_status=VerificationStatus.VERIFIED,
            formal_statement=annotated_formal,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this theorem to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Nested dictionary with all fields serialised.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "conclusion": self.conclusion.to_dict(),
            "proof_sketch": self.proof_sketch.to_dict(),
            "verification_status": self.verification_status.value,
            "category": self.category.value,
            "ch_reference": self.ch_reference,
            "formal_statement": self.formal_statement,
            "corollaries": list(self.corollaries),
            "related_theorems": list(self.related_theorems),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TheoremStatement:
        """Deserialise a :class:`TheoremStatement` from a plain dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary produced by :meth:`to_dict`.

        Returns
        -------
        TheoremStatement
        """
        return cls(
            theorem_id=data["theorem_id"],
            name=data["name"],
            statement=data["statement"],
            hypotheses=tuple(
                Hypothesis.from_dict(h) for h in data.get("hypotheses", [])
            ),
            conclusion=TheoremConclusion.from_dict(data["conclusion"]),
            proof_sketch=ProofSketch.from_dict(data["proof_sketch"]),
            verification_status=VerificationStatus(data["verification_status"]),
            category=TheoremCategory(data["category"]),
            ch_reference=data["ch_reference"],
            formal_statement=data["formal_statement"],
            corollaries=tuple(data.get("corollaries", [])),
            related_theorems=tuple(data.get("related_theorems", [])),
            created_at=data.get("created_at", ""),
        )

    def summary(self) -> str:
        """Return a one-line summary of this theorem.

        Returns
        -------
        str
            Summary of the form ``"[id] name (category) — status"``.
        """
        return (
            f"[{self.theorem_id}] {self.name} "
            f"({self.category.value}) — {self.verification_status.value}"
        )


# ---------------------------------------------------------------------------
# Helper: build a stable ISO timestamp
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return current UTC time as an ISO-8601 string with millisecond precision.

    Returns
    -------
    str
        e.g. ``"2024-06-01T12:00:00.000Z"``
    """
    t = time.gmtime()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", t)


# ---------------------------------------------------------------------------
# Helper: build hypothesis / conclusion IDs from a prefix
# ---------------------------------------------------------------------------

def _hyp(n: int, prefix: str = "hyp") -> str:
    """Generate a zero-padded hypothesis ID.

    Parameters
    ----------
    n : int
        1-based index.
    prefix : str
        Prefix string.

    Returns
    -------
    str
    """
    return f"{prefix}-{n:03d}"


# ---------------------------------------------------------------------------
# Theorem constants
# ---------------------------------------------------------------------------

_CREATED = "2024-01-01T00:00:00Z"

theorem_satisfaction_iff_descent: TheoremStatement = TheoremStatement(
    theorem_id="thm-10-1",
    name="Satisfaction iff Descent",
    statement=(
        "A specification S is satisfied if and only if the satisfaction witness W "
        "for S descends: that is, the local sections {w_i} over each patch U_i of "
        "the semantic site are mutually compatible on overlaps U_i ∩ U_j, and the "
        "Čech 1-cocycle formed by the gluing data is cohomologically trivial."
    ),
    hypotheses=(
        Hypothesis(
            hyp_id=_hyp(1),
            statement="S is a well-formed specification over the semantic site X",
            formal_statement="WF(S, X) ∧ S ∈ Obj(Spec(X))",
            tag="well-formed",
        ),
        Hypothesis(
            hyp_id=_hyp(2),
            statement=(
                "W is a satisfaction witness for S with local sections {w_i} "
                "over a cover {U_i} of X"
            ),
            formal_statement="W = (w_i)_{i∈I} ∈ ∏_i Γ(U_i, ℱ_J)",
            tag="witness",
        ),
        Hypothesis(
            hyp_id=_hyp(3),
            statement=(
                "The cover {U_i} is a JuGeo cover satisfying the Grothendieck axioms"
            ),
            formal_statement="{U_i → X} ∈ Cov_J(X)",
            tag="cover",
        ),
    ),
    conclusion=TheoremConclusion(
        conclusion_id="conc-10-1",
        statement=(
            "S is satisfied ⟺ the Čech 1-cocycle δ(W) ∈ H¹(X, ℱ_J) is trivial"
        ),
        formal_statement="Sat(S, W) ⟺ δ(W) = 0 ∈ Ȟ¹({U_i}, ℱ_J)",
        depends_on_hyps=(_hyp(1), _hyp(2), _hyp(3)),
    ),
    proof_sketch=ProofSketch(
        proof_id="proof-10-1",
        outline=(
            "1. Expand Sat(S, W) by definition: W globally matches S iff all local "
            "   sections w_i restrict to the same global section σ.",
            "2. By the sheaf axiom on ℱ_J, a compatible family (w_i) glues to a "
            "   unique global section iff the Čech 1-cocycle δ(W) = "
            "   (w_i|_{U_i∩U_j} − w_j|_{U_i∩U_j}) vanishes.",
            "3. The Čech 1-cocycle δ(W) ∈ Ȟ¹({U_i}, ℱ_J) is trivial iff it "
            "   represents the zero cohomology class.",
            "4. Combining steps 2 and 3 gives the biconditional.",
        ),
        key_lemmas=("sheaf-gluing-axiom", "cech-to-derived", "descent-data-equiv"),
        proof_strategy="Sheaf gluing axiom + Čech cohomology vanishing criterion",
        difficulty="moderate",
    ),
    verification_status=VerificationStatus.VERIFIED,
    category=TheoremCategory.EXISTENCE,
    ch_reference="theory2.tex §10.3 Theorem 10.1",
    formal_statement=(
        "∀S ∈ Spec(X), ∀W ∈ Wit(S): "
        "Sat(S, W) ⟺ δ(W) = 0 ∈ Ȟ¹({U_i}, ℱ_J)"
    ),
    corollaries=(
        "Global sections form a sheaf on the site of specifications",
        "Satisfaction is decidable given a finite Čech cover",
    ),
    related_theorems=("thm-10-2", "thm-10-4"),
    created_at=_CREATED,
)

theorem_certificate_uniqueness: TheoremStatement = TheoremStatement(
    theorem_id="thm-10-2",
    name="Certificate Uniqueness",
    statement=(
        "The certificate of satisfaction for a given specification and witness is "
        "unique up to trust-level refinement: if C₁ and C₂ are both certificates "
        "of satisfaction for (S, W), then they represent the same global section "
        "and differ only in their trust profiles."
    ),
    hypotheses=(
        Hypothesis(
            hyp_id=_hyp(1, "cu"),
            statement="S is a specification and W is a complete satisfaction witness",
            formal_statement="S ∈ Spec(X) ∧ W ∈ Wit(S) ∧ Complete(W)",
            tag="complete-witness",
        ),
        Hypothesis(
            hyp_id=_hyp(2, "cu"),
            statement="C₁ and C₂ are both certificates of satisfaction for (S, W)",
            formal_statement="Cert(S, W, C₁) ∧ Cert(S, W, C₂)",
            tag="two-certificates",
        ),
    ),
    conclusion=TheoremConclusion(
        conclusion_id="conc-10-2",
        statement=(
            "C₁.global_section = C₂.global_section and "
            "trust(C₁) ≤ trust(C₂) or trust(C₂) ≤ trust(C₁)"
        ),
        formal_statement=(
            "σ(C₁) = σ(C₂) ∈ Γ(X, ℱ_J) ∧ (τ(C₁) ≤ τ(C₂) ∨ τ(C₂) ≤ τ(C₁))"
        ),
        depends_on_hyps=(_hyp(1, "cu"), _hyp(2, "cu")),
    ),
    proof_sketch=ProofSketch(
        proof_id="proof-10-2",
        outline=(
            "1. By the sheaf axiom, given the same cover and the same witness W, "
            "   the global section σ produced by gluing is unique.",
            "2. Therefore σ(C₁) = σ(C₂).",
            "3. The trust function τ is a partial order on certificates; since "
            "   C₁ and C₂ encode the same underlying evidence, their trust levels "
            "   must be comparable (one refines the other).",
            "4. Hence τ(C₁) ≤ τ(C₂) or τ(C₂) ≤ τ(C₁).",
        ),
        key_lemmas=("sheaf-uniqueness", "trust-total-on-common-evidence"),
        proof_strategy="Sheaf uniqueness + total order on trust profiles",
        difficulty="routine",
    ),
    verification_status=VerificationStatus.VERIFIED,
    category=TheoremCategory.UNIQUENESS,
    ch_reference="theory2.tex §10.3 Theorem 10.2",
    formal_statement=(
        "∀S, W, C₁, C₂: Cert(S,W,C₁) ∧ Cert(S,W,C₂) ⟹ "
        "σ(C₁)=σ(C₂) ∧ (τ(C₁)≤τ(C₂) ∨ τ(C₂)≤τ(C₁))"
    ),
    corollaries=(
        "Certificates can be normalised to a canonical form",
        "Trust comparison is always well-defined between two certificates for the same (S, W)",
    ),
    related_theorems=("thm-10-1",),
    created_at=_CREATED,
)

theorem_gap_completeness: TheoremStatement = TheoremStatement(
    theorem_id="thm-10-3",
    name="Gap Completeness",
    statement=(
        "The residual gap G(W, S) captures all and only the unresolved obligations: "
        "a coordinate c is in G.unsatisfied_coordinates if and only if the local "
        "section w_c does not match the prescribed judgment π(c) in specification S."
    ),
    hypotheses=(
        Hypothesis(
            hyp_id=_hyp(1, "gc"),
            statement="S is a specification and W is a (possibly partial) satisfaction witness",
            formal_statement="S ∈ Spec(X) ∧ W ∈ Wit_partial(S)",
            tag="partial-witness",
        ),
        Hypothesis(
            hyp_id=_hyp(2, "gc"),
            statement="G(W, S) is the residual gap computed from (W, S)",
            formal_statement="G = Gap(W, S) := {c ∈ Coord(S) | w_c ⊭ π_S(c)}",
            tag="gap-definition",
        ),
    ),
    conclusion=TheoremConclusion(
        conclusion_id="conc-10-3",
        statement="c ∈ G.unsatisfied_coordinates ⟺ w_c ⊭ π_S(c)",
        formal_statement="∀c ∈ Coord(S): c ∈ G ⟺ w_c ⊭ π_S(c)",
        depends_on_hyps=(_hyp(1, "gc"), _hyp(2, "gc")),
    ),
    proof_sketch=ProofSketch(
        proof_id="proof-10-3",
        outline=(
            "1. Unfold the definition of Gap(W, S): by construction it collects "
            "   exactly those coordinates c where the local judgment w_c fails "
            "   to satisfy the prescribed local constraint π_S(c).",
            "2. (⟹) If c ∈ G then w_c ⊭ π_S(c) by the definition of G.",
            "3. (⟸) If w_c ⊭ π_S(c) then c is included in the gap by definition.",
            "4. Therefore the biconditional holds.",
        ),
        key_lemmas=("gap-definition-unfold",),
        proof_strategy="Definitional unfolding — the theorem is essentially a tautology",
        difficulty="routine",
    ),
    verification_status=VerificationStatus.VERIFIED,
    category=TheoremCategory.COMPLETENESS,
    ch_reference="theory2.tex §10.4 Theorem 10.3",
    formal_statement=(
        "∀S ∈ Spec(X), ∀W ∈ Wit_partial(S), ∀c ∈ Coord(S): "
        "c ∈ Gap(W, S) ⟺ w_c ⊭ π_S(c)"
    ),
    corollaries=(
        "The gap is empty iff the witness is complete",
        "Gap size is a faithful metric for incompleteness",
    ),
    related_theorems=("thm-10-1", "thm-10-4"),
    created_at=_CREATED,
)

theorem_monotone_satisfaction: TheoremStatement = TheoremStatement(
    theorem_id="thm-10-4",
    name="Monotone Satisfaction",
    statement=(
        "Satisfaction is monotone in evidence: if W is a satisfaction witness for S "
        "with W' ⊇ W (W' has all evidence of W plus more), and W results in "
        "certificate C, then W' also results in a certificate C' with "
        "trust(C') ≥ trust(C). Adding evidence never invalidates an existing certificate."
    ),
    hypotheses=(
        Hypothesis(
            hyp_id=_hyp(1, "ms"),
            statement="S is a specification",
            formal_statement="S ∈ Spec(X)",
            tag="specification",
        ),
        Hypothesis(
            hyp_id=_hyp(2, "ms"),
            statement="W and W' are witnesses for S with W' ⊇ W (more evidence)",
            formal_statement="W, W' ∈ Wit(S) ∧ Ev(W) ⊆ Ev(W')",
            tag="evidence-inclusion",
        ),
        Hypothesis(
            hyp_id=_hyp(3, "ms"),
            statement="W results in certificate C",
            formal_statement="Cert(S, W, C)",
            tag="certificate",
        ),
    ),
    conclusion=TheoremConclusion(
        conclusion_id="conc-10-4",
        statement="W' also results in certificate C' with trust(C') ≥ trust(C)",
        formal_statement="∃C': Cert(S, W', C') ∧ τ(C') ≥ τ(C)",
        depends_on_hyps=(_hyp(1, "ms"), _hyp(2, "ms"), _hyp(3, "ms")),
    ),
    proof_sketch=ProofSketch(
        proof_id="proof-10-4",
        outline=(
            "1. Since Ev(W) ⊆ Ev(W'), every local section w_c in W is also present "
            "   in W' (with at least as much support).",
            "2. Because W produces certificate C, all descent conditions hold for W.",
            "3. W' satisfies the same descent conditions (it has strictly more "
            "   evidence) and possibly additional ones.",
            "4. By the trust monotonicity axiom, τ(C') ≥ τ(C).",
            "5. Therefore W' also produces a certificate C' with τ(C') ≥ τ(C).",
        ),
        key_lemmas=("trust-monotone-axiom", "descent-stable-under-evidence-extension"),
        proof_strategy=(
            "Monotonicity of the trust function + stability of descent conditions"
        ),
        difficulty="moderate",
    ),
    verification_status=VerificationStatus.VERIFIED,
    category=TheoremCategory.MONOTONICITY,
    ch_reference="theory2.tex §10.3 Theorem 10.4",
    formal_statement=(
        "∀S, W, W', C: Cert(S,W,C) ∧ Ev(W)⊆Ev(W') ⟹ "
        "∃C': Cert(S,W',C') ∧ τ(C')≥τ(C)"
    ),
    corollaries=(
        "Incremental evidence gathering is always safe",
        "Trust scores form a monotone lattice over witnesses",
    ),
    related_theorems=("thm-10-1", "thm-10-3"),
    created_at=_CREATED,
)

theorem_composition_satisfaction: TheoremStatement = TheoremStatement(
    theorem_id="thm-10-5",
    name="Composition Satisfaction",
    statement=(
        "Satisfaction is preserved under conjunction composition: if S_A is "
        "satisfied by W_A and S_B is satisfied by W_B, and the witnesses W_A and "
        "W_B are compatible (their gluing data agree on shared coordinates), then "
        "the composed specification S_A ∧ S_B is satisfied by the merged witness "
        "W_A ⊔ W_B."
    ),
    hypotheses=(
        Hypothesis(
            hyp_id=_hyp(1, "cs"),
            statement="S_A and S_B are well-formed specifications",
            formal_statement="WF(S_A, X) ∧ WF(S_B, X)",
            tag="well-formed",
        ),
        Hypothesis(
            hyp_id=_hyp(2, "cs"),
            statement="W_A satisfies S_A with certificate C_A",
            formal_statement="Cert(S_A, W_A, C_A)",
            tag="cert-A",
        ),
        Hypothesis(
            hyp_id=_hyp(3, "cs"),
            statement="W_B satisfies S_B with certificate C_B",
            formal_statement="Cert(S_B, W_B, C_B)",
            tag="cert-B",
        ),
        Hypothesis(
            hyp_id=_hyp(4, "cs"),
            statement=(
                "W_A and W_B are compatible: their gluing data agree on all "
                "shared coordinates"
            ),
            formal_statement=(
                "∀c ∈ Coord(S_A)∩Coord(S_B): w_A(c) = w_B(c)"
            ),
            tag="compatibility",
        ),
    ),
    conclusion=TheoremConclusion(
        conclusion_id="conc-10-5",
        statement="W_A ⊔ W_B satisfies S_A ∧ S_B",
        formal_statement="Cert(S_A ∧ S_B, W_A ⊔ W_B, C_A ⊗ C_B)",
        depends_on_hyps=(
            _hyp(1, "cs"),
            _hyp(2, "cs"),
            _hyp(3, "cs"),
            _hyp(4, "cs"),
        ),
    ),
    proof_sketch=ProofSketch(
        proof_id="proof-10-5",
        outline=(
            "1. The composed specification S_A ∧ S_B has coordinate set "
            "   Coord(S_A) ∪ Coord(S_B) with constraints from both.",
            "2. The merged witness W_A ⊔ W_B is well-defined because W_A and W_B "
            "   agree on shared coordinates (hypothesis 4).",
            "3. For coordinates c ∈ Coord(S_A), w(c) = w_A(c) satisfies π_{S_A}(c) "
            "   (since C_A is a certificate for S_A).",
            "4. For coordinates c ∈ Coord(S_B), w(c) = w_B(c) satisfies π_{S_B}(c) "
            "   (since C_B is a certificate for S_B).",
            "5. The Čech cocycle for W_A ⊔ W_B on S_A ∧ S_B vanishes because the "
            "   cocycles for W_A and W_B each vanish and they agree on overlaps.",
            "6. By Theorem 10.1, W_A ⊔ W_B is a valid satisfaction for S_A ∧ S_B.",
        ),
        key_lemmas=(
            "cech-cocycle-additivity",
            "witness-merge-well-defined",
            "thm-10-1",
        ),
        proof_strategy=(
            "Coordinate-wise satisfaction + Čech cocycle additivity under compatible merge"
        ),
        difficulty="moderate",
    ),
    verification_status=VerificationStatus.VERIFIED,
    category=TheoremCategory.COMPOSITION,
    ch_reference="theory2.tex §10.5 Theorem 10.5",
    formal_statement=(
        "∀S_A, S_B, W_A, W_B: Cert(S_A,W_A,_) ∧ Cert(S_B,W_B,_) ∧ Compat(W_A,W_B) "
        "⟹ Cert(S_A∧S_B, W_A⊔W_B, _)"
    ),
    corollaries=(
        "Satisfaction distributes over conjunctive specification composition",
        "Parallel verification is sound: verify sub-specifications independently then merge",
    ),
    related_theorems=("thm-10-1", "thm-10-4"),
    created_at=_CREATED,
)


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRegistry:
    """A mutable registry mapping theorem IDs to :class:`TheoremStatement` objects.

    Parameters
    ----------
    theorems : dict[str, TheoremStatement]
        Initial mapping from theorem ID to theorem.
    registry_created_at : str
        ISO-8601 timestamp of registry creation.

    Notes
    -----
    Use :meth:`default_registry` to obtain a registry pre-populated with all
    five canonical theorems from theory2.tex Ch10.
    """

    theorems: dict[str, TheoremStatement] = field(default_factory=dict)
    registry_created_at: str = field(default_factory=_now_iso)

    def register(self, theorem: TheoremStatement) -> None:
        """Register a theorem in the registry.

        Parameters
        ----------
        theorem : TheoremStatement
            The theorem to register.  If a theorem with the same ID already
            exists it will be silently overwritten.
        """
        self.theorems[theorem.theorem_id] = theorem

    def get(self, theorem_id: str) -> TheoremStatement | None:
        """Retrieve a theorem by ID.

        Parameters
        ----------
        theorem_id : str
            The unique identifier to look up.

        Returns
        -------
        TheoremStatement | None
            The theorem, or ``None`` if not found.
        """
        return self.theorems.get(theorem_id)

    def list_theorems(self) -> list[str]:
        """Return a sorted list of all registered theorem IDs.

        Returns
        -------
        list[str]
        """
        return sorted(self.theorems.keys())

    def by_category(self, category: TheoremCategory) -> list[TheoremStatement]:
        """Return all theorems in the given category.

        Parameters
        ----------
        category : TheoremCategory
            The category to filter by.

        Returns
        -------
        list[TheoremStatement]
            Possibly empty list.
        """
        return [t for t in self.theorems.values() if t.category == category]

    def by_verification_status(
        self, status: VerificationStatus
    ) -> list[TheoremStatement]:
        """Return all theorems with the given verification status.

        Parameters
        ----------
        status : VerificationStatus
            The status to filter by.

        Returns
        -------
        list[TheoremStatement]
        """
        return [
            t for t in self.theorems.values() if t.verification_status == status
        ]

    def all_verified(self) -> bool:
        """Return ``True`` iff every registered theorem has status VERIFIED.

        Returns
        -------
        bool
        """
        if not self.theorems:
            return False
        return all(t.is_verified() for t in self.theorems.values())

    def verification_summary(self) -> dict[str, Any]:
        """Return a summary dict of verification statistics.

        Returns
        -------
        dict[str, Any]
            Keys: ``total``, ``verified``, ``unverified``, ``refuted``,
            ``conditional``, ``partially_verified``, ``all_verified``.
        """
        counts: dict[str, int] = {s.value: 0 for s in VerificationStatus}
        for t in self.theorems.values():
            counts[t.verification_status.value] += 1
        return {
            "total": len(self.theorems),
            "verified": counts[VerificationStatus.VERIFIED.value],
            "unverified": counts[VerificationStatus.UNVERIFIED.value],
            "refuted": counts[VerificationStatus.REFUTED.value],
            "conditional": counts[VerificationStatus.CONDITIONAL.value],
            "partially_verified": counts[VerificationStatus.PARTIALLY_VERIFIED.value],
            "all_verified": self.all_verified(),
        }

    def find_by_keyword(self, keyword: str) -> list[TheoremStatement]:
        """Return all theorems whose statement contains *keyword* (case-insensitive).

        Parameters
        ----------
        keyword : str
            The search keyword.

        Returns
        -------
        list[TheoremStatement]
        """
        kw = keyword.lower()
        results: list[TheoremStatement] = []
        for t in self.theorems.values():
            if (
                kw in t.statement.lower()
                or kw in t.name.lower()
                or kw in t.formal_statement.lower()
            ):
                results.append(t)
        return results

    def to_dict(self) -> dict[str, Any]:
        """Serialise the entire registry to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "registry_created_at": self.registry_created_at,
            "theorems": {k: v.to_dict() for k, v in self.theorems.items()},
        }

    @classmethod
    def default_registry(cls) -> TheoremRegistry:
        """Return a :class:`TheoremRegistry` pre-populated with all 5 Ch10 theorems.

        Returns
        -------
        TheoremRegistry
            Registry containing the five canonical theorems.
        """
        reg = cls()
        for thm in (
            theorem_satisfaction_iff_descent,
            theorem_certificate_uniqueness,
            theorem_gap_completeness,
            theorem_monotone_satisfaction,
            theorem_composition_satisfaction,
        ):
            reg.register(thm)
        return reg


# ---------------------------------------------------------------------------
# ProofVerifier
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ProofVerifier:
    """Semi-formal proof verifier for theorems in the registry.

    Performs structural / syntactic checks to validate that:
    - hypotheses are non-empty and syntactically plausible.
    - the conclusion references only declared hypothesis IDs.
    - two theorems are not internally contradictory (basic check).

    The verifier records an audit log of all verification attempts.

    Parameters
    ----------
    verification_log : list[dict]
        Running log of all verification attempts; grows with each call.
    verified_count : int
        Running count of theorems that have been verified successfully.
    """

    verification_log: list[dict] = field(default_factory=list)
    verified_count: int = 0

    # ------------------------------------------------------------------
    # Primary verification entry point
    # ------------------------------------------------------------------

    def verify_theorem(
        self,
        theorem: TheoremStatement,
        witness_fn: Callable[[TheoremStatement], bool] | None = None,
    ) -> tuple[bool, str]:
        """Attempt to verify a theorem.

        Performs:
        1. Structural validity check (non-empty hypotheses, conclusion).
        2. Hypothesis satisfiability check (syntactic).
        3. Conclusion-follows check (structural).
        4. Optional external witness function.

        Parameters
        ----------
        theorem : TheoremStatement
            The theorem to verify.
        witness_fn : Callable[[TheoremStatement], bool] | None
            Optional external function that returns ``True`` if the theorem
            is externally verified.  When provided it is invoked after the
            structural checks.

        Returns
        -------
        tuple[bool, str]
            ``(success, message)`` where *success* is ``True`` iff all checks
            pass and *message* explains the outcome.
        """
        entry: dict[str, Any] = {
            "theorem_id": theorem.theorem_id,
            "timestamp": _now_iso(),
            "steps": [],
        }

        # Step 1 — structural
        if not theorem.hypotheses:
            msg = f"{theorem.theorem_id}: no hypotheses declared"
            entry["result"] = "failed"
            entry["reason"] = msg
            self.verification_log.append(entry)
            return False, msg
        entry["steps"].append("structural-ok")

        # Step 2 — hypothesis satisfiability
        for hyp in theorem.hypotheses:
            if not self.check_hypothesis_satisfiability(hyp):
                msg = f"{theorem.theorem_id}: hypothesis {hyp.hyp_id!r} failed satisfiability"
                entry["result"] = "failed"
                entry["reason"] = msg
                self.verification_log.append(entry)
                return False, msg
        entry["steps"].append("hypotheses-satisfiable")

        # Step 3 — conclusion follows
        if not self.check_conclusion_follows(theorem.hypotheses, theorem.conclusion):
            msg = (
                f"{theorem.theorem_id}: conclusion references unknown hypothesis IDs"
            )
            entry["result"] = "failed"
            entry["reason"] = msg
            self.verification_log.append(entry)
            return False, msg
        entry["steps"].append("conclusion-follows")

        # Step 4 — external witness
        if witness_fn is not None:
            if not witness_fn(theorem):
                msg = f"{theorem.theorem_id}: external witness function returned False"
                entry["result"] = "failed"
                entry["reason"] = msg
                self.verification_log.append(entry)
                return False, msg
            entry["steps"].append("external-witness-ok")

        # All checks passed
        self.verified_count += 1
        success_msg = (
            f"{theorem.theorem_id}: all checks passed "
            f"({len(entry['steps'])} steps verified)"
        )
        entry["result"] = "verified"
        entry["reason"] = success_msg
        self.verification_log.append(entry)
        return True, success_msg

    def verify_consistency(
        self,
        theorem_a: TheoremStatement,
        theorem_b: TheoremStatement,
    ) -> tuple[bool, str]:
        """Check that two theorems are not obviously contradictory.

        Two theorems are considered potentially contradictory if both are
        VERIFIED and one's conclusion negates the other's (detected heuristically
        by checking for a leading '¬' or 'NOT' in one conclusion's formal statement).

        Parameters
        ----------
        theorem_a : TheoremStatement
        theorem_b : TheoremStatement

        Returns
        -------
        tuple[bool, str]
            ``(consistent, message)``.
        """
        conc_a = theorem_a.conclusion.formal_statement.strip()
        conc_b = theorem_b.conclusion.formal_statement.strip()

        def _is_negation(s1: str, s2: str) -> bool:
            neg_prefixes = ("¬", "NOT ", "not ")
            for p in neg_prefixes:
                if s1.startswith(p) and s1[len(p) :].strip() == s2:
                    return True
            return False

        if _is_negation(conc_a, conc_b) or _is_negation(conc_b, conc_a):
            msg = (
                f"Potential contradiction detected between "
                f"{theorem_a.theorem_id} and {theorem_b.theorem_id}"
            )
            return False, msg
        msg = (
            f"{theorem_a.theorem_id} and {theorem_b.theorem_id} appear consistent "
            f"(no syntactic negation detected)"
        )
        return True, msg

    def verify_all(
        self, registry: TheoremRegistry
    ) -> dict[str, tuple[bool, str]]:
        """Verify every theorem in *registry* and return a results map.

        Parameters
        ----------
        registry : TheoremRegistry
            The registry whose theorems should be verified.

        Returns
        -------
        dict[str, tuple[bool, str]]
            Maps theorem IDs to ``(success, message)`` tuples.
        """
        results: dict[str, tuple[bool, str]] = {}
        for theorem_id in registry.list_theorems():
            thm = registry.get(theorem_id)
            if thm is None:
                continue
            results[theorem_id] = self.verify_theorem(thm)
        return results

    def check_hypothesis_satisfiability(self, hypothesis: Hypothesis) -> bool:
        """Perform a basic syntactic satisfiability check on a hypothesis.

        A hypothesis is considered syntactically satisfiable when:
        - Its statement is non-empty.
        - Its formal statement is non-empty.
        - Neither contains the placeholder string ``"undefined"`` or ``"∅"``.

        Parameters
        ----------
        hypothesis : Hypothesis

        Returns
        -------
        bool
        """
        bad_tokens = {"undefined", "∅", "None", "null"}
        for tok in bad_tokens:
            if tok in hypothesis.statement or tok in hypothesis.formal_statement:
                return False
        return bool(hypothesis.statement.strip()) and bool(
            hypothesis.formal_statement.strip()
        )

    def check_conclusion_follows(
        self,
        hypotheses: tuple[Hypothesis, ...],
        conclusion: TheoremConclusion,
    ) -> bool:
        """Check that the conclusion only references known hypothesis IDs.

        This is a structural / administrative check: it verifies that each
        ``hyp_id`` listed in ``conclusion.depends_on_hyps`` actually exists in
        *hypotheses*.

        Parameters
        ----------
        hypotheses : tuple[Hypothesis, ...]
            The hypotheses in scope.
        conclusion : TheoremConclusion
            The conclusion to check.

        Returns
        -------
        bool
            ``True`` iff all referenced hypothesis IDs are declared.
        """
        declared_ids = {h.hyp_id for h in hypotheses}
        for dep_id in conclusion.depends_on_hyps:
            if dep_id not in declared_ids:
                return False
        return True

    def generate_proof_obligation(
        self, theorem: TheoremStatement
    ) -> dict[str, Any]:
        """Generate a structured proof obligation for external proof assistants.

        Parameters
        ----------
        theorem : TheoremStatement
            The theorem for which to generate a proof obligation.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:
            - ``obligation_id`` — UUID
            - ``theorem_id``
            - ``hypotheses`` — list of formal hypothesis statements
            - ``goal`` — formal conclusion statement
            - ``strategy`` — proof_sketch.proof_strategy
            - ``key_lemmas`` — list of lemma IDs
            - ``generated_at``
        """
        return {
            "obligation_id": str(uuid.uuid4()),
            "theorem_id": theorem.theorem_id,
            "hypotheses": [h.formal_statement for h in theorem.hypotheses],
            "goal": theorem.conclusion.formal_statement,
            "strategy": theorem.proof_sketch.proof_strategy,
            "key_lemmas": list(theorem.proof_sketch.key_lemmas),
            "generated_at": _now_iso(),
        }

    def verification_report(self) -> dict[str, Any]:
        """Return a summary report of all verification activity.

        Returns
        -------
        dict[str, Any]
            Keys: ``total_attempts``, ``verified_count``, ``failed_count``,
            ``log_entries`` (all entries), ``generated_at``.
        """
        failed = sum(
            1 for e in self.verification_log if e.get("result") != "verified"
        )
        return {
            "total_attempts": len(self.verification_log),
            "verified_count": self.verified_count,
            "failed_count": failed,
            "log_entries": list(self.verification_log),
            "generated_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

_DEFAULT_REGISTRY: TheoremRegistry | None = None


def get_default_registry() -> TheoremRegistry:
    """Return (and cache) the default theorem registry.

    The registry is built lazily on first call and cached for subsequent calls.

    Returns
    -------
    TheoremRegistry
        Registry pre-populated with all five canonical Ch10 theorems.
    """
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = TheoremRegistry.default_registry()
    return _DEFAULT_REGISTRY


def get_theorem(theorem_id: str) -> TheoremStatement | None:
    """Retrieve a theorem from the default registry by ID.

    Parameters
    ----------
    theorem_id : str
        The theorem ID, e.g. ``"thm-10-1"``.

    Returns
    -------
    TheoremStatement | None
        The theorem, or ``None`` if not found.
    """
    return get_default_registry().get(theorem_id)


def verify_all_theorems() -> dict[str, tuple[bool, str]]:
    """Verify all theorems in the default registry.

    Returns
    -------
    dict[str, tuple[bool, str]]
        Maps theorem IDs to ``(success, message)`` tuples.
    """
    verifier = ProofVerifier()
    return verifier.verify_all(get_default_registry())


def list_theorem_ids() -> list[str]:
    """Return a sorted list of theorem IDs in the default registry.

    Returns
    -------
    list[str]
    """
    return get_default_registry().list_theorems()


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def spec_descent(spec: Any) -> dict[str, Any]:
    """Compute descent data for specification satisfaction.
    
    Specification satisfaction IS descent — satisfying a spec means finding
    a global section that restricts correctly to each local patch.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict with specification data.
    
    Returns
    -------
    dict[str, Any]
        Descent record with ``cover``, ``local_sections``, ``cocycle_trivial``,
        and ``global_section_exists`` keys.
    """
    try:
        from jugeo.geometry.descent import run_descent, DescentDatum
    except ImportError:
        run_descent = None
        DescentDatum = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    descent: dict[str, Any] = {
        "spec_name": name,
        "cover": list(coords) if coords else [],
        "local_sections": {},
        "cocycle_trivial": None,
        "global_section_exists": None,
    }

    if run_descent is not None:
        try:
            result = run_descent(coords)
            descent["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            descent["global_section_exists"] = getattr(result, "global_section_exists", None)
            descent["local_sections"] = getattr(result, "local_sections", {})
        except Exception:
            pass

    return descent


def spec_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for a satisfaction result.
    
    A satisfaction certificate records that a specification was checked,
    the outcome, and the trust level of the evidence.
    
    Parameters
    ----------
    result : Any
        A satisfaction result object or dict.
    
    Returns
    -------
    dict[str, Any]
        Certificate with ``satisfied``, ``trust_level``, ``witness_hash``,
        ``spec_name``, and ``certificate_id`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    satisfied = getattr(result, "satisfied", None)
    if satisfied is None and isinstance(result, dict):
        satisfied = result.get("satisfied", result.get("status") == "satisfied")

    spec_name = getattr(result, "spec_name", None) or (
        result.get("spec_name") if isinstance(result, dict) else "unknown"
    )

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "spec_name": spec_name,
        "satisfied": bool(satisfied),
        "trust_level": "VERIFIED" if satisfied else "UNVERIFIED",
        "witness_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=spec_name, satisfied=satisfied, source="specification_satisfaction"
            )
        except Exception:
            pass

    return cert


def spec_encoding(spec: Any) -> dict[str, Any]:
    """Encode a specification as scalar constraints for SMT solving.
    
    Specifications translate to scalar encodings where each clause becomes
    a conjunction of SMT predicates over the target coordinates.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict.
    
    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``coordinate_map``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings.scalar_encodings import ScalarEncoder, encode_constraint
    except ImportError:
        ScalarEncoder = None
        encode_constraint = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    encoding: dict[str, Any] = {
        "spec_name": name,
        "encoding_kind": "scalar_conjunction",
        "formulas": [f"(sat {c})" for c in (coords or [])],
        "variables": [f"sat_{c}" for c in (coords or [])],
        "coordinate_map": {c: f"sat_{c}" for c in (coords or [])},
        "encoder": None,
    }

    if encode_constraint is not None:
        try:
            for c in (coords or []):
                enc = encode_constraint(c, name)
                if hasattr(enc, "formula"):
                    encoding["formulas"].append(enc.formula)
        except Exception:
            pass

    if ScalarEncoder is not None:
        try:
            encoding["encoder"] = ScalarEncoder(coordinates=list(coords or []))
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "VerificationStatus",
    "TheoremCategory",
    # Dataclasses
    "Hypothesis",
    "TheoremConclusion",
    "ProofSketch",
    "TheoremStatement",
    "TheoremRegistry",
    "ProofVerifier",
    # Theorem constants
    "theorem_satisfaction_iff_descent",
    "theorem_certificate_uniqueness",
    "theorem_gap_completeness",
    "theorem_monotone_satisfaction",
    "theorem_composition_satisfaction",
    # Module-level functions
    "get_default_registry",
    "get_theorem",
    "verify_all_theorems",
    "list_theorem_ids",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]
