r"""Core data models for JuGeo Chapter 2 research questions and thesis claims.

This module provides the complete set of structured data models that represent
the research questions, thesis claims, evidence plans, falsification criteria,
and contribution boundaries of the JuGeo thesis Chapter 2:
*Research Questions and Thesis Claims*.

JuGeo (Judgment Geometry) treats every AI reasoning step as a typed judgment
tuple :math:`J = (c, \varphi, A, E, O, B, T, \Pi)`.  The four thesis claims
are:

* **C1** — The judgment tuple faithfully represents semantic state.
* **C2** — Solver, runtime, and copilot/oracle evidence can be federated
  without collapsing support kinds.
* **C3** — Long-horizon orchestration is formalisable as semantic control.
* **C4** — Mathematical ideation (discovery) occurs and is measurable within
  JuGeo's geometry.

Trust is an ordered algebra, not a scalar.  Each claim must be supported by
evidence from one or more channels, and all claims carry explicit falsification
criteria so that the thesis is empirically tractable.

Model hierarchy
---------------

.. code-block:: text

    ResearchQuestion
        └── ThesisClaim (1..*)
              ├── EvidencePlan (1)
              │     └── EvidenceItem (1..*)
              ├── FalsificationCriteria (1)
              │     └── FalsificationCondition (1..*)
              └── ContributionBoundary (1)

Design principles
-----------------

1. **Immutability** — All primary models are frozen dataclasses.  Updates
   produce new instances, mirroring the append-only audit log.

2. **Typed evidence** — Evidence items carry their channel name and trust
   ceiling, preventing silent promotion.

3. **Explicit falsification** — Every claim must declare at least one
   falsification condition.  A claim without a falsification condition is
   ``UNFALSIFIABLE`` and is treated as a documentation note, not a thesis
   claim.

4. **Copilot provenance tracking** — Evidence items that originate from a
   copilot/oracle channel are tagged and subjected to the ``COPILOT_SUGGESTED``
   trust ceiling until explicitly promoted.

Theory alignment
----------------

Section 210 of Theory2.tex states the four claims; section 220 states
falsification criteria.  This module is the Python ground truth for both.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ClaimCategory(Enum):
    """Broad category of a thesis claim."""

    REPRESENTATIONAL = "representational"
    EVIDENTIAL = "evidential"
    OPERATIONAL = "operational"
    CREATIVE = "creative"


class EvidenceChannel(Enum):
    """Named evidence channel.

    Channels are ordered by their trust ceiling in the trust algebra.
    ``MECHANICAL_PROOF`` is highest; ``COPILOT_ORACLE`` is below
    ``RUNTIME_WITNESS``.
    """

    MECHANICAL_PROOF = "mechanical_proof"
    SOLVER_DISCHARGE = "solver_discharge"
    RUNTIME_WITNESS = "runtime_witness"
    HUMAN_ATTESTATION = "human_attestation"
    COPILOT_ORACLE = "copilot_oracle"
    INFORMAL_ARGUMENT = "informal_argument"

    @property
    def trust_ceiling(self) -> str:
        """Return the maximum trust level name for this channel."""
        ceilings = {
            "mechanical_proof": "MECHANICALLY_VERIFIED",
            "solver_discharge": "SOLVER_DISCHARGED",
            "runtime_witness": "RUNTIME_WITNESSED",
            "human_attestation": "HUMAN_ATTESTED",
            "copilot_oracle": "COPILOT_SUGGESTED",
            "informal_argument": "UNVERIFIED",
        }
        return ceilings[self.value]

    @property
    def is_automated(self) -> bool:
        """Return True if this channel produces evidence without human review."""
        return self in (
            EvidenceChannel.MECHANICAL_PROOF,
            EvidenceChannel.SOLVER_DISCHARGE,
            EvidenceChannel.RUNTIME_WITNESS,
            EvidenceChannel.COPILOT_ORACLE,
        )


class ClaimStrength(Enum):
    """How strongly a claim is supported by its evidence plan."""

    UNDETERMINED = "undetermined"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"
    DECISIVE = "decisive"

    @property
    def ordinal(self) -> int:
        return {
            "undetermined": 0,
            "weak": 1,
            "moderate": 2,
            "strong": 3,
            "decisive": 4,
        }[self.value]


class FalsificationOutcome(Enum):
    """Outcome of evaluating a falsification condition."""

    NOT_TESTED = "not_tested"
    PASSED = "passed"
    FALSIFIED = "falsified"
    INCONCLUSIVE = "inconclusive"


class ContributionScope(Enum):
    """Scope of a contribution boundary."""

    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    FUTURE_WORK = "future_work"
    RELATED_WORK = "related_work"


# ---------------------------------------------------------------------------
# Evidence items
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceItem:
    """A single item of evidence supporting a thesis claim.

    Parameters
    ----------
    item_id:
        Short identifier, e.g. ``"E1.1"``.
    description:
        Human-readable description of what this evidence is.
    channel:
        The :class:`EvidenceChannel` through which this evidence is produced.
    artifact_path:
        Dotted Python path to the artifact (module, class, or function) that
        generates or records this evidence.
    required:
        Whether this evidence item is required for the claim to be verified.
        Optional items provide additional corroboration but are not blocking.
    copilot_generated:
        Whether this evidence was produced with copilot assistance.  Copilot-
        generated evidence is subject to the ``COPILOT_SUGGESTED`` trust
        ceiling and requires explicit human promotion to advance.
    notes:
        Free-form notes from the researcher.
    """

    item_id: str
    description: str
    channel: EvidenceChannel
    artifact_path: str
    required: bool = True
    copilot_generated: bool = False
    notes: str = ""

    def effective_trust_ceiling(self) -> str:
        """Return the effective trust ceiling for this evidence item.

        If the item was copilot-generated, the ceiling is always
        ``COPILOT_SUGGESTED`` regardless of the channel, because copilot
        assistance introduces a semantic proposal that must be reviewed.
        """
        if self.copilot_generated:
            return "COPILOT_SUGGESTED"
        return self.channel.trust_ceiling

    def is_blocking(self) -> bool:
        """Return True if missing this evidence would block claim verification."""
        return self.required

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "description": self.description,
            "channel": self.channel.value,
            "artifact_path": self.artifact_path,
            "required": self.required,
            "copilot_generated": self.copilot_generated,
            "notes": self.notes,
            "effective_trust_ceiling": self.effective_trust_ceiling(),
        }


# ---------------------------------------------------------------------------
# EvidencePlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidencePlan:
    """A structured plan for gathering evidence to support a thesis claim.

    An evidence plan enumerates the items of evidence required and optional,
    specifies the channels through which evidence is expected to arrive, and
    declares the aggregation strategy (how individual items combine into a
    overall claim-strength assessment).

    Parameters
    ----------
    plan_id:
        Short identifier, e.g. ``"EP-C1"``.
    claim_id:
        Identifier of the :class:`ThesisClaim` this plan belongs to.
    items:
        Tuple of :class:`EvidenceItem` objects.
    aggregation_strategy:
        Name of the aggregation strategy: ``"all_required"`` (all required
        items must be satisfied), ``"majority_required"`` (majority of
        required items), or ``"any_required"`` (at least one).
    target_strength:
        The :class:`ClaimStrength` that a successful plan should achieve.
    deadline_hint:
        Optional prose hint about when this plan should be executed.
    """

    plan_id: str
    claim_id: str
    items: tuple[EvidenceItem, ...]
    aggregation_strategy: str
    target_strength: ClaimStrength
    deadline_hint: str = ""

    _VALID_STRATEGIES = frozenset(
        ["all_required", "majority_required", "any_required"]
    )

    def __post_init__(self) -> None:
        if self.aggregation_strategy not in self._VALID_STRATEGIES:
            raise ValueError(
                f"Unknown aggregation_strategy: {self.aggregation_strategy!r}. "
                f"Valid: {sorted(self._VALID_STRATEGIES)}"
            )

    def required_items(self) -> list[EvidenceItem]:
        """Return only the required evidence items."""
        return [i for i in self.items if i.required]

    def optional_items(self) -> list[EvidenceItem]:
        """Return only the optional evidence items."""
        return [i for i in self.items if not i.required]

    def copilot_items(self) -> list[EvidenceItem]:
        """Return evidence items that were copilot-generated."""
        return [i for i in self.items if i.copilot_generated]

    def channel_coverage(self) -> dict[str, int]:
        """Return a count of items per channel.

        Useful for assessing whether the plan is diverse enough across
        evidence channels.
        """
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.channel.value] = counts.get(item.channel.value, 0) + 1
        return counts

    def is_balanced(self) -> bool:
        """Return True if the plan uses at least two distinct channels.

        A single-channel plan is fragile; the thesis claims require evidence
        from at least solver + runtime or solver + copilot-review channels.
        """
        return len(self.channel_coverage()) >= 2

    def estimate_strength(self, completed_item_ids: frozenset[str]) -> ClaimStrength:
        """Estimate the current claim strength given a set of completed items.

        Parameters
        ----------
        completed_item_ids:
            Set of ``item_id`` values for items whose evidence has been
            gathered and accepted.

        Returns
        -------
        ClaimStrength
            Estimated strength from ``UNDETERMINED`` to ``DECISIVE``.
        """
        required = self.required_items()
        if not required:
            return ClaimStrength.UNDETERMINED
        completed_required = [i for i in required if i.item_id in completed_item_ids]
        ratio = len(completed_required) / len(required)
        completed_optional = [
            i for i in self.optional_items() if i.item_id in completed_item_ids
        ]
        bonus = 0.1 * len(completed_optional)
        score = min(1.0, ratio + bonus)
        if score < 0.25:
            return ClaimStrength.UNDETERMINED
        if score < 0.5:
            return ClaimStrength.WEAK
        if score < 0.75:
            return ClaimStrength.MODERATE
        if score < 0.95:
            return ClaimStrength.STRONG
        return ClaimStrength.DECISIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "claim_id": self.claim_id,
            "items": [i.to_dict() for i in self.items],
            "aggregation_strategy": self.aggregation_strategy,
            "target_strength": self.target_strength.value,
            "deadline_hint": self.deadline_hint,
        }


# ---------------------------------------------------------------------------
# FalsificationCondition and FalsificationCriteria
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FalsificationCondition:
    """A single testable condition that, if met, would falsify a claim.

    Parameters
    ----------
    condition_id:
        Short identifier, e.g. ``"FC1.1"``.
    description:
        Human-readable description of the falsification condition.
    test_procedure:
        Dotted Python path to the callable that evaluates this condition.
    expected_outcome:
        What a *passing* (non-falsifying) run looks like in prose.
    falsifying_observation:
        What would constitute a falsifying observation.
    severity:
        How severely falsification of this condition affects the thesis:
        ``"fatal"`` (thesis is wrong), ``"partial"`` (claim is weakened),
        or ``"minor"`` (refinement needed).
    """

    condition_id: str
    description: str
    test_procedure: str
    expected_outcome: str
    falsifying_observation: str
    severity: str
    outcome: FalsificationOutcome = FalsificationOutcome.NOT_TESTED

    _VALID_SEVERITIES = frozenset(["fatal", "partial", "minor"])

    def __post_init__(self) -> None:
        if self.severity not in self._VALID_SEVERITIES:
            raise ValueError(
                f"severity must be one of {sorted(self._VALID_SEVERITIES)}, "
                f"got {self.severity!r}"
            )

    def is_fatal(self) -> bool:
        """Return True if this is a thesis-fatal falsification condition."""
        return self.severity == "fatal"

    def is_tested(self) -> bool:
        """Return True if this condition has been evaluated."""
        return self.outcome != FalsificationOutcome.NOT_TESTED

    def with_outcome(self, outcome: FalsificationOutcome) -> "FalsificationCondition":
        """Return a new instance with the given outcome recorded."""
        from dataclasses import replace
        return replace(self, outcome=outcome)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "description": self.description,
            "test_procedure": self.test_procedure,
            "expected_outcome": self.expected_outcome,
            "falsifying_observation": self.falsifying_observation,
            "severity": self.severity,
            "outcome": self.outcome.value,
        }


@dataclass(frozen=True)
class FalsificationCriteria:
    """Complete falsification criteria for a thesis claim.

    Aggregates all :class:`FalsificationCondition` objects for one claim and
    provides methods for determining overall falsification status.

    Parameters
    ----------
    criteria_id:
        Short identifier, e.g. ``"FC-C1"``.
    claim_id:
        Identifier of the claim these criteria apply to.
    conditions:
        Tuple of :class:`FalsificationCondition` objects.
    requires_all_passed:
        If True, the claim is verified only when *all* conditions pass.
        If False, the claim is verified when no fatal condition is falsified.
    """

    criteria_id: str
    claim_id: str
    conditions: tuple[FalsificationCondition, ...]
    requires_all_passed: bool = True

    def is_falsified(self) -> bool:
        """Return True if any condition has been falsified."""
        return any(
            c.outcome == FalsificationOutcome.FALSIFIED for c in self.conditions
        )

    def is_fatally_falsified(self) -> bool:
        """Return True if any fatal condition has been falsified."""
        return any(
            c.outcome == FalsificationOutcome.FALSIFIED and c.is_fatal()
            for c in self.conditions
        )

    def untested_conditions(self) -> list[FalsificationCondition]:
        """Return conditions that have not yet been evaluated."""
        return [c for c in self.conditions if not c.is_tested()]

    def passed_conditions(self) -> list[FalsificationCondition]:
        """Return conditions that passed their test."""
        return [
            c for c in self.conditions if c.outcome == FalsificationOutcome.PASSED
        ]

    def coverage_fraction(self) -> float:
        """Return the fraction of conditions that have been tested."""
        if not self.conditions:
            return 1.0
        tested = sum(1 for c in self.conditions if c.is_tested())
        return tested / len(self.conditions)

    def overall_status(self) -> FalsificationOutcome:
        """Return the overall falsification status for this claim.

        Returns
        -------
        FalsificationOutcome
            ``FALSIFIED`` if any condition is falsified; ``INCONCLUSIVE`` if
            untested conditions remain; ``PASSED`` if all tested conditions
            passed and there are no untested ones; ``NOT_TESTED`` if nothing
            has been run.
        """
        if not any(c.is_tested() for c in self.conditions):
            return FalsificationOutcome.NOT_TESTED
        if self.is_falsified():
            return FalsificationOutcome.FALSIFIED
        if self.untested_conditions():
            return FalsificationOutcome.INCONCLUSIVE
        return FalsificationOutcome.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "criteria_id": self.criteria_id,
            "claim_id": self.claim_id,
            "conditions": [c.to_dict() for c in self.conditions],
            "requires_all_passed": self.requires_all_passed,
            "overall_status": self.overall_status().value,
            "coverage_fraction": self.coverage_fraction(),
        }


# ---------------------------------------------------------------------------
# ContributionBoundary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContributionBoundaryItem:
    """A single item in a contribution boundary.

    Parameters
    ----------
    item:
        Short description of the item.
    scope:
        :class:`ContributionScope` classification.
    rationale:
        Why this item is in or out of scope.
    """

    item: str
    scope: ContributionScope
    rationale: str

    def is_in_scope(self) -> bool:
        return self.scope == ContributionScope.IN_SCOPE

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "scope": self.scope.value,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class ContributionBoundary:
    """Explicit boundary of contributions for a thesis claim.

    Enumerates what the claim *does* contribute and what it explicitly *does
    not* claim, preventing over-generalisation during review.

    Parameters
    ----------
    boundary_id:
        Short identifier, e.g. ``"CB-C1"``.
    claim_id:
        Identifier of the claim this boundary applies to.
    items:
        Tuple of :class:`ContributionBoundaryItem` objects.
    """

    boundary_id: str
    claim_id: str
    items: tuple[ContributionBoundaryItem, ...]

    def in_scope(self) -> list[ContributionBoundaryItem]:
        return [i for i in self.items if i.is_in_scope()]

    def out_of_scope(self) -> list[ContributionBoundaryItem]:
        return [
            i
            for i in self.items
            if i.scope == ContributionScope.OUT_OF_SCOPE
        ]

    def future_work(self) -> list[ContributionBoundaryItem]:
        return [
            i
            for i in self.items
            if i.scope == ContributionScope.FUTURE_WORK
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "claim_id": self.claim_id,
            "items": [i.to_dict() for i in self.items],
        }


# ---------------------------------------------------------------------------
# ResearchQuestion
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchQuestion:
    """A top-level research question that the thesis aims to answer.

    Parameters
    ----------
    question_id:
        Short identifier, e.g. ``"RQ1"``.
    text:
        The research question in full, as it appears in the thesis.
    motivation:
        Why this question matters for the JuGeo research program.
    related_claim_ids:
        Tuple of claim identifiers whose answers contribute to this question.
    theory_section:
        Theory2.tex section that introduces this question.
    """

    question_id: str
    text: str
    motivation: str
    related_claim_ids: tuple[str, ...]
    theory_section: str

    def is_answered_by(self, claim_id: str) -> bool:
        """Return True if the given claim contributes to answering this question."""
        return claim_id in self.related_claim_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "motivation": self.motivation,
            "related_claim_ids": list(self.related_claim_ids),
            "theory_section": self.theory_section,
        }


# ---------------------------------------------------------------------------
# ThesisClaim — the central model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThesisClaim:
    """A structured thesis claim with evidence plan and falsification criteria.

    This is the central model of the research program.  It associates a claim
    identifier with its full evidence plan, falsification criteria, contribution
    boundary, and status information.

    Parameters
    ----------
    claim_id:
        Short identifier: ``"C1"`` through ``"C4"``.
    title:
        One-line claim title.
    statement:
        Full formal statement of the claim.
    category:
        :class:`ClaimCategory` classification.
    evidence_plan:
        :class:`EvidencePlan` for gathering supporting evidence.
    falsification_criteria:
        :class:`FalsificationCriteria` that would refute the claim.
    contribution_boundary:
        :class:`ContributionBoundary` delimiting the claim's scope.
    theory_section:
        Theory2.tex section that states this claim.
    strength:
        Current assessed :class:`ClaimStrength`.
    created_at:
        Unix timestamp when this claim was formalised.
    copilot_contribution:
        Description of any copilot-assisted component of the claim
        formalisation.  Empty string if no copilot involvement.
    """

    claim_id: str
    title: str
    statement: str
    category: ClaimCategory
    evidence_plan: EvidencePlan
    falsification_criteria: FalsificationCriteria
    contribution_boundary: ContributionBoundary
    theory_section: str
    strength: ClaimStrength = ClaimStrength.UNDETERMINED
    created_at: float = field(default_factory=time.time)
    copilot_contribution: str = ""

    def has_copilot_involvement(self) -> bool:
        """Return True if any component of this claim involved copilot assistance."""
        return bool(self.copilot_contribution) or bool(
            self.evidence_plan.copilot_items()
        )

    def is_falsifiable(self) -> bool:
        """Return True if the claim has at least one testable falsification condition."""
        return bool(self.falsification_criteria.conditions)

    def current_falsification_status(self) -> FalsificationOutcome:
        """Delegate to the falsification criteria for overall status."""
        return self.falsification_criteria.overall_status()

    def blocking_evidence_missing(self) -> list[EvidenceItem]:
        """Return required evidence items not yet recorded as completed.

        This is a conservative check: it returns all required items, since
        completion tracking is managed externally.  Caller must filter by
        their completion records.
        """
        return self.evidence_plan.required_items()

    def content_hash(self) -> str:
        """Return a SHA-256 digest of the claim's canonical representation."""
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "title": self.title,
            "statement": self.statement,
            "category": self.category.value,
            "evidence_plan": self.evidence_plan.to_dict(),
            "falsification_criteria": self.falsification_criteria.to_dict(),
            "contribution_boundary": self.contribution_boundary.to_dict(),
            "theory_section": self.theory_section,
            "strength": self.strength.value,
            "created_at": self.created_at,
            "copilot_contribution": self.copilot_contribution,
            "is_falsifiable": self.is_falsifiable(),
            "has_copilot_involvement": self.has_copilot_involvement(),
        }


# ---------------------------------------------------------------------------
# Canonical claim definitions
# ---------------------------------------------------------------------------


def _build_c1() -> ThesisClaim:
    """Construct the canonical definition of Thesis Claim C1.

    C1: The JuGeo judgment tuple (c,φ,A,E,O,B,T,Π) faithfully represents
    the full semantic state required for sound multi-agent reasoning.
    """
    plan = EvidencePlan(
        plan_id="EP-C1",
        claim_id="C1",
        items=(
            EvidenceItem(
                item_id="E1.1",
                description="Presheaf composition law holds for judgment tuples over arbitrary context morphisms",
                channel=EvidenceChannel.SOLVER_DISCHARGE,
                artifact_path="jugeo.thesis.research_program.representation.JudgmentPresheaf",
                required=True,
            ),
            EvidenceItem(
                item_id="E1.2",
                description="Coordinate system completeness: every semantic state maps to a unique coordinate",
                channel=EvidenceChannel.MECHANICAL_PROOF,
                artifact_path="jugeo.thesis.research_program.representation.CoordinateSystem",
                required=True,
            ),
            EvidenceItem(
                item_id="E1.3",
                description="Cover structure soundness: overlapping contexts agree on intersections",
                channel=EvidenceChannel.SOLVER_DISCHARGE,
                artifact_path="jugeo.thesis.research_program.representation.CoverStructure",
                required=True,
            ),
            EvidenceItem(
                item_id="E1.4",
                description="Copilot-assisted review of coordinate completeness argument",
                channel=EvidenceChannel.COPILOT_ORACLE,
                artifact_path="jugeo.thesis.research_program.representation.SemanticStateRepresentation",
                required=False,
                copilot_generated=True,
                notes="Copilot review is advisory; trust ceiling is COPILOT_SUGGESTED",
            ),
        ),
        aggregation_strategy="all_required",
        target_strength=ClaimStrength.STRONG,
    )
    criteria = FalsificationCriteria(
        criteria_id="FC-C1",
        claim_id="C1",
        conditions=(
            FalsificationCondition(
                condition_id="FC1.1",
                description="Presheaf composition law fails for some context morphism",
                test_procedure="jugeo.thesis.research_program.algorithms.falsification_test_suite.test_presheaf_composition",
                expected_outcome="Composition law holds for all tested morphisms",
                falsifying_observation="A context morphism exists for which f∘g ≠ presheaf(f)∘presheaf(g)",
                severity="fatal",
            ),
            FalsificationCondition(
                condition_id="FC1.2",
                description="Two distinct semantic states map to the same coordinate",
                test_procedure="jugeo.thesis.research_program.algorithms.falsification_test_suite.test_coordinate_injectivity",
                expected_outcome="Coordinate map is injective over the test suite",
                falsifying_observation="States s1 ≠ s2 with coordinate(s1) = coordinate(s2)",
                severity="fatal",
            ),
        ),
        requires_all_passed=True,
    )
    boundary = ContributionBoundary(
        boundary_id="CB-C1",
        claim_id="C1",
        items=(
            ContributionBoundaryItem(
                item="Full semantic-state representation for JuGeo judgment tuples",
                scope=ContributionScope.IN_SCOPE,
                rationale="This is the core representational claim of the thesis",
            ),
            ContributionBoundaryItem(
                item="General-purpose knowledge representation (e.g., Description Logics)",
                scope=ContributionScope.OUT_OF_SCOPE,
                rationale="JuGeo targets multi-agent judgment geometry specifically",
            ),
            ContributionBoundaryItem(
                item="Extension to continuous semantic spaces",
                scope=ContributionScope.FUTURE_WORK,
                rationale="Current treatment is discrete; continuous extension is open",
            ),
        ),
    )
    return ThesisClaim(
        claim_id="C1",
        title="Judgment tuple faithfully represents semantic state",
        statement=(
            "The judgment tuple J = (c, φ, A, E, O, B, T, Π) provides a "
            "sound and complete representation of semantic state for the "
            "purposes of multi-agent reasoning in JuGeo: distinct semantic "
            "states map to distinct tuples, and every admissible semantic "
            "transition is expressible as a tuple transformation."
        ),
        category=ClaimCategory.REPRESENTATIONAL,
        evidence_plan=plan,
        falsification_criteria=criteria,
        contribution_boundary=boundary,
        theory_section="§230",
        strength=ClaimStrength.MODERATE,
        copilot_contribution=(
            "Copilot assisted in drafting the presheaf naturality condition "
            "and the cover structure definition.  All resulting code carries "
            "COPILOT_SUGGESTED trust and has been reviewed."
        ),
    )


def _build_c2() -> ThesisClaim:
    """Construct the canonical definition of Thesis Claim C2.

    C2: Solver, runtime, and copilot/oracle evidence can be federated in a
    single judgment without collapsing their distinct support kinds.
    """
    plan = EvidencePlan(
        plan_id="EP-C2",
        claim_id="C2",
        items=(
            EvidenceItem(
                item_id="E2.1",
                description="Channel jurisdiction enforcement: copilot cannot exceed COPILOT_SUGGESTED ceiling",
                channel=EvidenceChannel.RUNTIME_WITNESS,
                artifact_path="jugeo.thesis.research_program.mixed_evidence.ChannelBoundary",
                required=True,
            ),
            EvidenceItem(
                item_id="E2.2",
                description="Federation kind-preservation: merging channels does not collapse to scalar trust",
                channel=EvidenceChannel.SOLVER_DISCHARGE,
                artifact_path="jugeo.thesis.research_program.mixed_evidence.FederationProtocol",
                required=True,
            ),
            EvidenceItem(
                item_id="E2.3",
                description="Jurisdiction map completeness: every admissible channel has a declared jurisdiction",
                channel=EvidenceChannel.HUMAN_ATTESTATION,
                artifact_path="jugeo.thesis.research_program.mixed_evidence.JurisdictionMap",
                required=True,
            ),
        ),
        aggregation_strategy="all_required",
        target_strength=ClaimStrength.STRONG,
    )
    criteria = FalsificationCriteria(
        criteria_id="FC-C2",
        claim_id="C2",
        conditions=(
            FalsificationCondition(
                condition_id="FC2.1",
                description="Copilot evidence silently exceeds its trust ceiling",
                test_procedure="jugeo.thesis.research_program.algorithms.falsification_test_suite.test_copilot_ceiling",
                expected_outcome="No copilot evidence item carries trust above COPILOT_SUGGESTED without explicit promotion",
                falsifying_observation="A copilot evidence item is recorded with trust > COPILOT_SUGGESTED without an explicit promotion record",
                severity="fatal",
            ),
            FalsificationCondition(
                condition_id="FC2.2",
                description="Federation collapses evidence kinds to a single scalar",
                test_procedure="jugeo.thesis.research_program.algorithms.falsification_test_suite.test_federation_kind_preservation",
                expected_outcome="Federated evidence retains per-channel kind labels",
                falsifying_observation="After federation, the output evidence is a scalar with no channel provenance",
                severity="fatal",
            ),
        ),
        requires_all_passed=True,
    )
    boundary = ContributionBoundary(
        boundary_id="CB-C2",
        claim_id="C2",
        items=(
            ContributionBoundaryItem(
                item="Federation of solver, runtime, and copilot channels",
                scope=ContributionScope.IN_SCOPE,
                rationale="These are the three primary evidence sources in JuGeo",
            ),
            ContributionBoundaryItem(
                item="Automated trust promotion without human review",
                scope=ContributionScope.OUT_OF_SCOPE,
                rationale="No silent promotion is permitted by the trust algebra",
            ),
        ),
    )
    return ThesisClaim(
        claim_id="C2",
        title="Mixed solver/runtime/copilot evidence is federatable without kind-collapse",
        statement=(
            "Given a judgment J with evidence from distinct channels "
            "(solver discharge, runtime witness, copilot/oracle proposal), "
            "the federation operation ⊕ produces a single evidence configuration "
            "that preserves channel provenance and respects each channel's trust "
            "ceiling, without collapsing the support kinds into a scalar."
        ),
        category=ClaimCategory.EVIDENTIAL,
        evidence_plan=plan,
        falsification_criteria=criteria,
        contribution_boundary=boundary,
        theory_section="§240",
        strength=ClaimStrength.MODERATE,
    )


def _build_c3() -> ThesisClaim:
    """Construct the canonical definition of Thesis Claim C3.

    C3: Long-horizon orchestration of multi-agent judgment tasks is
    formalisable as semantic control in JuGeo's geometry.
    """
    plan = EvidencePlan(
        plan_id="EP-C3",
        claim_id="C3",
        items=(
            EvidenceItem(
                item_id="E3.1",
                description="Lyapunov convergence: a semantic Lyapunov function exists for the orchestrator",
                channel=EvidenceChannel.MECHANICAL_PROOF,
                artifact_path="jugeo.thesis.research_program.long_horizon_orchestration.ConvergenceCondition",
                required=True,
            ),
            EvidenceItem(
                item_id="E3.2",
                description="Control law soundness: the orchestrator's control law is semantically admissible",
                channel=EvidenceChannel.SOLVER_DISCHARGE,
                artifact_path="jugeo.thesis.research_program.long_horizon_orchestration.ControlLawDefinition",
                required=True,
            ),
            EvidenceItem(
                item_id="E3.3",
                description="Horizon bound: orchestration terminates within a finite semantic horizon",
                channel=EvidenceChannel.RUNTIME_WITNESS,
                artifact_path="jugeo.thesis.research_program.long_horizon_orchestration.OrchestratorSpecification",
                required=True,
            ),
        ),
        aggregation_strategy="all_required",
        target_strength=ClaimStrength.STRONG,
        deadline_hint="Convergence proof is the critical path for C3 verification",
    )
    criteria = FalsificationCriteria(
        criteria_id="FC-C3",
        claim_id="C3",
        conditions=(
            FalsificationCondition(
                condition_id="FC3.1",
                description="Orchestrator diverges: no Lyapunov function exists",
                test_procedure="jugeo.thesis.research_program.algorithms.falsification_test_suite.test_orchestrator_convergence",
                expected_outcome="A semantic Lyapunov function V is found with V(J_t) decreasing",
                falsifying_observation="Proof search for a Lyapunov function terminates with UNSAT",
                severity="fatal",
            ),
        ),
        requires_all_passed=True,
    )
    boundary = ContributionBoundary(
        boundary_id="CB-C3",
        claim_id="C3",
        items=(
            ContributionBoundaryItem(
                item="Semantic control formalism for JuGeo orchestration",
                scope=ContributionScope.IN_SCOPE,
                rationale="Core orchestration claim",
            ),
            ContributionBoundaryItem(
                item="Real-time hard latency guarantees",
                scope=ContributionScope.OUT_OF_SCOPE,
                rationale="JuGeo targets semantic correctness, not wall-clock latency",
            ),
            ContributionBoundaryItem(
                item="Distributed fault tolerance",
                scope=ContributionScope.FUTURE_WORK,
                rationale="Fault tolerance requires extending the trust algebra",
            ),
        ),
    )
    return ThesisClaim(
        claim_id="C3",
        title="Long-horizon orchestration is formalised as semantic control",
        statement=(
            "Multi-agent task orchestration over long semantic horizons can be "
            "expressed as a control problem in JuGeo's judgment geometry: there "
            "exists a semantic Lyapunov function V such that the orchestrator's "
            "control law drives V(J_t) to zero along admissible trajectories."
        ),
        category=ClaimCategory.OPERATIONAL,
        evidence_plan=plan,
        falsification_criteria=criteria,
        contribution_boundary=boundary,
        theory_section="§250",
        strength=ClaimStrength.WEAK,
    )


def _build_c4() -> ThesisClaim:
    """Construct the canonical definition of Thesis Claim C4.

    C4: Mathematical ideation (discovery) occurs and is measurable within
    JuGeo's geometry.
    """
    plan = EvidencePlan(
        plan_id="EP-C4",
        claim_id="C4",
        items=(
            EvidenceItem(
                item_id="E4.1",
                description="Novelty measure non-degeneracy: measure is non-zero for genuinely new structures",
                channel=EvidenceChannel.SOLVER_DISCHARGE,
                artifact_path="jugeo.thesis.research_program.mathematical_ideation.NoveltyMeasure",
                required=True,
            ),
            EvidenceItem(
                item_id="E4.2",
                description="Purpose condition satisfaction: discovery engine satisfies stated purpose",
                channel=EvidenceChannel.RUNTIME_WITNESS,
                artifact_path="jugeo.thesis.research_program.mathematical_ideation.PurposeCondition",
                required=True,
            ),
            EvidenceItem(
                item_id="E4.3",
                description="Discovery engine termination: the engine terminates for all admissible inputs",
                channel=EvidenceChannel.MECHANICAL_PROOF,
                artifact_path="jugeo.thesis.research_program.mathematical_ideation.DiscoveryEngine",
                required=True,
            ),
            EvidenceItem(
                item_id="E4.4",
                description="Copilot-assisted brainstorming of ideation scenarios for novelty measure design",
                channel=EvidenceChannel.COPILOT_ORACLE,
                artifact_path="jugeo.thesis.research_program.mathematical_ideation.IdeationSpec",
                required=False,
                copilot_generated=True,
                notes="Brainstorming scenarios only; copilot trust ceiling applies",
            ),
        ),
        aggregation_strategy="all_required",
        target_strength=ClaimStrength.MODERATE,
    )
    criteria = FalsificationCriteria(
        criteria_id="FC-C4",
        claim_id="C4",
        conditions=(
            FalsificationCondition(
                condition_id="FC4.1",
                description="Novelty measure is degenerate (always zero or always nonzero)",
                test_procedure="jugeo.thesis.research_program.algorithms.falsification_test_suite.test_novelty_non_degeneracy",
                expected_outcome="Measure distinguishes novel from non-novel structures",
                falsifying_observation="μ(s) = 0 for all s, or μ(s) > 0 for all s including trivial structures",
                severity="fatal",
            ),
            FalsificationCondition(
                condition_id="FC4.2",
                description="Discovery engine does not terminate for some admissible input",
                test_procedure="jugeo.thesis.research_program.algorithms.falsification_test_suite.test_discovery_termination",
                expected_outcome="Engine terminates within the declared horizon bound",
                falsifying_observation="Engine runs indefinitely without producing a result",
                severity="partial",
            ),
        ),
        requires_all_passed=False,
    )
    boundary = ContributionBoundary(
        boundary_id="CB-C4",
        claim_id="C4",
        items=(
            ContributionBoundaryItem(
                item="Novelty measure for mathematical structures within JuGeo",
                scope=ContributionScope.IN_SCOPE,
                rationale="Core ideation claim",
            ),
            ContributionBoundaryItem(
                item="General artificial creativity or AGI ideation",
                scope=ContributionScope.OUT_OF_SCOPE,
                rationale="Claim is scoped to mathematical structures in JuGeo geometry",
            ),
            ContributionBoundaryItem(
                item="Empirical evaluation on large mathematical corpora",
                scope=ContributionScope.FUTURE_WORK,
                rationale="Requires curated corpus and compute budget beyond thesis scope",
            ),
        ),
    )
    return ThesisClaim(
        claim_id="C4",
        title="Mathematical ideation occurs and is measurable within JuGeo",
        statement=(
            "Within the JuGeo judgment geometry, a discovery engine can "
            "produce mathematical structures (conjectures, constructions, "
            "proof strategies) that are genuinely novel with respect to a "
            "defined novelty measure μ: StructureSpace → ℝ≥0, and the "
            "purpose condition P can be stated and verified within the "
            "same geometry."
        ),
        category=ClaimCategory.CREATIVE,
        evidence_plan=plan,
        falsification_criteria=criteria,
        contribution_boundary=boundary,
        theory_section="§260",
        strength=ClaimStrength.UNDETERMINED,
        copilot_contribution=(
            "Copilot contributed brainstorming scenarios for the novelty "
            "measure design.  These scenarios enter at COPILOT_SUGGESTED "
            "trust and were reviewed before being incorporated."
        ),
    )


# ---------------------------------------------------------------------------
# Module-level canonical claim instances
# ---------------------------------------------------------------------------

CLAIM_C1: ThesisClaim = _build_c1()
CLAIM_C2: ThesisClaim = _build_c2()
CLAIM_C3: ThesisClaim = _build_c3()
CLAIM_C4: ThesisClaim = _build_c4()

ALL_CLAIMS: tuple[ThesisClaim, ...] = (CLAIM_C1, CLAIM_C2, CLAIM_C3, CLAIM_C4)
"""All four canonical thesis claims in definition order."""


# ---------------------------------------------------------------------------
# Research question definitions
# ---------------------------------------------------------------------------


RESEARCH_QUESTIONS: tuple[ResearchQuestion, ...] = (
    ResearchQuestion(
        question_id="RQ1",
        text=(
            "Can a typed judgment tuple provide a sound and complete "
            "semantic-state representation for multi-agent AI reasoning?"
        ),
        motivation=(
            "Without a precise representation, claims about correctness and "
            "trust are informal and unverifiable.  RQ1 grounds the entire "
            "thesis in a concrete geometric object."
        ),
        related_claim_ids=("C1",),
        theory_section="§201",
    ),
    ResearchQuestion(
        question_id="RQ2",
        text=(
            "Can evidence from heterogeneous channels (solver, runtime, "
            "copilot/oracle) be combined without loss of provenance?"
        ),
        motivation=(
            "Real AI systems rely on many evidence sources.  If federation "
            "collapses provenance, trust algebra properties are lost and "
            "silent promotion becomes possible."
        ),
        related_claim_ids=("C2",),
        theory_section="§201",
    ),
    ResearchQuestion(
        question_id="RQ3",
        text=(
            "Can long-horizon multi-agent orchestration be formalised as "
            "a control problem in judgment geometry?"
        ),
        motivation=(
            "Orchestration is the operational backbone of JuGeo deployments. "
            "A control-theoretic formulation enables convergence guarantees "
            "that ad-hoc heuristics cannot provide."
        ),
        related_claim_ids=("C3",),
        theory_section="§201",
    ),
    ResearchQuestion(
        question_id="RQ4",
        text=(
            "Does the JuGeo geometry support measurable mathematical "
            "ideation (discovery)?"
        ),
        motivation=(
            "If JuGeo can measure and guide mathematical discovery, it "
            "becomes a platform for AI-assisted research, not merely a "
            "verification framework."
        ),
        related_claim_ids=("C4",),
        theory_section="§201",
    ),
)
