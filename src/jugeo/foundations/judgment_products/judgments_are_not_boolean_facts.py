"""Section 01 — Judgments are not boolean facts.

Theory2.tex Chapter 5, Section 5.1.

This module argues (and implements) the core claim of Chapter 5: a
*judgment* in the jugeo semantic system is not a proposition that is
merely ``True`` or ``False``.  It is a **first-class semantic object**
carrying:

* A structured proposition (``Proposition``) with free variables.
* A carrier type (``Carrier``) naming what the claim is about.
* An evidence bundle (``EvidenceBundle``) with provenance.
* Residual obligations still to be resolved.
* Obstructions blocking full verification.
* A trust annotation from the ordered trust algebra.
* Full provenance of how the judgment was assembled.

The Boolean reduction *J → {True, False}* loses all of this structure.
Verification tools that reduce judgments to booleans discard the
semantic product and cannot compose, compare, or refine judgments
after the fact.

Classes
-------
* :class:`JudgmentAsObject` — wraps a ``Judgment`` and exposes it as a
  first-class value, blocking accidental boolean coercion.
* :class:`NonBooleanJudgment` — a judgment carrying explicit
  ``TruthDegree`` values beyond True/False (multi-valued truth).
* :class:`StructuredJudgment` — a judgment with an explicit
  structural decomposition into named sub-judgments.
* :class:`JudgmentComparison` — the result of comparing two
  ``JudgmentAsObject`` instances.
* :class:`JudgmentProductAlgebra` — static algebraic operations
  treating judgments as non-boolean objects.

References
----------
theory2.tex §5.1 Def 1, Prop 1–2, Thm 1.

# copilot: s01 — judgments are first-class semantic objects, not booleans.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    EvidenceItem,
    Judgment,
    JudgmentAlgebra,
    JudgmentBuilder,
    JudgmentClause,
    JudgmentStatus,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.judgments.sections import Section

from jugeo.foundations.judgment_products.models import (
    ComparisonMap,
    JudgmentProduct,
    LocalJudgmentSection,
    ProductKind,
    ProductStatus,
)


# ---------------------------------------------------------------------------
# Truth degree (multi-valued truth)
# ---------------------------------------------------------------------------


class TruthDegree(str, Enum):
    """Multi-valued truth degree for a non-boolean judgment.

    Members
    -------
    REFUTED
        The proposition has been directly contradicted by evidence.
    ABSENT
        No evidence has been presented in either direction.
    PARTIAL
        Some evidence supports the proposition; the rest is unresolved.
    CONDITIONAL
        The proposition holds subject to named residual conditions.
    SUPPORTED
        Strong evidence supports the proposition; no counter-evidence.
    CERTIFIED
        Formally certified with a proof or solver discharge.
    """

    REFUTED = "refuted"
    ABSENT = "absent"
    PARTIAL = "partial"
    CONDITIONAL = "conditional"
    SUPPORTED = "supported"
    CERTIFIED = "certified"

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------

    _ORDER: dict[str, int]

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)

    @classmethod
    def _numeric(cls, degree: "TruthDegree") -> int:
        _order = {
            "refuted": 0,
            "absent": 1,
            "partial": 2,
            "conditional": 3,
            "supported": 4,
            "certified": 5,
        }
        return _order[degree.value]

    def dominates(self, other: "TruthDegree") -> bool:
        """Return ``True`` iff this degree is strictly stronger than *other*.

        Parameters
        ----------
        other:
            The degree to compare against.

        Returns
        -------
        bool
        """
        return TruthDegree._numeric(self) > TruthDegree._numeric(other)

    def meet(self, other: "TruthDegree") -> "TruthDegree":
        """Return the meet (minimum) of this degree and *other*.

        The meet is the weakest of the two values under the natural
        ordering REFUTED < ABSENT < PARTIAL < CONDITIONAL < SUPPORTED
        < CERTIFIED.

        Parameters
        ----------
        other:
            The other degree.

        Returns
        -------
        TruthDegree
        """
        self_n = TruthDegree._numeric(self)
        other_n = TruthDegree._numeric(other)
        by_n = {v: k for k, v in {
            "refuted": 0, "absent": 1, "partial": 2,
            "conditional": 3, "supported": 4, "certified": 5,
        }.items()}
        return TruthDegree(by_n[min(self_n, other_n)])

    def join(self, other: "TruthDegree") -> "TruthDegree":
        """Return the join (maximum) of this degree and *other*.

        Parameters
        ----------
        other:
            The other degree.

        Returns
        -------
        TruthDegree
        """
        self_n = TruthDegree._numeric(self)
        other_n = TruthDegree._numeric(other)
        by_n = {v: k for k, v in {
            "refuted": 0, "absent": 1, "partial": 2,
            "conditional": 3, "supported": 4, "certified": 5,
        }.items()}
        return TruthDegree(by_n[max(self_n, other_n)])


# ---------------------------------------------------------------------------
# JudgmentAsObject
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgmentAsObject:
    """A ``Judgment`` wrapped as a first-class, non-boolean semantic value.

    This class deliberately prevents accidental boolean coercion.
    Calling ``bool(judgment_as_object)`` raises ``TypeError`` so that
    code cannot silently discard the rich semantic structure.

    Parameters
    ----------
    judgment:
        The wrapped ``Judgment`` instance.
    object_id:
        Stable identifier for this wrapper (defaults to a fresh UUID).
    canonical_label:
        A short, human-readable name for display purposes.
    frozen_at:
        ISO-8601 timestamp when the wrapper was created.
    extra:
        Arbitrary key-value metadata attached by the producing algorithm.
    """

    judgment: Judgment
    object_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    canonical_label: str = ""
    frozen_at: str = field(default_factory=lambda: __import__("time").strftime(
        "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()
    ))
    extra: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def proposition(self) -> Proposition:
        """Return the underlying proposition.

        Returns
        -------
        Proposition
        """
        return self.judgment.proposition

    def status(self) -> JudgmentStatus:
        """Return the judgment lifecycle status.

        Returns
        -------
        JudgmentStatus
        """
        return self.judgment.status

    def trust_level(self) -> TrustLevel:
        """Return the trust floor of the underlying judgment's evidence.

        Returns
        -------
        TrustLevel
        """
        if self.judgment.evidence.is_empty():
            return TrustLevel.UNVERIFIED
        weakest = self.judgment.evidence.weakest()
        return weakest.trust_level if weakest else TrustLevel.UNVERIFIED

    def residual_count(self) -> int:
        """Return the number of open residual obligations.

        Returns
        -------
        int
        """
        return len(self.judgment.obligations)

    def is_settled(self) -> bool:
        """Return ``True`` iff the judgment is fully settled.

        Returns
        -------
        bool
        """
        return self.judgment.status == JudgmentStatus.SETTLED

    def content_hash(self) -> str:
        """Compute a stable content hash for this object.

        Returns
        -------
        str
            16-character hex digest.
        """
        return self.judgment.content_hash()

    def to_product(self) -> JudgmentProduct:
        """Convert to an atomic :class:`JudgmentProduct`.

        Returns
        -------
        JudgmentProduct
            An atomic product containing this single judgment.
        """
        status = (
            ProductStatus.DISCHARGED
            if self.is_settled()
            else (
                ProductStatus.OBSTRUCTED
                if self.judgment.obstructions
                else ProductStatus.ASSEMBLED
            )
        )
        return JudgmentProduct(
            product_id=self.object_id,
            kind=ProductKind.ATOMIC,
            status=status,
            proposition_label=(
                self.canonical_label or self.judgment.proposition.formula
            ),
            constituent_hashes=(self.content_hash(),),
            evidence=self.judgment.evidence,
            residuals=self.judgment.obligations,
            obstructions=self.judgment.obstructions,
            trust=self.judgment.trust,
            provenance=self.judgment.provenance,
        )

    def with_label(self, label: str) -> "JudgmentAsObject":
        """Return a copy with an updated canonical label.

        Parameters
        ----------
        label:
            New label string.

        Returns
        -------
        JudgmentAsObject
        """
        return replace(self, canonical_label=label)

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "object_id": self.object_id,
            "canonical_label": self.canonical_label,
            "status": self.status().value,
            "trust_level": self.trust_level().name,
            "residual_count": self.residual_count(),
            "content_hash": self.content_hash(),
            "frozen_at": self.frozen_at,
        }

    # ------------------------------------------------------------------
    # Boolean coercion guard
    # ------------------------------------------------------------------

    def __bool__(self) -> bool:
        raise TypeError(
            f"JudgmentAsObject is not a boolean value.  "
            f"Use .is_settled(), .trust_level(), or .to_product() to "
            f"access the semantic structure of this judgment.\n"
            f"  judgment={self.judgment!r}"
        )

    def __repr__(self) -> str:
        return (
            f"JudgmentAsObject(id={self.object_id!r}, "
            f"label={self.canonical_label!r}, "
            f"status={self.status().value})"
        )


# ---------------------------------------------------------------------------
# NonBooleanJudgment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NonBooleanJudgment:
    """A judgment carrying a :class:`TruthDegree` value beyond True/False.

    This class makes the multi-valued truth structure of a judgment
    explicit.  Instead of a collapsed boolean, it carries:

    * ``degree`` — where on the REFUTED → CERTIFIED lattice this judgment sits.
    * ``basis`` — which evidence items contribute to the degree assignment.
    * ``conditions`` — named conditions that must hold for CONDITIONAL degrees.

    Theory reference: theory2.tex §5.1 Prop 1.

    Parameters
    ----------
    base:
        The underlying :class:`JudgmentAsObject`.
    degree:
        The multi-valued truth degree assigned to this judgment.
    basis:
        Tuple of evidence-item canonical keys supporting the degree.
    conditions:
        Named conditions required for CONDITIONAL or PARTIAL degrees.
    degree_justification:
        Free-text explanation of why this degree was assigned.
    """

    base: JudgmentAsObject
    degree: TruthDegree = TruthDegree.ABSENT
    basis: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    degree_justification: str = ""

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_certifiable(self) -> bool:
        """Return ``True`` iff the degree is CERTIFIED.

        Returns
        -------
        bool
        """
        return self.degree == TruthDegree.CERTIFIED

    def is_conditional(self) -> bool:
        """Return ``True`` iff the degree is CONDITIONAL (conditions pending).

        Returns
        -------
        bool
        """
        return self.degree == TruthDegree.CONDITIONAL

    def is_refuted(self) -> bool:
        """Return ``True`` iff the degree is REFUTED.

        Returns
        -------
        bool
        """
        return self.degree == TruthDegree.REFUTED

    def with_degree(self, degree: TruthDegree, justification: str = "") -> "NonBooleanJudgment":
        """Return a copy with an updated truth degree.

        Parameters
        ----------
        degree:
            The new ``TruthDegree``.
        justification:
            Optional free-text justification.

        Returns
        -------
        NonBooleanJudgment
        """
        return replace(self, degree=degree, degree_justification=justification)

    def meet(self, other: "NonBooleanJudgment") -> "NonBooleanJudgment":
        """Return the pointwise meet of this judgment and *other*.

        The result takes the meet of the two truth degrees and combines
        both basis sets.

        Parameters
        ----------
        other:
            The other ``NonBooleanJudgment``.

        Returns
        -------
        NonBooleanJudgment
        """
        combined_basis = tuple(set(self.basis) | set(other.basis))
        combined_conditions = tuple(set(self.conditions) | set(other.conditions))
        return replace(
            self,
            degree=self.degree.meet(other.degree),
            basis=combined_basis,
            conditions=combined_conditions,
            degree_justification=(
                f"meet({self.degree.value}, {other.degree.value})"
            ),
        )

    def join(self, other: "NonBooleanJudgment") -> "NonBooleanJudgment":
        """Return the pointwise join of this judgment and *other*.

        The result takes the join of the two truth degrees.

        Parameters
        ----------
        other:
            The other ``NonBooleanJudgment``.

        Returns
        -------
        NonBooleanJudgment
        """
        combined_basis = tuple(set(self.basis) | set(other.basis))
        return replace(
            self,
            degree=self.degree.join(other.degree),
            basis=combined_basis,
            degree_justification=(
                f"join({self.degree.value}, {other.degree.value})"
            ),
        )

    def resolve_conditions(self, resolved: Sequence[str]) -> "NonBooleanJudgment":
        """Return a copy with *resolved* conditions removed.

        If all conditions are resolved and degree was CONDITIONAL, promotes
        the degree to SUPPORTED.

        Parameters
        ----------
        resolved:
            Condition labels that have been discharged.

        Returns
        -------
        NonBooleanJudgment
        """
        remaining = tuple(c for c in self.conditions if c not in resolved)
        new_degree = self.degree
        if not remaining and self.degree == TruthDegree.CONDITIONAL:
            new_degree = TruthDegree.SUPPORTED
        return replace(
            self,
            conditions=remaining,
            degree=new_degree,
            degree_justification=(
                f"Conditions resolved: {list(resolved)}"
                if resolved else self.degree_justification
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "base": self.base.to_mapping(),
            "degree": self.degree.value,
            "basis": list(self.basis),
            "conditions": list(self.conditions),
            "degree_justification": self.degree_justification,
        }

    def __repr__(self) -> str:
        return (
            f"NonBooleanJudgment(degree={self.degree.value}, "
            f"base={self.base.object_id!r}, "
            f"conditions={len(self.conditions)})"
        )


# ---------------------------------------------------------------------------
# StructuredJudgment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructuredJudgment:
    """A judgment with an explicit structural decomposition into sub-judgments.

    A ``StructuredJudgment`` models the case where a judgment is
    assembled from named *components*, each of which is itself a
    ``NonBooleanJudgment``.  The top-level degree is the meet of all
    component degrees.

    Theory reference: theory2.tex §5.1 Prop 2.

    Parameters
    ----------
    components:
        Ordered tuple of ``(name, NonBooleanJudgment)`` pairs.
    overall_degree:
        Pre-computed overall degree (the meet of all components).
    structural_label:
        Name for the structural decomposition (e.g. ``"contract_check"``).
    composition_rule:
        Description of how component degrees are combined (e.g.
        ``"meet"`` or ``"weakest_link"``).
    missing_components:
        Names of components not yet assembled.
    """

    components: tuple[tuple[str, NonBooleanJudgment], ...] = ()
    overall_degree: TruthDegree = TruthDegree.ABSENT
    structural_label: str = ""
    composition_rule: str = "meet"
    missing_components: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_components(
        cls,
        components: Sequence[tuple[str, NonBooleanJudgment]],
        label: str = "",
    ) -> "StructuredJudgment":
        """Build a ``StructuredJudgment`` from a sequence of named components.

        The overall degree is computed as the meet of all component degrees.

        Parameters
        ----------
        components:
            Sequence of ``(name, NonBooleanJudgment)`` pairs.
        label:
            Optional structural label.

        Returns
        -------
        StructuredJudgment
        """
        comps = tuple(components)
        if not comps:
            return cls(
                components=comps,
                overall_degree=TruthDegree.ABSENT,
                structural_label=label,
            )
        degrees = [j.degree for _, j in comps]
        meet_degree = degrees[0]
        for d in degrees[1:]:
            meet_degree = meet_degree.meet(d)
        return cls(
            components=comps,
            overall_degree=meet_degree,
            structural_label=label,
            composition_rule="meet",
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def component_names(self) -> tuple[str, ...]:
        """Return the names of all components in order.

        Returns
        -------
        tuple[str, ...]
        """
        return tuple(name for name, _ in self.components)

    def get_component(self, name: str) -> NonBooleanJudgment | None:
        """Return the component with the given *name*, or ``None``.

        Parameters
        ----------
        name:
            Component name to look up.

        Returns
        -------
        NonBooleanJudgment | None
        """
        for n, j in self.components:
            if n == name:
                return j
        return None

    def is_complete(self) -> bool:
        """Return ``True`` iff no components are missing.

        Returns
        -------
        bool
        """
        return len(self.missing_components) == 0

    def weakest_component(self) -> tuple[str, NonBooleanJudgment] | None:
        """Return the name and judgment of the weakest component.

        The weakest component has the minimal ``TruthDegree``.  If two
        components are equally weak, the first one (in order) is returned.

        Returns
        -------
        tuple[str, NonBooleanJudgment] | None
            None if there are no components.
        """
        if not self.components:
            return None
        name, weakest = self.components[0]
        for n, j in self.components[1:]:
            if j.degree.meet(weakest.degree) == j.degree:
                if j.degree != weakest.degree:
                    name, weakest = n, j
        return name, weakest

    def add_component(
        self, name: str, judgment: NonBooleanJudgment
    ) -> "StructuredJudgment":
        """Return a copy with a new component appended.

        The ``overall_degree`` is updated to the new meet.

        Parameters
        ----------
        name:
            Component name.
        judgment:
            The ``NonBooleanJudgment`` for this component.

        Returns
        -------
        StructuredJudgment
        """
        new_components = self.components + ((name, judgment),)
        return StructuredJudgment.from_components(
            new_components, label=self.structural_label
        )

    def recompute_degree(self) -> "StructuredJudgment":
        """Return a copy with the overall degree recomputed from components.

        Returns
        -------
        StructuredJudgment
        """
        return StructuredJudgment.from_components(
            list(self.components), label=self.structural_label
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "structural_label": self.structural_label,
            "overall_degree": self.overall_degree.value,
            "composition_rule": self.composition_rule,
            "components": [
                {"name": n, "judgment": j.to_mapping()}
                for n, j in self.components
            ],
            "missing_components": list(self.missing_components),
            "is_complete": self.is_complete(),
        }

    def __repr__(self) -> str:
        return (
            f"StructuredJudgment(label={self.structural_label!r}, "
            f"degree={self.overall_degree.value}, "
            f"components={len(self.components)})"
        )


# ---------------------------------------------------------------------------
# JudgmentComparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgmentComparison:
    """The result of comparing two :class:`JudgmentAsObject` instances.

    This records the outcome of a pairwise comparison without reducing
    it to a boolean.  The comparison can discover:

    * Shared evidence (evidence items present in both).
    * Diverging evidence (items in one but not the other).
    * Residual correspondence (which residuals correspond).
    * Trust ordering (which judgment has stronger trust).

    Parameters
    ----------
    left_id:
        ``object_id`` of the left (source) judgment.
    right_id:
        ``object_id`` of the right (target) judgment.
    left_degree:
        Truth degree of the left judgment.
    right_degree:
        Truth degree of the right judgment.
    shared_evidence_keys:
        Evidence canonical keys present in both.
    left_only_evidence_keys:
        Evidence keys present only in the left judgment.
    right_only_evidence_keys:
        Evidence keys present only in the right judgment.
    residual_correspondence:
        Pairs ``(left_residual_label, right_residual_label)`` that
        correspond under the comparison.
    left_dominates:
        ``True`` iff the left judgment's truth degree dominates the right.
    notes:
        Free-text summary of the comparison.
    """

    left_id: str
    right_id: str
    left_degree: TruthDegree = TruthDegree.ABSENT
    right_degree: TruthDegree = TruthDegree.ABSENT
    shared_evidence_keys: tuple[str, ...] = ()
    left_only_evidence_keys: tuple[str, ...] = ()
    right_only_evidence_keys: tuple[str, ...] = ()
    residual_correspondence: tuple[tuple[str, str], ...] = ()
    left_dominates: bool | None = None
    notes: str = ""

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_equivalent(self) -> bool:
        """Return ``True`` iff both judgments have the same degree.

        Returns
        -------
        bool
        """
        return self.left_degree == self.right_degree

    def divergence_count(self) -> int:
        """Return the number of evidence items that differ between the two.

        Returns
        -------
        int
        """
        return len(self.left_only_evidence_keys) + len(self.right_only_evidence_keys)

    def to_comparison_map(self, map_id: str = "") -> ComparisonMap:
        """Convert to a :class:`ComparisonMap` model.

        Parameters
        ----------
        map_id:
            Optional stable ID for the resulting map.

        Returns
        -------
        ComparisonMap
        """
        from jugeo.judgments.comparisons import ComparisonMode as CM
        mode = (
            CM.EQUIVALENCE
            if self.is_equivalent()
            else (
                CM.REFINEMENT
                if self.left_dominates
                else CM.REGRESSION
            )
        )
        return ComparisonMap(
            map_id=map_id or f"cmp_{self.left_id[:6]}_{self.right_id[:6]}",
            source_id=self.left_id,
            target_id=self.right_id,
            mode=mode,
            is_morphism=self.is_equivalent() or bool(self.left_dominates),
            compatible_residual_pairs=self.residual_correspondence,
            notes=self.notes,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "left_id": self.left_id,
            "right_id": self.right_id,
            "left_degree": self.left_degree.value,
            "right_degree": self.right_degree.value,
            "shared_evidence": len(self.shared_evidence_keys),
            "divergence_count": self.divergence_count(),
            "residual_correspondence": [
                list(p) for p in self.residual_correspondence
            ],
            "left_dominates": self.left_dominates,
            "notes": self.notes,
        }

    def __repr__(self) -> str:
        return (
            f"JudgmentComparison({self.left_id!r} vs {self.right_id!r}, "
            f"degrees=({self.left_degree.value}, {self.right_degree.value}))"
        )


# ---------------------------------------------------------------------------
# JudgmentProductAlgebra
# ---------------------------------------------------------------------------


class JudgmentProductAlgebra:
    """Static algebraic operations for non-boolean judgment composition.

    All methods are pure functions (no side effects, no mutation).  They
    operate on the classes defined in this module and return new instances.

    Theory reference: theory2.tex §5.1 Thm 1.
    """

    # ------------------------------------------------------------------
    # Wrapping / unwrapping
    # ------------------------------------------------------------------

    @staticmethod
    def wrap(judgment: Judgment, label: str = "") -> JudgmentAsObject:
        """Wrap a :class:`Judgment` as a first-class object.

        Parameters
        ----------
        judgment:
            The ``Judgment`` to wrap.
        label:
            Optional canonical label.

        Returns
        -------
        JudgmentAsObject
        """
        return JudgmentAsObject(
            judgment=judgment,
            object_id=judgment.content_hash()[:12],
            canonical_label=label or judgment.proposition.formula[:60],
        )

    @staticmethod
    def assign_degree(obj: JudgmentAsObject) -> NonBooleanJudgment:
        """Assign a :class:`TruthDegree` to a :class:`JudgmentAsObject`.

        The degree is derived from the judgment's status and trust level:

        * SETTLED + VERIFIED_PROOF → CERTIFIED
        * SETTLED + SOLVER_DISCHARGED → SUPPORTED
        * CHALLENGED → CONDITIONAL
        * OBSTRUCTED → PARTIAL or REFUTED
        * PROPOSED → ABSENT or PARTIAL

        Parameters
        ----------
        obj:
            The ``JudgmentAsObject`` to classify.

        Returns
        -------
        NonBooleanJudgment
        """
        status = obj.status()
        trust = obj.trust_level()
        has_obs = bool(obj.judgment.obstructions)
        has_res = obj.residual_count() > 0

        if status == JudgmentStatus.OBSTRUCTED:
            degree = TruthDegree.REFUTED if trust == TrustLevel.CONTRADICTED else TruthDegree.PARTIAL
        elif status == JudgmentStatus.SETTLED:
            if trust >= TrustLevel.VERIFIED_PROOF:
                degree = TruthDegree.CERTIFIED
            elif trust >= TrustLevel.SOLVER_DISCHARGED:
                degree = TruthDegree.SUPPORTED
            else:
                degree = TruthDegree.SUPPORTED
        elif status == JudgmentStatus.CHALLENGED:
            degree = TruthDegree.CONDITIONAL
        elif has_res:
            degree = TruthDegree.CONDITIONAL if has_res and not has_obs else TruthDegree.PARTIAL
        else:
            degree = TruthDegree.ABSENT

        basis = tuple(
            item.canonical_key()
            for item in obj.judgment.evidence.items
        )
        return NonBooleanJudgment(
            base=obj,
            degree=degree,
            basis=basis,
            degree_justification=(
                f"status={status.value}, trust={trust.name}"
            ),
        )

    @staticmethod
    def compose_non_boolean(
        left: NonBooleanJudgment, right: NonBooleanJudgment
    ) -> NonBooleanJudgment:
        """Compose two ``NonBooleanJudgment`` instances via the meet.

        The composition uses the core :class:`JudgmentAlgebra` to compose
        the base judgments, then recomputes the degree.

        Parameters
        ----------
        left:
            The left operand.
        right:
            The right operand.

        Returns
        -------
        NonBooleanJudgment
        """
        composed_judgment = JudgmentAlgebra.compose(
            left.base.judgment, right.base.judgment
        )
        composed_obj = JudgmentProductAlgebra.wrap(
            composed_judgment,
            label=f"({left.base.canonical_label} ∘ {right.base.canonical_label})",
        )
        return left.meet(right) if left.degree.meet(right.degree) == left.degree else right.meet(left)

    @staticmethod
    def compare(
        left: JudgmentAsObject, right: JudgmentAsObject
    ) -> JudgmentComparison:
        """Compare two :class:`JudgmentAsObject` instances.

        Computes degree assignments for both, identifies shared vs.
        diverging evidence, and records residual correspondences.

        Parameters
        ----------
        left:
            The left judgment.
        right:
            The right judgment.

        Returns
        -------
        JudgmentComparison
        """
        left_nbj = JudgmentProductAlgebra.assign_degree(left)
        right_nbj = JudgmentProductAlgebra.assign_degree(right)

        left_keys = set(left_nbj.basis)
        right_keys = set(right_nbj.basis)
        shared = tuple(left_keys & right_keys)
        left_only = tuple(left_keys - right_keys)
        right_only = tuple(right_keys - left_keys)

        left_res = {
            o.description if hasattr(o, "description") else str(o)
            for o in left.judgment.obligations
        }
        right_res = {
            o.description if hasattr(o, "description") else str(o)
            for o in right.judgment.obligations
        }
        correspondence = tuple(
            (lr, rr) for lr in left_res for rr in right_res if lr == rr
        )

        left_dom = left_nbj.degree.dominates(right_nbj.degree)
        right_dom = right_nbj.degree.dominates(left_nbj.degree)
        dominates = True if left_dom else (False if right_dom else None)

        return JudgmentComparison(
            left_id=left.object_id,
            right_id=right.object_id,
            left_degree=left_nbj.degree,
            right_degree=right_nbj.degree,
            shared_evidence_keys=shared,
            left_only_evidence_keys=left_only,
            right_only_evidence_keys=right_only,
            residual_correspondence=correspondence,
            left_dominates=dominates,
            notes=(
                f"Degrees: {left_nbj.degree.value} vs {right_nbj.degree.value}"
            ),
        )

    @staticmethod
    def structurally_decompose(
        clauses: Sequence[JudgmentClause],
        base_obj: JudgmentAsObject,
    ) -> StructuredJudgment:
        """Build a :class:`StructuredJudgment` from a judgment's clauses.

        Each clause becomes one component of the structured judgment.

        Parameters
        ----------
        clauses:
            The ``JudgmentClause`` objects to decompose into.
        base_obj:
            The base ``JudgmentAsObject`` providing the root judgment.

        Returns
        -------
        StructuredJudgment
        """
        components: list[tuple[str, NonBooleanJudgment]] = []
        for clause in clauses:
            satisfied = clause.satisfied
            if satisfied is True:
                deg = TruthDegree.SUPPORTED
            elif satisfied is False:
                deg = TruthDegree.REFUTED
            else:
                deg = TruthDegree.CONDITIONAL if clause.obligations else TruthDegree.ABSENT
            nbj = NonBooleanJudgment(
                base=base_obj,
                degree=deg,
                basis=clause.evidence_channels,
                conditions=clause.obligations,
                degree_justification=f"clause={clause.name}",
            )
            components.append((clause.name, nbj))
        missing = tuple(
            c.name for c in clauses if c.is_pending()
        )
        sj = StructuredJudgment.from_components(components, label=base_obj.canonical_label)
        return replace(sj, missing_components=missing)

    @staticmethod
    def collect_degrees(
        objects: Sequence[JudgmentAsObject],
    ) -> tuple[NonBooleanJudgment, ...]:
        """Assign degrees to every object in *objects*.

        Parameters
        ----------
        objects:
            Sequence of ``JudgmentAsObject`` instances to classify.

        Returns
        -------
        tuple[NonBooleanJudgment, ...]
        """
        return tuple(
            JudgmentProductAlgebra.assign_degree(obj) for obj in objects
        )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Enumerations
    "TruthDegree",
    # Models
    "JudgmentAsObject",
    "NonBooleanJudgment",
    "StructuredJudgment",
    "JudgmentComparison",
    # Algebra
    "JudgmentProductAlgebra",
]

# copilot: s01 — judgments are first-class semantic objects, not booleans.
