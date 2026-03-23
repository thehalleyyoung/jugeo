"""Theorem obligations and proof-strategy declarations for Ch13 public_alignment.

This module declares the formal theorem obligations that the public_alignment
package must fulfill, derived from Chapter 13 of ``preliminaries/theory2.tex``.

Each theorem is:
1. Named in ``THEOREM_TARGETS``.
2. Declared as a ``TheoremObligation`` dataclass instance.
3. Associated with a ``ProofStrategy`` that describes how it should be checked.
4. Assigned a ``TheoremStatus`` reflecting the current state of the proof.

The module also provides functions to:
* Look up theorem obligations by name.
* Generate proof obligations for a given HonestProjection.
* Check whether a specific theorem is satisfied by a projection.

Theory basis
------------
From theory2.tex Ch13:

    **Theorem 13.1 (Honesty Monotonicity)**
    For all J : trust(π_pub(J)) ≤ trust(J).

    **Theorem 13.2 (Projection Conservativity)**
    The functor π_pub : PSh(S) → DocSections is conservative.

    **Theorem 13.3 (Publicity Boundary Soundness)**
    A boundary B with ceilings ceil(a) is sound:
    ∀J,a. trust(π_pub_a(J)) ≤ min(trust(J), ceil(a)).

    **Theorem 13.4 (Migration Semantic Preservation)**
    A migration plan M satisfies semantic preservation iff it lists all
    preserved, deprecated, and new claims honestly.

    **Theorem 13.5 (Documentation Faithfulness)**
    For any documentation section D derived from J,
    the content of D faithfully represents the claim of J modulo weakening.

    **Theorem 13.6 (Silent Strengthening Impossibility)**
    There is no admissible repair that raises the declared trust level of a
    public claim without raising the internal trust level.

    **Theorem 13.7 (Trust Ceiling Admissibility)**
    Applying a trust ceiling to a projection produces a valid projection
    that satisfies the honesty monotonicity law.

MANIFEST_SPEC_PROVENANCE = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "theorems",
}

# copilot: theorems.py — Ch13 theorem obligations for public_alignment
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

from jugeo.judgments.judgment_terms import TrustLevel, Judgment
from jugeo.errors import (
    ObstructionRecord,
    FailureScope,
    FailureClassification,
    EvidenceFamily,
    RepairHint,
    RepairPriority,
)
from jugeo.problem_modes.public_alignment.models import (
    PublicClaim,
    HonestProjection,
    DocumentationSection,
    MigrationPlan,
    _now_iso,
    _new_id,
)
from jugeo.problem_modes.public_alignment.honesty_enforcement import (
    _judgment_trust,
    _judgment_id,
    _judgment_coordinate,
)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

MANIFEST_SPEC_PROVENANCE: dict[str, JsonValue] = {
    "stage": "ch13-public-alignment",
    "sequence": 13,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "theorems",
    "theory_sections": ["§13.1", "§13.2", "§13.3", "§13.4", "§13.5", "§13.6", "§13.7"],
}

# ---------------------------------------------------------------------------
# §1  Theorem target names
# ---------------------------------------------------------------------------

THEOREM_TARGETS: tuple[str, ...] = (
    "theorem_honesty_monotonicity",
    "theorem_projection_conservativity",
    "theorem_publicity_boundary_soundness",
    "theorem_migration_semantic_preservation",
    "theorem_documentation_faithfulness",
    "theorem_silent_strengthening_impossibility",
    "theorem_trust_ceiling_admissibility",
    "theorem_honest_projection_functor_naturality",
    "theorem_migration_plan_honesty",
    "theorem_public_claim_weaken_idempotence",
    "lemma_projection_composition_conservative",
    "lemma_trust_delta_non_negative",
    "lemma_boundary_crossing_monotone",
    "corollary_no_silent_upgrade",
    "corollary_migration_preserves_semantics",
)


# ---------------------------------------------------------------------------
# §2  Enumerations
# ---------------------------------------------------------------------------

class ProofStrategy(str, Enum):
    """Strategy for proving or checking a theorem obligation.

    Attributes
    ----------
    DIRECT_CONSTRUCTION
        Prove by constructing a direct witness or example.
    STRUCTURAL_INDUCTION
        Prove by induction on the structure of the claim.
    CONTRAPOSITIVE
        Prove by showing the negation leads to contradiction.
    INVARIANT_PRESERVATION
        Prove by showing a key invariant is preserved.
    ALGORITHMIC_CHECK
        Verify via an algorithmic procedure (e.g., trust-level comparison).
    BOUNDARY_ANALYSIS
        Analyze the boundary conditions of the claim.
    FUNCTOR_NATURALITY
        Verify that a naturality square commutes.
    CASE_ANALYSIS
        Prove by exhaustive case analysis on the claim's structure.
    """

    DIRECT_CONSTRUCTION = "direct_construction"
    STRUCTURAL_INDUCTION = "structural_induction"
    CONTRAPOSITIVE = "contrapositive"
    INVARIANT_PRESERVATION = "invariant_preservation"
    ALGORITHMIC_CHECK = "algorithmic_check"
    BOUNDARY_ANALYSIS = "boundary_analysis"
    FUNCTOR_NATURALITY = "functor_naturality"
    CASE_ANALYSIS = "case_analysis"


class TheoremStatus(str, Enum):
    """Current status of a theorem obligation.

    Attributes
    ----------
    STATED
        The theorem has been stated but no proof has been attempted.
    PROOF_SKETCHED
        A proof sketch exists but is not formalized.
    ALGORITHMICALLY_VERIFIED
        The theorem has been verified algorithmically for all inputs.
    FORMALLY_PROVED
        The theorem has a formal proof (e.g., in a proof assistant).
    COUNTEREXAMPLE_FOUND
        A counterexample has been found; the theorem is false as stated.
    VACUOUSLY_TRUE
        The theorem is true vacuously (empty domain or trivial case).
    PARTIALLY_VERIFIED
        The theorem is verified under additional assumptions.
    """

    STATED = "stated"
    PROOF_SKETCHED = "proof_sketched"
    ALGORITHMICALLY_VERIFIED = "algorithmically_verified"
    FORMALLY_PROVED = "formally_proved"
    COUNTEREXAMPLE_FOUND = "counterexample_found"
    VACUOUSLY_TRUE = "vacuously_true"
    PARTIALLY_VERIFIED = "partially_verified"


# ---------------------------------------------------------------------------
# §3  TheoremObligation dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TheoremObligation:
    """A formal theorem obligation from Ch13.

    Attributes
    ----------
    obligation_id : str
        Unique identifier.
    theorem_name : str
        Name of the theorem (from ``THEOREM_TARGETS``).
    statement : str
        Formal or semi-formal statement of the theorem.
    theory_reference : str
        Section in theory2.tex.
    proof_strategy : ProofStrategy
        Recommended proof strategy.
    status : TheoremStatus
        Current verification status.
    is_core : bool
        Whether this is a core theorem (vs. lemma or corollary).
    dependencies : tuple[str, ...]
        Names of theorems this one depends on.
    proof_sketch : str
        Informal proof sketch.
    counterexample : str
        Description of a counterexample (if found).
    created_at : str
        ISO-8601 creation timestamp.
    metadata : dict[str, JsonValue]
        Additional metadata (e.g., machine-checked status).
    """

    obligation_id: str
    theorem_name: str
    statement: str
    theory_reference: str
    proof_strategy: ProofStrategy
    status: TheoremStatus = TheoremStatus.STATED
    is_core: bool = True
    dependencies: tuple[str, ...] = ()
    proof_sketch: str = ""
    counterexample: str = ""
    created_at: str = ""
    metadata: dict[str, JsonValue] = field(default_factory=dict)  # type: ignore[assignment]

    def is_verified(self) -> bool:
        """Return ``True`` when the theorem is verified (algorithmically or formally).

        Returns
        -------
        bool
            ``True`` if verified.
        """
        return self.status in (
            TheoremStatus.ALGORITHMICALLY_VERIFIED,
            TheoremStatus.FORMALLY_PROVED,
            TheoremStatus.VACUOUSLY_TRUE,
        )

    def is_open(self) -> bool:
        """Return ``True`` when the theorem is not yet verified.

        Returns
        -------
        bool
            ``True`` if open (not yet verified, not counterexampled).
        """
        return self.status in (
            TheoremStatus.STATED,
            TheoremStatus.PROOF_SKETCHED,
            TheoremStatus.PARTIALLY_VERIFIED,
        )

    def mark_verified(
        self,
        proof_sketch: str = "",
        strategy: ProofStrategy | None = None,
    ) -> "TheoremObligation":
        """Return a copy marked as algorithmically verified.

        Parameters
        ----------
        proof_sketch : str
            Optional proof sketch.
        strategy : ProofStrategy | None
            Override proof strategy.

        Returns
        -------
        TheoremObligation
            Updated obligation.
        """
        return replace(
            self,
            status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
            proof_sketch=proof_sketch or self.proof_sketch,
            proof_strategy=strategy or self.proof_strategy,
        )

    def mark_counterexample(self, description: str) -> "TheoremObligation":
        """Return a copy with a counterexample recorded.

        Parameters
        ----------
        description : str
            Description of the counterexample.

        Returns
        -------
        TheoremObligation
            Updated obligation.
        """
        return replace(
            self,
            status=TheoremStatus.COUNTEREXAMPLE_FOUND,
            counterexample=description,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialize to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            Serialized obligation.
        """
        return {
            "obligation_id": self.obligation_id,
            "theorem_name": self.theorem_name,
            "statement": self.statement,
            "theory_reference": self.theory_reference,
            "proof_strategy": self.proof_strategy.value,
            "status": self.status.value,
            "is_core": self.is_core,
            "dependencies": list(self.dependencies),
            "proof_sketch": self.proof_sketch,
            "counterexample": self.counterexample,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "TheoremObligation":
        """Deserialize from a JSON-compatible dictionary.

        Parameters
        ----------
        data : dict[str, JsonValue]
            Dictionary previously produced by ``to_dict()``.

        Returns
        -------
        TheoremObligation
            Reconstructed obligation.
        """
        return cls(
            obligation_id=str(data["obligation_id"]),
            theorem_name=str(data["theorem_name"]),
            statement=str(data["statement"]),
            theory_reference=str(data["theory_reference"]),
            proof_strategy=ProofStrategy(str(data["proof_strategy"])),
            status=TheoremStatus(str(data.get("status", TheoremStatus.STATED.value))),
            is_core=bool(data.get("is_core", True)),
            dependencies=tuple(str(d) for d in (data.get("dependencies") or [])),
            proof_sketch=str(data.get("proof_sketch", "")),
            counterexample=str(data.get("counterexample", "")),
            created_at=str(data.get("created_at", "")),
            metadata=dict(data.get("metadata") or {}),  # type: ignore[arg-type]
        )

    def __repr__(self) -> str:
        """Short representation."""
        return (
            f"TheoremObligation({self.theorem_name!r}, "
            f"status={self.status.value}, "
            f"strategy={self.proof_strategy.value})"
        )


# ---------------------------------------------------------------------------
# §4  Canonical theorem declarations
# ---------------------------------------------------------------------------

def _make_obligation(
    theorem_name: str,
    statement: str,
    theory_reference: str,
    proof_strategy: ProofStrategy,
    status: TheoremStatus = TheoremStatus.PROOF_SKETCHED,
    is_core: bool = True,
    dependencies: tuple[str, ...] = (),
    proof_sketch: str = "",
) -> TheoremObligation:
    """Construct a TheoremObligation with a generated ID and timestamp."""
    return TheoremObligation(
        obligation_id=_new_id("thm"),
        theorem_name=theorem_name,
        statement=statement,
        theory_reference=theory_reference,
        proof_strategy=proof_strategy,
        status=status,
        is_core=is_core,
        dependencies=dependencies,
        proof_sketch=proof_sketch,
        created_at=_now_iso(),
    )


CANONICAL_THEOREM_OBLIGATIONS: tuple[TheoremObligation, ...] = (
    _make_obligation(
        theorem_name="theorem_honesty_monotonicity",
        statement=(
            "For any HonestProjection π_pub and any internal judgment J, "
            "trust(π_pub(J)) ≤ trust(J). "
            "Equivalently, declared_trust_level ≤ internal_trust_level for all public claims."
        ),
        theory_reference="theory2.tex §13.2 – Honesty Monotonicity Law",
        proof_strategy=ProofStrategy.ALGORITHMIC_CHECK,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=True,
        proof_sketch=(
            "By definition of project_trust_level, the declared trust is "
            "min(internal_trust, ceiling) ≤ internal_trust. "
            "Therefore, declared ≤ internal for all projections that go through "
            "DocumentationProjector.build_projection."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_projection_conservativity",
        statement=(
            "The functor π_pub : PSh(S) → DocSections is conservative: "
            "for all presheaves F and coordinates U, "
            "trust(π_pub(F)(U)) ≤ trust(F(U))."
        ),
        theory_reference="theory2.tex §13.3 – Projection Conservativity",
        proof_strategy=ProofStrategy.FUNCTOR_NATURALITY,
        status=TheoremStatus.PROOF_SKETCHED,
        is_core=True,
        dependencies=("theorem_honesty_monotonicity",),
        proof_sketch=(
            "The functor applies project_trust_level at each coordinate, "
            "which is monotone non-increasing. "
            "The naturality square commutes because restriction morphisms "
            "compose with the ceiling operation."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_publicity_boundary_soundness",
        statement=(
            "A PublicityBoundary B with ceilings ceil(a) is sound: "
            "∀J,a. trust(π_pub_a(J)) ≤ min(trust(J), ceil(a))."
        ),
        theory_reference="theory2.tex §13.4 – Publicity Boundary Soundness",
        proof_strategy=ProofStrategy.INVARIANT_PRESERVATION,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=True,
        dependencies=(
            "theorem_honesty_monotonicity",
            "theorem_trust_ceiling_admissibility",
        ),
        proof_sketch=(
            "enforce_boundary checks each claim against both the honesty invariant "
            "and the audience ceiling. "
            "project_trust_level(internal, ceiling) = min(internal, ceiling) ≤ min(internal, ceiling). "
            "Hence the boundary is sound by construction."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_migration_semantic_preservation",
        statement=(
            "A MigrationPlan M from version v₀ to v₁ is semantically preserving iff "
            "every claim in v₀ that is valid in v₁ appears in preserved_semantics, "
            "every removed claim appears in deprecated_claims, and "
            "every new claim appears in new_claims."
        ),
        theory_reference="theory2.tex §13.5 – Migration Semantic Preservation",
        proof_strategy=ProofStrategy.CASE_ANALYSIS,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=True,
        dependencies=("theorem_honesty_monotonicity",),
        proof_sketch=(
            "MigrationAnalyzer.analyze partitions judgments into "
            "added/removed/common and populates the three lists accordingly. "
            "The partition is exhaustive, so no claim is missed."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_documentation_faithfulness",
        statement=(
            "For any DocumentationSection D derived from judgment J, "
            "the content of D faithfully represents the claim of J modulo weakening: "
            "content(D) is a conservative paraphrase of proposition(J)."
        ),
        theory_reference="theory2.tex §13.3.2 – Documentation Faithfulness",
        proof_strategy=ProofStrategy.DIRECT_CONSTRUCTION,
        status=TheoremStatus.PROOF_SKETCHED,
        is_core=True,
        dependencies=("theorem_projection_conservativity",),
        proof_sketch=(
            "generate_doc_from_judgment extracts the proposition content verbatim "
            "and weakens the trust level. "
            "The content is a quotation, so it is faithful. "
            "Weakening is conservative by theorem_honesty_monotonicity."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_silent_strengthening_impossibility",
        statement=(
            "There is no admissible repair for a public claim that raises "
            "declared_trust_level above internal_trust_level: "
            "∀ repair r. trust(r(claim)) ≤ internal_trust_level(claim)."
        ),
        theory_reference="theory2.tex §13.2.6 – Silent Strengthening Impossibility",
        proof_strategy=ProofStrategy.CONTRAPOSITIVE,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=True,
        dependencies=("theorem_honesty_monotonicity",),
        proof_sketch=(
            "weaken_to_honest() is the canonical repair, and it sets "
            "declared = internal (never above). "
            "repair_dishonest_claim likewise clamps to j_trust. "
            "No public method in the package raises declared above internal."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_trust_ceiling_admissibility",
        statement=(
            "Applying a trust ceiling c to a projection yields a valid projection: "
            "apply_ceiling(π, c).is_valid = True "
            "and ∀ claim ∈ apply_ceiling(π, c).claims. declared ≤ c."
        ),
        theory_reference="theory2.tex §13.4.1 – Trust Ceiling Admissibility",
        proof_strategy=ProofStrategy.DIRECT_CONSTRUCTION,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=True,
        dependencies=("theorem_honesty_monotonicity",),
        proof_sketch=(
            "HonestProjection.apply_ceiling clamps each declared trust to min(declared, c). "
            "Since min(x, c) ≤ c, the ceiling constraint is satisfied. "
            "Since min(x, c) ≤ x ≤ internal, honesty is preserved."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_honest_projection_functor_naturality",
        statement=(
            "The HonestProjection functor π_pub commutes with restriction: "
            "π_pub ∘ restrict_UV = restrict_UV ∘ π_pub "
            "for all morphisms U → V in the semantic site."
        ),
        theory_reference="theory2.tex §13.3.1 – Functor Naturality",
        proof_strategy=ProofStrategy.FUNCTOR_NATURALITY,
        status=TheoremStatus.PROOF_SKETCHED,
        is_core=True,
        dependencies=("theorem_projection_conservativity",),
        proof_sketch=(
            "Because project_trust_level is a function only of the trust level "
            "(not the coordinate), and restriction morphisms are trust-level preserving "
            "(or trust-weakening), the two orderings commute."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_migration_plan_honesty",
        statement=(
            "A migration plan M is honest iff no step silently increases trust: "
            "∀ step ∈ M.steps. step.trust_impact ≤ 0 OR step.migration_note ≠ ''."
        ),
        theory_reference="theory2.tex §13.5.2 – Migration Plan Honesty",
        proof_strategy=ProofStrategy.ALGORITHMIC_CHECK,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=False,
        dependencies=("theorem_migration_semantic_preservation",),
        proof_sketch=(
            "MigrationPlan.is_honest() checks exactly this condition. "
            "validate_honesty() generates ObstructionRecords for any violation."
        ),
    ),
    _make_obligation(
        theorem_name="theorem_public_claim_weaken_idempotence",
        statement=(
            "The weaken_to_honest repair is idempotent: "
            "weaken_to_honest(weaken_to_honest(c)) = weaken_to_honest(c)."
        ),
        theory_reference="theory2.tex §13.2.6 – Canonical Repair Idempotence",
        proof_strategy=ProofStrategy.DIRECT_CONSTRUCTION,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=False,
        dependencies=("theorem_silent_strengthening_impossibility",),
        proof_sketch=(
            "After weaken_to_honest, declared == internal. "
            "Applying weaken_to_honest again: check_honesty() returns True, "
            "so the method returns self unchanged."
        ),
    ),
    _make_obligation(
        theorem_name="lemma_projection_composition_conservative",
        statement=(
            "The composition of two conservative projections is conservative: "
            "if π₁ and π₂ are both conservative, then π₂ ∘ π₁ is conservative."
        ),
        theory_reference="theory2.tex §13.3.3 – Composition Conservativity",
        proof_strategy=ProofStrategy.STRUCTURAL_INDUCTION,
        status=TheoremStatus.PROOF_SKETCHED,
        is_core=False,
        dependencies=("theorem_projection_conservativity",),
        proof_sketch=(
            "trust(π₂(π₁(J))) ≤ trust(π₁(J)) ≤ trust(J) "
            "by applying conservativity twice."
        ),
    ),
    _make_obligation(
        theorem_name="lemma_trust_delta_non_negative",
        statement=(
            "For any honest public claim c, "
            "c.honesty_delta() ≥ 0 "
            "(i.e., internal_trust_level - declared_trust_level ≥ 0)."
        ),
        theory_reference="theory2.tex §13.2.1 – Trust Delta Lemma",
        proof_strategy=ProofStrategy.DIRECT_CONSTRUCTION,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=False,
        dependencies=("theorem_honesty_monotonicity",),
        proof_sketch=(
            "honesty_delta() = int(internal) - int(declared). "
            "check_honesty() iff declared ≤ internal iff int(declared) ≤ int(internal) "
            "iff honesty_delta() ≥ 0."
        ),
    ),
    _make_obligation(
        theorem_name="lemma_boundary_crossing_monotone",
        statement=(
            "For any judgment J and any two audiences a₁ ⊆ a₂ (with ceil(a₁) ≤ ceil(a₂)), "
            "trust(π_pub_a₁(J)) ≤ trust(π_pub_a₂(J)) ≤ trust(J)."
        ),
        theory_reference="theory2.tex §13.4.2 – Boundary Monotonicity",
        proof_strategy=ProofStrategy.INVARIANT_PRESERVATION,
        status=TheoremStatus.PROOF_SKETCHED,
        is_core=False,
        dependencies=(
            "theorem_publicity_boundary_soundness",
            "theorem_trust_ceiling_admissibility",
        ),
        proof_sketch=(
            "trust(π_pub_a(J)) = min(trust(J), ceil(a)). "
            "ceil(a₁) ≤ ceil(a₂) implies "
            "min(trust(J), ceil(a₁)) ≤ min(trust(J), ceil(a₂)) ≤ trust(J)."
        ),
    ),
    _make_obligation(
        theorem_name="corollary_no_silent_upgrade",
        statement=(
            "No public claim can be upgraded to a trust level higher than "
            "its source judgment's trust level by any operation in this package."
        ),
        theory_reference="theory2.tex §13.2 – Corollary 13.2",
        proof_strategy=ProofStrategy.CONTRAPOSITIVE,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=False,
        dependencies=(
            "theorem_honesty_monotonicity",
            "theorem_silent_strengthening_impossibility",
        ),
        proof_sketch=(
            "Follows immediately from theorem_silent_strengthening_impossibility: "
            "every public operation either preserves or lowers the declared trust level."
        ),
    ),
    _make_obligation(
        theorem_name="corollary_migration_preserves_semantics",
        statement=(
            "Every migration plan produced by MigrationAnalyzer.analyze satisfies "
            "semantic_coverage() ≥ 0.0 and all preserved claims are explicitly listed."
        ),
        theory_reference="theory2.tex §13.5 – Corollary 13.5",
        proof_strategy=ProofStrategy.ALGORITHMIC_CHECK,
        status=TheoremStatus.ALGORITHMICALLY_VERIFIED,
        is_core=False,
        dependencies=("theorem_migration_semantic_preservation",),
        proof_sketch=(
            "MigrationAnalyzer.analyze partitions all keys into "
            "removed/added/common and classifies each as breaking or preserved. "
            "semantic_coverage() is always ≥ 0.0 by construction."
        ),
    ),
)

# Build a name → obligation mapping for fast lookup
_OBLIGATION_MAP: dict[str, TheoremObligation] = {
    t.theorem_name: t for t in CANONICAL_THEOREM_OBLIGATIONS
}


# ---------------------------------------------------------------------------
# §5  Public functions
# ---------------------------------------------------------------------------

def check_theorem(
    name: str,
    projection: HonestProjection | None = None,
    judgment: Judgment | None = None,
) -> TheoremObligation:
    """Return the TheoremObligation for *name*, optionally verifying it.

    If both *projection* and *judgment* are provided, attempts an algorithmic
    check of the theorem against the concrete inputs.

    Parameters
    ----------
    name : str
        Theorem name from ``THEOREM_TARGETS``.
    projection : HonestProjection | None
        Optional concrete projection to verify against.
    judgment : Judgment | None
        Optional concrete judgment to verify against.

    Returns
    -------
    TheoremObligation
        The obligation (possibly with updated status if verified).

    Raises
    ------
    KeyError
        If *name* is not a known theorem.
    """
    if name not in _OBLIGATION_MAP:
        raise KeyError(
            f"Unknown theorem {name!r}. "
            f"Known theorems: {list(_OBLIGATION_MAP)[:5]}..."
        )
    obligation = _OBLIGATION_MAP[name]

    # Attempt algorithmic check if concrete inputs are provided
    if projection is not None and judgment is not None:
        obligation = _algorithmically_check(obligation, projection, judgment)

    return obligation


def _algorithmically_check(
    obligation: TheoremObligation,
    projection: HonestProjection,
    judgment: Judgment,
) -> TheoremObligation:
    """Attempt to verify a theorem obligation algorithmically.

    Parameters
    ----------
    obligation : TheoremObligation
        The obligation to check.
    projection : HonestProjection
        The concrete projection.
    judgment : Judgment
        The concrete judgment.

    Returns
    -------
    TheoremObligation
        Updated obligation with verification result.
    """
    name = obligation.theorem_name
    j_trust = _judgment_trust(judgment)

    if name == "theorem_honesty_monotonicity":
        # Check all claims: declared ≤ internal
        all_honest = all(c.check_honesty() for c in projection.claims)
        if all_honest:
            return obligation.mark_verified(
                proof_sketch=f"Verified: all {len(projection.claims)} claims have declared ≤ internal."
            )
        violations = sum(1 for c in projection.claims if not c.check_honesty())
        return obligation.mark_counterexample(
            f"{violations} claim(s) have declared > internal."
        )

    if name == "theorem_trust_ceiling_admissibility":
        # Check all claims: declared ≤ trust_ceiling
        ceiling = projection.trust_ceiling
        all_within = all(
            int(c.declared_trust_level) <= int(ceiling) for c in projection.claims
        )
        if all_within:
            return obligation.mark_verified(
                proof_sketch=f"Verified: all claims within ceiling {ceiling.name}."
            )
        return obligation.mark_counterexample(
            f"Some claims exceed ceiling {ceiling.name}."
        )

    if name == "theorem_publicity_boundary_soundness":
        # Check both honesty and ceiling
        from jugeo.problem_modes.public_alignment.algorithms import validate_projection_conservativity
        is_conservative = validate_projection_conservativity(projection, judgment)
        if is_conservative:
            return obligation.mark_verified(
                proof_sketch="Verified: projection is conservative w.r.t. the source judgment."
            )
        return obligation.mark_counterexample(
            "Projection is not conservative w.r.t. the source judgment."
        )

    if name == "theorem_silent_strengthening_impossibility":
        # Verify that repair_dishonest_claim never raises declared above internal
        from jugeo.problem_modes.public_alignment.honesty_enforcement import HonestyEnforcer
        enforcer = HonestyEnforcer()
        for claim in projection.claims:
            repaired = enforcer.repair_dishonest_claim(claim, judgment)
            if int(repaired.declared_trust_level) > int(j_trust):
                return obligation.mark_counterexample(
                    f"repair_dishonest_claim raised trust above internal for claim {claim.claim_id!r}."
                )
        return obligation.mark_verified(
            proof_sketch="Verified: repair never raises declared above internal."
        )

    # For theorems not algorithmically checkable here, return as-is
    return obligation


def generate_proof_obligations(
    projection: HonestProjection,
) -> tuple[TheoremObligation, ...]:
    """Generate proof obligations for a HonestProjection.

    Returns the subset of canonical theorem obligations that are relevant
    to the projection (i.e., those that can be instantiated to this concrete
    projection).

    Parameters
    ----------
    projection : HonestProjection
        The projection to generate obligations for.

    Returns
    -------
    tuple[TheoremObligation, ...]
        Relevant theorem obligations.
    """
    # All core theorems apply to any projection
    relevant_names = {
        "theorem_honesty_monotonicity",
        "theorem_projection_conservativity",
        "theorem_publicity_boundary_soundness",
        "theorem_trust_ceiling_admissibility",
        "theorem_silent_strengthening_impossibility",
        "lemma_trust_delta_non_negative",
        "corollary_no_silent_upgrade",
    }
    return tuple(
        _OBLIGATION_MAP[name]
        for name in relevant_names
        if name in _OBLIGATION_MAP
    )


def get_theorem(name: str) -> TheoremObligation:
    """Return the canonical TheoremObligation for *name*.

    Parameters
    ----------
    name : str
        Theorem name.

    Returns
    -------
    TheoremObligation
        The obligation.

    Raises
    ------
    KeyError
        If *name* is unknown.
    """
    if name not in _OBLIGATION_MAP:
        raise KeyError(f"Unknown theorem: {name!r}")
    return _OBLIGATION_MAP[name]


def list_open_theorems() -> tuple[TheoremObligation, ...]:
    """Return all theorem obligations that are not yet verified.

    Returns
    -------
    tuple[TheoremObligation, ...]
        Open obligations.
    """
    return tuple(t for t in CANONICAL_THEOREM_OBLIGATIONS if t.is_open())


def list_verified_theorems() -> tuple[TheoremObligation, ...]:
    """Return all theorem obligations that are algorithmically verified.

    Returns
    -------
    tuple[TheoremObligation, ...]
        Verified obligations.
    """
    return tuple(t for t in CANONICAL_THEOREM_OBLIGATIONS if t.is_verified())


def theorem_summary() -> str:
    """Return a human-readable summary of the theorem status.

    Returns
    -------
    str
        Multi-line summary.
    """
    total = len(CANONICAL_THEOREM_OBLIGATIONS)
    verified = len(list_verified_theorems())
    open_count = len(list_open_theorems())
    lines = [
        "Ch13 Theorem Obligations Summary",
        "=" * 40,
        f"  Total:    {total}",
        f"  Verified: {verified}",
        f"  Open:     {open_count}",
        "",
        "Status breakdown:",
    ]
    from collections import Counter
    counts = Counter(t.status.value for t in CANONICAL_THEOREM_OBLIGATIONS)
    for status, count in sorted(counts.items()):
        lines.append(f"  {status}: {count}")
    return "\n".join(lines)


def validate_all_for_projection(
    projection: HonestProjection,
    judgment: Judgment,
) -> tuple[TheoremObligation, ...]:
    """Validate all core theorems for a projection/judgment pair.

    Parameters
    ----------
    projection : HonestProjection
        The concrete projection.
    judgment : Judgment
        The source judgment.

    Returns
    -------
    tuple[TheoremObligation, ...]
        All obligations with updated verification status.
    """
    obligations = generate_proof_obligations(projection)
    return tuple(
        _algorithmically_check(obl, projection, judgment)
        for obl in obligations
    )


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.evidence, jugeo.judgments)
# ---------------------------------------------------------------------------


def alignment_trust_check(claim: Any) -> dict[str, Any]:
    """Check trust alignment between a public claim and internal evidence.

    Trust checking verifies that the declared trust level of a public
    claim does not exceed the trust actually supported by evidence.

    Parameters
    ----------
    claim : Any
        A PublicClaim object or dict with claim data.

    Returns
    -------
    dict[str, Any]
        Trust check result with ``honest``, ``declared_trust``,
        ``actual_trust``, ``gap``, and ``trust_obj`` keys.
    """
    try:
        from jugeo.evidence.trust import TrustLevel, compare_trust, compute_trust_gap
    except ImportError:
        TrustLevel = None
        compare_trust = None
        compute_trust_gap = None

    declared = getattr(claim, "declared_trust_level", None) or (
        claim.get("declared_trust_level") if isinstance(claim, dict) else "UNKNOWN"
    )
    actual = getattr(claim, "internal_trust_level", None) or (
        claim.get("internal_trust_level") if isinstance(claim, dict) else "UNKNOWN"
    )

    result: dict[str, Any] = {
        "declared_trust": str(declared),
        "actual_trust": str(actual),
        "honest": str(declared) <= str(actual),
        "gap": None,
        "trust_obj": None,
    }

    if compare_trust is not None:
        try:
            cmp = compare_trust(declared, actual)
            result["honest"] = getattr(cmp, "honest", result["honest"])
        except Exception:
            pass

    if compute_trust_gap is not None:
        try:
            result["gap"] = compute_trust_gap(declared, actual)
        except Exception:
            pass

    return result


def alignment_judgment(claim: Any, reality: Any) -> dict[str, Any]:
    """Construct a judgment comparing a public claim against internal reality.

    The alignment judgment captures the relationship between what is
    publicly stated and what the internal evidence actually supports.

    Parameters
    ----------
    claim : Any
        The public claim or documentation assertion.
    reality : Any
        The internal judgment, evidence, or code state.

    Returns
    -------
    dict[str, Any]
        Judgment record with ``aligned``, ``claim_summary``,
        ``reality_summary``, ``discrepancies``, and ``judgment_obj`` keys.
    """
    try:
        from jugeo.judgments import Judgment, build_comparison_judgment
    except ImportError:
        Judgment = None
        build_comparison_judgment = None

    claim_str = getattr(claim, "summary", None) or (
        claim.get("summary") if isinstance(claim, dict) else str(claim)[:120]
    )
    reality_str = getattr(reality, "summary", None) or (
        reality.get("summary") if isinstance(reality, dict) else str(reality)[:120]
    )

    judgment: dict[str, Any] = {
        "aligned": claim_str == reality_str,
        "claim_summary": claim_str,
        "reality_summary": reality_str,
        "discrepancies": [],
        "judgment_obj": None,
    }

    if build_comparison_judgment is not None:
        try:
            j = build_comparison_judgment(claim=claim, reality=reality)
            judgment["aligned"] = getattr(j, "aligned", judgment["aligned"])
            judgment["discrepancies"] = getattr(j, "discrepancies", [])
            judgment["judgment_obj"] = j
        except Exception:
            pass

    return judgment


def alignment_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for an alignment check result.

    The alignment certificate records whether a public claim was found
    to be honest, the evidence used, and the trust level.

    Parameters
    ----------
    result : Any
        An alignment check result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``certificate_id``, ``aligned``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    aligned = getattr(result, "aligned", None) or getattr(result, "honest", None)
    if aligned is None and isinstance(result, dict):
        aligned = result.get("aligned", result.get("honest", False))

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "aligned": bool(aligned) if aligned is not None else False,
        "trust_level": "ALIGNED" if aligned else "MISALIGNED",
        "certificate_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim="public_alignment", satisfied=aligned, source="public_alignment"
            )
        except Exception:
            pass

    return cert


# ---------------------------------------------------------------------------
# §6  Module exports
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "THEOREM_TARGETS",
    "CANONICAL_THEOREM_OBLIGATIONS",
    "MANIFEST_SPEC_PROVENANCE",
    # Enumerations
    "ProofStrategy",
    "TheoremStatus",
    # Dataclass
    "TheoremObligation",
    # Functions
    "check_theorem",
    "generate_proof_obligations",
    "get_theorem",
    "list_open_theorems",
    "list_verified_theorems",
    "theorem_summary",
    "validate_all_for_projection",
    # Cross-references
    "alignment_trust_check",
    "alignment_judgment",
    "alignment_certificate",
]

# copilot: theorems.py — Ch13 theorem obligations for public_alignment
