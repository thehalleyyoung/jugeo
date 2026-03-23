"""Section 02 — Residual obligations are the living semantic content.

Theory2.tex Chapter 5, Section 5.2.

The central thesis of this section is that *residual obligations* are
not failures or errors to be swept under the rug.  They are the
**living semantic content** of a partially-verified judgment: structured
objects that record exactly what remains to be proved, what evidence
would discharge them, and how they propagate through algebraic
operations.

In a boolean verification system, an open residual causes the entire
judgment to be marked FAIL.  In the jugeo semantic system, an open
residual is carried forward as a first-class :class:`ResidualObligation`
inside the :class:`JudgmentProduct`, evolving as new evidence arrives.

Key ideas
---------
1. A ``ResidualObligation`` is *live*: it has a lifecycle (OPEN →
   PARTIALLY_DISCHARGED → DISCHARGED / BLOCKED).
2. Obligations can be *tracked* by an :class:`ObligationTracker`,
   which maintains the global state of obligation discharge.
3. The :class:`ResidualDischarger` attempts to satisfy obligations using
   available evidence, using pluggable discharge strategies.
4. The :class:`ResidualPropagator` propagates obligations through
   composition, restriction, and transport operations.

Classes
-------
* :class:`ObligationStatus` — lifecycle status of a single obligation.
* :class:`DischargeStrategy` — enum of strategies for obligation discharge.
* :class:`LiveResidualObligation` — an extended residual with lifecycle state.
* :class:`ObligationTracker` — tracks live obligations across products.
* :class:`ResidualDischarger` — attempts to discharge obligations.
* :class:`ResidualPropagator` — propagates residuals through algebraic maps.
* :class:`ResidualSystem` — top-level coordinator for the residual machinery.

References
----------
theory2.tex §5.2 Def 1, Prop 3–4, Cor 1.

# copilot: s02 — residuals are living semantic content, not failure flags.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    JudgmentStatus,
    Obstruction,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustLevel,
)

from jugeo.foundations.judgment_products.models import (
    JudgmentProduct,
    LocalJudgmentSection,
    ProductStatus,
)


# ---------------------------------------------------------------------------
# Supporting enumerations
# ---------------------------------------------------------------------------


class ObligationStatus(str, Enum):
    """Lifecycle status of a :class:`LiveResidualObligation`.

    Members
    -------
    OPEN
        The obligation has been created but no discharge attempt has been made.
    IN_PROGRESS
        A discharge attempt is underway (e.g. a solver is running).
    PARTIALLY_DISCHARGED
        Some sub-goals are satisfied; others remain.
    DISCHARGED
        The obligation is fully satisfied; no further action needed.
    BLOCKED
        The obligation cannot be discharged due to an obstruction.
    ESCALATED
        The obligation has been escalated to a higher authority
        (e.g. human review, external oracle).
    ABANDONED
        The obligation was explicitly dropped (with justification).
    """

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PARTIALLY_DISCHARGED = "partially_discharged"
    DISCHARGED = "discharged"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"

    def is_terminal(self) -> bool:
        """Return ``True`` iff this is a terminal (non-evolving) status.

        Returns
        -------
        bool
        """
        return self in (
            ObligationStatus.DISCHARGED,
            ObligationStatus.BLOCKED,
            ObligationStatus.ABANDONED,
        )

    def is_live(self) -> bool:
        """Return ``True`` iff this obligation is still evolving.

        Returns
        -------
        bool
        """
        return not self.is_terminal()


class DischargeStrategy(str, Enum):
    """Strategy used by the :class:`ResidualDischarger` to satisfy obligations.

    Members
    -------
    EVIDENCE_MATCH
        Looks for an evidence item whose payload satisfies the obligation.
    SOLVER_CALL
        Delegates to an external solver (SAT, SMT, etc.).
    ORACLE_QUERY
        Queries an oracle (LLM-assisted) for a discharge certificate.
    HUMAN_REVIEW
        Escalates to a human reviewer.
    STRUCTURAL_SIMPLIFICATION
        Simplifies the obligation algebraically before re-attempting.
    SUBSUMPTION
        Marks the obligation as discharged because a stronger obligation
        covering it is already discharged.
    """

    EVIDENCE_MATCH = "evidence_match"
    SOLVER_CALL = "solver_call"
    ORACLE_QUERY = "oracle_query"
    HUMAN_REVIEW = "human_review"
    STRUCTURAL_SIMPLIFICATION = "structural_simplification"
    SUBSUMPTION = "subsumption"


class PropagationDirection(str, Enum):
    """Direction of residual propagation.

    Members
    -------
    FORWARD
        Propagate from a base judgment product to a composed one.
    BACKWARD
        Propagate from a composed product back to constituents
        (e.g. when a discharge in the composition partially satisfies
        a base obligation).
    RESTRICTION
        Propagate from a global product to a restricted one.
    TRANSPORT
        Propagate along a coordinate morphism.
    """

    FORWARD = "forward"
    BACKWARD = "backward"
    RESTRICTION = "restriction"
    TRANSPORT = "transport"


# ---------------------------------------------------------------------------
# LiveResidualObligation
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True, slots=True)
class LiveResidualObligation:
    """An extended residual obligation with full lifecycle state.

    Wraps the core :class:`jugeo.judgments.judgment_terms.ResidualObligation`
    and adds tracking metadata: status, discharge attempts, evidence that
    partially satisfies it, and provenance of each status transition.

    Theory reference: theory2.tex §5.2 Def 1.

    Parameters
    ----------
    core:
        The underlying ``ResidualObligation``.
    obligation_id:
        Stable unique identifier.
    status:
        Current lifecycle status.
    product_id:
        ``product_id`` of the ``JudgmentProduct`` that owns this obligation.
    contributing_evidence_keys:
        Evidence canonical keys that partially or fully satisfy this obligation.
    blocking_obstructions:
        Obstruction descriptions that block full discharge.
    discharge_attempts:
        Tuple of ``(strategy, outcome_note)`` pairs recording past attempts.
    created_at:
        ISO-8601 timestamp of creation.
    last_updated_at:
        ISO-8601 timestamp of last status change.
    notes:
        Free-text notes from the discharger or propagator.
    """

    core: ResidualObligation
    obligation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    status: ObligationStatus = ObligationStatus.OPEN
    product_id: str = ""
    contributing_evidence_keys: tuple[str, ...] = ()
    blocking_obstructions: tuple[str, ...] = ()
    discharge_attempts: tuple[tuple[str, str], ...] = ()
    created_at: str = field(default_factory=_now_iso)
    last_updated_at: str = field(default_factory=_now_iso)
    notes: str = ""

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        """Return ``True`` iff this obligation is in an open, live state.

        Returns
        -------
        bool
        """
        return self.status.is_live()

    def is_discharged(self) -> bool:
        """Return ``True`` iff this obligation has been fully discharged.

        Returns
        -------
        bool
        """
        return self.status == ObligationStatus.DISCHARGED

    def is_blocked(self) -> bool:
        """Return ``True`` iff this obligation is blocked by an obstruction.

        Returns
        -------
        bool
        """
        return self.status == ObligationStatus.BLOCKED

    def description(self) -> str:
        """Return the description from the core obligation.

        Returns
        -------
        str
        """
        if hasattr(self.core, "description"):
            return str(self.core.description)
        return repr(self.core)

    def attempt_count(self) -> int:
        """Return the number of discharge attempts made so far.

        Returns
        -------
        int
        """
        return len(self.discharge_attempts)

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def transition_to(
        self,
        new_status: ObligationStatus,
        note: str = "",
    ) -> "LiveResidualObligation":
        """Return a copy transitioned to *new_status*.

        Parameters
        ----------
        new_status:
            The target ``ObligationStatus``.
        note:
            Optional note explaining the transition.

        Returns
        -------
        LiveResidualObligation

        Raises
        ------
        ValueError
            If the transition is not logically permitted
            (e.g. DISCHARGED → OPEN).
        """
        if self.status.is_terminal() and new_status != self.status:
            raise ValueError(
                f"Cannot transition from terminal status "
                f"{self.status.value!r} to {new_status.value!r}."
            )
        new_notes = f"{self.notes}\n[{_now_iso()}] → {new_status.value}: {note}".strip()
        return replace(
            self,
            status=new_status,
            last_updated_at=_now_iso(),
            notes=new_notes,
        )

    def record_attempt(
        self,
        strategy: DischargeStrategy,
        outcome: str,
    ) -> "LiveResidualObligation":
        """Return a copy with a new discharge attempt appended.

        Parameters
        ----------
        strategy:
            The ``DischargeStrategy`` that was tried.
        outcome:
            A short description of what happened.

        Returns
        -------
        LiveResidualObligation
        """
        new_attempts = self.discharge_attempts + ((strategy.value, outcome),)
        return replace(
            self,
            discharge_attempts=new_attempts,
            last_updated_at=_now_iso(),
        )

    def add_evidence(self, key: str) -> "LiveResidualObligation":
        """Return a copy with *key* added to contributing evidence.

        Parameters
        ----------
        key:
            Canonical key of a contributing evidence item.

        Returns
        -------
        LiveResidualObligation
        """
        if key in self.contributing_evidence_keys:
            return self
        return replace(
            self,
            contributing_evidence_keys=self.contributing_evidence_keys + (key,),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "obligation_id": self.obligation_id,
            "status": self.status.value,
            "product_id": self.product_id,
            "description": self.description(),
            "contributing_evidence": list(self.contributing_evidence_keys),
            "blocking_obstructions": list(self.blocking_obstructions),
            "attempt_count": self.attempt_count(),
            "created_at": self.created_at,
            "last_updated_at": self.last_updated_at,
            "notes": self.notes,
        }

    def __repr__(self) -> str:
        return (
            f"LiveResidualObligation(id={self.obligation_id!r}, "
            f"status={self.status.value}, "
            f"product={self.product_id!r})"
        )


# ---------------------------------------------------------------------------
# ObligationTracker
# ---------------------------------------------------------------------------


class ObligationTracker:
    """Tracks the lifecycle of residual obligations across judgment products.

    An ``ObligationTracker`` maintains a mutable registry of
    :class:`LiveResidualObligation` objects keyed by ``obligation_id``.
    It provides bulk queries (all open, all blocked, etc.) and supports
    atomic status transitions.

    This class is *not* frozen and uses a mutable internal dictionary.
    It is intended to be created once per verification session and
    updated incrementally as obligations are discharged.

    Parameters
    ----------
    obligations:
        Initial sequence of ``LiveResidualObligation`` objects to track.
    """

    def __init__(
        self, obligations: Sequence[LiveResidualObligation] | None = None
    ) -> None:
        self._registry: dict[str, LiveResidualObligation] = {}
        if obligations:
            for ob in obligations:
                self._registry[ob.obligation_id] = ob

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, obligation: LiveResidualObligation) -> None:
        """Add *obligation* to the tracker.

        If an obligation with the same ``obligation_id`` already exists,
        it is replaced.

        Parameters
        ----------
        obligation:
            The ``LiveResidualObligation`` to register.
        """
        self._registry[obligation.obligation_id] = obligation

    def register_from_product(self, product: JudgmentProduct) -> int:
        """Register all residuals from *product* as live obligations.

        Parameters
        ----------
        product:
            The ``JudgmentProduct`` whose residuals should be tracked.

        Returns
        -------
        int
            Number of new obligations registered.
        """
        count = 0
        for res in product.residuals:
            lro = LiveResidualObligation(
                core=res,
                product_id=product.product_id,
                status=ObligationStatus.OPEN,
            )
            self.register(lro)
            count += 1
        return count

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, obligation_id: str) -> LiveResidualObligation | None:
        """Look up an obligation by ID.

        Parameters
        ----------
        obligation_id:
            The ``obligation_id`` to look up.

        Returns
        -------
        LiveResidualObligation | None
        """
        return self._registry.get(obligation_id)

    def all_obligations(self) -> tuple[LiveResidualObligation, ...]:
        """Return all tracked obligations.

        Returns
        -------
        tuple[LiveResidualObligation, ...]
        """
        return tuple(self._registry.values())

    def open_obligations(self) -> tuple[LiveResidualObligation, ...]:
        """Return all obligations with a live (non-terminal) status.

        Returns
        -------
        tuple[LiveResidualObligation, ...]
        """
        return tuple(o for o in self._registry.values() if o.is_open())

    def discharged_obligations(self) -> tuple[LiveResidualObligation, ...]:
        """Return all fully-discharged obligations.

        Returns
        -------
        tuple[LiveResidualObligation, ...]
        """
        return tuple(o for o in self._registry.values() if o.is_discharged())

    def blocked_obligations(self) -> tuple[LiveResidualObligation, ...]:
        """Return all blocked obligations.

        Returns
        -------
        tuple[LiveResidualObligation, ...]
        """
        return tuple(o for o in self._registry.values() if o.is_blocked())

    def for_product(
        self, product_id: str
    ) -> tuple[LiveResidualObligation, ...]:
        """Return all obligations belonging to *product_id*.

        Parameters
        ----------
        product_id:
            The product whose obligations to retrieve.

        Returns
        -------
        tuple[LiveResidualObligation, ...]
        """
        return tuple(
            o for o in self._registry.values() if o.product_id == product_id
        )

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------

    def transition(
        self,
        obligation_id: str,
        new_status: ObligationStatus,
        note: str = "",
    ) -> bool:
        """Transition an obligation to *new_status*.

        Parameters
        ----------
        obligation_id:
            The obligation to transition.
        new_status:
            Target status.
        note:
            Optional transition note.

        Returns
        -------
        bool
            ``True`` iff the transition succeeded; ``False`` if the
            obligation was not found or the transition was rejected.
        """
        ob = self._registry.get(obligation_id)
        if ob is None:
            return False
        try:
            updated = ob.transition_to(new_status, note)
            self._registry[obligation_id] = updated
            return True
        except ValueError:
            return False

    def mark_discharged(self, obligation_id: str, note: str = "") -> bool:
        """Convenience: transition *obligation_id* to DISCHARGED.

        Parameters
        ----------
        obligation_id:
            The obligation to discharge.
        note:
            Optional discharge note.

        Returns
        -------
        bool
        """
        return self.transition(
            obligation_id, ObligationStatus.DISCHARGED, note
        )

    def mark_blocked(self, obligation_id: str, obstruction: str) -> bool:
        """Convenience: transition *obligation_id* to BLOCKED.

        Parameters
        ----------
        obligation_id:
            The obligation to block.
        obstruction:
            Description of the blocking obstruction.

        Returns
        -------
        bool
        """
        ob = self._registry.get(obligation_id)
        if ob is None:
            return False
        try:
            updated = ob.transition_to(ObligationStatus.BLOCKED, obstruction)
            updated = replace(
                updated,
                blocking_obstructions=updated.blocking_obstructions + (obstruction,),
            )
            self._registry[obligation_id] = updated
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, int]:
        """Return a status-count summary of all tracked obligations.

        Returns
        -------
        dict[str, int]
            Maps status value strings to counts.
        """
        counts: dict[str, int] = {}
        for ob in self._registry.values():
            key = ob.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def is_fully_discharged(self) -> bool:
        """Return ``True`` iff every tracked obligation is discharged.

        Returns
        -------
        bool
        """
        return all(o.is_discharged() for o in self._registry.values())

    def __len__(self) -> int:
        return len(self._registry)

    def __repr__(self) -> str:
        s = self.summary()
        return f"ObligationTracker(total={len(self)}, summary={s})"


# ---------------------------------------------------------------------------
# ResidualDischarger
# ---------------------------------------------------------------------------


@dataclass
class DischargeResult:
    """The outcome of a single discharge attempt.

    Parameters
    ----------
    obligation_id:
        The ID of the obligation that was attempted.
    strategy:
        The strategy that was used.
    succeeded:
        Whether the discharge was successful.
    partial:
        Whether the discharge was partial (some sub-goals satisfied).
    evidence_key:
        The canonical key of the evidence item that discharged it, if any.
    new_status:
        The resulting ``ObligationStatus`` after the attempt.
    notes:
        Free-text notes from the discharger.
    """

    obligation_id: str
    strategy: DischargeStrategy
    succeeded: bool = False
    partial: bool = False
    evidence_key: str = ""
    new_status: ObligationStatus = ObligationStatus.OPEN
    notes: str = ""


class ResidualDischarger:
    """Attempts to discharge residual obligations using available evidence.

    The ``ResidualDischarger`` applies a sequence of :class:`DischargeStrategy`
    values against a set of :class:`LiveResidualObligation` objects,
    updating an :class:`ObligationTracker` as it proceeds.

    Theory reference: theory2.tex §5.2 Prop 3.

    Parameters
    ----------
    tracker:
        The ``ObligationTracker`` to update.
    strategies:
        Ordered sequence of strategies to try, in priority order.
        Defaults to ``[EVIDENCE_MATCH, STRUCTURAL_SIMPLIFICATION,
        SUBSUMPTION]``.
    """

    def __init__(
        self,
        tracker: ObligationTracker,
        strategies: Sequence[DischargeStrategy] | None = None,
    ) -> None:
        self.tracker = tracker
        self.strategies: tuple[DischargeStrategy, ...] = tuple(
            strategies
            if strategies is not None
            else [
                DischargeStrategy.EVIDENCE_MATCH,
                DischargeStrategy.STRUCTURAL_SIMPLIFICATION,
                DischargeStrategy.SUBSUMPTION,
            ]
        )

    # ------------------------------------------------------------------
    # Core discharge logic
    # ------------------------------------------------------------------

    def attempt_discharge(
        self,
        obligation: LiveResidualObligation,
        evidence_bundle: EvidenceBundle,
    ) -> DischargeResult:
        """Attempt to discharge *obligation* using *evidence_bundle*.

        Tries each configured strategy in order.  Returns the result of
        the first strategy that succeeds or partially succeeds.  If no
        strategy succeeds, returns a result with ``succeeded=False``.

        Parameters
        ----------
        obligation:
            The obligation to discharge.
        evidence_bundle:
            The evidence available for discharge.

        Returns
        -------
        DischargeResult
        """
        for strategy in self.strategies:
            result = self._apply_strategy(strategy, obligation, evidence_bundle)
            if result.succeeded or result.partial:
                return result
        return DischargeResult(
            obligation_id=obligation.obligation_id,
            strategy=self.strategies[-1] if self.strategies else DischargeStrategy.EVIDENCE_MATCH,
            succeeded=False,
            notes="All strategies exhausted without discharge.",
        )

    def _apply_strategy(
        self,
        strategy: DischargeStrategy,
        obligation: LiveResidualObligation,
        evidence_bundle: EvidenceBundle,
    ) -> DischargeResult:
        """Apply a single *strategy* to *obligation*.

        Parameters
        ----------
        strategy:
            The strategy to apply.
        obligation:
            The obligation to attempt.
        evidence_bundle:
            Available evidence.

        Returns
        -------
        DischargeResult
        """
        if strategy == DischargeStrategy.EVIDENCE_MATCH:
            return self._evidence_match(obligation, evidence_bundle)
        if strategy == DischargeStrategy.STRUCTURAL_SIMPLIFICATION:
            return self._structural_simplify(obligation)
        if strategy == DischargeStrategy.SUBSUMPTION:
            return self._subsumption(obligation)
        # Default: not applicable
        return DischargeResult(
            obligation_id=obligation.obligation_id,
            strategy=strategy,
            succeeded=False,
            notes=f"Strategy {strategy.value} not implemented; skipped.",
        )

    def _evidence_match(
        self,
        obligation: LiveResidualObligation,
        evidence_bundle: EvidenceBundle,
    ) -> DischargeResult:
        """Attempt evidence-match discharge.

        Looks for any evidence item in the bundle that is SOLVER_PROOF
        or FORMAL_PROOF with trust ≥ SOLVER_DISCHARGED.

        Parameters
        ----------
        obligation:
            The obligation.
        evidence_bundle:
            Available evidence.

        Returns
        -------
        DischargeResult
        """
        strong_items = [
            item
            for item in evidence_bundle.items
            if item.trust_level >= TrustLevel.SOLVER_DISCHARGED
            and item.kind
            in (EvidenceItemKind.SOLVER_PROOF, EvidenceItemKind.FORMAL_PROOF)
        ]
        if strong_items:
            best = strong_items[0]
            key = best.canonical_key()
            return DischargeResult(
                obligation_id=obligation.obligation_id,
                strategy=DischargeStrategy.EVIDENCE_MATCH,
                succeeded=True,
                evidence_key=key,
                new_status=ObligationStatus.DISCHARGED,
                notes=f"Discharged by evidence {key!r}.",
            )
        # Partial: any evidence of any kind
        any_items = list(evidence_bundle.valid_items())
        if any_items:
            key = any_items[0].canonical_key()
            return DischargeResult(
                obligation_id=obligation.obligation_id,
                strategy=DischargeStrategy.EVIDENCE_MATCH,
                succeeded=False,
                partial=True,
                evidence_key=key,
                new_status=ObligationStatus.PARTIALLY_DISCHARGED,
                notes=f"Partial evidence found: {key!r}.",
            )
        return DischargeResult(
            obligation_id=obligation.obligation_id,
            strategy=DischargeStrategy.EVIDENCE_MATCH,
            succeeded=False,
            notes="No applicable evidence found.",
        )

    def _structural_simplify(
        self, obligation: LiveResidualObligation
    ) -> DischargeResult:
        """Attempt to simplify the obligation structurally.

        If the obligation has no remaining sub-conditions (empty
        conditions tuple), it can be vacuously discharged.

        Parameters
        ----------
        obligation:
            The obligation.

        Returns
        -------
        DischargeResult
        """
        core = obligation.core
        # If core has a trivially true formula, discharge it
        formula = getattr(core, "formula", None) or ""
        if formula in ("True", "true", "⊤", ""):
            return DischargeResult(
                obligation_id=obligation.obligation_id,
                strategy=DischargeStrategy.STRUCTURAL_SIMPLIFICATION,
                succeeded=True,
                new_status=ObligationStatus.DISCHARGED,
                notes="Vacuously satisfied (trivial formula).",
            )
        return DischargeResult(
            obligation_id=obligation.obligation_id,
            strategy=DischargeStrategy.STRUCTURAL_SIMPLIFICATION,
            succeeded=False,
            notes="No structural simplification available.",
        )

    def _subsumption(
        self, obligation: LiveResidualObligation
    ) -> DischargeResult:
        """Attempt subsumption discharge.

        If any already-discharged obligation in the tracker has a
        description that subsumes (contains) this obligation's description,
        the obligation is discharged by subsumption.

        Parameters
        ----------
        obligation:
            The obligation.

        Returns
        -------
        DischargeResult
        """
        desc = obligation.description()
        for other in self.tracker.discharged_obligations():
            if other.obligation_id == obligation.obligation_id:
                continue
            if desc in other.description():
                return DischargeResult(
                    obligation_id=obligation.obligation_id,
                    strategy=DischargeStrategy.SUBSUMPTION,
                    succeeded=True,
                    new_status=ObligationStatus.DISCHARGED,
                    notes=(
                        f"Subsumed by discharged obligation "
                        f"{other.obligation_id!r}."
                    ),
                )
        return DischargeResult(
            obligation_id=obligation.obligation_id,
            strategy=DischargeStrategy.SUBSUMPTION,
            succeeded=False,
            notes="No subsuming obligation found.",
        )

    # ------------------------------------------------------------------
    # Bulk discharge
    # ------------------------------------------------------------------

    def discharge_all(
        self, evidence_bundle: EvidenceBundle
    ) -> tuple[int, int]:
        """Attempt to discharge all open obligations in the tracker.

        Parameters
        ----------
        evidence_bundle:
            Evidence to use for all discharge attempts.

        Returns
        -------
        tuple[int, int]
            ``(discharged_count, still_open_count)``.
        """
        discharged = 0
        for ob in self.tracker.open_obligations():
            result = self.attempt_discharge(ob, evidence_bundle)
            if result.succeeded:
                self.tracker.transition(
                    ob.obligation_id,
                    ObligationStatus.DISCHARGED,
                    result.notes,
                )
                discharged += 1
            elif result.partial:
                self.tracker.transition(
                    ob.obligation_id,
                    ObligationStatus.PARTIALLY_DISCHARGED,
                    result.notes,
                )
        still_open = len(self.tracker.open_obligations())
        return discharged, still_open

    def update_product_status(self, product: JudgmentProduct) -> JudgmentProduct:
        """Return *product* with status updated to reflect discharge progress.

        Parameters
        ----------
        product:
            The product whose status to update.

        Returns
        -------
        JudgmentProduct
        """
        product_obs = self.tracker.for_product(product.product_id)
        all_done = all(o.is_discharged() for o in product_obs)
        any_blocked = any(o.is_blocked() for o in product_obs)
        if all_done and not product.has_obstructions():
            return product.with_status(ProductStatus.DISCHARGED)
        if any_blocked:
            return product.with_status(ProductStatus.OBSTRUCTED)
        return product


# ---------------------------------------------------------------------------
# ResidualPropagator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PropagationRecord:
    """Record of a single residual propagation event.

    Parameters
    ----------
    source_product_id:
        The product from which residuals were propagated.
    target_product_id:
        The product to which residuals were propagated.
    direction:
        Propagation direction.
    propagated_ids:
        Obligation IDs that were propagated.
    suppressed_ids:
        Obligation IDs that were suppressed (not propagated).
    notes:
        Free-text notes.
    timestamp:
        ISO-8601 timestamp.
    """

    source_product_id: str
    target_product_id: str
    direction: PropagationDirection = PropagationDirection.FORWARD
    propagated_ids: tuple[str, ...] = ()
    suppressed_ids: tuple[str, ...] = ()
    notes: str = ""
    timestamp: str = field(default_factory=_now_iso)

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "source": self.source_product_id,
            "target": self.target_product_id,
            "direction": self.direction.value,
            "propagated": list(self.propagated_ids),
            "suppressed": list(self.suppressed_ids),
            "notes": self.notes,
            "timestamp": self.timestamp,
        }


class ResidualPropagator:
    """Propagates residual obligations through algebraic operations.

    The ``ResidualPropagator`` implements the propagation rules from
    theory2.tex §5.2 Prop 4:

    * **Forward** (composition): residuals from constituents are merged
      into the product, de-duplicated by description.
    * **Backward** (discharge feedback): when a discharge in a composed
      product satisfies a constituent residual, the constituent is updated.
    * **Restriction**: residuals of a restricted product are a subset of
      the original, filtered by coordinate relevance.
    * **Transport**: residuals are transported along a morphism,
      relabelling coordinate-specific parts.

    Parameters
    ----------
    tracker:
        The ``ObligationTracker`` to update during propagation.
    """

    def __init__(self, tracker: ObligationTracker) -> None:
        self.tracker = tracker
        self._records: list[PropagationRecord] = []

    # ------------------------------------------------------------------
    # Propagation operations
    # ------------------------------------------------------------------

    def propagate_forward(
        self,
        sources: Sequence[JudgmentProduct],
        target: JudgmentProduct,
    ) -> PropagationRecord:
        """Propagate residuals from *sources* into *target* (forward direction).

        Collects all open obligations from each source product,
        de-duplicates by description, and registers them on *target*.

        Parameters
        ----------
        sources:
            The constituent products whose residuals to propagate.
        target:
            The composed product that should inherit the residuals.

        Returns
        -------
        PropagationRecord
        """
        seen_descriptions: set[str] = set()
        propagated_ids: list[str] = []
        suppressed_ids: list[str] = []

        for source in sources:
            for ob in self.tracker.for_product(source.product_id):
                if not ob.is_open():
                    suppressed_ids.append(ob.obligation_id)
                    continue
                desc = ob.description()
                if desc in seen_descriptions:
                    suppressed_ids.append(ob.obligation_id)
                    continue
                seen_descriptions.add(desc)
                forwarded = replace(ob, product_id=target.product_id)
                self.tracker.register(forwarded)
                propagated_ids.append(ob.obligation_id)

        record = PropagationRecord(
            source_product_id=",".join(p.product_id for p in sources),
            target_product_id=target.product_id,
            direction=PropagationDirection.FORWARD,
            propagated_ids=tuple(propagated_ids),
            suppressed_ids=tuple(suppressed_ids),
            notes=f"Forward propagation from {len(sources)} source(s).",
        )
        self._records.append(record)
        return record

    def propagate_restriction(
        self,
        source: JudgmentProduct,
        target: JudgmentProduct,
        coordinate_label: str,
    ) -> PropagationRecord:
        """Propagate restricted residuals from *source* to *target*.

        Only obligations whose description mentions *coordinate_label*
        (or are coordinate-agnostic) are propagated.

        Parameters
        ----------
        source:
            The product being restricted.
        target:
            The restricted product.
        coordinate_label:
            The coordinate label to filter by.

        Returns
        -------
        PropagationRecord
        """
        propagated_ids: list[str] = []
        suppressed_ids: list[str] = []

        for ob in self.tracker.for_product(source.product_id):
            desc = ob.description()
            if coordinate_label in desc or not desc:
                forwarded = replace(ob, product_id=target.product_id)
                self.tracker.register(forwarded)
                propagated_ids.append(ob.obligation_id)
            else:
                suppressed_ids.append(ob.obligation_id)

        record = PropagationRecord(
            source_product_id=source.product_id,
            target_product_id=target.product_id,
            direction=PropagationDirection.RESTRICTION,
            propagated_ids=tuple(propagated_ids),
            suppressed_ids=tuple(suppressed_ids),
            notes=f"Restriction to coordinate {coordinate_label!r}.",
        )
        self._records.append(record)
        return record

    def propagate_transport(
        self,
        source: JudgmentProduct,
        target: JudgmentProduct,
        morphism_label: str,
    ) -> PropagationRecord:
        """Transport residuals from *source* to *target* along a morphism.

        Residuals are carried verbatim to the target product with a note
        recording the transport morphism.

        Parameters
        ----------
        source:
            The source product.
        target:
            The transported product.
        morphism_label:
            Label of the coordinate morphism.

        Returns
        -------
        PropagationRecord
        """
        propagated_ids: list[str] = []

        for ob in self.tracker.for_product(source.product_id):
            transported = replace(
                ob,
                product_id=target.product_id,
                notes=ob.notes + f"\n[transported along {morphism_label!r}]",
            )
            self.tracker.register(transported)
            propagated_ids.append(ob.obligation_id)

        record = PropagationRecord(
            source_product_id=source.product_id,
            target_product_id=target.product_id,
            direction=PropagationDirection.TRANSPORT,
            propagated_ids=tuple(propagated_ids),
            notes=f"Transport along morphism {morphism_label!r}.",
        )
        self._records.append(record)
        return record

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def propagation_history(self) -> tuple[PropagationRecord, ...]:
        """Return all propagation records in chronological order.

        Returns
        -------
        tuple[PropagationRecord, ...]
        """
        return tuple(self._records)

    def __repr__(self) -> str:
        return (
            f"ResidualPropagator(records={len(self._records)}, "
            f"tracker={self.tracker!r})"
        )


# ---------------------------------------------------------------------------
# ResidualSystem (top-level coordinator)
# ---------------------------------------------------------------------------


class ResidualSystem:
    """Top-level coordinator for the residual obligation machinery.

    A ``ResidualSystem`` combines an :class:`ObligationTracker`,
    a :class:`ResidualDischarger`, and a :class:`ResidualPropagator`
    into a single cohesive API.

    Parameters
    ----------
    strategies:
        Discharge strategies to use, in priority order.
    """

    def __init__(
        self, strategies: Sequence[DischargeStrategy] | None = None
    ) -> None:
        self.tracker = ObligationTracker()
        self.discharger = ResidualDischarger(self.tracker, strategies)
        self.propagator = ResidualPropagator(self.tracker)

    # ------------------------------------------------------------------
    # High-level operations
    # ------------------------------------------------------------------

    def ingest_product(self, product: JudgmentProduct) -> int:
        """Register all residuals from *product* with the tracker.

        Parameters
        ----------
        product:
            The ``JudgmentProduct`` to ingest.

        Returns
        -------
        int
            Number of obligations registered.
        """
        return self.tracker.register_from_product(product)

    def discharge_product(
        self, product: JudgmentProduct, evidence: EvidenceBundle
    ) -> JudgmentProduct:
        """Attempt to discharge all obligations for *product*.

        Parameters
        ----------
        product:
            The product whose obligations to discharge.
        evidence:
            Available evidence bundle.

        Returns
        -------
        JudgmentProduct
            The product with updated status.
        """
        self.discharger.discharge_all(evidence)
        return self.discharger.update_product_status(product)

    def summary(self) -> dict[str, Any]:
        """Return a summary of the current system state.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "tracker_summary": self.tracker.summary(),
            "total_obligations": len(self.tracker),
            "fully_discharged": self.tracker.is_fully_discharged(),
            "propagation_records": len(self.propagator.propagation_history()),
        }

    def __repr__(self) -> str:
        return (
            f"ResidualSystem(tracker={self.tracker!r}, "
            f"strategies={[s.value for s in self.discharger.strategies]})"
        )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Enumerations
    "ObligationStatus",
    "DischargeStrategy",
    "PropagationDirection",
    # Models
    "LiveResidualObligation",
    "PropagationRecord",
    "DischargeResult",
    # Tracker
    "ObligationTracker",
    # Discharger
    "ResidualDischarger",
    # Propagator
    "ResidualPropagator",
    # System
    "ResidualSystem",
]

# copilot: s02 — residuals are living semantic content, not failure flags.
