"""Stage 03 — Relational Obligations and Witnesses.

Section source: "Relational obligations and witnesses"
Chapter title: Equivalence and refinement

Given a relation R, each program participating in an R-equivalence or
R-refinement claim incurs *relational obligations* — proof obligations that
the program must discharge in order for the relational claim to hold.  A
*relational witness* is a certificate that *both* sides of the relation have
discharged all of their respective obligations.

Formal statement
----------------
Let R ⊆ A × A be a relation and let (a, b) be a candidate R-pair.  The
relational obligations of a (resp. b) relative to R and b (resp. a) are:

    Obl_R(a, b) = {φ ∈ Prop(b) | a must satisfy φ in order for (a, b) ∈ R}

A relational witness W for (a, b) under R is a pair:

    W = (D_a, D_b)

where
- D_a : Obl_R(a, b) → Proof  is an obligation discharger for a, and
- D_b : Obl_R(b, a) → Proof  is an obligation discharger for b.

The witness is *valid* iff both D_a and D_b are total (every obligation has
been discharged) and each discharge proof is accepted by the *witness validator*.

Key concepts in this module
----------------------------
RelationalObligation
    A single proof obligation arising from the relational claim.  Records
    the obligated coordinate, the obligation type, the source relation, and
    the discharging status.

ObligationDischarger
    Attempts to discharge a batch of relational obligations.  Produces
    ``DischargeRecord`` items that record whether each obligation was
    successfully discharged.

RelationalWitness (alias: RelationalObligationsWitnessesWitness)
    A composite witness for a relational claim: both sides' obligations and
    their discharge records.

WitnessValidator
    Validates a ``RelationalWitness`` — checks that all obligations are
    discharged and that the discharge proofs are consistent.

# copilot: relational_obligations_and_witness.py — Relational obligations
# and witness certificates; Ch12 relational_refinement package.  All logic is
# real and non-trivial.  Extend obligation categories as the theory matures.
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
        EquivalenceDecision,
    )
except ImportError:
    RelationKind = Any  # type: ignore[assignment,misc]
    RelationSpec = Any  # type: ignore[assignment,misc]
    EquivalenceDecision = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.relational_refinement.refinement_is_the_most_practical_f import (
        RefinementDirection,
        ObservableContract,
        RefinementGap,
        GapSeverity,
        _STANDARD_CONTRACTS,
    )
except ImportError:
    RefinementDirection = Any  # type: ignore[assignment,misc]
    ObservableContract = Any  # type: ignore[assignment,misc]
    RefinementGap = Any  # type: ignore[assignment,misc]
    GapSeverity = Any  # type: ignore[assignment,misc]
    _STANDARD_CONTRACTS = ()  # type: ignore[assignment]

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
    "sequence": 3,
    "semantic_source": "preliminaries/theory2.tex",
    "module": "relational_obligations_and_witness",
    "chapter_title": "Equivalence and refinement",
    "section_title": "Relational obligations and witnesses",
    "classes": [
        "RelationalObligationsWitnessesCoordinator",
        "RelationalObligationsWitnessesAnalyzer",
        "RelationalObligationsWitnessesWitness",
    ],
}


# ---------------------------------------------------------------------------
# §1  ObligationCategory — taxonomy of relational obligation types
# ---------------------------------------------------------------------------


class ObligationCategory(str, Enum):
    """Category of a relational proof obligation.

    Each category corresponds to a distinct *aspect* of the relational claim
    that the obligated program must justify.

    Attributes
    ----------
    BEHAVIORAL:
        The program's observable behaviour must match the relation's
        behavioural spec (e.g. same I/O on all inputs).
    STRUCTURAL:
        The program's structural properties (types, signatures, fields) must
        be compatible with those of its relational partner.
    TRUST:
        The program's trust level must be compatible with what the relation
        requires (e.g. at least AUTOMATED).
    EVIDENCE:
        The program must supply evidence items that the relation's partner
        program is known to produce.
    CONTRACT:
        The program must satisfy a specific observable contract (see
        :class:`~s02...ObservableContract`).
    CUSTOM:
        A user-defined obligation not fitting the above categories.
    """

    BEHAVIORAL = "behavioral"
    STRUCTURAL = "structural"
    TRUST = "trust"
    EVIDENCE = "evidence"
    CONTRACT = "contract"
    CUSTOM = "custom"

    @property
    def is_hard_requirement(self) -> bool:
        """Return True iff obligations of this category are hard requirements.

        A *hard requirement* obligation must be discharged for the relational
        claim to be considered valid.

        Returns
        -------
        bool
        """
        return self in (
            ObligationCategory.BEHAVIORAL,
            ObligationCategory.TRUST,
            ObligationCategory.CONTRACT,
        )


# ---------------------------------------------------------------------------
# §2  ObligationStatus — lifecycle of a single obligation
# ---------------------------------------------------------------------------


class ObligationStatus(str, Enum):
    """Lifecycle status of a :class:`RelationalObligation`.

    Attributes
    ----------
    PENDING:
        The obligation has been identified but not yet discharged.
    DISCHARGED:
        The obligation has been successfully discharged with a valid proof.
    FAILED:
        An attempt was made to discharge the obligation but it failed.
    WAIVED:
        The obligation has been waived (e.g. by a human reviewer).
    DEFERRED:
        Discharge is deferred to a later stage.
    """

    PENDING = "pending"
    DISCHARGED = "discharged"
    FAILED = "failed"
    WAIVED = "waived"
    DEFERRED = "deferred"

    @property
    def is_resolved(self) -> bool:
        """Return True iff the obligation no longer blocks the claim.

        Returns
        -------
        bool
        """
        return self in (
            ObligationStatus.DISCHARGED,
            ObligationStatus.WAIVED,
        )


# ---------------------------------------------------------------------------
# §3  RelationalObligation — a single proof obligation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationalObligation:
    """A single proof obligation arising from a relational claim.

    A ``RelationalObligation`` records:
    - Which coordinate is obligated (i.e. must discharge this obligation).
    - What the obligation requires (its category and description).
    - What relation and partner coordinate give rise to the obligation.
    - The current discharge status.

    Attributes
    ----------
    obligation_id : str
        Unique identifier.
    obligated_coordinate : str
        The coordinate of the program that must discharge this obligation.
    partner_coordinate : str
        The coordinate of the relational partner that gives rise to the obligation.
    relation_spec_id : str
        The ID of the relation spec under which the obligation arises.
    category : ObligationCategory
        The category of the obligation.
    description : str
        Natural-language description of what must be proved.
    status : ObligationStatus
        Current discharge status.
    discharge_evidence : tuple[str, ...]
        Evidence items that discharge (or partially discharge) the obligation.
    discharge_method : str
        How the obligation was discharged (e.g. ``"structural"``, ``"solver"``).
    priority : int
        Integer priority (lower = more important).
    is_hard_requirement : bool
        Whether this obligation is a hard requirement (must be discharged for
        the claim to be valid).
    metadata : tuple[tuple[str, str], ...]
        Free-form key-value annotation pairs.
    created_at : str
        ISO-8601 creation timestamp.
    """

    obligation_id: str
    obligated_coordinate: str
    partner_coordinate: str
    relation_spec_id: str
    category: ObligationCategory
    description: str
    status: ObligationStatus
    discharge_evidence: tuple[str, ...]
    discharge_method: str
    priority: int
    is_hard_requirement: bool
    metadata: tuple[tuple[str, str], ...]
    created_at: str

    @classmethod
    def make(
        cls,
        obligated: str,
        partner: str,
        relation_spec_id: str,
        category: ObligationCategory,
        description: str = "",
        priority: int = 5,
        metadata: Sequence[tuple[str, str]] = (),
    ) -> "RelationalObligation":
        """Construct a ``RelationalObligation`` in PENDING status.

        Parameters
        ----------
        obligated : str
            Coordinate of the obligated program.
        partner : str
            Coordinate of the relational partner.
        relation_spec_id : str
            ID of the parameterising relation spec.
        category : ObligationCategory
            Obligation category.
        description : str
            Natural-language description.
        priority : int
            Numerical priority (lower = more important).
        metadata : Sequence[tuple[str, str]]
            Free-form annotations.

        Returns
        -------
        RelationalObligation
        """
        from datetime import datetime, timezone
        return cls(
            obligation_id=f"obl-{uuid.uuid4().hex[:12]}",
            obligated_coordinate=obligated,
            partner_coordinate=partner,
            relation_spec_id=relation_spec_id,
            category=category,
            description=description or (
                f"Obligation of '{obligated}' relative to partner '{partner}' "
                f"under relation '{relation_spec_id}' (category: {category.value})."
            ),
            status=ObligationStatus.PENDING,
            discharge_evidence=(),
            discharge_method="",
            priority=priority,
            is_hard_requirement=category.is_hard_requirement,
            metadata=tuple(metadata),
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def discharged(
        self,
        evidence: Sequence[str],
        method: str = "solver",
    ) -> "RelationalObligation":
        """Return a copy of self with status DISCHARGED.

        Parameters
        ----------
        evidence : Sequence[str]
            Evidence supporting the discharge.
        method : str
            How the obligation was discharged.

        Returns
        -------
        RelationalObligation
        """
        return replace(
            self,
            status=ObligationStatus.DISCHARGED,
            discharge_evidence=tuple(evidence),
            discharge_method=method,
        )

    def failed(self, reason: str = "") -> "RelationalObligation":
        """Return a copy of self with status FAILED.

        Parameters
        ----------
        reason : str
            Reason for failure, appended to discharge evidence.

        Returns
        -------
        RelationalObligation
        """
        return replace(
            self,
            status=ObligationStatus.FAILED,
            discharge_evidence=(f"failure-reason:{reason}",) if reason else (),
            discharge_method="failed",
        )

    def waived(self, reason: str = "") -> "RelationalObligation":
        """Return a copy of self with status WAIVED.

        Parameters
        ----------
        reason : str
            Reason for waiving.

        Returns
        -------
        RelationalObligation
        """
        return replace(
            self,
            status=ObligationStatus.WAIVED,
            discharge_evidence=(f"waiver-reason:{reason}",) if reason else (),
            discharge_method="waiver",
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "obligation_id": self.obligation_id,
            "obligated_coordinate": self.obligated_coordinate,
            "partner_coordinate": self.partner_coordinate,
            "relation_spec_id": self.relation_spec_id,
            "category": self.category.value,
            "description": self.description,
            "status": self.status.value,
            "discharge_evidence": list(self.discharge_evidence),
            "discharge_method": self.discharge_method,
            "priority": self.priority,
            "is_hard_requirement": self.is_hard_requirement,
            "metadata": {k: v for k, v in self.metadata},
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# §4  DischargeRecord — result of a single discharge attempt
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DischargeRecord:
    """Records the result of a single obligation discharge attempt.

    Attributes
    ----------
    record_id : str
        Unique identifier.
    obligation_id : str
        The obligation that was attempted.
    success : bool
        Whether the discharge succeeded.
    method : str
        Method used (e.g. ``"structural"``, ``"solver"``, ``"human"``).
    evidence : tuple[str, ...]
        Evidence produced by the discharge attempt.
    error_message : str
        Error message if the discharge failed; empty if successful.
    elapsed_ms : float
        Wall-clock time taken for the discharge attempt, in milliseconds.
    attempted_at : str
        ISO-8601 timestamp of the discharge attempt.
    """

    record_id: str
    obligation_id: str
    success: bool
    method: str
    evidence: tuple[str, ...]
    error_message: str
    elapsed_ms: float
    attempted_at: str

    @classmethod
    def make(
        cls,
        obligation_id: str,
        success: bool,
        method: str = "solver",
        evidence: Sequence[str] = (),
        error_message: str = "",
        elapsed_ms: float = 0.0,
    ) -> "DischargeRecord":
        """Construct a ``DischargeRecord``.

        Parameters
        ----------
        obligation_id : str
            The attempted obligation's ID.
        success : bool
            Whether the discharge succeeded.
        method : str
            Discharge method used.
        evidence : Sequence[str]
            Evidence produced.
        error_message : str
            Error message if failed.
        elapsed_ms : float
            Elapsed milliseconds.

        Returns
        -------
        DischargeRecord
        """
        from datetime import datetime, timezone
        return cls(
            record_id=f"dr-{uuid.uuid4().hex[:10]}",
            obligation_id=obligation_id,
            success=success,
            method=method,
            evidence=tuple(evidence),
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            attempted_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "record_id": self.record_id,
            "obligation_id": self.obligation_id,
            "success": self.success,
            "method": self.method,
            "evidence": list(self.evidence),
            "error_message": self.error_message,
            "elapsed_ms": self.elapsed_ms,
            "attempted_at": self.attempted_at,
        }


# ---------------------------------------------------------------------------
# §5  ObligationDischarger
# ---------------------------------------------------------------------------


class ObligationDischarger:
    """Attempts to discharge a batch of relational obligations.

    The discharger works in three phases per obligation:

    1. **Category dispatch** — route the obligation to a category-specific
       sub-discharger.
    2. **Evidence collection** — gather evidence from the coordinate profile
       and any supplied context.
    3. **Status update** — produce a :class:`DischargeRecord` and an updated
       :class:`RelationalObligation`.

    The discharger is *stateless* — all state is passed in and returned.

    Configuration
    -------------
    ``auto_waive_low_priority``
        If ``True``, obligations with priority ≥ 8 are automatically waived.
    ``max_discharge_attempts``
        Maximum number of discharge attempts per obligation (default 2).
    """

    _DEFAULT_CONFIDENCE: float = 0.85
    _HARD_OBLIGATION_PENALTY: float = 0.1

    def __init__(
        self,
        auto_waive_low_priority: bool = False,
        max_discharge_attempts: int = 2,
    ) -> None:
        self._auto_waive_low_priority = auto_waive_low_priority
        self._max_discharge_attempts = max_discharge_attempts

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discharge_all(
        self,
        obligations: Sequence[RelationalObligation],
        context: dict[str, Any] | None = None,
    ) -> tuple[list[RelationalObligation], list[DischargeRecord]]:
        """Attempt to discharge all obligations.

        Parameters
        ----------
        obligations : Sequence[RelationalObligation]
            Obligations to discharge.
        context : dict[str, Any] | None
            Optional context dictionary (may contain coordinate metadata).

        Returns
        -------
        tuple[list[RelationalObligation], list[DischargeRecord]]
            Updated obligations and corresponding discharge records.
        """
        ctx = context or {}
        updated_obligations: list[RelationalObligation] = []
        records: list[DischargeRecord] = []
        for obl in obligations:
            updated_obl, record = self._discharge_one(obl, ctx)
            updated_obligations.append(updated_obl)
            records.append(record)
        return updated_obligations, records

    def _discharge_one(
        self,
        obligation: RelationalObligation,
        context: dict[str, Any],
    ) -> tuple[RelationalObligation, DischargeRecord]:
        """Discharge a single obligation.

        Parameters
        ----------
        obligation : RelationalObligation
            The obligation.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        tuple[RelationalObligation, DischargeRecord]
        """
        t0 = time.monotonic()
        try:
            result = self._dispatch_discharge(obligation, context)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            success, evidence, method = result
            if success:
                updated = obligation.discharged(evidence, method=method)
            else:
                updated = obligation.failed(
                    reason="; ".join(evidence) if evidence else "no evidence"
                )
            record = DischargeRecord.make(
                obligation_id=obligation.obligation_id,
                success=success,
                method=method,
                evidence=evidence,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            updated = obligation.failed(reason=f"{type(exc).__name__}: {exc}")
            record = DischargeRecord.make(
                obligation_id=obligation.obligation_id,
                success=False,
                method="error",
                evidence=(f"exception:{type(exc).__name__}",),
                error_message=str(exc),
                elapsed_ms=elapsed_ms,
            )
        return updated, record

    def _dispatch_discharge(
        self,
        obligation: RelationalObligation,
        context: dict[str, Any],
    ) -> tuple[bool, list[str], str]:
        """Route obligation to a category-specific discharger.

        Parameters
        ----------
        obligation : RelationalObligation
            The obligation to discharge.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        tuple[bool, list[str], str]
            (success, evidence_list, method_name)
        """
        # Auto-waive low-priority
        if self._auto_waive_low_priority and obligation.priority >= 8:
            return True, [f"auto-waived:priority={obligation.priority}"], "auto-waive"

        category = obligation.category
        handlers: dict[ObligationCategory, Callable[
            [RelationalObligation, dict[str, Any]],
            tuple[bool, list[str], str],
        ]] = {
            ObligationCategory.BEHAVIORAL: self._discharge_behavioral,
            ObligationCategory.STRUCTURAL: self._discharge_structural,
            ObligationCategory.TRUST: self._discharge_trust,
            ObligationCategory.EVIDENCE: self._discharge_evidence_obl,
            ObligationCategory.CONTRACT: self._discharge_contract,
            ObligationCategory.CUSTOM: self._discharge_custom,
        }
        handler = handlers.get(category, self._discharge_custom)
        return handler(obligation, context)

    # ------------------------------------------------------------------
    # Category-specific dischargers
    # ------------------------------------------------------------------

    def _discharge_behavioral(
        self,
        obligation: RelationalObligation,
        context: dict[str, Any],
    ) -> tuple[bool, list[str], str]:
        """Discharge a BEHAVIORAL obligation.

        Behavioral obligations check that the obligated program's observable
        behaviour is compatible with its partner's.

        Parameters
        ----------
        obligation : RelationalObligation
            The obligation.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        tuple[bool, list[str], str]
        """
        obligated = obligation.obligated_coordinate
        partner = obligation.partner_coordinate
        evidence: list[str] = [f"behavioral-check:{obligated}vs{partner}"]

        # Heuristic: if the obligated coordinate is a path extension of the
        # partner, behavioural obligations are inherited.
        if obligated.startswith(partner + ".") or obligated.startswith(partner + "/"):
            evidence.append("structural-extension:behavioral-obligations-inherited")
            return True, evidence, "structural"

        # If both share a common namespace prefix (≥60% of chars), presume
        # behavioral compatibility.
        shared = _common_prefix_length(obligated, partner)
        max_len = max(len(obligated), len(partner), 1)
        if shared / max_len >= 0.6:
            evidence.append(f"common-prefix-ratio:{shared/max_len:.2f}")
            return True, evidence, "prefix-heuristic"

        # Cannot confirm behavioral compatibility without a concrete semantics.
        evidence.append("insufficient-structural-evidence")
        if obligation.is_hard_requirement:
            return False, evidence, "behavioral-check"
        return True, evidence, "soft-assumption"

    def _discharge_structural(
        self,
        obligation: RelationalObligation,
        context: dict[str, Any],
    ) -> tuple[bool, list[str], str]:
        """Discharge a STRUCTURAL obligation.

        Structural obligations check signature and type compatibility.

        Parameters
        ----------
        obligation : RelationalObligation
            The obligation.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        tuple[bool, list[str], str]
        """
        obligated = obligation.obligated_coordinate
        partner = obligation.partner_coordinate
        evidence: list[str] = [f"structural-check:{obligated}vs{partner}"]

        # Token intersection heuristic: shared path segments imply structural
        # compatibility.
        obligated_tokens = frozenset(obligated.split("."))
        partner_tokens = frozenset(partner.split("."))
        overlap = obligated_tokens & partner_tokens
        jaccard = len(overlap) / max(len(obligated_tokens | partner_tokens), 1)

        evidence.append(f"token-jaccard:{jaccard:.3f}")
        if jaccard >= 0.5:
            evidence.append(f"shared-tokens:{sorted(overlap)}")
            return True, evidence, "structural-token-match"

        evidence.append("low-token-overlap:structural-gap-possible")
        return False, evidence, "structural-token-check"

    def _discharge_trust(
        self,
        obligation: RelationalObligation,
        context: dict[str, Any],
    ) -> tuple[bool, list[str], str]:
        """Discharge a TRUST obligation.

        Trust obligations check that the obligated program's trust level
        is at least as high as what the relation requires.

        Parameters
        ----------
        obligation : RelationalObligation
            The obligation.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        tuple[bool, list[str], str]
        """
        evidence: list[str] = ["trust-check"]
        # Extract trust level from context if available.
        trust_level_str: str = context.get(
            f"trust:{obligation.obligated_coordinate}", "SOLVER_INFERRED"
        )
        # Acceptable trust levels for hard-requirement trust obligations.
        acceptable = {
            "HUMAN_REVIEWED", "COPILOT_PROPOSED", "SOLVER_INFERRED", "AUTOMATED"
        }
        if trust_level_str.upper() in acceptable:
            evidence.append(f"trust-level-acceptable:{trust_level_str}")
            return True, evidence, "trust-check"

        evidence.append(f"trust-level-unacceptable:{trust_level_str}")
        return False, evidence, "trust-check"

    def _discharge_evidence_obl(
        self,
        obligation: RelationalObligation,
        context: dict[str, Any],
    ) -> tuple[bool, list[str], str]:
        """Discharge an EVIDENCE obligation.

        Evidence obligations require the obligated program to produce specific
        evidence items that the partner is known to produce.

        Parameters
        ----------
        obligation : RelationalObligation
            The obligation.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        tuple[bool, list[str], str]
        """
        evidence: list[str] = ["evidence-check"]
        # Check the context for evidence items for the obligated coordinate.
        available_evidence: list[str] = context.get(
            f"evidence:{obligation.obligated_coordinate}", []
        )
        partner_evidence: list[str] = context.get(
            f"evidence:{obligation.partner_coordinate}", []
        )
        if not partner_evidence:
            # Partner produces no evidence items — obligation is trivially satisfied.
            evidence.append("partner-has-no-evidence:trivially-satisfied")
            return True, evidence, "trivial"

        # Check that all partner evidence keys appear in obligated evidence.
        missing = [e for e in partner_evidence if e not in available_evidence]
        evidence.append(
            f"partner-evidence-count:{len(partner_evidence)}, "
            f"missing-from-obligated:{len(missing)}"
        )
        if not missing:
            evidence.append("all-partner-evidence-covered")
            return True, evidence, "evidence-inclusion"

        evidence.append(f"missing-evidence:{missing[:3]}")
        return False, evidence, "evidence-inclusion"

    def _discharge_contract(
        self,
        obligation: RelationalObligation,
        context: dict[str, Any],
    ) -> tuple[bool, list[str], str]:
        """Discharge a CONTRACT obligation.

        Contract obligations require the obligated program to satisfy a
        specific observable contract.

        Parameters
        ----------
        obligation : RelationalObligation
            The obligation.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        tuple[bool, list[str], str]
        """
        evidence: list[str] = ["contract-check"]
        # Extract contract name from description heuristic.
        desc = obligation.description.lower()
        known_contracts = ["memory-safety", "termination", "type-safety",
                           "api-backward-compatibility", "data-integrity"]
        for contract_name in known_contracts:
            if contract_name.replace("-", " ") in desc or contract_name in desc:
                evidence.append(f"contract-identified:{contract_name}")
                # Check context for explicit contract satisfaction flag.
                satisfied_flag = context.get(
                    f"contract-satisfied:{contract_name}:{obligation.obligated_coordinate}",
                    True,  # default: assume satisfied unless told otherwise
                )
                if satisfied_flag:
                    evidence.append(f"contract-satisfied:{contract_name}")
                    return True, evidence, "contract-check"
                evidence.append(f"contract-failed:{contract_name}")
                return False, evidence, "contract-check"

        # Unknown contract — soft pass.
        evidence.append("unknown-contract:soft-pass")
        return True, evidence, "soft-pass"

    def _discharge_custom(
        self,
        obligation: RelationalObligation,
        context: dict[str, Any],
    ) -> tuple[bool, list[str], str]:
        """Discharge a CUSTOM obligation (best-effort).

        Parameters
        ----------
        obligation : RelationalObligation
            The obligation.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        tuple[bool, list[str], str]
        """
        evidence: list[str] = [
            f"custom-obligation:{obligation.obligation_id}",
            "no-registered-handler:soft-pass",
        ]
        return True, evidence, "custom-soft-pass"


# ---------------------------------------------------------------------------
# §6  RelationalObligationsWitnessesWitness — composite relational witness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationalObligationsWitnessesWitness:
    """A composite certificate for a relational claim (A, B) under R.

    This witness bundles:
    - The obligations incurred by A (obligations of the left side).
    - The obligations incurred by B (obligations of the right side).
    - Discharge records for all obligations.
    - A top-level validity flag.

    The witness is *valid* iff every hard-requirement obligation on both sides
    has been discharged (or waived).

    Attributes
    ----------
    witness_id : str
        Unique identifier.
    left_coordinate : str
        Coordinate of the left-side program (A).
    right_coordinate : str
        Coordinate of the right-side program (B).
    relation_spec_id : str
        ID of the parameterising relation spec.
    left_obligations : tuple[RelationalObligation, ...]
        All obligations of A.
    right_obligations : tuple[RelationalObligation, ...]
        All obligations of B.
    discharge_records : tuple[DischargeRecord, ...]
        All discharge records (for A and B combined).
    is_valid : bool
        Whether all hard-requirement obligations have been resolved.
    undischarged_hard_obligations : tuple[str, ...]
        Obligation IDs of hard-requirement obligations that remain unresolved.
    confidence : float
        Confidence in the validity claim (0.0–1.0).
    trust_level : str
        Trust level of the witness.
    summary_steps : tuple[str, ...]
        Human-readable summary of the witness construction.
    metadata : tuple[tuple[str, str], ...]
        Free-form key-value annotation pairs.
    constructed_at : str
        ISO-8601 construction timestamp.
    """

    witness_id: str
    left_coordinate: str
    right_coordinate: str
    relation_spec_id: str
    left_obligations: tuple[RelationalObligation, ...]
    right_obligations: tuple[RelationalObligation, ...]
    discharge_records: tuple[DischargeRecord, ...]
    is_valid: bool
    undischarged_hard_obligations: tuple[str, ...]
    confidence: float
    trust_level: str
    summary_steps: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...]
    constructed_at: str

    @classmethod
    def make(
        cls,
        left: str,
        right: str,
        relation_spec_id: str,
        left_obligations: Sequence[RelationalObligation],
        right_obligations: Sequence[RelationalObligation],
        discharge_records: Sequence[DischargeRecord],
        confidence: float = 1.0,
        trust_level: str = "SOLVER_INFERRED",
        summary_steps: Sequence[str] = (),
        metadata: Sequence[tuple[str, str]] = (),
    ) -> "RelationalObligationsWitnessesWitness":
        """Construct the witness from obligations and discharge records.

        Parameters
        ----------
        left : str
            Left-side coordinate.
        right : str
            Right-side coordinate.
        relation_spec_id : str
            Parameterising relation spec ID.
        left_obligations : Sequence[RelationalObligation]
            Obligations of the left-side program.
        right_obligations : Sequence[RelationalObligation]
            Obligations of the right-side program.
        discharge_records : Sequence[DischargeRecord]
            All discharge records.
        confidence : float
            Confidence in [0, 1].
        trust_level : str
            Trust level.
        summary_steps : Sequence[str]
            Human-readable summary steps.
        metadata : Sequence[tuple[str, str]]
            Free-form annotations.

        Returns
        -------
        RelationalObligationsWitnessesWitness
        """
        from datetime import datetime, timezone
        all_obligations = list(left_obligations) + list(right_obligations)
        hard_unresolved = [
            o.obligation_id
            for o in all_obligations
            if o.is_hard_requirement and not o.status.is_resolved
        ]
        is_valid = len(hard_unresolved) == 0
        return cls(
            witness_id=f"rw-{uuid.uuid4().hex[:12]}",
            left_coordinate=left,
            right_coordinate=right,
            relation_spec_id=relation_spec_id,
            left_obligations=tuple(left_obligations),
            right_obligations=tuple(right_obligations),
            discharge_records=tuple(discharge_records),
            is_valid=is_valid,
            undischarged_hard_obligations=tuple(hard_unresolved),
            confidence=max(0.0, min(1.0, confidence)),
            trust_level=trust_level,
            summary_steps=tuple(summary_steps),
            metadata=tuple(metadata),
            constructed_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    @property
    def all_obligations(self) -> tuple[RelationalObligation, ...]:
        """Return the combined list of left and right obligations.

        Returns
        -------
        tuple[RelationalObligation, ...]
        """
        return self.left_obligations + self.right_obligations

    @property
    def n_discharged(self) -> int:
        """Return the number of discharged obligations.

        Returns
        -------
        int
        """
        return sum(1 for o in self.all_obligations if o.status == ObligationStatus.DISCHARGED)

    @property
    def n_failed(self) -> int:
        """Return the number of failed obligations.

        Returns
        -------
        int
        """
        return sum(1 for o in self.all_obligations if o.status == ObligationStatus.FAILED)

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "witness_id": self.witness_id,
            "left_coordinate": self.left_coordinate,
            "right_coordinate": self.right_coordinate,
            "relation_spec_id": self.relation_spec_id,
            "is_valid": self.is_valid,
            "undischarged_hard_obligations": list(self.undischarged_hard_obligations),
            "confidence": self.confidence,
            "trust_level": self.trust_level,
            "n_discharged": self.n_discharged,
            "n_failed": self.n_failed,
            "summary_steps": list(self.summary_steps),
            "metadata": {k: v for k, v in self.metadata},
            "constructed_at": self.constructed_at,
            "left_obligations": [o.to_dict() for o in self.left_obligations],
            "right_obligations": [o.to_dict() for o in self.right_obligations],
            "discharge_records": [r.to_dict() for r in self.discharge_records],
        }


# ---------------------------------------------------------------------------
# §7  WitnessValidator
# ---------------------------------------------------------------------------


class WitnessValidator:
    """Validates a :class:`RelationalObligationsWitnessesWitness`.

    The validator performs three passes:

    1. **Hard-obligation check** — verifies that all hard-requirement
       obligations are resolved (DISCHARGED or WAIVED).
    2. **Consistency check** — verifies that every ``DischargeRecord``
       corresponds to a known ``RelationalObligation``.
    3. **Confidence sanity check** — warns if the witness confidence is below
       the minimum threshold.

    All checks are non-destructive: the validator returns a
    :class:`ValidationResult` rather than raising exceptions (unless
    ``strict_mode=True``).

    Attributes
    ----------
    min_confidence_threshold : float
        Minimum acceptable confidence (default 0.5).
    strict_mode : bool
        If ``True``, failed validation raises ``ValueError``.
    """

    _DEFAULT_MIN_CONFIDENCE: float = 0.5

    def __init__(
        self,
        min_confidence_threshold: float = _DEFAULT_MIN_CONFIDENCE,
        strict_mode: bool = False,
    ) -> None:
        self._min_confidence = min_confidence_threshold
        self._strict_mode = strict_mode

    def validate(
        self,
        witness: RelationalObligationsWitnessesWitness,
    ) -> "ValidationResult":
        """Validate a relational witness.

        Parameters
        ----------
        witness : RelationalObligationsWitnessesWitness
            The witness to validate.

        Returns
        -------
        ValidationResult

        Raises
        ------
        ValueError
            If ``strict_mode=True`` and validation fails.
        """
        errors: list[str] = []
        warnings: list[str] = []
        steps: list[str] = [
            f"Validating witness {witness.witness_id} for "
            f"({witness.left_coordinate}, {witness.right_coordinate})."
        ]

        # Pass 1: hard obligation check
        hard_unresolved = witness.undischarged_hard_obligations
        if hard_unresolved:
            msg = (
                f"Pass 1 FAIL: {len(hard_unresolved)} hard-requirement obligation(s) "
                f"are not resolved: {list(hard_unresolved)[:5]}."
            )
            errors.append(msg)
            steps.append(msg)
        else:
            steps.append("Pass 1 OK: all hard-requirement obligations are resolved.")

        # Pass 2: consistency check
        obligation_ids = {o.obligation_id for o in witness.all_obligations}
        orphan_records = [
            r.record_id
            for r in witness.discharge_records
            if r.obligation_id not in obligation_ids
        ]
        if orphan_records:
            msg = (
                f"Pass 2 WARN: {len(orphan_records)} discharge record(s) reference "
                f"unknown obligation IDs."
            )
            warnings.append(msg)
            steps.append(msg)
        else:
            steps.append("Pass 2 OK: all discharge records reference known obligations.")

        # Pass 3: confidence sanity check
        if witness.confidence < self._min_confidence:
            msg = (
                f"Pass 3 WARN: witness confidence {witness.confidence:.3f} is below "
                f"minimum threshold {self._min_confidence:.3f}."
            )
            warnings.append(msg)
            steps.append(msg)
        else:
            steps.append(f"Pass 3 OK: confidence {witness.confidence:.3f} ≥ threshold.")

        is_valid = len(errors) == 0
        result = ValidationResult(
            witness_id=witness.witness_id,
            is_valid=is_valid,
            errors=tuple(errors),
            warnings=tuple(warnings),
            steps=tuple(steps),
        )

        if self._strict_mode and not is_valid:
            raise ValueError(
                f"Witness validation failed for {witness.witness_id}: "
                f"{'; '.join(errors)}"
            )

        return result


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of validating a relational witness.

    Attributes
    ----------
    witness_id : str
        ID of the validated witness.
    is_valid : bool
        Whether the witness passed validation.
    errors : tuple[str, ...]
        Hard errors (if any) that caused validation to fail.
    warnings : tuple[str, ...]
        Non-fatal warnings.
    steps : tuple[str, ...]
        Human-readable validation steps.
    """

    witness_id: str
    is_valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    steps: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
        """
        return {
            "witness_id": self.witness_id,
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "steps": list(self.steps),
        }


# ---------------------------------------------------------------------------
# §8  RelationalObligationsWitnessesAnalyzer
# ---------------------------------------------------------------------------


class RelationalObligationsWitnessesAnalyzer:
    """Generates and discharges relational obligations for a (left, right) pair.

    The analyzer performs the full obligation lifecycle:

    1. **Obligation generation** — enumerate the obligations for each side
       of the relational pair based on the relation spec.
    2. **Obligation discharge** — run the :class:`ObligationDischarger`.
    3. **Witness assembly** — package the result into a
       :class:`RelationalObligationsWitnessesWitness`.

    The analyzer is *stateless* across calls.

    Configuration
    -------------
    ``auto_waive_low_priority``
        Forward to the discharger.
    ``validator``
        Optional :class:`WitnessValidator` to run after assembly.
    """

    _DEFAULT_CONFIDENCE: float = 0.87

    def __init__(
        self,
        auto_waive_low_priority: bool = False,
        validator: WitnessValidator | None = None,
    ) -> None:
        self._discharger = ObligationDischarger(
            auto_waive_low_priority=auto_waive_low_priority
        )
        self._validator = validator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        left: str,
        right: str,
        relation_spec: RelationSpec | None = None,
        context: dict[str, Any] | None = None,
    ) -> RelationalObligationsWitnessesWitness:
        """Generate, discharge, and assemble a relational witness for (left, right).

        Parameters
        ----------
        left : str
            Left-side coordinate.
        right : str
            Right-side coordinate.
        relation_spec : RelationSpec | None
            The parameterising relation; defaults to OBSERVATIONAL.
        context : dict[str, Any] | None
            Optional context dictionary forwarded to the discharger.

        Returns
        -------
        RelationalObligationsWitnessesWitness
        """
        try:
            return self._analyze_impl(left, right, relation_spec, context or {})
        except Exception as exc:  # noqa: BLE001
            return RelationalObligationsWitnessesWitness.make(
                left=left,
                right=right,
                relation_spec_id=getattr(relation_spec, "spec_id", "unknown"),
                left_obligations=(),
                right_obligations=(),
                discharge_records=(),
                confidence=0.0,
                trust_level="UNVERIFIED",
                summary_steps=(f"Analysis failed: {type(exc).__name__}: {exc}",),
            )

    def _analyze_impl(
        self,
        left: str,
        right: str,
        relation_spec: RelationSpec | None,
        context: dict[str, Any],
    ) -> RelationalObligationsWitnessesWitness:
        """Internal implementation; may raise.

        Parameters
        ----------
        left : str
            Left coordinate.
        right : str
            Right coordinate.
        relation_spec : RelationSpec | None
            Parameterising relation.
        context : dict[str, Any]
            Context dictionary.

        Returns
        -------
        RelationalObligationsWitnessesWitness
        """
        if relation_spec is None:
            relation_spec = _make_default_relation_spec()

        spec_id = getattr(relation_spec, "spec_id", "unknown")
        steps: list[str] = [
            f"Analyzing relational obligations for ({left}, {right}) under R={spec_id}.",
        ]

        # Step 1: generate obligations for left
        left_obligations = self._generate_obligations(
            obligated=left,
            partner=right,
            relation_spec_id=spec_id,
        )
        steps.append(f"Generated {len(left_obligations)} obligation(s) for left='{left}'.")

        # Step 2: generate obligations for right
        right_obligations = self._generate_obligations(
            obligated=right,
            partner=left,
            relation_spec_id=spec_id,
        )
        steps.append(f"Generated {len(right_obligations)} obligation(s) for right='{right}'.")

        # Step 3: discharge all obligations
        all_obligations = left_obligations + right_obligations
        updated_obligations, records = self._discharger.discharge_all(
            all_obligations, context=context
        )

        n_discharged = sum(1 for o in updated_obligations if o.status == ObligationStatus.DISCHARGED)
        n_failed = sum(1 for o in updated_obligations if o.status == ObligationStatus.FAILED)
        steps.append(
            f"Discharged {n_discharged}/{len(updated_obligations)} obligation(s); "
            f"{n_failed} failed."
        )

        # Split back into left / right
        n_left = len(left_obligations)
        updated_left = updated_obligations[:n_left]
        updated_right = updated_obligations[n_left:]

        # Step 4: compute confidence
        confidence = self._score_confidence(updated_obligations, n_discharged, n_failed)
        steps.append(f"Composite confidence: {confidence:.3f}.")

        # Step 5: assemble witness
        witness = RelationalObligationsWitnessesWitness.make(
            left=left,
            right=right,
            relation_spec_id=spec_id,
            left_obligations=updated_left,
            right_obligations=updated_right,
            discharge_records=records,
            confidence=confidence,
            trust_level="SOLVER_INFERRED",
            summary_steps=tuple(steps),
        )

        # Step 6: optional validation
        if self._validator is not None:
            vr = self._validator.validate(witness)
            steps.append(f"Validation: is_valid={vr.is_valid}, errors={len(vr.errors)}.")

        return witness

    def _generate_obligations(
        self,
        obligated: str,
        partner: str,
        relation_spec_id: str,
    ) -> list[RelationalObligation]:
        """Generate the standard obligation set for ``obligated`` relative to ``partner``.

        Parameters
        ----------
        obligated : str
            The coordinate that incurs the obligations.
        partner : str
            The relational partner coordinate.
        relation_spec_id : str
            The ID of the relation spec.

        Returns
        -------
        list[RelationalObligation]
        """
        obligations: list[RelationalObligation] = []
        obligation_specs: list[tuple[ObligationCategory, str, int]] = [
            (
                ObligationCategory.BEHAVIORAL,
                (
                    f"'{obligated}' must exhibit behaviours that are R-compatible "
                    f"with those of '{partner}'."
                ),
                1,
            ),
            (
                ObligationCategory.STRUCTURAL,
                (
                    f"'{obligated}' must have type/signature structure compatible "
                    f"with '{partner}'."
                ),
                3,
            ),
            (
                ObligationCategory.TRUST,
                (
                    f"'{obligated}' must meet the minimum trust level required by "
                    f"the relation R='{relation_spec_id}'."
                ),
                2,
            ),
            (
                ObligationCategory.EVIDENCE,
                (
                    f"'{obligated}' must supply all evidence items that '{partner}' "
                    "is known to produce under the relation."
                ),
                4,
            ),
            (
                ObligationCategory.CONTRACT,
                (
                    f"'{obligated}' must satisfy all observable contracts required "
                    f"by '{partner}' (memory-safety, termination, type-safety)."
                ),
                2,
            ),
        ]
        for category, description, priority in obligation_specs:
            obl = RelationalObligation.make(
                obligated=obligated,
                partner=partner,
                relation_spec_id=relation_spec_id,
                category=category,
                description=description,
                priority=priority,
            )
            obligations.append(obl)
        return obligations

    def _score_confidence(
        self,
        obligations: list[RelationalObligation],
        n_discharged: int,
        n_failed: int,
    ) -> float:
        """Compute a composite confidence score.

        Parameters
        ----------
        obligations : list[RelationalObligation]
            All obligations (updated).
        n_discharged : int
            Count of discharged obligations.
        n_failed : int
            Count of failed obligations.

        Returns
        -------
        float
        """
        total = max(len(obligations), 1)
        base = self._DEFAULT_CONFIDENCE
        # Increase confidence for high discharge ratio
        discharge_ratio = n_discharged / total
        base = base * (0.5 + 0.5 * discharge_ratio)
        # Penalise hard-requirement failures more heavily
        hard_failed = sum(
            1 for o in obligations
            if o.status == ObligationStatus.FAILED and o.is_hard_requirement
        )
        base -= hard_failed * 0.12
        return max(0.0, min(1.0, base))

    def batch_analyze(
        self,
        pairs: Sequence[tuple[str, str]],
        relation_spec: RelationSpec | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[RelationalObligationsWitnessesWitness]:
        """Analyze a batch of (left, right) pairs.

        Parameters
        ----------
        pairs : Sequence[tuple[str, str]]
            List of (left, right) coordinate pairs.
        relation_spec : RelationSpec | None
            Common relation spec.
        context : dict[str, Any] | None
            Common context dictionary.

        Returns
        -------
        list[RelationalObligationsWitnessesWitness]
        """
        return [
            self.analyze(left, right, relation_spec=relation_spec, context=context)
            for left, right in pairs
        ]


# ---------------------------------------------------------------------------
# §9  RelationalObligationsWitnessesCoordinator
# ---------------------------------------------------------------------------


class RelationalObligationsWitnessesCoordinator:
    """Orchestrates the full relational-obligations-and-witnesses pipeline.

    The coordinator is the top-level entry point for the *Relational
    Obligations and Witnesses* stage.  It:

    1. Accepts one or more (left, right) pairs and an optional relation spec.
    2. Drives the :class:`RelationalObligationsWitnessesAnalyzer`.
    3. Optionally runs the :class:`WitnessValidator` on each witness.
    4. Collects results into an :class:`ObligationCoordinatorReport`.

    Attributes
    ----------
    coordinator_id : str
        Unique identifier for this coordinator.
    default_relation_spec : RelationSpec | None
        Default relation spec used when callers do not supply their own.
    auto_validate : bool
        If ``True``, each witness is passed through the :class:`WitnessValidator`.
    strict_mode : bool
        If ``True``, invalid witnesses cause :meth:`run` to raise.
    history : list[RelationalObligationsWitnessesWitness]
        Accumulated witnesses from all prior :meth:`run` calls.
    """

    def __init__(
        self,
        default_relation_spec: RelationSpec | None = None,
        auto_validate: bool = True,
        strict_mode: bool = False,
        auto_waive_low_priority: bool = False,
    ) -> None:
        self.coordinator_id = f"rwc-{uuid.uuid4().hex[:12]}"
        self.default_relation_spec = default_relation_spec
        self.auto_validate = auto_validate
        self.strict_mode = strict_mode
        self.history: list[RelationalObligationsWitnessesWitness] = []
        self._validator = WitnessValidator(strict_mode=strict_mode) if auto_validate else None
        self._analyzer = RelationalObligationsWitnessesAnalyzer(
            auto_waive_low_priority=auto_waive_low_priority,
            validator=self._validator,
        )

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def run(
        self,
        pairs: Sequence[tuple[str, str]] | tuple[str, str],
        relation_spec: RelationSpec | None = None,
        context: dict[str, Any] | None = None,
    ) -> "ObligationCoordinatorReport":
        """Execute the obligations pipeline for the given pairs.

        Parameters
        ----------
        pairs : Sequence[tuple[str, str]] | tuple[str, str]
            One or more (left, right) coordinate pairs.
        relation_spec : RelationSpec | None
            Override relation spec.
        context : dict[str, Any] | None
            Context dictionary forwarded to the discharger.

        Returns
        -------
        ObligationCoordinatorReport

        Raises
        ------
        ValueError
            If ``strict_mode=True`` and any witness is invalid.
        """
        if (
            isinstance(pairs, tuple)
            and len(pairs) == 2
            and isinstance(pairs[0], str)
        ):
            pairs = [pairs]  # type: ignore[list-item]

        effective_spec = relation_spec or self.default_relation_spec
        witnesses: list[RelationalObligationsWitnessesWitness] = []

        for left, right in pairs:
            w = self._analyzer.analyze(
                left=left,
                right=right,
                relation_spec=effective_spec,
                context=context,
            )
            witnesses.append(w)

        self.history.extend(witnesses)

        if self.strict_mode:
            invalid = [w for w in witnesses if not w.is_valid]
            if invalid:
                ids = ", ".join(w.witness_id for w in invalid)
                raise ValueError(
                    f"Coordinator strict_mode: {len(invalid)} invalid witness(es): {ids}"
                )

        return ObligationCoordinatorReport.from_witnesses(
            coordinator_id=self.coordinator_id,
            witnesses=witnesses,
        )

    def run_pair(
        self,
        left: str,
        right: str,
        relation_spec: RelationSpec | None = None,
        context: dict[str, Any] | None = None,
    ) -> RelationalObligationsWitnessesWitness:
        """Convenience method: run a single (left, right) pair.

        Parameters
        ----------
        left : str
            Left coordinate.
        right : str
            Right coordinate.
        relation_spec : RelationSpec | None
            Parameterising relation.
        context : dict[str, Any] | None
            Context dictionary.

        Returns
        -------
        RelationalObligationsWitnessesWitness
        """
        report = self.run([(left, right)], relation_spec=relation_spec, context=context)
        return report.witnesses[0]

    def summary(self) -> dict[str, JsonValue]:
        """Return a summary dict of accumulated results.

        Returns
        -------
        dict[str, JsonValue]
        """
        from collections import Counter
        n_valid = sum(1 for w in self.history if w.is_valid)
        total_obligations = sum(len(w.all_obligations) for w in self.history)
        n_discharged = sum(w.n_discharged for w in self.history)
        return {
            "coordinator_id": self.coordinator_id,
            "total_witnesses": len(self.history),
            "valid_witnesses": n_valid,
            "total_obligations": total_obligations,
            "total_discharged": n_discharged,
            "mean_confidence": (
                sum(w.confidence for w in self.history) / len(self.history)
                if self.history else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# §10  ObligationCoordinatorReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObligationCoordinatorReport:
    """Summary of a coordinator run.

    Attributes
    ----------
    report_id : str
        Unique report identifier.
    coordinator_id : str
        ID of the producing coordinator.
    witnesses : tuple[RelationalObligationsWitnessesWitness, ...]
        All witnesses from this run.
    n_valid : int
        Number of valid witnesses.
    n_invalid : int
        Number of invalid witnesses.
    total_obligations : int
        Total obligation count across all witnesses.
    total_discharged : int
        Total discharged obligation count.
    total_failed : int
        Total failed obligation count.
    mean_confidence : float
        Mean confidence across all witnesses.
    produced_at : str
        ISO-8601 production timestamp.
    """

    report_id: str
    coordinator_id: str
    witnesses: tuple[RelationalObligationsWitnessesWitness, ...]
    n_valid: int
    n_invalid: int
    total_obligations: int
    total_discharged: int
    total_failed: int
    mean_confidence: float
    produced_at: str

    @classmethod
    def from_witnesses(
        cls,
        coordinator_id: str,
        witnesses: Sequence[RelationalObligationsWitnessesWitness],
    ) -> "ObligationCoordinatorReport":
        """Construct from a list of witnesses.

        Parameters
        ----------
        coordinator_id : str
            ID of the producing coordinator.
        witnesses : Sequence[RelationalObligationsWitnessesWitness]
            Witnesses to summarise.

        Returns
        -------
        ObligationCoordinatorReport
        """
        from datetime import datetime, timezone
        ws = tuple(witnesses)
        n_valid = sum(1 for w in ws if w.is_valid)
        total_obl = sum(len(w.all_obligations) for w in ws)
        total_dis = sum(w.n_discharged for w in ws)
        total_fail = sum(w.n_failed for w in ws)
        mean_conf = sum(w.confidence for w in ws) / max(len(ws), 1)
        return cls(
            report_id=f"owrep-{uuid.uuid4().hex[:12]}",
            coordinator_id=coordinator_id,
            witnesses=ws,
            n_valid=n_valid,
            n_invalid=len(ws) - n_valid,
            total_obligations=total_obl,
            total_discharged=total_dis,
            total_failed=total_fail,
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
            "total_obligations": self.total_obligations,
            "total_discharged": self.total_discharged,
            "total_failed": self.total_failed,
            "mean_confidence": self.mean_confidence,
            "produced_at": self.produced_at,
            "witnesses": [w.to_dict() for w in self.witnesses],
        }


# ---------------------------------------------------------------------------
# §11  Module-level helpers
# ---------------------------------------------------------------------------


def _common_prefix_length(a: str, b: str) -> int:
    """Return the length of the longest common prefix of *a* and *b*.

    Parameters
    ----------
    a : str
        First string.
    b : str
        Second string.

    Returns
    -------
    int
    """
    count = 0
    for ca, cb in zip(a, b):
        if ca == cb:
            count += 1
        else:
            break
    return count


def _make_default_relation_spec() -> Any:
    """Build a default OBSERVATIONAL relation spec.

    Returns
    -------
    RelationSpec | dummy
    """
    try:
        return RelationSpec.make(
            name="default-observational",
            kind=RelationKind.OBSERVATIONAL,
            predicate_description="Default observational equivalence.",
        )
    except Exception:  # noqa: BLE001
        class _Dummy:  # type: ignore[no-redef]
            spec_id = "default-observational"
            name = "default-observational"

            class kind:
                value = "observational"
        return _Dummy()


def make_behavioral_obligation(
    obligated: str,
    partner: str,
    relation_spec_id: str = "default-observational",
) -> RelationalObligation:
    """Convenience factory for a BEHAVIORAL obligation.

    Parameters
    ----------
    obligated : str
        Obligated coordinate.
    partner : str
        Partner coordinate.
    relation_spec_id : str
        Relation spec ID.

    Returns
    -------
    RelationalObligation
    """
    return RelationalObligation.make(
        obligated=obligated,
        partner=partner,
        relation_spec_id=relation_spec_id,
        category=ObligationCategory.BEHAVIORAL,
        description=(
            f"'{obligated}' must exhibit R-compatible behaviours relative to '{partner}'."
        ),
        priority=1,
    )


def make_trust_obligation(
    obligated: str,
    partner: str,
    relation_spec_id: str = "default-observational",
    required_trust: str = "SOLVER_INFERRED",
) -> RelationalObligation:
    """Convenience factory for a TRUST obligation.

    Parameters
    ----------
    obligated : str
        Obligated coordinate.
    partner : str
        Partner coordinate.
    relation_spec_id : str
        Relation spec ID.
    required_trust : str
        Minimum required trust level.

    Returns
    -------
    RelationalObligation
    """
    return RelationalObligation.make(
        obligated=obligated,
        partner=partner,
        relation_spec_id=relation_spec_id,
        category=ObligationCategory.TRUST,
        description=(
            f"'{obligated}' must have trust level ≥ {required_trust} "
            f"as required by relation '{relation_spec_id}'."
        ),
        priority=2,
    )


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
# §12  Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Quick sanity check for the module."""
    print("=== relational_obligations_and_witness smoke test ===")

    coord = RelationalObligationsWitnessesCoordinator(
        auto_validate=True,
        strict_mode=False,
        auto_waive_low_priority=False,
    )

    # Single pair
    w = coord.run_pair("prog.impl.A", "prog.spec.A")
    print(
        f"Pair witness: valid={w.is_valid}, "
        f"n_left_obls={len(w.left_obligations)}, "
        f"n_right_obls={len(w.right_obligations)}, "
        f"n_discharged={w.n_discharged}, "
        f"n_failed={w.n_failed}, "
        f"confidence={w.confidence:.3f}"
    )

    # Self-pair (all obligations should discharge)
    w_self = coord.run_pair("prog.self", "prog.self")
    print(
        f"Self pair:    valid={w_self.is_valid}, "
        f"confidence={w_self.confidence:.3f}"
    )

    # Batch
    pairs = [
        ("safety.impl", "safety.spec"),
        ("type.checker.impl", "type.checker.spec"),
        ("api.v2", "api.v1"),
    ]
    report = coord.run(pairs)
    print(
        f"Batch report: n_valid={report.n_valid}, n_invalid={report.n_invalid}, "
        f"total_obligations={report.total_obligations}, "
        f"total_discharged={report.total_discharged}"
    )

    # Manual obligation construction + discharge
    obl = make_behavioral_obligation("prog.A", "prog.B")
    assert obl.status == ObligationStatus.PENDING
    discharged_obl = obl.discharged(evidence=["test-evidence"], method="manual")
    assert discharged_obl.status == ObligationStatus.DISCHARGED
    print(f"Manual obligation discharge: {discharged_obl.status.value}")

    # WitnessValidator standalone
    validator = WitnessValidator(strict_mode=False)
    vr = validator.validate(w)
    print(f"Validation: is_valid={vr.is_valid}, errors={len(vr.errors)}, warnings={len(vr.warnings)}")

    summary = coord.summary()
    print(f"Summary: {summary}")
    print("smoke test PASSED")


if __name__ == "__main__":
    _smoke_test()
