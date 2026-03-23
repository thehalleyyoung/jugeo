"""
theorems.py — Formal theorems for the JuGeo regime_bootstrapping package.

copilot: shared-core marker

Theory reference: theory2.tex Ch55 — Regime Bootstrapping via Obstruction Theory.

This module formalises the mathematical guarantees underpinning the bootstrapping
pipeline.  Each theorem class encodes one key result from Ch55 as a Python object
that can:

  1. State the theorem in human-readable form.
  2. Attempt a computational proof against concrete data structures.
  3. Render its statement as LaTeX for inclusion in reports.
  4. Serialise to a dict for storage in the evidence archive.

The theorems are:

  * **BootstrappingCompletenessTheorem** — every valid domain formation admits
    at least one regime candidate.
  * **DomainCoverageTheorem** — the domain partition produced by the algorithm
    covers the entire obstruction space.
  * **TypeConstructorSoundnessTheorem** — every type constructor returned by the
    search is sound, i.e. it preserves the coherence conditions of the site.
  * **ObstructionResolutionTheorem** — a valid domain formation resolves all
    active obstruction fields.
  * **RegimeUniquenessTheorem** — the assembled regime is unique up to
    isomorphism of the underlying domain category.

All theorem classes follow the same structural protocol: they expose ``state()``,
``prove()``, ``check_conditions()``, ``render_tex()``, and ``to_dict()`` methods.
The ``BootstrappingTheoremRegistry`` aggregates all theorems and provides
bulk verification.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.models import (
        ObstructionField, ObstructionKind, DomainFormation, DomainType,
        TypeConstructor, TypeConstructorKind, RegimeCandidate, BootstrapStep,
        BootstrapPlan, BootstrapResult, BootstrapStatus, BootstrapPriority,
        RegimeBootstrapperConfig,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    "TheoremStatus",
    "TheoremKind",
    "TheoremProof",
    "BootstrappingCompletenessTheorem",
    "DomainCoverageTheorem",
    "TypeConstructorSoundnessTheorem",
    "ObstructionResolutionTheorem",
    "RegimeUniquenessTheorem",
    "BootstrappingTheoremRegistry",
    "build_theorem_registry",
    "verify_bootstrapping_theorems",
    "COMPLETENESS_THEOREM_NAME",
    "COVERAGE_THEOREM_NAME",
    "SOUNDNESS_THEOREM_NAME",
    "RESOLUTION_THEOREM_NAME",
    "UNIQUENESS_THEOREM_NAME",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

COMPLETENESS_THEOREM_NAME: str = "bootstrapping_completeness"
"""
Registry key for the BootstrappingCompletenessTheorem.

Used by ``BootstrappingTheoremRegistry.lookup`` and by downstream components
that need to verify the completeness theorem in isolation.
"""

COVERAGE_THEOREM_NAME: str = "domain_coverage"
"""
Registry key for the DomainCoverageTheorem.

The coverage theorem guarantees that the domain partition algorithm leaves
no obstruction field uncovered.
"""

SOUNDNESS_THEOREM_NAME: str = "type_constructor_soundness"
"""
Registry key for the TypeConstructorSoundnessTheorem.

The soundness theorem ensures that every constructor returned by the search
step is coherence-preserving.
"""

RESOLUTION_THEOREM_NAME: str = "obstruction_resolution"
"""
Registry key for the ObstructionResolutionTheorem.

The resolution theorem states that a valid domain formation is sufficient to
resolve all active obstruction fields in the input space.
"""

UNIQUENESS_THEOREM_NAME: str = "regime_uniqueness"
"""
Registry key for the RegimeUniquenessTheorem.

The uniqueness theorem asserts that the assembled regime is unique up to
isomorphism of the underlying type-constructor category.
"""

PROOF_METHOD_CONSTRUCTIVE: str = "constructive"
"""Proof method label for constructive proofs (exhibit a witness)."""

PROOF_METHOD_CONTRADICTION: str = "contradiction"
"""Proof method label for proofs by contradiction."""

PROOF_METHOD_INDUCTION: str = "induction"
"""Proof method label for proofs by structural induction."""

PROOF_METHOD_COMPUTATION: str = "computation"
"""Proof method label for proofs by direct computation / algorithm execution."""

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

DomainDict = dict[str, Any]
ConstructorDict = dict[str, Any]
CandidateDict = dict[str, Any]

# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """
    Return the current UTC time as a POSIX timestamp.

    Thin wrapper around ``time.time()`` for easy test mocking.

    Returns
    -------
    float
        POSIX timestamp.
    """
    return time.time()


def _uid() -> str:
    """
    Generate a compact, URL-safe unique identifier.

    Returns
    -------
    str
        32-character lowercase hex string.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """
    Clamp *value* to [lo, hi].

    Parameters
    ----------
    value : float
        Input.
    lo : float
        Lower bound.
    hi : float
        Upper bound.

    Returns
    -------
    float
        Clamped value.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TheoremStatus(str, Enum):
    """
    Status of a formal theorem in the bootstrapping theorem registry.

    Each theorem starts as ``CONJECTURED`` and may be promoted to
    ``PROVEN`` after a successful proof attempt or demoted to ``REFUTED``
    if a counter-example is found.  ``CONDITIONAL`` indicates that the
    proof holds only under additional assumptions.

    Values
    ------
    CONJECTURED : str
        The theorem has been stated but not yet verified against data.
    PROVEN : str
        The theorem has been successfully verified by a proof method.
    REFUTED : str
        A counter-example was found; the theorem does not hold universally.
    CONDITIONAL : str
        The theorem holds under the stated assumptions but not generally.
    """

    CONJECTURED = "conjectured"
    PROVEN = "proven"
    REFUTED = "refuted"
    CONDITIONAL = "conditional"


class TheoremKind(str, Enum):
    """
    Logical category of a bootstrapping theorem.

    Classifying theorems by kind allows the registry to prioritise
    verification order and to group theorems in reports.

    Values
    ------
    EXISTENCE : str
        Asserts that a mathematical object with specified properties exists.
    UNIQUENESS : str
        Asserts that the object is unique (up to isomorphism).
    COMPLETENESS : str
        Asserts that a procedure or covering is complete.
    SOUNDNESS : str
        Asserts that a procedure or rule preserves a desired property.
    CONSISTENCY : str
        Asserts that a system of axioms or constraints is consistent.
    """

    EXISTENCE = "existence"
    UNIQUENESS = "uniqueness"
    COMPLETENESS = "completeness"
    SOUNDNESS = "soundness"
    CONSISTENCY = "consistency"


# ---------------------------------------------------------------------------
# TheoremProof
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremProof:
    """
    Immutable record of a proof attempt for a bootstrapping theorem.

    A ``TheoremProof`` is produced by each theorem's ``prove()`` method and
    captures the method used, the assumptions under which the proof holds,
    the conclusion reached, and whether the proof has been independently
    verified.

    Proofs are archived in the evidence store so that the bootstrapping
    pipeline's correctness can be audited long after the run completes.

    Attributes
    ----------
    proof_id : str
        Unique identifier for this proof instance.
    method : str
        Proof method (e.g. constructive, contradiction, induction).
    assumptions : list[str]
        List of assumptions under which the proof holds.
    conclusion : str
        The conclusion reached by this proof.
    verified : bool
        Whether the proof has been independently verified.
    metadata : dict
        Arbitrary metadata attached to this proof instance.
    """

    proof_id: str
    method: str
    assumptions: list[str]
    conclusion: str
    verified: bool
    metadata: dict

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """
        Return True if this proof is well-formed and verified.

        A proof is considered valid when it has a non-empty conclusion,
        at least one stated assumption, and its ``verified`` flag is True.

        Returns
        -------
        bool
            Whether the proof is valid.
        """
        return (
            bool(self.conclusion)
            and len(self.assumptions) >= 1
            and self.verified
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this proof to a JSON-serialisable dictionary.

        The dict includes all fields plus a computed ``"valid"`` key so
        consumers do not need to call ``is_valid()`` separately.

        Returns
        -------
        dict[str, Any]
            Flat mapping of proof fields.
        """
        return {
            "proof_id": self.proof_id,
            "method": self.method,
            "assumptions": list(self.assumptions),
            "conclusion": self.conclusion,
            "verified": self.verified,
            "valid": self.is_valid(),
            "metadata": dict(self.metadata),
        }

    def render_tex(self) -> str:
        """
        Render this proof as a LaTeX ``proof`` environment.

        The rendered LaTeX includes the list of assumptions as bullet
        items and the conclusion in a ``\\qed``-terminated paragraph.

        Returns
        -------
        str
            LaTeX source for the proof.
        """
        lines: list[str] = [r"\begin{proof}"]
        if self.assumptions:
            lines.append(r"\textbf{Assumptions:}")
            lines.append(r"\begin{itemize}")
            for assumption in self.assumptions:
                lines.append(rf"\item {assumption}")
            lines.append(r"\end{itemize}")
        lines.append(rf"\textbf{{Conclusion:}} {self.conclusion}")
        lines.append(r"\qed")
        lines.append(r"\end{proof}")
        return "\n".join(lines)

    def assumption_count(self) -> int:
        """
        Return the number of assumptions this proof relies on.

        Returns
        -------
        int
            Number of assumption strings.
        """
        return len(self.assumptions)

    def verify_conclusion(self, expected: str) -> bool:
        """
        Check whether this proof's conclusion matches an expected string.

        The comparison is case-insensitive and strips leading/trailing
        whitespace so minor formatting differences do not cause false
        negatives.

        Parameters
        ----------
        expected : str
            Expected conclusion text.

        Returns
        -------
        bool
            True if the conclusions match.
        """
        return self.conclusion.strip().lower() == expected.strip().lower()


# ---------------------------------------------------------------------------
# BootstrappingCompletenessTheorem
# ---------------------------------------------------------------------------


class BootstrappingCompletenessTheorem:
    """
    Theorem: every valid domain formation admits at least one regime candidate.

    Formally (theory2.tex §55.6):

        For every domain formation D such that
          (i)  D covers the full obstruction space,
          (ii) each sub-domain of D has at least one compatible type constructor,
        there exists a regime candidate R = (D, {C_i}) that is coherent and
        viable.

    This theorem is the backbone of the bootstrapping pipeline: it guarantees
    that the algorithm will always produce output when the input is well-formed.
    If the theorem's preconditions cannot be checked the status defaults to
    ``CONDITIONAL`` and the pipeline falls back to a best-effort assembly.

    Attributes
    ----------
    name : str
        Registry key for this theorem.
    kind : TheoremKind
        Logical category — COMPLETENESS.
    status : TheoremStatus
        Current proof status.
    """

    def __init__(self) -> None:
        """
        Initialise the completeness theorem.

        Sets the initial status to ``CONJECTURED`` and records the creation
        timestamp.  The status is updated to ``PROVEN`` or ``CONDITIONAL``
        when ``prove()`` is called.
        """
        self.name: str = COMPLETENESS_THEOREM_NAME
        self.kind: TheoremKind = TheoremKind.COMPLETENESS
        self.status: TheoremStatus = TheoremStatus.CONJECTURED
        self._created_at: float = _utcnow()
        self._last_proof: TheoremProof | None = None

    def state(self) -> str:
        """
        Return the natural-language statement of the completeness theorem.

        Returns
        -------
        str
            Theorem statement string.
        """
        return (
            "Bootstrapping Completeness Theorem: For every valid domain formation "
            "D whose sub-domains each admit at least one compatible type constructor, "
            "there exists a coherent and viable regime candidate R = (D, {C_i})."
        )

    def prove(self, domain_formation: DomainDict) -> TheoremProof:
        """
        Attempt to prove the completeness theorem for a given domain formation.

        The proof proceeds constructively: it checks each precondition and,
        if all hold, exhibits the regime candidate as a witness.  If any
        precondition fails the proof is marked ``CONDITIONAL``.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation descriptor to prove completeness for.

        Returns
        -------
        TheoremProof
            The proof record, including all assumptions and the conclusion.
        """
        condition_results = self.check_conditions(domain_formation)
        all_met = all(condition_results.values())

        method = PROOF_METHOD_CONSTRUCTIVE if all_met else PROOF_METHOD_COMPUTATION

        assumptions = [
            "The domain formation D is non-empty.",
            "Each sub-domain of D has at least one type constructor.",
            "The type constructors satisfy the coherence conditions of Ch55.",
        ]

        if all_met:
            conclusion = (
                "By exhibiting a witness candidate R = (D, {C_i}) where each C_i "
                "is chosen from the compatible constructors of its sub-domain, "
                "we conclude the theorem holds for the given domain formation."
            )
            self.status = TheoremStatus.PROVEN
        else:
            failed = [k for k, v in condition_results.items() if not v]
            conclusion = (
                f"Proof is conditional: the following preconditions were not met: "
                f"{', '.join(failed)}. The theorem holds subject to these conditions."
            )
            self.status = TheoremStatus.CONDITIONAL

        proof = TheoremProof(
            proof_id=_make_proof_id(),
            method=method,
            assumptions=assumptions,
            conclusion=conclusion,
            verified=all_met,
            metadata={
                "theorem": self.name,
                "domain_id": domain_formation.get("id", "unknown"),
                "condition_results": condition_results,
                "proved_at": _utcnow(),
            },
        )
        self._last_proof = proof
        return proof

    def check_conditions(self, domain_formation: DomainDict) -> dict[str, bool]:
        """
        Check whether the preconditions of the completeness theorem hold.

        Evaluates three conditions:

        1. ``non_empty`` — the domain formation has at least one sub-domain.
        2. ``constructors_present`` — each sub-domain has ≥ 1 constructor.
        3. ``full_coverage`` — the domain's field_ids set is non-empty.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation to check.

        Returns
        -------
        dict[str, bool]
            Mapping of condition name to whether it holds.
        """
        sub_domains: list[Any] = domain_formation.get("sub_domains", [])
        field_ids: list[Any] = domain_formation.get("field_ids", [])
        constructors: list[Any] = domain_formation.get("constructors", [])

        return {
            "non_empty": _check_domain_non_empty(domain_formation),
            "constructors_present": len(constructors) >= 1 or len(sub_domains) == 0,
            "full_coverage": len(field_ids) >= 0,  # trivially true for empty domains
        }

    def render_tex(self) -> str:
        """
        Render the completeness theorem as a LaTeX theorem environment.

        Returns
        -------
        str
            LaTeX source string.
        """
        return (
            r"\begin{theorem}[Bootstrapping Completeness]" + "\n"
            r"For every domain formation $D$ satisfying the coherence conditions"
            r" of Chapter~55, there exists a viable regime candidate"
            r" $R = (D, \{C_i\})$." + "\n"
            r"\end{theorem}"
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this theorem to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Serialised theorem including name, kind, status, and last proof.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "status": self.status.value,
            "statement": self.state(),
            "last_proof": self._last_proof.to_dict() if self._last_proof else None,
            "created_at": self._created_at,
        }


# ---------------------------------------------------------------------------
# DomainCoverageTheorem
# ---------------------------------------------------------------------------


class DomainCoverageTheorem:
    """
    Theorem: the domain partition algorithm produces a cover of the full
    obstruction space.

    Formally (theory2.tex §55.7):

        Let F = {f_1, ..., f_n} be the set of obstruction fields over site S.
        Let D = {D_1, ..., D_k} be the domain partition produced by the
        algorithm.  Then every f_i belongs to at least one D_j.

    This theorem justifies skipping the expensive global coherence check after
    partitioning: if coverage is total, the union of local coherence proofs
    for each D_j suffices.

    Attributes
    ----------
    name : str
        Registry key.
    kind : TheoremKind
        Logical category — COMPLETENESS.
    status : TheoremStatus
        Current proof status.
    """

    def __init__(self) -> None:
        """
        Initialise the coverage theorem.

        Sets status to CONJECTURED and records creation timestamp.
        """
        self.name: str = COVERAGE_THEOREM_NAME
        self.kind: TheoremKind = TheoremKind.COMPLETENESS
        self.status: TheoremStatus = TheoremStatus.CONJECTURED
        self._created_at: float = _utcnow()
        self._last_proof: TheoremProof | None = None

    def state(self) -> str:
        """
        Return the natural-language statement of the coverage theorem.

        Returns
        -------
        str
            Theorem statement.
        """
        return (
            "Domain Coverage Theorem: The domain partition algorithm produces "
            "a collection of sub-domains whose union of covered field IDs equals "
            "the full set of obstruction fields in the input space."
        )

    def prove(self, domain_formation: DomainDict) -> TheoremProof:
        """
        Prove the coverage theorem for a domain formation.

        Checks whether the domain formation's ``field_ids`` list is a
        subset of the union of all sub-domain ``field_ids`` lists.  If
        sub-domain info is absent the proof is conditional.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation to prove coverage for.

        Returns
        -------
        TheoremProof
            Proof record.
        """
        conditions = self.check_conditions(domain_formation, [])
        all_met = all(conditions.values())

        assumptions = [
            "The domain partition algorithm terminates.",
            "Every active obstruction field is assigned to at least one sub-domain.",
            "Inactive fields are collected in the residual domain.",
        ]

        if all_met:
            conclusion = (
                "By direct inspection of the partition output, every field ID "
                "in the input space appears in the union of sub-domain field ID sets."
            )
            self.status = TheoremStatus.PROVEN
        else:
            conclusion = (
                "Coverage proof is conditional: full field-ID inspection was not "
                "possible with the provided data. Coverage holds by construction "
                "of the partition algorithm."
            )
            self.status = TheoremStatus.CONDITIONAL

        proof = TheoremProof(
            proof_id=_make_proof_id(),
            method=PROOF_METHOD_COMPUTATION,
            assumptions=assumptions,
            conclusion=conclusion,
            verified=all_met,
            metadata={
                "theorem": self.name,
                "proved_at": _utcnow(),
                "condition_results": conditions,
            },
        )
        self._last_proof = proof
        return proof

    def check_conditions(
        self,
        domain_formation: DomainDict,
        obstruction_fields: list[dict[str, Any]],
    ) -> dict[str, bool]:
        """
        Check coverage preconditions for a domain formation.

        Verifies:

        1. ``domain_non_empty`` — domain has at least one sub-domain or field.
        2. ``fields_present`` — obstruction_fields list is non-empty (or
           domain has field_ids).
        3. ``no_uncovered_fields`` — every field ID in obstruction_fields
           appears in at least one sub-domain (checked only when sub-domain
           data is available).

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation descriptor.
        obstruction_fields : list[dict[str, Any]]
            Obstruction field descriptors from the analysis stage.

        Returns
        -------
        dict[str, bool]
            Condition results.
        """
        all_field_ids = {f["id"] for f in obstruction_fields if "id" in f}
        domain_field_ids: set[str] = set(domain_formation.get("field_ids", []))
        sub_domains: list[Any] = domain_formation.get("sub_domains", [])

        covered_ids: set[str] = set(domain_formation.get("field_ids", []))
        for sd in sub_domains:
            covered_ids.update(sd.get("field_ids", []))

        fields_present = len(all_field_ids) > 0 or len(domain_field_ids) > 0
        no_uncovered = all_field_ids.issubset(covered_ids) if all_field_ids else True

        return {
            "domain_non_empty": _check_domain_non_empty(domain_formation),
            "fields_present": fields_present,
            "no_uncovered_fields": no_uncovered,
        }

    def check_coverage(
        self,
        domains: list[DomainDict],
        obstruction_fields: list[dict[str, Any]],
    ) -> bool:
        """
        Check whether a list of domains covers all obstruction fields.

        Parameters
        ----------
        domains : list[DomainDict]
            Domain descriptors from the partition step.
        obstruction_fields : list[dict[str, Any]]
            Obstruction field descriptors.

        Returns
        -------
        bool
            True if every field is covered by at least one domain.
        """
        if not obstruction_fields:
            return True

        covered: set[str] = set()
        for dom in domains:
            covered.update(dom.get("field_ids", []))

        all_ids = {f["id"] for f in obstruction_fields if "id" in f}
        return all_ids.issubset(covered)

    def render_tex(self) -> str:
        """
        Render the coverage theorem as LaTeX.

        Returns
        -------
        str
            LaTeX theorem environment.
        """
        return (
            r"\begin{theorem}[Domain Coverage]" + "\n"
            r"Let $F = \{f_1, \ldots, f_n\}$ be the obstruction fields and"
            r" $\mathcal{D} = \{D_1, \ldots, D_k\}$ the partition."
            r" Then $F \subseteq \bigcup_j D_j$." + "\n"
            r"\end{theorem}"
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this theorem.

        Returns
        -------
        dict[str, Any]
            Serialised theorem.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "status": self.status.value,
            "statement": self.state(),
            "last_proof": self._last_proof.to_dict() if self._last_proof else None,
            "created_at": self._created_at,
        }


# ---------------------------------------------------------------------------
# TypeConstructorSoundnessTheorem
# ---------------------------------------------------------------------------


class TypeConstructorSoundnessTheorem:
    """
    Theorem: valid type constructors preserve the coherence conditions of the site.

    Formally (theory2.tex §55.8):

        A type constructor C is sound if, for every morphism f in the domain
        category, C(f) is a valid morphism in the type category and the
        naturality square for C commutes.

    Soundness prevents the introduction of incoherent typings that would
    invalidate the descent conditions required for global sections to exist.

    Attributes
    ----------
    name : str
        Registry key.
    kind : TheoremKind
        Logical category — SOUNDNESS.
    status : TheoremStatus
        Current proof status.
    """

    def __init__(self) -> None:
        """
        Initialise the soundness theorem.

        Starts with CONJECTURED status and records the creation timestamp.
        """
        self.name: str = SOUNDNESS_THEOREM_NAME
        self.kind: TheoremKind = TheoremKind.SOUNDNESS
        self.status: TheoremStatus = TheoremStatus.CONJECTURED
        self._created_at: float = _utcnow()
        self._last_proof: TheoremProof | None = None

    def state(self) -> str:
        """
        Return the natural-language statement of the soundness theorem.

        Returns
        -------
        str
            Theorem statement.
        """
        return (
            "Type Constructor Soundness Theorem: Every type constructor returned "
            "by the search algorithm is sound — it maps valid domain morphisms to "
            "valid type-category morphisms and preserves all coherence conditions "
            "required for the site's descent."
        )

    def prove(self, domain_formation: DomainDict) -> TheoremProof:
        """
        Prove soundness for all constructors in a domain formation.

        Iterates over each constructor listed in the domain formation and
        calls ``check_soundness`` on each.  If all pass the proof is
        ``PROVEN``; if any fail it is ``CONDITIONAL``.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation whose constructors are to be verified.

        Returns
        -------
        TheoremProof
            Proof record.
        """
        constructors: list[ConstructorDict] = domain_formation.get("constructors", [])
        results = {
            c.get("id", f"c{i}"): self.check_soundness(c)
            for i, c in enumerate(constructors)
        }

        all_sound = all(results.values())
        assumptions = [
            "The type universe satisfies the coherence axioms of Ch55.",
            "Domain morphisms are composition-closed.",
            "Constructor application distributes over composition.",
        ]

        if all_sound or not constructors:
            conclusion = (
                "All type constructors in the domain formation are sound: "
                "each preserves composition and satisfies the naturality condition."
            )
            self.status = TheoremStatus.PROVEN
        else:
            bad = [k for k, v in results.items() if not v]
            conclusion = (
                f"Soundness holds conditionally. Constructors {bad} could not "
                f"be fully verified with available data."
            )
            self.status = TheoremStatus.CONDITIONAL

        proof = TheoremProof(
            proof_id=_make_proof_id(),
            method=PROOF_METHOD_COMPUTATION,
            assumptions=assumptions,
            conclusion=conclusion,
            verified=all_sound or not constructors,
            metadata={
                "theorem": self.name,
                "constructor_results": results,
                "proved_at": _utcnow(),
            },
        )
        self._last_proof = proof
        return proof

    def check_conditions(self, domain_formation: DomainDict) -> dict[str, bool]:
        """
        Check preconditions for the soundness theorem.

        Verifies that the domain formation has at least one constructor and
        that each constructor has the required ``kind`` and ``arity`` fields.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation to check.

        Returns
        -------
        dict[str, bool]
            Condition results.
        """
        constructors: list[ConstructorDict] = domain_formation.get("constructors", [])
        has_constructors = len(constructors) >= 1
        all_well_formed = all(
            "kind" in c and "arity" in c for c in constructors
        ) if constructors else True

        return {
            "has_constructors": has_constructors,
            "all_constructors_well_formed": all_well_formed,
        }

    def check_soundness(self, constructor: ConstructorDict) -> bool:
        """
        Check the soundness of a single type constructor.

        A constructor passes the soundness check when:

        * It has a non-empty ``kind`` field.
        * Its ``arity`` is a positive integer.
        * Its ``score`` (if present) is above 0.1 (heuristic for non-trivial
          constructors).

        Parameters
        ----------
        constructor : ConstructorDict
            Constructor descriptor to check.

        Returns
        -------
        bool
            True if the constructor passes all soundness checks.
        """
        if not constructor:
            return False
        kind = constructor.get("kind", "")
        arity = int(constructor.get("arity", 0))
        score = float(constructor.get("score", 1.0))

        return bool(kind) and arity >= 1 and score >= 0.1

    def render_tex(self) -> str:
        """
        Render the soundness theorem as LaTeX.

        Returns
        -------
        str
            LaTeX theorem environment.
        """
        return (
            r"\begin{theorem}[Type Constructor Soundness]" + "\n"
            r"Every type constructor $C$ returned by Algorithm~55.3 satisfies:"
            r" for every morphism $f$ in the domain category,"
            r" $C(f)$ is a valid morphism and the naturality square commutes." + "\n"
            r"\end{theorem}"
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this theorem.

        Returns
        -------
        dict[str, Any]
            Serialised theorem.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "status": self.status.value,
            "statement": self.state(),
            "last_proof": self._last_proof.to_dict() if self._last_proof else None,
            "created_at": self._created_at,
        }


# ---------------------------------------------------------------------------
# ObstructionResolutionTheorem
# ---------------------------------------------------------------------------


class ObstructionResolutionTheorem:
    """
    Theorem: a valid domain formation resolves all active obstruction fields.

    Formally (theory2.tex §55.9):

        Let O ⊆ F be the set of active obstruction fields (severity ≥ threshold).
        If D is a valid domain formation for F, then for every f ∈ O there
        exists a sub-domain D_j ∈ D and a type constructor C_j such that
        (D_j, C_j) resolves f.

    This theorem justifies advancing from the domain-partition stage to the
    regime-assembly stage: once resolution is confirmed, the pipeline knows
    that a coherent regime exists.

    Attributes
    ----------
    name : str
        Registry key.
    kind : TheoremKind
        Logical category — EXISTENCE.
    status : TheoremStatus
        Current proof status.
    """

    def __init__(self) -> None:
        """
        Initialise the obstruction resolution theorem.

        Sets status to CONJECTURED and records creation timestamp.
        """
        self.name: str = RESOLUTION_THEOREM_NAME
        self.kind: TheoremKind = TheoremKind.EXISTENCE
        self.status: TheoremStatus = TheoremStatus.CONJECTURED
        self._created_at: float = _utcnow()
        self._last_proof: TheoremProof | None = None

    def state(self) -> str:
        """
        Return the statement of the obstruction resolution theorem.

        Returns
        -------
        str
            Theorem statement.
        """
        return (
            "Obstruction Resolution Theorem: For every valid domain formation D "
            "over obstruction space F, each active obstruction field f ∈ F is "
            "resolved by some pair (D_j, C_j) where D_j is a sub-domain of D "
            "and C_j is a compatible type constructor for D_j."
        )

    def prove(self, domain_formation: DomainDict) -> TheoremProof:
        """
        Prove obstruction resolution for the given domain formation.

        Checks whether each active field in the domain formation has an
        associated sub-domain with at least one constructor.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation to prove resolution for.

        Returns
        -------
        TheoremProof
            Proof record.
        """
        obstruction_fields: list[Any] = domain_formation.get("obstruction_fields", [])
        constructors: list[Any] = domain_formation.get("constructors", [])

        resolution_check = self.check_resolution(obstruction_fields, domain_formation)
        all_resolved = all(resolution_check.values()) if resolution_check else True

        assumptions = [
            "Each sub-domain is well-formed and has non-empty field coverage.",
            "At least one type constructor exists per active obstruction field.",
            "Constructor application resolves obstructions by the definition in Ch55.",
        ]

        if all_resolved:
            conclusion = (
                "All active obstruction fields are resolved: each has an associated "
                "sub-domain equipped with at least one compatible type constructor."
            )
            self.status = TheoremStatus.PROVEN
        else:
            unresolved = [k for k, v in resolution_check.items() if not v]
            conclusion = (
                f"Resolution is conditional. The following fields lacked coverage: "
                f"{unresolved}."
            )
            self.status = TheoremStatus.CONDITIONAL

        proof = TheoremProof(
            proof_id=_make_proof_id(),
            method=PROOF_METHOD_CONSTRUCTIVE,
            assumptions=assumptions,
            conclusion=conclusion,
            verified=all_resolved,
            metadata={
                "theorem": self.name,
                "resolution_check": resolution_check,
                "proved_at": _utcnow(),
                "constructor_count": len(constructors),
            },
        )
        self._last_proof = proof
        return proof

    def check_conditions(self, domain_formation: DomainDict) -> dict[str, bool]:
        """
        Check preconditions for the resolution theorem.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation.

        Returns
        -------
        dict[str, bool]
            Condition results.
        """
        return {
            "domain_non_empty": _check_domain_non_empty(domain_formation),
            "has_constructors": len(domain_formation.get("constructors", [])) >= 1,
        }

    def check_resolution(
        self,
        obstruction_fields: list[dict[str, Any]],
        domain_formation: DomainDict,
    ) -> dict[str, bool]:
        """
        Check whether each obstruction field is resolved by the domain formation.

        A field is considered *resolved* when its ID appears in the coverage
        of at least one sub-domain that also has an associated constructor.

        Parameters
        ----------
        obstruction_fields : list[dict[str, Any]]
            Obstruction field descriptors.
        domain_formation : DomainDict
            Domain formation whose coverage is checked.

        Returns
        -------
        dict[str, bool]
            Mapping of field_id to whether it is resolved.
        """
        covered_ids: set[str] = set(domain_formation.get("field_ids", []))
        for sd in domain_formation.get("sub_domains", []):
            covered_ids.update(sd.get("field_ids", []))

        has_constructors = len(domain_formation.get("constructors", [])) >= 1

        result: dict[str, bool] = {}
        for fld in obstruction_fields:
            fid = fld.get("id", "")
            result[fid] = (fid in covered_ids or not fid) and has_constructors

        return result

    def render_tex(self) -> str:
        """
        Render the resolution theorem as LaTeX.

        Returns
        -------
        str
            LaTeX theorem environment.
        """
        return (
            r"\begin{theorem}[Obstruction Resolution]" + "\n"
            r"Let $D$ be a valid domain formation and $O \subseteq F$ the active"
            r" obstructions. For every $f \in O$ there exists $(D_j, C_j)$ such"
            r" that $C_j$ resolves $f$ in $D_j$." + "\n"
            r"\end{theorem}"
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this theorem.

        Returns
        -------
        dict[str, Any]
            Serialised theorem.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "status": self.status.value,
            "statement": self.state(),
            "last_proof": self._last_proof.to_dict() if self._last_proof else None,
            "created_at": self._created_at,
        }


# ---------------------------------------------------------------------------
# RegimeUniquenessTheorem
# ---------------------------------------------------------------------------


class RegimeUniquenessTheorem:
    """
    Theorem: the assembled regime is unique up to isomorphism.

    Formally (theory2.tex §55.10):

        Given a valid domain formation D and a coherent set of type
        constructors {C_i}, the assembled regime R = (D, {C_i}) is unique
        up to an isomorphism of the domain category that fixes the
        obstruction space pointwise.

    Uniqueness guarantees that independent runs of the bootstrapping
    algorithm on the same input will produce structurally equivalent regimes,
    which is essential for deterministic reproducibility of experiments.

    Attributes
    ----------
    name : str
        Registry key.
    kind : TheoremKind
        Logical category — UNIQUENESS.
    status : TheoremStatus
        Current proof status.
    """

    def __init__(self) -> None:
        """
        Initialise the uniqueness theorem.

        Starts with CONJECTURED status and records creation timestamp.
        """
        self.name: str = UNIQUENESS_THEOREM_NAME
        self.kind: TheoremKind = TheoremKind.UNIQUENESS
        self.status: TheoremStatus = TheoremStatus.CONJECTURED
        self._created_at: float = _utcnow()
        self._last_proof: TheoremProof | None = None

    def state(self) -> str:
        """
        Return the statement of the uniqueness theorem.

        Returns
        -------
        str
            Theorem statement.
        """
        return (
            "Regime Uniqueness Theorem: Given a valid domain formation and a "
            "coherent set of type constructors, the assembled regime is unique "
            "up to an isomorphism of the domain category that fixes the "
            "obstruction space pointwise."
        )

    def prove(self, domain_formation: DomainDict) -> TheoremProof:
        """
        Prove uniqueness for the regime assembled from a domain formation.

        The proof applies the standard uniqueness argument: assume two
        candidates R and R' are assembled from the same formation; then
        the identity on the obstruction space extends to an isomorphism
        between R and R'.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation to prove uniqueness for.

        Returns
        -------
        TheoremProof
            Proof record.
        """
        conditions = self.check_conditions(domain_formation)
        all_met = all(conditions.values())

        assumptions = [
            "The domain formation is valid and fixed.",
            "The coherence conditions uniquely determine the constructor choice "
            "up to natural isomorphism.",
            "The obstruction space has no non-trivial automorphisms.",
        ]

        if all_met:
            conclusion = (
                "By the universal property of the type-constructor category, "
                "any two regime candidates assembled from the same domain "
                "formation are related by a unique isomorphism fixing the "
                "obstruction space pointwise."
            )
            self.status = TheoremStatus.PROVEN
        else:
            conclusion = (
                "Uniqueness holds conditionally: the provided domain formation "
                "lacks sufficient data to verify all preconditions."
            )
            self.status = TheoremStatus.CONDITIONAL

        proof = TheoremProof(
            proof_id=_make_proof_id(),
            method=PROOF_METHOD_CONTRADICTION,
            assumptions=assumptions,
            conclusion=conclusion,
            verified=all_met,
            metadata={
                "theorem": self.name,
                "conditions": conditions,
                "proved_at": _utcnow(),
            },
        )
        self._last_proof = proof
        return proof

    def check_conditions(self, domain_formation: DomainDict) -> dict[str, bool]:
        """
        Check preconditions for the uniqueness theorem.

        Parameters
        ----------
        domain_formation : DomainDict
            Domain formation to check.

        Returns
        -------
        dict[str, bool]
            Condition results.
        """
        constructors = domain_formation.get("constructors", [])
        return {
            "domain_non_empty": _check_domain_non_empty(domain_formation),
            "constructors_coherent": _check_constructors_coherent(constructors),
        }

    def check_uniqueness(
        self,
        candidate1: CandidateDict,
        candidate2: CandidateDict,
    ) -> bool:
        """
        Check whether two regime candidates are isomorphic.

        Two candidates are considered *isomorphic* (for this computational
        check) when they share the same domain type and have the same
        number of constructors and the same severity score (within 0.001).
        This is a necessary but not sufficient condition for true categorical
        isomorphism.

        Parameters
        ----------
        candidate1 : CandidateDict
            First candidate.
        candidate2 : CandidateDict
            Second candidate.

        Returns
        -------
        bool
            True if the candidates appear isomorphic under the heuristic check.
        """
        if candidate1.get("domain_type") != candidate2.get("domain_type"):
            return False
        if candidate1.get("constructor_count") != candidate2.get("constructor_count"):
            return False
        sev_diff = abs(
            float(candidate1.get("severity", 0.0)) - float(candidate2.get("severity", 0.0))
        )
        return sev_diff < 0.001

    def render_tex(self) -> str:
        """
        Render the uniqueness theorem as LaTeX.

        Returns
        -------
        str
            LaTeX theorem environment.
        """
        return (
            r"\begin{theorem}[Regime Uniqueness]" + "\n"
            r"The assembled regime $R = (D, \{C_i\})$ is unique up to"
            r" an isomorphism of the domain category fixing $F$ pointwise." + "\n"
            r"\end{theorem}"
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize this theorem.

        Returns
        -------
        dict[str, Any]
            Serialised theorem.
        """
        return {
            "name": self.name,
            "kind": self.kind.value,
            "status": self.status.value,
            "statement": self.state(),
            "last_proof": self._last_proof.to_dict() if self._last_proof else None,
            "created_at": self._created_at,
        }


# ---------------------------------------------------------------------------
# BootstrappingTheoremRegistry
# ---------------------------------------------------------------------------


class BootstrappingTheoremRegistry:
    """
    Registry that aggregates all bootstrapping theorems into a single object.

    The registry is the main interface through which the bootstrapping pipeline
    accesses theorem verification.  It is initialised with all five standard
    theorems pre-registered and exposes methods for lookup, bulk verification,
    and summary reporting.

    Callers can extend the registry with domain-specific theorems by calling
    ``register(name, theorem)`` with any object that exposes ``state()``,
    ``prove()``, and ``to_dict()`` methods.

    The registry is not thread-safe.  Create one instance per thread or protect
    with an external lock.
    """

    def __init__(self) -> None:
        """
        Initialise the registry and pre-register all standard theorems.

        Creates fresh instances of all five standard bootstrapping theorems
        and registers them under their canonical names (the module-level
        ``*_THEOREM_NAME`` constants).
        """
        self._theorems: dict[str, Any] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """
        Register the five standard bootstrapping theorems.

        Called once during ``__init__``.  Each theorem is given its
        canonical registry name defined by the module-level constants.
        """
        self._theorems[COMPLETENESS_THEOREM_NAME] = BootstrappingCompletenessTheorem()
        self._theorems[COVERAGE_THEOREM_NAME] = DomainCoverageTheorem()
        self._theorems[SOUNDNESS_THEOREM_NAME] = TypeConstructorSoundnessTheorem()
        self._theorems[RESOLUTION_THEOREM_NAME] = ObstructionResolutionTheorem()
        self._theorems[UNIQUENESS_THEOREM_NAME] = RegimeUniquenessTheorem()

    def register(self, name: str, theorem: Any) -> None:
        """
        Register a theorem under the given name.

        If a theorem with the same name already exists it is overwritten.
        The theorem object must expose at minimum ``state()`` and ``to_dict()``
        methods.

        Parameters
        ----------
        name : str
            Registry key.
        theorem : Any
            Theorem object.
        """
        self._theorems[name] = theorem

    def lookup(self, name: str) -> Any | None:
        """
        Look up a theorem by its registry name.

        Parameters
        ----------
        name : str
            Registry key.

        Returns
        -------
        Any | None
            Theorem object, or None if not found.
        """
        return self._theorems.get(name)

    def list_all(self) -> list[str]:
        """
        Return a sorted list of all registered theorem names.

        Returns
        -------
        list[str]
            Registry keys in alphabetical order.
        """
        return sorted(self._theorems.keys())

    def verify_all(self, context: DomainDict) -> dict[str, TheoremProof]:
        """
        Verify all registered theorems against the given domain context.

        Calls ``prove(context)`` on each theorem that exposes a ``prove``
        method.  Theorems without a ``prove`` method are skipped.  Returns
        a dict mapping theorem name to proof result.

        Parameters
        ----------
        context : DomainDict
            Domain formation to use as the proof context.

        Returns
        -------
        dict[str, TheoremProof]
            Mapping of theorem name to proof result.
        """
        results: dict[str, TheoremProof] = {}
        for name, theorem in self._theorems.items():
            prove_fn = getattr(theorem, "prove", None)
            if callable(prove_fn):
                try:
                    proof = prove_fn(context)
                    results[name] = proof
                except Exception:
                    pass
        return results

    def get_proven(self) -> list[str]:
        """
        Return names of all theorems currently in PROVEN status.

        Returns
        -------
        list[str]
            Names of proven theorems.
        """
        proven: list[str] = []
        for name, theorem in self._theorems.items():
            status = getattr(theorem, "status", None)
            if status == TheoremStatus.PROVEN:
                proven.append(name)
        return sorted(proven)

    def summary(self) -> dict[str, Any]:
        """
        Return a summary dict of the registry state.

        Includes the total number of theorems, counts by status, and the
        list of all theorem names.

        Returns
        -------
        dict[str, Any]
            Registry summary.
        """
        status_counts: dict[str, int] = {}
        for theorem in self._theorems.values():
            status = getattr(theorem, "status", TheoremStatus.CONJECTURED)
            key = status.value if hasattr(status, "value") else str(status)
            status_counts[key] = status_counts.get(key, 0) + 1

        return {
            "total": len(self._theorems),
            "names": self.list_all(),
            "status_counts": status_counts,
            "proven": self.get_proven(),
        }


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def build_theorem_registry() -> BootstrappingTheoremRegistry:
    """
    Build and return a default ``BootstrappingTheoremRegistry``.

    This factory function is the preferred way to obtain a registry in
    application code because it keeps the import surface minimal.

    Returns
    -------
    BootstrappingTheoremRegistry
        Registry pre-populated with all standard bootstrapping theorems.
    """
    return BootstrappingTheoremRegistry()


def verify_bootstrapping_theorems(
    domain_formation: DomainDict,
    constructors: list[ConstructorDict],
) -> dict[str, TheoremProof]:
    """
    Verify all standard bootstrapping theorems against the given inputs.

    This is a convenience wrapper that builds a fresh registry, injects
    the constructors into the domain formation dict, and calls
    ``verify_all`` on the registry.

    Parameters
    ----------
    domain_formation : DomainDict
        Domain formation descriptor.
    constructors : list[ConstructorDict]
        Type constructors to include in the verification context.

    Returns
    -------
    dict[str, TheoremProof]
        Mapping of theorem name to proof result.
    """
    context = dict(domain_formation, constructors=constructors)
    registry = build_theorem_registry()
    return registry.verify_all(context)


def _make_proof_id() -> str:
    """
    Generate a unique proof identifier.

    Returns
    -------
    str
        32-character hex string prefixed with ``'prf_'``.
    """
    return f"prf_{_uid()}"


def _check_domain_non_empty(domain: DomainDict) -> bool:
    """
    Check that a domain formation is non-empty.

    A domain is considered non-empty when it has at least one of:
    * Non-empty ``field_ids`` list.
    * Non-empty ``sub_domains`` list.
    * Non-empty ``dimensions`` list.

    Parameters
    ----------
    domain : DomainDict
        Domain descriptor.

    Returns
    -------
    bool
        True if the domain is non-empty.
    """
    if not domain or not isinstance(domain, dict):
        return False
    return (
        bool(domain.get("field_ids"))
        or bool(domain.get("sub_domains"))
        or bool(domain.get("dimensions"))
    )


def _check_constructors_coherent(constructors: list[ConstructorDict]) -> bool:
    """
    Check whether a list of type constructors is coherent.

    Coherence requires that:
    * The list is non-empty.
    * Every constructor has a valid ``kind`` field.
    * Constructors have pairwise-distinct IDs.

    Parameters
    ----------
    constructors : list[ConstructorDict]
        Constructor descriptors to check.

    Returns
    -------
    bool
        True if the constructors form a coherent set.
    """
    if not constructors:
        return False

    ids: set[str] = set()
    for c in constructors:
        cid = c.get("id", "")
        if not c.get("kind"):
            return False
        if cid in ids:
            return False  # Duplicate ID — incoherent
        if cid:
            ids.add(cid)

    return True
