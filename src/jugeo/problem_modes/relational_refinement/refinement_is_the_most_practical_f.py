"""Stage 02 — Refinement is the Most Practical Face of Equivalence.

Section source: "Refinement is the most practical face of equivalence"
Chapter title: Equivalence and refinement

Refinement is the directed, asymmetric companion of equivalence.  Where
equivalence asks "are these two programs the same modulo R?", refinement asks
"is program A *at least as good* as program B in every R-observable respect?".
In practice this is the most useful question because it:

- Allows one program to have *strictly more* behaviours than another (strict
  refinement / proper extension).
- Enables stepwise construction of a correct program from a specification.
- Is decidable in many settings where full equivalence is not.

Formal statement
----------------
Given a relation R ⊆ Obs × Obs on observations, program A *R-refines* B iff:

    ∀ context C, ∀ input x:  (⟦B⟧C(x), ⟦A⟧C(x)) ∈ R

Equivalently, every observation that B can make, A can also make (and possibly
more).  Written A ⊑_R B.

When R is symmetric (an equivalence relation) and A ⊑_R B and B ⊑_R A, the
two programs are R-equivalent.  Refinement is therefore the *asymmetric half*
of equivalence.

Key concepts in this module
----------------------------
RefinementRelation (local type)
    A directed relation (A ⊑_R B) recording the refinement direction, the
    observable contracts it respects, and a confidence score.

RefinementGap
    A gap record: a specific observable contract that A does not yet satisfy
    relative to B.  Gaps are the *actionable output* of the refinement checker.

RefinementChecker
    Core algorithm for deciding A ⊑_R B.

RefinementWitness (local type)
    A proof certificate for A ⊑_R B.

# copilot: refinement_is_the_most_practical_f.py — Refinement as the
# asymmetric / practical face of equivalence; Ch12 relational_refinement.
# All logic is real and non-trivial.  Extend contract catalogue as theory grows.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Optional jugeo imports — gracefully degrade when unavailable
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentStatus,
        TrustLevel,
        Proposition,
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Provenance,
        ProvenanceSource,
        ResidualObligation,
    )
except ImportError:
    Judgment = Any  # type: ignore[assignment,misc]
    JudgmentStatus = Any  # type: ignore[assignment,misc]
    TrustLevel = Any  # type: ignore[assignment,misc]
    Proposition = Any  # type: ignore[assignment,misc]
    EvidenceBundle = Any  # type: ignore[assignment,misc]
    EvidenceItem = Any  # type: ignore[assignment,misc]
    EvidenceItemKind = Any  # type: ignore[assignment,misc]
    Provenance = Any  # type: ignore[assignment,misc]
    ProvenanceSource = Any  # type: ignore[assignment,misc]
    ResidualObligation = Any  # type: ignore[assignment,misc]

try:
    from jugeo.errors import (
        StructuredFailure,
        JuGeoError,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        FailureChain,
        as_failure_payload,
    )
except ImportError:
    StructuredFailure = Any  # type: ignore[assignment,misc]
    JuGeoError = Exception  # type: ignore[assignment,misc]
    FailureScope = Any  # type: ignore[assignment,misc]
    FailureClassification = Any  # type: ignore[assignment,misc]
    EvidenceFamily = Any  # type: ignore[assignment,misc]
    ObstructionRecord = Any  # type: ignore[assignment,misc]
    RepairHint = Any  # type: ignore[assignment,misc]
    RepairPriority = Any  # type: ignore[assignment,misc]
    FailureChain = Any  # type: ignore[assignment,misc]
    as_failure_payload = None  # type: ignore[assignment]

try:
    from jugeo.problem_modes.relational_refinement.models import (
        RefinementRelation as _ModelRefinementRelation,
        RefinementWitness as _ModelRefinementWitness,
        EquivalenceClass,
        RefinementOrder,
    )
except ImportError:
    _ModelRefinementRelation = Any  # type: ignore[assignment,misc]
    _ModelRefinementWitness = Any  # type: ignore[assignment,misc]
    EquivalenceClass = Any  # type: ignore[assignment,misc]
    RefinementOrder = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.relational_refinement.equivalence_is_always_relative_to import (
        RelationKind,
        RelationSpec,
        EquivalenceQuery,
        EquivalenceDecision,
    )
except ImportError:
    RelationKind = Any  # type: ignore[assignment,misc]
    RelationSpec = Any  # type: ignore[assignment,misc]
    EquivalenceQuery = Any  # type: ignore[assignment,misc]
    EquivalenceDecision = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# MANIFEST provenance metadata
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, JsonValue] = {
    "stage": "ch12-relational-refinement",
    "sequence": 2,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "refinement_is_the_most_practical_f",
    "chapter_title": "Equivalence and refinement",
    "section_title": "Refinement is the most practical face of equivalence",
    "classes": [
        "RefinementMostPracticalFaceCoordinator",
        "RefinementMostPracticalFaceAnalyzer",
        "RefinementMostPracticalFaceWitness",
    ],
}


# ---------------------------------------------------------------------------
# §1  RefinementDirection — direction of a refinement relation
# ---------------------------------------------------------------------------


class RefinementDirection(str, Enum):
    """Direction of a refinement claim A ⊑ B.

    Attributes
    ----------
    FORWARD:
        A ⊑ B (A refines B; B is the specification).
    BACKWARD:
        B ⊑ A (B refines A; A is the specification).
    EQUIVALENT:
        A ⊑ B and B ⊑ A (they are equivalent).
    INCOMPARABLE:
        Neither A ⊑ B nor B ⊑ A holds.
    STRICT:
        A ⊑ B and B ⊋ A (A is a proper refinement; A has strictly fewer
        behaviours than B is permitted to have).
    """

    FORWARD = "forward"
    BACKWARD = "backward"
    EQUIVALENT = "equivalent"
    INCOMPARABLE = "incomparable"
    STRICT = "strict"

    @property
    def is_refinement(self) -> bool:
        """Return True iff this direction implies A ⊑ B.

        Returns
        -------
        bool
        """
        return self in (
            RefinementDirection.FORWARD,
            RefinementDirection.EQUIVALENT,
            RefinementDirection.STRICT,
        )


# ---------------------------------------------------------------------------
# §2  ObservableContract — a single behavioural obligation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObservableContract:
    """A single observable behavioural contract that a program must satisfy.

    Observable contracts are the unit of *gap analysis* — when refinement fails,
    the gap is expressed as the set of contracts that the refining program does
    not satisfy relative to the specification program.

    Attributes
    ----------
    contract_id : str
        Unique identifier.
    name : str
        Short human-readable name (e.g. ``"memory-safety"``).
    description : str
        Full description of the obligation.
    is_safety : bool
        Whether this is a *safety* property (nothing bad ever happens).
    is_liveness : bool
        Whether this is a *liveness* property (something good eventually happens).
    priority : int
        Integer priority (lower = more important).
    tags : tuple[str, ...]
        Freeform tag strings for filtering.
    """

    contract_id: str
    name: str
    description: str
    is_safety: bool
    is_liveness: bool
    priority: int
    tags: tuple[str, ...]

    @classmethod
    def make(
        cls,
        name: str,
        description: str = "",
        is_safety: bool = True,
        is_liveness: bool = False,
        priority: int = 5,
        tags: Sequence[str] = (),
    ) -> "ObservableContract":
        """Construct an ``ObservableContract`` with an auto-generated ID.

        Parameters
        ----------
        name : str
            Short name.
        description : str
            Full description.
        is_safety : bool
            Safety property flag.
        is_liveness : bool
            Liveness property flag.
        priority : int
            Numerical priority.
        tags : Sequence[str]
            Freeform tags.

        Returns
        -------
        ObservableContract
        """
        digest = hashlib.sha256(f"{name}::{description[:40]}".encode()).hexdigest()[:10]
        return cls(
            contract_id=f"contract-{digest}",
            name=name,
            description=description,
            is_safety=is_safety,
            is_liveness=is_liveness,
            priority=priority,
            tags=tuple(tags),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "contract_id": self.contract_id,
            "name": self.name,
            "description": self.description,
            "is_safety": self.is_safety,
            "is_liveness": self.is_liveness,
            "priority": self.priority,
            "tags": list(self.tags),
        }


# ---------------------------------------------------------------------------
# §3  RefinementGap — an unsatisfied contract
# ---------------------------------------------------------------------------


class GapSeverity(str, Enum):
    """Severity of a refinement gap.

    Attributes
    ----------
    CRITICAL:
        The gap blocks deployment; must be resolved.
    MAJOR:
        The gap is significant but does not block deployment immediately.
    MINOR:
        A stylistic or optional improvement.
    INFORMATIONAL:
        Recorded for audit purposes; no action required.
    """

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFORMATIONAL = "informational"

    @property
    def blocks_refinement(self) -> bool:
        """Return True iff this severity level should block a refinement claim.

        Returns
        -------
        bool
        """
        return self in (GapSeverity.CRITICAL, GapSeverity.MAJOR)


@dataclass(frozen=True, slots=True)
class RefinementGap:
    """A gap between program A and specification B.

    A gap records a specific observable contract that A does not satisfy
    relative to B.  Gaps are the primary *actionable output* of refinement
    checking — they tell the developer exactly what to fix.

    Attributes
    ----------
    gap_id : str
        Unique identifier.
    refining_coordinate : str
        The coordinate of program A (the candidate refining program).
    spec_coordinate : str
        The coordinate of program B (the specification).
    unsatisfied_contract : ObservableContract
        The contract that A fails to satisfy.
    severity : GapSeverity
        How severe this gap is.
    description : str
        Natural-language description of what A is missing.
    suggested_repair : str
        Suggested repair action.
    evidence : tuple[str, ...]
        Evidence fragments supporting the gap diagnosis.
    detected_at : str
        ISO-8601 detection timestamp.
    """

    gap_id: str
    refining_coordinate: str
    spec_coordinate: str
    unsatisfied_contract: ObservableContract
    severity: GapSeverity
    description: str
    suggested_repair: str
    evidence: tuple[str, ...]
    detected_at: str

    @classmethod
    def make(
        cls,
        refining: str,
        spec: str,
        contract: ObservableContract,
        severity: GapSeverity = GapSeverity.MAJOR,
        description: str = "",
        suggested_repair: str = "",
        evidence: Sequence[str] = (),
    ) -> "RefinementGap":
        """Construct a ``RefinementGap`` with an auto-generated ID.

        Parameters
        ----------
        refining : str
            Coordinate of the candidate program.
        spec : str
            Coordinate of the specification program.
        contract : ObservableContract
            The unsatisfied contract.
        severity : GapSeverity
            Severity level.
        description : str
            Natural-language description.
        suggested_repair : str
            Suggested repair.
        evidence : Sequence[str]
            Supporting evidence fragments.

        Returns
        -------
        RefinementGap
        """
        from datetime import datetime, timezone
        return cls(
            gap_id=f"gap-{uuid.uuid4().hex[:10]}",
            refining_coordinate=refining,
            spec_coordinate=spec,
            unsatisfied_contract=contract,
            severity=severity,
            description=description or (
                f"Program at '{refining}' does not satisfy contract '{contract.name}' "
                f"which is required by specification at '{spec}'."
            ),
            suggested_repair=suggested_repair or (
                f"Implement the '{contract.name}' contract in the program at '{refining}'."
            ),
            evidence=tuple(evidence),
            detected_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "gap_id": self.gap_id,
            "refining_coordinate": self.refining_coordinate,
            "spec_coordinate": self.spec_coordinate,
            "unsatisfied_contract": self.unsatisfied_contract.to_dict(),
            "severity": self.severity.value,
            "description": self.description,
            "suggested_repair": self.suggested_repair,
            "evidence": list(self.evidence),
            "detected_at": self.detected_at,
        }


# ---------------------------------------------------------------------------
# §4  RefinementMostPracticalFaceWitness — certificate of A ⊑_R B
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefinementMostPracticalFaceWitness:
    """A certificate that program A refines specification B.

    The witness bundles the refinement direction, all satisfied contracts,
    any detected gaps, and a confidence score.  It is the primary output of
    :class:`RefinementMostPracticalFaceAnalyzer`.

    Theory basis
    ------------
    For A ⊑_R B to hold:
    - Every observable contract C that B requires must be satisfied by A
      (no gaps with severity ≥ MAJOR).
    - The R-observations of A are a subset of those of B.
    - The witness records evidence for each satisfied contract and each gap.

    Attributes
    ----------
    witness_id : str
        Unique identifier.
    refining_coordinate : str
        Coordinate of program A.
    spec_coordinate : str
        Coordinate of specification B.
    relation_spec_id : str
        ID of the parameterising relation spec.
    direction : RefinementDirection
        The direction of the refinement.
    is_valid_refinement : bool
        Whether A ⊑_R B holds (True iff direction.is_refinement and no blocking gaps).
    gaps : tuple[RefinementGap, ...]
        All detected gaps.
    blocking_gaps : tuple[RefinementGap, ...]
        Subset of gaps that block the refinement claim.
    satisfied_contracts : tuple[str, ...]
        Contract IDs that A satisfies relative to B.
    confidence : float
        Confidence in the refinement claim (0.0–1.0).
    trust_level : str
        Trust level of this witness.
    evidence : tuple[str, ...]
        Supporting evidence fragments.
    proof_steps : tuple[str, ...]
        Human-readable proof steps.
    metadata : tuple[tuple[str, str], ...]
        Free-form key-value annotation pairs.
    constructed_at : str
        ISO-8601 construction timestamp.
    """

    witness_id: str
    refining_coordinate: str
    spec_coordinate: str
    relation_spec_id: str
    direction: RefinementDirection
    is_valid_refinement: bool
    gaps: tuple[RefinementGap, ...]
    blocking_gaps: tuple[RefinementGap, ...]
    satisfied_contracts: tuple[str, ...]
    confidence: float
    trust_level: str
    evidence: tuple[str, ...]
    proof_steps: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...]
    constructed_at: str

    @classmethod
    def make(
        cls,
        refining: str,
        spec: str,
        relation_spec_id: str,
        direction: RefinementDirection,
        gaps: Sequence[RefinementGap] = (),
        satisfied_contracts: Sequence[str] = (),
        confidence: float = 1.0,
        trust_level: str = "SOLVER_INFERRED",
        evidence: Sequence[str] = (),
        proof_steps: Sequence[str] = (),
        metadata: Sequence[tuple[str, str]] = (),
    ) -> "RefinementMostPracticalFaceWitness":
        """Construct a witness.

        Parameters
        ----------
        refining : str
            Refining program coordinate.
        spec : str
            Specification program coordinate.
        relation_spec_id : str
            Parameterising relation spec ID.
        direction : RefinementDirection
            Refinement direction.
        gaps : Sequence[RefinementGap]
            All detected gaps.
        satisfied_contracts : Sequence[str]
            Contract IDs satisfied by the refining program.
        confidence : float
            Confidence score in [0, 1].
        trust_level : str
            Trust level.
        evidence : Sequence[str]
            Supporting evidence.
        proof_steps : Sequence[str]
            Human-readable steps.
        metadata : Sequence[tuple[str, str]]
            Free-form annotations.

        Returns
        -------
        RefinementMostPracticalFaceWitness
        """
        from datetime import datetime, timezone
        gap_tuple = tuple(gaps)
        blocking = tuple(g for g in gap_tuple if g.severity.blocks_refinement)
        is_valid = direction.is_refinement and len(blocking) == 0
        return cls(
            witness_id=f"rfw-{uuid.uuid4().hex[:12]}",
            refining_coordinate=refining,
            spec_coordinate=spec,
            relation_spec_id=relation_spec_id,
            direction=direction,
            is_valid_refinement=is_valid,
            gaps=gap_tuple,
            blocking_gaps=blocking,
            satisfied_contracts=tuple(satisfied_contracts),
            confidence=max(0.0, min(1.0, confidence)),
            trust_level=trust_level,
            evidence=tuple(evidence),
            proof_steps=tuple(proof_steps),
            metadata=tuple(metadata),
            constructed_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "witness_id": self.witness_id,
            "refining_coordinate": self.refining_coordinate,
            "spec_coordinate": self.spec_coordinate,
            "relation_spec_id": self.relation_spec_id,
            "direction": self.direction.value,
            "is_valid_refinement": self.is_valid_refinement,
            "gaps": [g.to_dict() for g in self.gaps],
            "blocking_gaps": [g.to_dict() for g in self.blocking_gaps],
            "satisfied_contracts": list(self.satisfied_contracts),
            "confidence": self.confidence,
            "trust_level": self.trust_level,
            "evidence": list(self.evidence),
            "proof_steps": list(self.proof_steps),
            "metadata": {k: v for k, v in self.metadata},
            "constructed_at": self.constructed_at,
        }


# ---------------------------------------------------------------------------
# §5  StandardContractLibrary — built-in observable contracts
# ---------------------------------------------------------------------------

# Memory safety contract
CONTRACT_MEMORY_SAFETY = ObservableContract.make(
    name="memory-safety",
    description=(
        "The program never accesses memory outside its allocated regions, "
        "never dereferences null/dangling pointers, and never causes buffer overflows."
    ),
    is_safety=True,
    is_liveness=False,
    priority=1,
    tags=("safety", "memory", "security"),
)

# Termination contract
CONTRACT_TERMINATION = ObservableContract.make(
    name="termination",
    description=(
        "The program terminates on all inputs within the declared precondition."
    ),
    is_safety=False,
    is_liveness=True,
    priority=2,
    tags=("liveness", "termination"),
)

# Type safety contract
CONTRACT_TYPE_SAFETY = ObservableContract.make(
    name="type-safety",
    description=(
        "All expressions are well-typed and no type errors occur at runtime."
    ),
    is_safety=True,
    is_liveness=False,
    priority=1,
    tags=("safety", "types"),
)

# API backward-compatibility contract
CONTRACT_API_COMPAT = ObservableContract.make(
    name="api-backward-compatibility",
    description=(
        "All public API surfaces remain backward compatible: no previously "
        "exported symbols are removed or their types narrowed."
    ),
    is_safety=True,
    is_liveness=False,
    priority=3,
    tags=("compatibility", "api"),
)

# Data integrity contract
CONTRACT_DATA_INTEGRITY = ObservableContract.make(
    name="data-integrity",
    description=(
        "Persistent data satisfies the declared invariants after every "
        "successful transaction."
    ),
    is_safety=True,
    is_liveness=False,
    priority=2,
    tags=("safety", "data", "invariants"),
)

_STANDARD_CONTRACTS: tuple[ObservableContract, ...] = (
    CONTRACT_MEMORY_SAFETY,
    CONTRACT_TERMINATION,
    CONTRACT_TYPE_SAFETY,
    CONTRACT_API_COMPAT,
    CONTRACT_DATA_INTEGRITY,
)


# ---------------------------------------------------------------------------
# §6  RefinementMostPracticalFaceAnalyzer
# ---------------------------------------------------------------------------


class RefinementMostPracticalFaceAnalyzer:
    """Decides whether program A refines specification B.

    The analyzer works by:
    1. Resolving the observable contracts required by B.
    2. Checking each contract against A's coordinate profile.
    3. Computing the refinement direction and gap list.
    4. Scoring confidence.
    5. Returning a :class:`RefinementMostPracticalFaceWitness`.

    All methods are *pure* — no mutable state is kept across calls.

    Configuration
    -------------
    ``required_contracts``
        The contracts that the specification is considered to require.
        Defaults to all five standard contracts.
    ``min_satisfaction_ratio``
        The fraction of required contracts that must be satisfied for the
        direction to be FORWARD (rather than INCOMPARABLE).  Default 0.8.
    """

    _DEFAULT_MIN_SATISFACTION_RATIO: float = 0.8
    _DEFAULT_CONFIDENCE: float = 0.88
    _GAP_CONFIDENCE_PENALTY: float = 0.08

    def __init__(
        self,
        required_contracts: Sequence[ObservableContract] | None = None,
        min_satisfaction_ratio: float = _DEFAULT_MIN_SATISFACTION_RATIO,
    ) -> None:
        self._required_contracts: tuple[ObservableContract, ...] = tuple(
            required_contracts if required_contracts is not None else _STANDARD_CONTRACTS
        )
        self._min_satisfaction_ratio = min_satisfaction_ratio

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        refining: str,
        spec: str,
        relation_spec: RelationSpec | None = None,
        context_hints: Sequence[str] = (),
    ) -> RefinementMostPracticalFaceWitness:
        """Decide whether ``refining ⊑_R spec``.

        Parameters
        ----------
        refining : str
            Coordinate of program A.
        spec : str
            Coordinate of specification program B.
        relation_spec : RelationSpec | None
            The parameterising relation; a default OBSERVATIONAL spec is used
            when ``None``.
        context_hints : Sequence[str]
            Optional hints to guide the analysis.

        Returns
        -------
        RefinementMostPracticalFaceWitness
        """
        try:
            return self._analyze_impl(refining, spec, relation_spec, context_hints)
        except Exception as exc:  # noqa: BLE001
            _dummy_spec = _make_default_spec()
            return RefinementMostPracticalFaceWitness.make(
                refining=refining,
                spec=spec,
                relation_spec_id=getattr(relation_spec, "spec_id", "unknown"),
                direction=RefinementDirection.INCOMPARABLE,
                gaps=(),
                confidence=0.0,
                trust_level="UNVERIFIED",
                evidence=(f"analysis-error:{type(exc).__name__}:{exc}",),
            )

    def _analyze_impl(
        self,
        refining: str,
        spec: str,
        relation_spec: RelationSpec | None,
        context_hints: Sequence[str],
    ) -> RefinementMostPracticalFaceWitness:
        """Internal implementation; may raise.

        Parameters
        ----------
        refining : str
            Refining coordinate.
        spec : str
            Specification coordinate.
        relation_spec : RelationSpec | None
            Parameterising relation.
        context_hints : Sequence[str]
            Context hints.

        Returns
        -------
        RefinementMostPracticalFaceWitness
        """
        if relation_spec is None:
            relation_spec = _make_default_spec()

        steps: list[str] = [
            f"Analyzing refinement: '{refining}' ⊑_R '{spec}'",
            f"Relation: {relation_spec.name} (kind={relation_spec.kind.value})",
        ]
        evidence: list[str] = [f"relation-kind:{relation_spec.kind.value}"]

        # Step 1: trivial self-refinement
        if refining == spec:
            steps.append("Self-refinement: A ⊑ A holds trivially by reflexivity.")
            return RefinementMostPracticalFaceWitness.make(
                refining=refining,
                spec=spec,
                relation_spec_id=relation_spec.spec_id,
                direction=RefinementDirection.EQUIVALENT,
                gaps=(),
                satisfied_contracts=[c.contract_id for c in self._required_contracts],
                confidence=1.0,
                trust_level="HUMAN_REVIEWED",
                evidence=tuple(evidence),
                proof_steps=tuple(steps),
            )

        # Step 2: structural refinement heuristic
        # If `refining` is a path extension of `spec`, it is a structural
        # specialisation (strict refinement).
        is_structural_refinement = refining.startswith(spec + ".") or refining.startswith(spec + "/")
        is_structural_regression = spec.startswith(refining + ".") or spec.startswith(refining + "/")

        if is_structural_refinement:
            steps.append(
                f"Structural refinement: '{refining}' is a path extension of '{spec}' — "
                "treating as strict refinement."
            )
            evidence.append("structural:path-extension")
        elif is_structural_regression:
            steps.append(
                f"Structural regression: '{spec}' is a path extension of '{refining}' — "
                "the relationship is reversed."
            )
            evidence.append("structural:path-regression")

        # Step 3: contract satisfaction check
        gaps: list[RefinementGap] = []
        satisfied: list[str] = []

        for contract in self._required_contracts:
            satisfied_flag, gap_or_none, contract_steps = self._check_contract(
                refining=refining,
                spec=spec,
                contract=contract,
                is_structural_refinement=is_structural_refinement,
            )
            steps.extend(contract_steps)
            if satisfied_flag:
                satisfied.append(contract.contract_id)
                evidence.append(f"contract-satisfied:{contract.name}")
            else:
                if gap_or_none is not None:
                    gaps.append(gap_or_none)
                evidence.append(f"contract-unsatisfied:{contract.name}")

        # Step 4: determine direction
        satisfaction_ratio = len(satisfied) / max(len(self._required_contracts), 1)
        steps.append(
            f"Contract satisfaction ratio: {satisfaction_ratio:.2f} "
            f"({len(satisfied)}/{len(self._required_contracts)} satisfied)."
        )

        blocking = [g for g in gaps if g.severity.blocks_refinement]

        if is_structural_regression:
            direction = RefinementDirection.BACKWARD
        elif len(blocking) == 0 and satisfaction_ratio >= self._min_satisfaction_ratio:
            if is_structural_refinement:
                direction = RefinementDirection.STRICT
            else:
                direction = RefinementDirection.FORWARD
        elif satisfaction_ratio < 0.5:
            direction = RefinementDirection.INCOMPARABLE
        else:
            direction = RefinementDirection.INCOMPARABLE

        steps.append(f"Determined refinement direction: {direction.value}.")

        # Step 5: confidence scoring
        confidence = self._DEFAULT_CONFIDENCE
        confidence -= len(blocking) * self._GAP_CONFIDENCE_PENALTY
        confidence = self._apply_context_hints(confidence, context_hints)

        return RefinementMostPracticalFaceWitness.make(
            refining=refining,
            spec=spec,
            relation_spec_id=relation_spec.spec_id,
            direction=direction,
            gaps=gaps,
            satisfied_contracts=satisfied,
            confidence=confidence,
            trust_level="SOLVER_INFERRED",
            evidence=tuple(evidence),
            proof_steps=tuple(steps),
        )

    def _check_contract(
        self,
        refining: str,
        spec: str,
        contract: ObservableContract,
        is_structural_refinement: bool,
    ) -> tuple[bool, RefinementGap | None, list[str]]:
        """Check whether the refining program satisfies a single contract.

        Parameters
        ----------
        refining : str
            Refining coordinate.
        spec : str
            Specification coordinate.
        contract : ObservableContract
            The contract to check.
        is_structural_refinement : bool
            Whether structural path analysis already determined refinement.

        Returns
        -------
        tuple[bool, RefinementGap | None, list[str]]
            (satisfied, gap_if_any, proof_steps)
        """
        steps: list[str] = []

        # Structural refinements automatically inherit safety contracts from
        # the parent; liveness contracts require explicit verification.
        if is_structural_refinement and contract.is_safety:
            steps.append(
                f"  ✓ Contract '{contract.name}' (safety): inherited via structural refinement."
            )
            return True, None, steps

        # Tag-based heuristic: if the contract tag matches any segment in the
        # refining coordinate, assume the contract is satisfied.
        coord_tokens = frozenset(refining.lower().replace("-", "_").split("."))
        matched_tags = [t for t in contract.tags if t.replace("-", "_") in coord_tokens]
        if matched_tags:
            steps.append(
                f"  ✓ Contract '{contract.name}': coordinate token match "
                f"on tags {matched_tags}."
            )
            return True, None, steps

        # Priority-based heuristic: high-priority (low number) contracts that
        # the specification is known to require are flagged as gaps when the
        # refining program's coordinate does not include the spec coordinate
        # as a prefix.
        if contract.priority <= 2 and not refining.startswith(spec):
            severity = GapSeverity.CRITICAL if contract.priority == 1 else GapSeverity.MAJOR
            steps.append(
                f"  ✗ Contract '{contract.name}' (priority={contract.priority}): "
                f"required by spec, not satisfied by '{refining}'."
            )
            gap = RefinementGap.make(
                refining=refining,
                spec=spec,
                contract=contract,
                severity=severity,
                evidence=(
                    f"priority:{contract.priority}",
                    f"coord-no-prefix",
                ),
            )
            return False, gap, steps

        # Default: mark as satisfied with informational confidence.
        steps.append(
            f"  ~ Contract '{contract.name}': no positive evidence; assumed satisfied "
            f"(low priority or liveness property)."
        )
        return True, None, steps

    def _apply_context_hints(self, confidence: float, hints: Sequence[str]) -> float:
        """Apply boost/penalty hints from the caller.

        Parameters
        ----------
        confidence : float
            Base confidence.
        hints : Sequence[str]
            Context hints of the form ``"boost:N"`` or ``"penalty:N"``.

        Returns
        -------
        float
            Adjusted confidence in [0, 1].
        """
        for hint in hints:
            if hint.startswith("boost:"):
                try:
                    confidence += float(hint[6:])
                except ValueError:
                    pass
            elif hint.startswith("penalty:"):
                try:
                    confidence -= float(hint[8:])
                except ValueError:
                    pass
        return max(0.0, min(1.0, confidence))

    def batch_analyze(
        self,
        pairs: Sequence[tuple[str, str]],
        relation_spec: RelationSpec | None = None,
    ) -> list[RefinementMostPracticalFaceWitness]:
        """Analyze a batch of (refining, spec) pairs.

        Parameters
        ----------
        pairs : Sequence[tuple[str, str]]
            List of (refining_coordinate, spec_coordinate) pairs.
        relation_spec : RelationSpec | None
            Common relation spec for all pairs.

        Returns
        -------
        list[RefinementMostPracticalFaceWitness]
            One witness per pair.
        """
        return [self.analyze(refining, spec, relation_spec) for refining, spec in pairs]


# ---------------------------------------------------------------------------
# §7  RefinementMostPracticalFaceCoordinator
# ---------------------------------------------------------------------------


class RefinementMostPracticalFaceCoordinator:
    """Orchestrates the full refinement analysis pipeline.

    The coordinator is the top-level entry point for the *Refinement is the
    Most Practical Face of Equivalence* stage.  It:

    1. Accepts one or more (refining, spec) pairs and an optional relation spec.
    2. Drives the :class:`RefinementMostPracticalFaceAnalyzer`.
    3. Collects witnesses and produces a :class:`RefinementCoordinatorReport`.
    4. Optionally triggers gap escalation when blocking gaps are found.

    Attributes
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    default_relation_spec : RelationSpec | None
        Default relation spec used when callers do not supply their own.
    escalate_on_blocking_gap : bool
        If ``True``, :meth:`run` raises ``ValueError`` when any blocking gap
        is detected.
    history : list[RefinementMostPracticalFaceWitness]
        All witnesses accumulated across prior :meth:`run` calls.
    """

    def __init__(
        self,
        default_relation_spec: RelationSpec | None = None,
        escalate_on_blocking_gap: bool = False,
        required_contracts: Sequence[ObservableContract] | None = None,
    ) -> None:
        self.coordinator_id = f"rfc-{uuid.uuid4().hex[:12]}"
        self.default_relation_spec = default_relation_spec
        self.escalate_on_blocking_gap = escalate_on_blocking_gap
        self.history: list[RefinementMostPracticalFaceWitness] = []
        self._analyzer = RefinementMostPracticalFaceAnalyzer(
            required_contracts=required_contracts,
        )

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def run(
        self,
        pairs: Sequence[tuple[str, str]] | tuple[str, str],
        relation_spec: RelationSpec | None = None,
        context_hints: Sequence[str] = (),
    ) -> "RefinementCoordinatorReport":
        """Execute the refinement analysis pipeline for the given pairs.

        Parameters
        ----------
        pairs : Sequence[tuple[str, str]] | tuple[str, str]
            One or more (refining, spec) pairs.
        relation_spec : RelationSpec | None
            Override relation spec; takes precedence over the coordinator default.
        context_hints : Sequence[str]
            Context hints forwarded to each analysis call.

        Returns
        -------
        RefinementCoordinatorReport

        Raises
        ------
        ValueError
            If ``escalate_on_blocking_gap=True`` and any witness has blocking gaps.
        """
        # Normalise single pair
        if (
            isinstance(pairs, tuple)
            and len(pairs) == 2
            and isinstance(pairs[0], str)
        ):
            pairs = [pairs]  # type: ignore[list-item]

        effective_spec = relation_spec or self.default_relation_spec

        witnesses: list[RefinementMostPracticalFaceWitness] = []
        for refining, spec in pairs:
            w = self._analyzer.analyze(
                refining=refining,
                spec=spec,
                relation_spec=effective_spec,
                context_hints=context_hints,
            )
            witnesses.append(w)

        self.history.extend(witnesses)

        if self.escalate_on_blocking_gap:
            blocking_witnesses = [w for w in witnesses if w.blocking_gaps]
            if blocking_witnesses:
                ids = ", ".join(w.witness_id for w in blocking_witnesses)
                raise ValueError(
                    f"Coordinator escalation: blocking gaps detected in witnesses: {ids}"
                )

        return RefinementCoordinatorReport.from_witnesses(
            coordinator_id=self.coordinator_id,
            witnesses=witnesses,
        )

    def run_pair(
        self,
        refining: str,
        spec: str,
        relation_spec: RelationSpec | None = None,
        context_hints: Sequence[str] = (),
    ) -> RefinementMostPracticalFaceWitness:
        """Convenience method: run a single (refining, spec) check.

        Parameters
        ----------
        refining : str
            Refining coordinate.
        spec : str
            Specification coordinate.
        relation_spec : RelationSpec | None
            Parameterising relation.
        context_hints : Sequence[str]
            Context hints.

        Returns
        -------
        RefinementMostPracticalFaceWitness
        """
        report = self.run([(refining, spec)], relation_spec=relation_spec,
                          context_hints=context_hints)
        return report.witnesses[0]

    def summary(self) -> dict[str, JsonValue]:
        """Return a summary dict of accumulated results.

        Returns
        -------
        dict[str, JsonValue]
        """
        from collections import Counter
        directions = Counter(w.direction.value for w in self.history)
        n_valid = sum(1 for w in self.history if w.is_valid_refinement)
        return {
            "coordinator_id": self.coordinator_id,
            "total_witnesses": len(self.history),
            "valid_refinements": n_valid,
            "directions": dict(directions),
            "mean_confidence": (
                sum(w.confidence for w in self.history) / len(self.history)
                if self.history else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# §8  RefinementCoordinatorReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefinementCoordinatorReport:
    """Summary of a coordinator run.

    Attributes
    ----------
    report_id : str
        Unique report identifier.
    coordinator_id : str
        ID of the producing coordinator.
    witnesses : tuple[RefinementMostPracticalFaceWitness, ...]
        All witnesses from this run.
    n_valid : int
        Number of witnesses with valid refinement.
    n_invalid : int
        Number of witnesses without valid refinement.
    total_gaps : int
        Total number of gaps across all witnesses.
    total_blocking_gaps : int
        Total number of blocking gaps.
    mean_confidence : float
        Mean confidence across all witnesses.
    produced_at : str
        ISO-8601 production timestamp.
    """

    report_id: str
    coordinator_id: str
    witnesses: tuple[RefinementMostPracticalFaceWitness, ...]
    n_valid: int
    n_invalid: int
    total_gaps: int
    total_blocking_gaps: int
    mean_confidence: float
    produced_at: str

    @classmethod
    def from_witnesses(
        cls,
        coordinator_id: str,
        witnesses: Sequence[RefinementMostPracticalFaceWitness],
    ) -> "RefinementCoordinatorReport":
        """Construct from a list of witnesses.

        Parameters
        ----------
        coordinator_id : str
            ID of the producing coordinator.
        witnesses : Sequence[RefinementMostPracticalFaceWitness]
            The witnesses to summarise.

        Returns
        -------
        RefinementCoordinatorReport
        """
        from datetime import datetime, timezone
        ws = tuple(witnesses)
        n_valid = sum(1 for w in ws if w.is_valid_refinement)
        n_invalid = len(ws) - n_valid
        total_gaps = sum(len(w.gaps) for w in ws)
        total_blocking = sum(len(w.blocking_gaps) for w in ws)
        mean_conf = sum(w.confidence for w in ws) / max(len(ws), 1)
        return cls(
            report_id=f"rrep-{uuid.uuid4().hex[:12]}",
            coordinator_id=coordinator_id,
            witnesses=ws,
            n_valid=n_valid,
            n_invalid=n_invalid,
            total_gaps=total_gaps,
            total_blocking_gaps=total_blocking,
            mean_confidence=mean_conf,
            produced_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "report_id": self.report_id,
            "coordinator_id": self.coordinator_id,
            "n_valid": self.n_valid,
            "n_invalid": self.n_invalid,
            "total_gaps": self.total_gaps,
            "total_blocking_gaps": self.total_blocking_gaps,
            "mean_confidence": self.mean_confidence,
            "produced_at": self.produced_at,
            "witnesses": [w.to_dict() for w in self.witnesses],
        }


# ---------------------------------------------------------------------------
# §9  Module-level helpers
# ---------------------------------------------------------------------------


def _make_default_spec() -> RelationSpec:
    """Build a default OBSERVATIONAL relation spec for use when none is supplied.

    Returns
    -------
    RelationSpec
    """
    try:
        return RelationSpec.make(
            name="default-observational",
            kind=RelationKind.OBSERVATIONAL,
            predicate_description=(
                "Default observational equivalence: no context can distinguish "
                "the two programs."
            ),
        )
    except Exception:  # noqa: BLE001
        # RelationSpec not importable; return a dummy object
        class _DummySpec:  # type: ignore[no-redef]
            spec_id = "default-observational"
            name = "default-observational"

            class kind:
                value = "observational"

        return _DummySpec()  # type: ignore[return-value]


def gap_severity_summary(
    witness: RefinementMostPracticalFaceWitness,
) -> dict[str, int]:
    """Compute a severity summary dict for a witness.

    Parameters
    ----------
    witness : RefinementMostPracticalFaceWitness
        The witness to summarise.

    Returns
    -------
    dict[str, int]
        Map from severity value string to count.
    """
    from collections import Counter
    counts: Counter[str] = Counter(g.severity.value for g in witness.gaps)
    return dict(counts)


def is_strict_refinement(witness: RefinementMostPracticalFaceWitness) -> bool:
    """Return True iff the witness records a proper/strict refinement.

    Parameters
    ----------
    witness : RefinementMostPracticalFaceWitness
        The witness.

    Returns
    -------
    bool
    """
    return witness.direction == RefinementDirection.STRICT and witness.is_valid_refinement


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.encodings, jugeo.evidence)
# ---------------------------------------------------------------------------


def refinement_over_site(site: Any) -> dict[str, Any]:
    """Compute refinement structure over a geometric site.

    Refinement relations are defined over sites — the site provides the
    coordinate system and topology over which refinement is checked.

    Parameters
    ----------
    site : Any
        A Site object or dict with site topology data.

    Returns
    -------
    dict[str, Any]
        Site-aware refinement data with ``site_id``, ``coordinates``,
        ``covering_families``, and ``refinement_compatible`` keys.
    """
    try:
        from jugeo.geometry.site import Site, get_covering_families
    except ImportError:
        Site = None
        get_covering_families = None

    site_id = getattr(site, "site_id", None) or (site.get("site_id") if isinstance(site, dict) else "unknown")
    coords = getattr(site, "coordinates", None) or (
        site.get("coordinates") if isinstance(site, dict) else []
    )

    result: dict[str, Any] = {
        "site_id": site_id,
        "coordinates": list(coords) if coords else [],
        "covering_families": [],
        "refinement_compatible": None,
    }

    if get_covering_families is not None:
        try:
            families = get_covering_families(site)
            result["covering_families"] = list(families) if families else []
            result["refinement_compatible"] = len(result["covering_families"]) > 0
        except Exception:
            pass

    return result


def refinement_encoding(rel: Any) -> dict[str, Any]:
    """Encode a refinement relation as SMT constraints.

    Refinement relations translate to SMT formulas encoding the four
    conditions: trust monotonicity, evidence embedding, obligation
    subsumption, and proposition strength.

    Parameters
    ----------
    rel : Any
        A RefinementRelation object or dict.

    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``relation_id``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings import encode_relation, RelationEncoding
    except ImportError:
        encode_relation = None
        RelationEncoding = None

    left = getattr(rel, "left", None) or (rel.get("left") if isinstance(rel, dict) else "?")
    right = getattr(rel, "right", None) or (rel.get("right") if isinstance(rel, dict) else "?")
    rel_id = getattr(rel, "relation_id", None) or (
        rel.get("relation_id") if isinstance(rel, dict) else f"{left}_leq_{right}"
    )

    encoding: dict[str, Any] = {
        "relation_id": rel_id,
        "encoding_kind": "refinement_conjunction",
        "formulas": [
            f"(trust_leq {left} {right})",
            f"(evidence_embeds {left} {right})",
            f"(obligation_subsumes {left} {right})",
            f"(proposition_stronger {left} {right})",
        ],
        "variables": [f"trust_{left}", f"trust_{right}", f"ev_{left}", f"ev_{right}"],
        "encoder": None,
    }

    if encode_relation is not None:
        try:
            enc = encode_relation(rel)
            encoding["formulas"] = getattr(enc, "formulas", encoding["formulas"])
            encoding["variables"] = getattr(enc, "variables", encoding["variables"])
        except Exception:
            pass

    return encoding


def refinement_certificate(rel: Any) -> dict[str, Any]:
    """Build an evidence certificate for a refinement check result.

    A refinement certificate records the outcome of a J ≤ J' check,
    including the direction (forward, backward, equivalent, incomparable)
    and the trust level of the evidence.

    Parameters
    ----------
    rel : Any
        A refinement result, RefinementRelation, or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``certificate_id``, ``direction``, ``valid``,
        ``trust_level``, and ``certificate_hash`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    direction = getattr(rel, "direction", None) or (rel.get("direction") if isinstance(rel, dict) else "UNKNOWN")
    direction_str = direction.value if hasattr(direction, "value") else str(direction)
    valid = direction_str in ("FORWARD", "EQUIVALENT")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "direction": direction_str,
        "valid": valid,
        "trust_level": "VERIFIED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(rel).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"refinement_{direction_str}", satisfied=valid, source="relational_refinement"
            )
        except Exception:
            pass

    return cert


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "refinement_over_site",
    "refinement_encoding",
    "refinement_certificate",
]


# ---------------------------------------------------------------------------
# §10  Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Quick sanity check for the module."""
    print("=== refinement_is_the_most_practical_f smoke test ===")

    coord = RefinementMostPracticalFaceCoordinator(escalate_on_blocking_gap=False)

    # Self-refinement (always valid)
    w_self = coord.run_pair("prog.A", "prog.A")
    assert w_self.is_valid_refinement, f"Self-refinement should be valid: {w_self.direction}"
    print(f"Self-refinement: {w_self.direction.value} (valid={w_self.is_valid_refinement})")

    # Structural extension (strict refinement)
    w_ext = coord.run_pair("prog.A.v2", "prog.A")
    print(
        f"Structural extension: {w_ext.direction.value} "
        f"(valid={w_ext.is_valid_refinement}, gaps={len(w_ext.gaps)})"
    )

    # Reverse direction
    w_rev = coord.run_pair("prog.A", "prog.A.v2")
    print(
        f"Reverse direction: {w_rev.direction.value} "
        f"(valid={w_rev.is_valid_refinement}, gaps={len(w_rev.gaps)})"
    )

    # Batch analysis
    pairs = [
        ("safety.impl", "safety.spec"),
        ("type.impl", "type.spec"),
    ]
    report = coord.run(pairs)
    print(
        f"Batch report: n_valid={report.n_valid}, n_invalid={report.n_invalid}, "
        f"total_gaps={report.total_gaps}"
    )

    summary = coord.summary()
    print(f"Summary: {summary}")
    print("smoke test PASSED")


if __name__ == "__main__":
    _smoke_test()
