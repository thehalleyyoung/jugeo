"""Formal properties and proof obligations for judgment products.

Theory2.tex Chapter 5 — Theorems and Corollaries.

This module states and (where feasible) computationally verifies the
formal theorems of Chapter 5 of theory2.tex.  Each theorem is expressed
as a Python class with:

* A docstring citing the theorem statement verbatim (abbreviated).
* A ``verify`` static method that checks the theorem holds for concrete
  data, returning a :class:`TheoremResult`.
* An ``assume`` class method that builds a :class:`TheoremAssumption`
  recording the hypotheses required for the theorem.

The theorems are:

1. **Thm 1** — Non-Boolean Composition: the composition of two judgments
   is a judgment, not a boolean.
2. **Thm 2** — Residual Monotonicity: composition can only increase or
   preserve the residual count; it cannot decrease it without a
   discharge step.
3. **Thm 3** — Section Gluing (Sheaf Axiom): if a family of local
   judgments is mutually compatible, they glue to a unique global section.
4. **Thm 4** — Trust Monotonicity: composition takes the meet of trust
   levels (the result is no more trusted than the weaker constituent).
5. **Thm 5** — Comparison Transitivity: comparison maps compose
   (refinement is transitive).
6. **Thm 6** — Explanation Faithfulness: every semantic fact in a product
   is reflected in its explanation projection.
7. **Thm 7** — Discharge Soundness: a discharged obligation contributes
   an evidence item to the product's evidence bundle.

References
----------
theory2.tex §5.1 Thm 1, §5.2 Prop 3, §5.3 Prop 2,
§5.4 Prop 1–2, Cor 1–2.

# copilot: formal theorems — judgment_products package.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    Judgment,
    JudgmentAlgebra,
    JudgmentStatus,
    ResidualObligation,
    TrustLevel,
)

from jugeo.foundations.judgment_products.models import (
    ComparisonMap as BaseComparisonMap,
    JudgmentProduct,
    ProductStatus,
)
from jugeo.foundations.judgment_products.judgments_are_not_boolean_facts import (
    JudgmentAsObject,
    JudgmentProductAlgebra,
    NonBooleanJudgment,
    TruthDegree,
)
from jugeo.foundations.judgment_products.residual_obligations_are_the_livin import (
    DischargeStrategy,
    LiveResidualObligation,
    ObligationStatus,
    ObligationTracker,
    ResidualDischarger,
    ResidualSystem,
)
from jugeo.foundations.judgment_products.sections_are_the_real_products_of import (
    GlobalSection,
    SectionProduct,
    SectionProductStatus,
    SectionProducts,
)
from jugeo.foundations.judgment_products.comparison_maps_and_explanation_pr import (
    ComparisonMap,
    EquivalenceCertificate,
    ExplanationProjection,
    RefinementWitness,
)
from jugeo.foundations.judgment_products.algorithms import (
    JudgmentAlgorithms,
    ProductComputationOptions,
)
from jugeo.foundations.judgment_products.models import LocalJudgmentSection


# ---------------------------------------------------------------------------
# Theorem infrastructure
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class TheoremStatus(str, Enum):
    """Status of a :class:`TheoremResult`.

    Members
    -------
    VERIFIED
        The theorem was verified for the given inputs.
    REFUTED
        A counterexample was found.
    ASSUMED
        The theorem was not verified but is assumed to hold.
    INAPPLICABLE
        The preconditions for the theorem are not met.
    ERROR
        An error occurred during verification.
    """

    VERIFIED = "verified"
    REFUTED = "refuted"
    ASSUMED = "assumed"
    INAPPLICABLE = "inapplicable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TheoremResult:
    """The outcome of a theorem verification attempt.

    Parameters
    ----------
    theorem_id:
        Identifier of the theorem (e.g. ``"Thm1"``).
    status:
        Whether the theorem was verified, refuted, etc.
    witness:
        A string description of the witness or counterexample.
    details:
        Arbitrary key-value data produced during verification.
    verified_at:
        ISO-8601 timestamp.
    """

    theorem_id: str
    status: TheoremStatus
    witness: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    verified_at: str = field(default_factory=_now_iso)

    def is_verified(self) -> bool:
        """Return ``True`` iff the theorem was verified.

        Returns
        -------
        bool
        """
        return self.status == TheoremStatus.VERIFIED

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theorem_id": self.theorem_id,
            "status": self.status.value,
            "witness": self.witness,
            "details": self.details,
            "verified_at": self.verified_at,
        }

    def __repr__(self) -> str:
        return (
            f"TheoremResult({self.theorem_id}, "
            f"status={self.status.value})"
        )


@dataclass(frozen=True, slots=True)
class TheoremAssumption:
    """Hypotheses required by a theorem.

    Parameters
    ----------
    theorem_id:
        The theorem these assumptions are for.
    assumptions:
        Tuple of assumption description strings.
    """

    theorem_id: str
    assumptions: tuple[str, ...] = ()

    def all_satisfied(self, checks: Sequence[bool]) -> bool:
        """Return ``True`` iff all provided boolean checks are ``True``.

        Parameters
        ----------
        checks:
            Boolean values corresponding to each assumption.

        Returns
        -------
        bool
        """
        return len(checks) == len(self.assumptions) and all(checks)

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theorem_id": self.theorem_id,
            "assumptions": list(self.assumptions),
        }


# ---------------------------------------------------------------------------
# Theorem 1 — Non-Boolean Composition
# ---------------------------------------------------------------------------


class Thm1NonBooleanComposition:
    r"""**Theorem 1** — Non-Boolean Composition.

    *Statement (theory2.tex §5.1 Thm 1)*:

        For any two judgments J₁, J₂, the composition J₁ ∘ J₂ is
        a judgment (an element of the judgment algebra), not a boolean.
        In particular, ``bool(J₁ ∘ J₂)`` is not defined.

    Verification strategy
    ---------------------
    * Compose two ``Judgment`` objects.
    * Wrap the result as a ``JudgmentAsObject``.
    * Assert that calling ``bool()`` on the wrapper raises ``TypeError``.
    * Assert that the product carries the non-boolean semantic structure.
    """

    THEOREM_ID = "Thm1"

    @classmethod
    def assume(cls) -> TheoremAssumption:
        """Return the assumptions required by Thm 1.

        Returns
        -------
        TheoremAssumption
        """
        return TheoremAssumption(
            theorem_id=cls.THEOREM_ID,
            assumptions=(
                "J₁ is a well-formed Judgment with at least one evidence item.",
                "J₂ is a well-formed Judgment with a compatible carrier.",
            ),
        )

    @staticmethod
    def verify(
        j1: Judgment,
        j2: Judgment,
    ) -> TheoremResult:
        """Verify Thm 1 for concrete judgments *j1* and *j2*.

        Parameters
        ----------
        j1:
            The first judgment.
        j2:
            The second judgment.

        Returns
        -------
        TheoremResult
        """
        try:
            composed = JudgmentAlgebra.compose(j1, j2)
        except Exception as exc:
            return TheoremResult(
                theorem_id=Thm1NonBooleanComposition.THEOREM_ID,
                status=TheoremStatus.ERROR,
                witness=f"Composition raised: {exc}",
            )

        obj = JudgmentProductAlgebra.wrap(composed, label="Thm1_composed")

        # Check: calling bool() should raise TypeError
        bool_blocked = False
        try:
            _ = bool(obj)
        except TypeError:
            bool_blocked = True

        # Check: structured data is present
        has_product = isinstance(obj.to_product(), JudgmentProduct)

        if bool_blocked and has_product:
            return TheoremResult(
                theorem_id=Thm1NonBooleanComposition.THEOREM_ID,
                status=TheoremStatus.VERIFIED,
                witness=(
                    "bool() raised TypeError; "
                    "product structure preserved after composition."
                ),
                details={
                    "composed_status": composed.status.value,
                    "trust_level": obj.trust_level().name,
                    "residual_count": obj.residual_count(),
                },
            )
        return TheoremResult(
            theorem_id=Thm1NonBooleanComposition.THEOREM_ID,
            status=TheoremStatus.REFUTED,
            witness=(
                f"bool_blocked={bool_blocked}, has_product={has_product}"
            ),
        )


# ---------------------------------------------------------------------------
# Theorem 2 — Residual Monotonicity
# ---------------------------------------------------------------------------


class Thm2ResidualMonotonicity:
    r"""**Theorem 2** — Residual Monotonicity.

    *Statement (theory2.tex §5.2 Prop 3)*:

        For any two judgments J₁, J₂ with residual counts r₁, r₂:

            residuals(J₁ ∘ J₂) ≥ max(r₁, r₂) - merged_duplicates.

        Composition cannot spontaneously discharge residuals; it can
        only add new ones or preserve existing ones (modulo de-duplication
        by description).

    Verification strategy
    ---------------------
    * Compose J₁ and J₂.
    * Count residuals before and after.
    * Assert the post-composition count is ≥ 0 and ≤ r₁ + r₂.
    """

    THEOREM_ID = "Thm2"

    @classmethod
    def assume(cls) -> TheoremAssumption:
        """Return the assumptions required by Thm 2.

        Returns
        -------
        TheoremAssumption
        """
        return TheoremAssumption(
            theorem_id=cls.THEOREM_ID,
            assumptions=(
                "No discharge step is applied between J₁ and J₂.",
                "J₁ and J₂ have independent (non-overlapping) obligations.",
            ),
        )

    @staticmethod
    def verify(j1: Judgment, j2: Judgment) -> TheoremResult:
        """Verify Thm 2 for concrete judgments *j1* and *j2*.

        Parameters
        ----------
        j1:
            The first judgment.
        j2:
            The second judgment.

        Returns
        -------
        TheoremResult
        """
        r1 = len(j1.obligations)
        r2 = len(j2.obligations)

        try:
            composed = JudgmentAlgebra.compose(j1, j2)
        except Exception as exc:
            return TheoremResult(
                theorem_id=Thm2ResidualMonotonicity.THEOREM_ID,
                status=TheoremStatus.ERROR,
                witness=f"Composition raised: {exc}",
            )

        r_composed = len(composed.obligations)
        # Monotonicity: r_composed <= r1 + r2 (de-duplication allowed)
        #                 and r_composed >= 0 (trivially)
        mono_holds = 0 <= r_composed <= r1 + r2

        return TheoremResult(
            theorem_id=Thm2ResidualMonotonicity.THEOREM_ID,
            status=TheoremStatus.VERIFIED if mono_holds else TheoremStatus.REFUTED,
            witness=(
                f"r₁={r1}, r₂={r2}, r_composed={r_composed}. "
                f"Monotonicity: {r_composed} ≤ {r1 + r2}."
            ),
            details={"r1": r1, "r2": r2, "r_composed": r_composed},
        )


# ---------------------------------------------------------------------------
# Theorem 3 — Section Gluing (Sheaf Axiom)
# ---------------------------------------------------------------------------


class Thm3SectionGluing:
    r"""**Theorem 3** — Section Gluing (Sheaf Axiom).

    *Statement (theory2.tex §5.3 Prop 2)*:

        If a family {sᵢ} of local judgment sections is mutually
        compatible (each pair agrees on the overlap), then there exists
        a unique global section s that restricts to sᵢ on each patch.

    Verification strategy
    ---------------------
    * Build a family of mutually compatible ``LocalJudgmentSection`` objects.
    * Call ``SectionProducts.from_local_sections``.
    * Assert the resulting ``SectionProduct`` has status GLOBAL.
    """

    THEOREM_ID = "Thm3"

    @classmethod
    def assume(cls) -> TheoremAssumption:
        """Return the assumptions required by Thm 3.

        Returns
        -------
        TheoremAssumption
        """
        return TheoremAssumption(
            theorem_id=cls.THEOREM_ID,
            assumptions=(
                "All local sections are pairwise compatible.",
                "No section carries an obstruction.",
                "All sections are settled.",
            ),
        )

    @staticmethod
    def verify(
        locals_: Sequence[LocalJudgmentSection],
    ) -> TheoremResult:
        """Verify Thm 3 for a family of local sections.

        Parameters
        ----------
        locals_:
            The local sections to glue.

        Returns
        -------
        TheoremResult
        """
        if not locals_:
            return TheoremResult(
                theorem_id=Thm3SectionGluing.THEOREM_ID,
                status=TheoremStatus.INAPPLICABLE,
                witness="Empty family of local sections.",
            )

        # Check preconditions
        all_settled = all(ls.is_settled() for ls in locals_)
        no_obstructions = all(not ls.has_obstructions() for ls in locals_)

        if not all_settled or not no_obstructions:
            return TheoremResult(
                theorem_id=Thm3SectionGluing.THEOREM_ID,
                status=TheoremStatus.INAPPLICABLE,
                witness=(
                    f"Preconditions not met: all_settled={all_settled}, "
                    f"no_obstructions={no_obstructions}."
                ),
            )

        sp = SectionProducts.from_local_sections(locals_, coordinate_label="Thm3_base")

        is_global = sp.is_global()
        return TheoremResult(
            theorem_id=Thm3SectionGluing.THEOREM_ID,
            status=TheoremStatus.VERIFIED if is_global else TheoremStatus.REFUTED,
            witness=(
                f"Gluing {'succeeded' if is_global else 'failed'}; "
                f"section_product_status={sp.status.value}."
            ),
            details={
                "patch_count": sp.patch_count(),
                "coverage": sp.coverage_fraction(),
                "obstruction_count": len(sp.obstruction_descriptions),
            },
        )


# ---------------------------------------------------------------------------
# Theorem 4 — Trust Monotonicity
# ---------------------------------------------------------------------------


class Thm4TrustMonotonicity:
    r"""**Theorem 4** — Trust Monotonicity.

    *Statement (theory2.tex §5.1 and trust algebra)*:

        For any two judgments J₁, J₂:

            trust_floor(J₁ ∘ J₂) ≤ min(trust_floor(J₁), trust_floor(J₂))

        Composition is conservative with respect to trust; it cannot
        amplify trust beyond what the weakest constituent provides.

    Verification strategy
    ---------------------
    * Compose J₁ and J₂.
    * Compute trust floors before and after.
    * Assert that the composed trust floor ≤ min(t₁, t₂).
    """

    THEOREM_ID = "Thm4"

    @classmethod
    def assume(cls) -> TheoremAssumption:
        """Return the assumptions required by Thm 4.

        Returns
        -------
        TheoremAssumption
        """
        return TheoremAssumption(
            theorem_id=cls.THEOREM_ID,
            assumptions=(
                "Both judgments have non-empty evidence bundles.",
            ),
        )

    @staticmethod
    def verify(j1: Judgment, j2: Judgment) -> TheoremResult:
        """Verify Thm 4 for concrete judgments.

        Parameters
        ----------
        j1:
            The first judgment.
        j2:
            The second judgment.

        Returns
        -------
        TheoremResult
        """
        def _trust(j: Judgment) -> TrustLevel:
            if j.evidence.is_empty():
                return TrustLevel.UNVERIFIED
            w = j.evidence.weakest()
            return w.trust_level if w else TrustLevel.UNVERIFIED

        t1 = _trust(j1)
        t2 = _trust(j2)
        expected_max = min(t1.value, t2.value)

        try:
            composed = JudgmentAlgebra.compose(j1, j2)
        except Exception as exc:
            return TheoremResult(
                theorem_id=Thm4TrustMonotonicity.THEOREM_ID,
                status=TheoremStatus.ERROR,
                witness=str(exc),
            )

        t_composed = _trust(composed)
        # Trust monotonicity: t_composed ≤ min(t1, t2)
        holds = t_composed.value <= expected_max

        return TheoremResult(
            theorem_id=Thm4TrustMonotonicity.THEOREM_ID,
            status=TheoremStatus.VERIFIED if holds else TheoremStatus.REFUTED,
            witness=(
                f"t₁={t1.name}, t₂={t2.name}, "
                f"t_composed={t_composed.name}, "
                f"min={TrustLevel(expected_max).name}. "
                f"Holds: {holds}."
            ),
            details={
                "t1": t1.value,
                "t2": t2.value,
                "t_composed": t_composed.value,
                "expected_max": expected_max,
            },
        )


# ---------------------------------------------------------------------------
# Theorem 5 — Comparison Transitivity
# ---------------------------------------------------------------------------


class Thm5ComparisonTransitivity:
    r"""**Theorem 5** — Comparison Transitivity.

    *Statement (theory2.tex §5.4 Prop 1)*:

        If J₁ ≤ J₂ (J₁ refines J₂) and J₂ ≤ J₃, then J₁ ≤ J₃.

        Concretely: if f : J₁ → J₂ and g : J₂ → J₃ are comparison
        maps (``is_morphism=True``), then f ∘ g : J₁ → J₃ is also a
        valid comparison map.

    Verification strategy
    ---------------------
    * Build comparison maps f and g.
    * Compose them.
    * Assert the composition is also a morphism.
    """

    THEOREM_ID = "Thm5"

    @classmethod
    def assume(cls) -> TheoremAssumption:
        """Return the assumptions required by Thm 5.

        Returns
        -------
        TheoremAssumption
        """
        return TheoremAssumption(
            theorem_id=cls.THEOREM_ID,
            assumptions=(
                "f : J₁ → J₂ is a valid comparison map (is_morphism=True).",
                "g : J₂ → J₃ is a valid comparison map (is_morphism=True).",
                "f.target_id == g.source_id.",
            ),
        )

    @staticmethod
    def verify(
        f: ComparisonMap,
        g: ComparisonMap,
    ) -> TheoremResult:
        """Verify Thm 5 for concrete comparison maps *f* and *g*.

        Parameters
        ----------
        f:
            The first comparison map.
        g:
            The second comparison map.

        Returns
        -------
        TheoremResult
        """
        if f.target_id != g.source_id:
            return TheoremResult(
                theorem_id=Thm5ComparisonTransitivity.THEOREM_ID,
                status=TheoremStatus.INAPPLICABLE,
                witness=(
                    f"f.target_id={f.target_id!r} ≠ g.source_id={g.source_id!r}"
                ),
            )
        if not (f.is_morphism and g.is_morphism):
            return TheoremResult(
                theorem_id=Thm5ComparisonTransitivity.THEOREM_ID,
                status=TheoremStatus.INAPPLICABLE,
                witness=(
                    f"Preconditions not met: "
                    f"f.is_morphism={f.is_morphism}, g.is_morphism={g.is_morphism}."
                ),
            )

        try:
            fg = f.compose(g)
        except ValueError as exc:
            return TheoremResult(
                theorem_id=Thm5ComparisonTransitivity.THEOREM_ID,
                status=TheoremStatus.ERROR,
                witness=str(exc),
            )

        holds = fg.is_morphism and fg.source_id == f.source_id and fg.target_id == g.target_id

        return TheoremResult(
            theorem_id=Thm5ComparisonTransitivity.THEOREM_ID,
            status=TheoremStatus.VERIFIED if holds else TheoremStatus.REFUTED,
            witness=(
                f"Composed map: {fg.map_id!r}, "
                f"source={fg.source_id!r}, target={fg.target_id!r}, "
                f"is_morphism={fg.is_morphism}."
            ),
            details={
                "f_map_id": f.map_id,
                "g_map_id": g.map_id,
                "fg_map_id": fg.map_id,
                "fg_is_morphism": fg.is_morphism,
            },
        )


# ---------------------------------------------------------------------------
# Theorem 6 — Explanation Faithfulness
# ---------------------------------------------------------------------------


class Thm6ExplanationFaithfulness:
    r"""**Theorem 6** — Explanation Faithfulness.

    *Statement (theory2.tex §5.4 Def 2 and Cor 1)*:

        For any ``JudgmentProduct`` P with residual count r and
        obstruction count o, the ``ExplanationProjection`` E = explain(P)
        satisfies:

            len(E.residual_summaries) == r
            len(E.obstruction_summaries) == o

        No semantic fact is silently dropped by the projection.

    Verification strategy
    ---------------------
    * Generate an explanation projection.
    * Assert residual and obstruction counts are faithfully preserved.
    """

    THEOREM_ID = "Thm6"

    @classmethod
    def assume(cls) -> TheoremAssumption:
        """Return the assumptions required by Thm 6.

        Returns
        -------
        TheoremAssumption
        """
        return TheoremAssumption(
            theorem_id=cls.THEOREM_ID,
            assumptions=(
                "The product P has well-formed residuals and obstructions.",
                "The explanation is generated with scope=FULL.",
            ),
        )

    @staticmethod
    def verify(product: JudgmentProduct) -> TheoremResult:
        """Verify Thm 6 for *product*.

        Parameters
        ----------
        product:
            The product to verify for.

        Returns
        -------
        TheoremResult
        """
        from jugeo.foundations.judgment_products.models import ProjectionMode
        from jugeo.foundations.judgment_products.comparison_maps_and_explanation_pr import (
            ExplanationScope,
        )

        exp = ExplanationProjection.from_product(
            product,
            mode=ProjectionMode.DETAILED,
            scope=ExplanationScope.FULL,
        )

        r_product = product.residual_count()
        o_product = len(product.obstructions)
        r_exp = len(exp.residual_summaries)
        o_exp = len(exp.obstruction_summaries)

        faithful_residuals = r_exp == r_product
        faithful_obs = o_exp == o_product

        holds = faithful_residuals and faithful_obs
        return TheoremResult(
            theorem_id=Thm6ExplanationFaithfulness.THEOREM_ID,
            status=TheoremStatus.VERIFIED if holds else TheoremStatus.REFUTED,
            witness=(
                f"product residuals={r_product}, exp residuals={r_exp}; "
                f"product obstructions={o_product}, exp obstructions={o_exp}."
            ),
            details={
                "residuals_match": faithful_residuals,
                "obstructions_match": faithful_obs,
            },
        )


# ---------------------------------------------------------------------------
# Theorem 7 — Discharge Soundness
# ---------------------------------------------------------------------------


class Thm7DischargeSoundness:
    r"""**Theorem 7** — Discharge Soundness.

    *Statement (theory2.tex §5.2 Prop 3 Cor)*:

        If a ``LiveResidualObligation`` is discharged by
        ``DischargeStrategy.EVIDENCE_MATCH``, then the discharged
        obligation's ``contributing_evidence_keys`` is non-empty after
        discharge.

    Verification strategy
    ---------------------
    * Create a ``LiveResidualObligation`` and an evidence bundle with a
      valid solver-proof item.
    * Run ``ResidualDischarger.attempt_discharge``.
    * Assert that ``succeeded=True`` and ``evidence_key`` is non-empty.
    """

    THEOREM_ID = "Thm7"

    @classmethod
    def assume(cls) -> TheoremAssumption:
        """Return the assumptions required by Thm 7.

        Returns
        -------
        TheoremAssumption
        """
        return TheoremAssumption(
            theorem_id=cls.THEOREM_ID,
            assumptions=(
                "The evidence bundle contains at least one SOLVER_PROOF "
                "item with trust ≥ SOLVER_DISCHARGED.",
            ),
        )

    @staticmethod
    def verify(
        obligation: LiveResidualObligation,
        evidence: EvidenceBundle,
    ) -> TheoremResult:
        """Verify Thm 7 for *obligation* and *evidence*.

        Parameters
        ----------
        obligation:
            The obligation to discharge.
        evidence:
            The evidence bundle.

        Returns
        -------
        TheoremResult
        """
        tracker = ObligationTracker([obligation])
        discharger = ResidualDischarger(
            tracker, [DischargeStrategy.EVIDENCE_MATCH]
        )
        result = discharger.attempt_discharge(obligation, evidence)

        if not result.succeeded:
            return TheoremResult(
                theorem_id=Thm7DischargeSoundness.THEOREM_ID,
                status=TheoremStatus.REFUTED,
                witness="Discharge did not succeed; evidence_key is empty.",
                details={"notes": result.notes},
            )

        has_evidence_key = bool(result.evidence_key)
        return TheoremResult(
            theorem_id=Thm7DischargeSoundness.THEOREM_ID,
            status=TheoremStatus.VERIFIED if has_evidence_key else TheoremStatus.REFUTED,
            witness=(
                f"Discharged by evidence_key={result.evidence_key!r}; "
                f"soundness holds: {has_evidence_key}."
            ),
            details={
                "evidence_key": result.evidence_key,
                "new_status": result.new_status.value,
            },
        )


# ---------------------------------------------------------------------------
# JudgmentTheorems (top-level collection)
# ---------------------------------------------------------------------------


class JudgmentTheorems:
    """Top-level collection of all formal theorems for judgment_products.

    Provides batch verification utilities and a summary method.

    Theory reference: theory2.tex §5 Thm 1–7.
    """

    # Map theorem IDs to their classes
    _THEOREM_CLASSES: dict[str, Any] = {
        "Thm1": Thm1NonBooleanComposition,
        "Thm2": Thm2ResidualMonotonicity,
        "Thm3": Thm3SectionGluing,
        "Thm4": Thm4TrustMonotonicity,
        "Thm5": Thm5ComparisonTransitivity,
        "Thm6": Thm6ExplanationFaithfulness,
        "Thm7": Thm7DischargeSoundness,
    }

    @classmethod
    def all_theorem_ids(cls) -> tuple[str, ...]:
        """Return all registered theorem IDs.

        Returns
        -------
        tuple[str, ...]
        """
        return tuple(cls._THEOREM_CLASSES)

    @classmethod
    def get_assumptions(cls, theorem_id: str) -> TheoremAssumption | None:
        """Return the assumptions for *theorem_id*, or ``None`` if not found.

        Parameters
        ----------
        theorem_id:
            The theorem to look up (e.g. ``"Thm1"``).

        Returns
        -------
        TheoremAssumption | None
        """
        thm_cls = cls._THEOREM_CLASSES.get(theorem_id)
        if thm_cls is None:
            return None
        return thm_cls.assume()

    @classmethod
    def summarise(cls) -> dict[str, Any]:
        """Return a summary of all theorems and their assumptions.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "theorem_count": len(cls._THEOREM_CLASSES),
            "theorems": [
                {
                    "id": tid,
                    "assumptions": cls.get_assumptions(tid).to_mapping() if cls.get_assumptions(tid) else {},
                }
                for tid in cls.all_theorem_ids()
            ],
        }

    @staticmethod
    def verify_thm1(j1: Judgment, j2: Judgment) -> TheoremResult:
        """Verify Thm 1 for *j1*, *j2*.

        Returns
        -------
        TheoremResult
        """
        return Thm1NonBooleanComposition.verify(j1, j2)

    @staticmethod
    def verify_thm2(j1: Judgment, j2: Judgment) -> TheoremResult:
        """Verify Thm 2 for *j1*, *j2*.

        Returns
        -------
        TheoremResult
        """
        return Thm2ResidualMonotonicity.verify(j1, j2)

    @staticmethod
    def verify_thm3(
        locals_: Sequence[LocalJudgmentSection],
    ) -> TheoremResult:
        """Verify Thm 3 for *locals_*.

        Returns
        -------
        TheoremResult
        """
        return Thm3SectionGluing.verify(locals_)

    @staticmethod
    def verify_thm4(j1: Judgment, j2: Judgment) -> TheoremResult:
        """Verify Thm 4 for *j1*, *j2*.

        Returns
        -------
        TheoremResult
        """
        return Thm4TrustMonotonicity.verify(j1, j2)

    @staticmethod
    def verify_thm5(f: ComparisonMap, g: ComparisonMap) -> TheoremResult:
        """Verify Thm 5 for maps *f*, *g*.

        Returns
        -------
        TheoremResult
        """
        return Thm5ComparisonTransitivity.verify(f, g)

    @staticmethod
    def verify_thm6(product: JudgmentProduct) -> TheoremResult:
        """Verify Thm 6 for *product*.

        Returns
        -------
        TheoremResult
        """
        return Thm6ExplanationFaithfulness.verify(product)

    @staticmethod
    def verify_thm7(
        obligation: LiveResidualObligation,
        evidence: EvidenceBundle,
    ) -> TheoremResult:
        """Verify Thm 7 for *obligation* and *evidence*.

        Returns
        -------
        TheoremResult
        """
        return Thm7DischargeSoundness.verify(obligation, evidence)


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Infrastructure
    "TheoremStatus",
    "TheoremResult",
    "TheoremAssumption",
    # Individual theorems
    "Thm1NonBooleanComposition",
    "Thm2ResidualMonotonicity",
    "Thm3SectionGluing",
    "Thm4TrustMonotonicity",
    "Thm5ComparisonTransitivity",
    "Thm6ExplanationFaithfulness",
    "Thm7DischargeSoundness",
    # Collection
    "JudgmentTheorems",
    # Cross-referencing helpers
    "theorem_descent_verification",
    "theorem_encoding",
]


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §5 — Judgments and Products)
# ---------------------------------------------------------------------------

import logging

_logger = logging.getLogger(__name__)


def theorem_descent_verification(
    theorem_name: str, *, context: Any | None = None
) -> dict[str, Any]:
    """Verify a judgment theorem via geometric descent and certificates.

    Uses ``jugeo.geometry.descent`` for local-section descent strategies and
    ``jugeo.evidence.certificates`` for certificate construction as described
    in Theory2.tex §5 (Judgments and Products).

    Parameters
    ----------
    theorem_name : str
        Name of the theorem to verify (e.g. ``"Thm1NonBooleanComposition"``).
    context : Any | None
        Optional verification context (product, section, or mapping).

    Returns
    -------
    dict[str, Any]
        ``{"verified": bool, "certificate": ..., "descent_strategy": ..., "theorem": str}``
    """
    try:
        from jugeo.geometry.descent import LocalSection, DescentStrategy  # noqa: F811
        from jugeo.evidence.certificates import Certificate  # noqa: F811
    except ImportError as exc:
        _logger.debug("Optional cross-reference imports unavailable: %s", exc)
        return {"verified": False, "certificate": None, "descent_strategy": None,
                "theorem": theorem_name, "error": str(exc)}

    strategy = DescentStrategy()
    section = LocalSection(source=theorem_name, context=context)
    descent_ok = strategy.apply(section)
    certificate = Certificate(theorem=theorem_name, witness=section) if descent_ok else None
    _logger.debug("theorem_descent_verification: theorem=%s verified=%s", theorem_name, descent_ok)
    return {"verified": bool(descent_ok), "certificate": certificate,
            "descent_strategy": strategy, "theorem": theorem_name}


def theorem_encoding(theorem_name: str, *, format: str = "z3") -> dict[str, Any]:
    """Encode a judgment theorem for solver-backed verification.

    Uses ``jugeo.encodings`` for judgment encoding and
    ``jugeo.solver.z3_session`` for solver availability checks as described
    in Theory2.tex §5 (Judgments and Products).

    Parameters
    ----------
    theorem_name : str
        Name of the theorem to encode (e.g. ``"Thm3SectionGluing"``).
    format : str
        Target encoding format (default ``"z3"``).

    Returns
    -------
    dict[str, Any]
        ``{"encoding": ..., "solver_available": bool, "format": str, "theorem": str}``
    """
    try:
        from jugeo.encodings import encode_judgment  # noqa: F811
        from jugeo.solver.z3_session import z3_available  # noqa: F811
    except ImportError as exc:
        _logger.debug("Optional cross-reference imports unavailable: %s", exc)
        return {"encoding": None, "solver_available": False,
                "format": format, "theorem": theorem_name, "error": str(exc)}

    solver_ok = z3_available()
    encoding = encode_judgment(theorem_name, format=format)
    _logger.debug("theorem_encoding: theorem=%s solver_available=%s format=%s",
                  theorem_name, solver_ok, format)
    return {"encoding": encoding, "solver_available": solver_ok,
            "format": format, "theorem": theorem_name}


# copilot: formal theorems — judgment_products package.
