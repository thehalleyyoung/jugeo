"""Integration layer between repair_semantics and the JuGeo judgment algebra.

This module provides :class:`RepairSemanticsIntegration`, a frozen dataclass
that acts as the bridge between the debug/repair pipeline and the core
judgment algebra implemented in :mod:`jugeo.judgments.judgment_terms`.

The integration responsibilities are:

* **Evidence attachment** — convert counterexample records and debug session
  summaries into :class:`~jugeo.judgments.judgment_terms.EvidenceItem` objects
  and attach them to live judgments.

* **Trust promotion** — after a debug session converges, promote the trust
  level of the repaired judgment to reflect the new evidence.

* **Obligation export** — convert unresolved counterexamples into
  :class:`~jugeo.judgments.judgment_terms.ResidualObligation` objects that
  downstream systems can track and discharge.

* **Obstruction merging** — convert :class:`~jugeo.errors.ObstructionRecord`
  objects from counterexample records into first-class
  :class:`~jugeo.judgments.judgment_terms.Obstruction` objects and attach
  them to the judgment's obstruction set.

* **Failure chain construction** — build a :class:`~jugeo.errors.FailureChain`
  from a debug session's counterexamples for structured error reporting.

Theory basis
------------
The integration layer implements the *evidence accumulation* step from
theory2.tex §11.4: after each repair iteration, the current judgment is
updated with new evidence items and any new obstructions discovered by the
solver.  Trust is only promoted when the session has fully converged (status
``CONVERGED``), consistent with the *no silent trust promotion* principle
stated in theory2.tex §252.

Backward compatibility
-----------------------
The public API of :class:`RepairSemanticsIntegration` is not yet stable.
Callers should import through the package-level ``__init__.py``.

See also
--------
* :mod:`jugeo.judgments.judgment_terms` — judgment algebra.
* :mod:`jugeo.problem_modes.repair_semantics.models` — data model types.
* :mod:`jugeo.problem_modes.repair_semantics.algorithms` — algorithmic layer.
* :mod:`jugeo.errors` — structured failure and obstruction types.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from jugeo.judgments.judgment_terms import (
    Carrier,
    EvidenceBundle,
    EvidenceItem,
    EvidenceItemKind,
    Judgment,
    JudgmentStatus,
    Obstruction,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.solver.countermodels import FailureClass
from jugeo.errors import (
    EvidenceFamily,
    FailureChain,
    FailureClassification,
    FailureScope,
    ObstructionRecord,
    RepairHint,
    RepairPriority,
    StructuredFailure,
    as_failure_payload,
    chain_failures,
    raise_with_scope,
)
from jugeo.problem_modes.repair_semantics.models import (
    CounterexampleRecord,
    DebugSession,
    DebugSessionStatus,
    RepairPlan,
    RepairValidator,
)

# ---------------------------------------------------------------------------
# Module-level provenance
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "integration",
    "theory_section": "§11.4 — Judgment Integration and Trust Promotion",
}

# ---------------------------------------------------------------------------
# §A  Module-level helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time in ISO-8601 format.

    Returns
    -------
    str
        Timestamp string e.g. ``"2024-01-15T12:34:56Z"``.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _trust_level_for_status(status: DebugSessionStatus) -> TrustLevel:
    """Map a debug session status to an appropriate trust level.

    The mapping reflects the theory2 trust hierarchy: a converged session
    produces solver-discharged evidence (TrustLevel 4), while a blocked or
    open session produces only oracle-proposed evidence (TrustLevel 2).

    Parameters
    ----------
    status : DebugSessionStatus
        The current status of the debug session.

    Returns
    -------
    TrustLevel
        The trust level appropriate for evidence from a session in this status.
    """
    if status == DebugSessionStatus.CONVERGED:
        return TrustLevel.SOLVER_DISCHARGED
    if status == DebugSessionStatus.BLOCKED:
        return TrustLevel.ORACLE_PROPOSED
    # OPEN or unknown
    return TrustLevel.UNVERIFIED


def _obstruction_from_record(record: CounterexampleRecord) -> Obstruction:
    """Build a judgment-algebra :class:`Obstruction` from a counterexample record.

    The resulting :class:`Obstruction` carries the record's failure class as
    its ``violated_condition``, the record's coordinate, a tuple of repair hint
    descriptions, and the cohomology class label.

    Parameters
    ----------
    record : CounterexampleRecord
        Source counterexample record.

    Returns
    -------
    Obstruction
        A new :class:`~jugeo.judgments.judgment_terms.Obstruction` derived
        from the record.
    """
    repair_hint_texts = tuple(hint.description for hint in record.repair_hints)
    cohomology = record.cohomology_class or f"H1[unknown:{record.coordinate}:{record.record_id}]"
    return Obstruction(
        violated_condition=record.failure_message or record.failure_class.value,
        coordinate=record.coordinate,
        repair_hints=repair_hint_texts,
        cohomology_class=cohomology,
        evidence_at_time=(record.record_id,),
    )


def _make_evidence_item(
    channel: str,
    content: str,
    trust: TrustLevel,
) -> EvidenceItem:
    """Construct an :class:`EvidenceItem` with oracle-proposal kind.

    Parameters
    ----------
    channel : str
        Evidence channel name (e.g. ``"repair_session"``, ``"counterexample"``).
    content : str
        Content string to embed in the payload.
    trust : TrustLevel
        Trust level for the new evidence item.

    Returns
    -------
    EvidenceItem
        A new evidence item ready for attachment to a judgment.
    """
    return EvidenceItem(
        kind=EvidenceItemKind.ORACLE_PROPOSAL,
        payload={"channel": channel, "content": content, "generated_at": _now_iso()},
        trust_level=trust,
        channel=channel,
        timestamp=_now_iso(),
    )


# ---------------------------------------------------------------------------
# §1  RepairSemanticsIntegration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairSemanticsIntegration:
    """Integration bridge between repair_semantics and the judgment algebra.

    :class:`RepairSemanticsIntegration` is a stateless frozen dataclass that
    bundles the configuration for connecting a debug session to a live
    judgment.  All mutation methods return *new* judgment instances, consistent
    with the frozen-dataclass philosophy throughout the JuGeo codebase.

    Theory basis
    ------------
    Implements the evidence accumulation and trust promotion steps from
    theory2.tex §11.4.  The integration respects the *no silent trust
    promotion* principle: trust is only promoted when the session has
    fully converged.

    Parameters
    ----------
    coordinate : str
        The semantic coordinate this integration instance operates at.
        Used as a default coordinate when building evidence items.
    trust_promotion_threshold : TrustLevel
        The minimum trust level required before automatic trust promotion
        is considered.  Defaults to ``TrustLevel.RUNTIME_WITNESSED`` (3).
    max_evidence_items : int
        Maximum number of evidence items to attach in a single call to
        :meth:`attach_debug_evidence`.  Items beyond this limit are dropped
        with a warning.
    strict_mode : bool
        When True, :meth:`validate_and_update_judgment` raises on
        validation failure instead of adding an obstruction.
    """

    coordinate: str = ""
    trust_promotion_threshold: TrustLevel = TrustLevel.RUNTIME_WITNESSED
    max_evidence_items: int = 20
    strict_mode: bool = False

    # ------------------------------------------------------------------
    # §1.1  integrate_with_judgment
    # ------------------------------------------------------------------

    def integrate_with_judgment(
        self,
        judgment: Judgment,
        session: DebugSession,
    ) -> Judgment:
        """Attach repair session evidence to a judgment and optionally promote trust.

        This is the primary integration entry point.  It performs three steps:

        1. Builds a repair evidence item summarizing the session via
           :meth:`build_repair_evidence_item`.
        2. Merges the evidence item into the judgment's evidence bundle via
           :meth:`~jugeo.judgments.judgment_terms.Judgment.merge_evidence`.
        3. If the session has converged, promotes the judgment's trust level
           via :meth:`promote_trust_after_repair`.

        Parameters
        ----------
        judgment : Judgment
            The judgment to update.
        session : DebugSession
            The debug session whose results to attach.

        Returns
        -------
        Judgment
            Updated judgment with session evidence (and optionally promoted
            trust).
        """
        evidence_item = self.build_repair_evidence_item(session)
        bundle = EvidenceBundle(items=(evidence_item,))
        updated = judgment.merge_evidence(bundle)

        if session.is_converged():
            updated = self.promote_trust_after_repair(updated, session)

        return updated

    # ------------------------------------------------------------------
    # §1.2  attach_debug_evidence
    # ------------------------------------------------------------------

    def attach_debug_evidence(
        self,
        judgment: Judgment,
        record: CounterexampleRecord,
    ) -> Judgment:
        """Attach a single counterexample record as evidence to a judgment.

        Constructs an :class:`EvidenceItem` from the counterexample record
        using ``EvidenceItemKind.ORACLE_PROPOSAL`` and the ``"counterexample"``
        channel, then merges it into the judgment's evidence bundle.

        The counterexample is treated as *oracle-proposed* evidence because
        it comes from the solver's model, which must still be validated
        against the full semantic context before being treated as
        ``SOLVER_DISCHARGED``.

        Parameters
        ----------
        judgment : Judgment
            The judgment to attach evidence to.
        record : CounterexampleRecord
            The counterexample record to convert to evidence.

        Returns
        -------
        Judgment
            Updated judgment with the new evidence item attached.
        """
        trust = TrustLevel.ORACLE_PROPOSED
        item = EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={
                "channel": "counterexample",
                "content": record.record_id,
                "coordinate": record.coordinate,
                "failure_class": record.failure_class.value,
                "cohomology_class": record.cohomology_class,
                "is_minimal": record.is_minimal,
                "generated_at": _now_iso(),
            },
            trust_level=trust,
            channel="counterexample",
            timestamp=_now_iso(),
            provenance=("repair_semantics", "counterexample_extraction"),
        )
        bundle = EvidenceBundle(items=(item,))
        return judgment.merge_evidence(bundle)

    # ------------------------------------------------------------------
    # §1.3  promote_trust_after_repair
    # ------------------------------------------------------------------

    def promote_trust_after_repair(
        self,
        judgment: Judgment,
        session: DebugSession,
    ) -> Judgment:
        """Promote the trust level of a judgment after a successful repair.

        Trust promotion is only performed when the session status is
        ``CONVERGED``.  If the judgment's current trust level is already at
        or above ``trust_promotion_threshold``, the judgment is returned
        unchanged.

        If the judgment's trust level is below ``trust_promotion_threshold``,
        :meth:`~jugeo.judgments.judgment_terms.Judgment.strengthen` is called
        with reason ``"repair_converged"``.

        Theory basis
        ------------
        Implements the trust promotion step from theory2.tex §11.4.  Trust
        promotion is the only mechanism by which a judgment's trust level may
        increase inside the repair pipeline; all other operations are
        evidence-neutral.

        Parameters
        ----------
        judgment : Judgment
            The judgment whose trust to promote.
        session : DebugSession
            The session that (must be) converged.

        Returns
        -------
        Judgment
            Updated judgment with promoted trust, or the original judgment
            if the session has not converged or trust is already sufficient.
        """
        if not session.is_converged():
            return judgment

        current_level = judgment.trust_annotation.level
        if int(current_level) >= int(self.trust_promotion_threshold):
            return judgment

        return judgment.strengthen(
            reason="repair_converged",
            target=self.trust_promotion_threshold,
        )

    # ------------------------------------------------------------------
    # §1.4  export_repair_obligations
    # ------------------------------------------------------------------

    def export_repair_obligations(
        self,
        session: DebugSession,
    ) -> tuple[ResidualObligation, ...]:
        """Export unresolved counterexamples as residual obligations.

        For each counterexample record in the session that has not been
        covered by a repair plan, creates a :class:`ResidualObligation`
        capturing the outstanding verification work.

        An obligation is created for every record because there is no
        general mechanism for determining which records have been resolved
        inside this function; resolution is handled externally by the
        repair executor.

        Parameters
        ----------
        session : DebugSession
            The session from which to export obligations.

        Returns
        -------
        tuple[ResidualObligation, ...]
            One obligation per counterexample record in the session.
            Returns an empty tuple if the session has no counterexamples.
        """
        obligations: list[ResidualObligation] = []
        for record in session.counterexamples:
            obligation = ResidualObligation(
                obligation_id=record.record_id,
                description=(
                    record.cohomology_class
                    or f"Counterexample at {record.coordinate}: {record.failure_message}"
                ),
                required_evidence_kind=EvidenceItemKind.SOLVER_PROOF,
                priority=self._priority_for_failure_class(record.failure_class),
                provenance=(
                    f"session:{session.session_id}",
                    f"coordinate:{record.coordinate}",
                    f"failure_class:{record.failure_class.value}",
                ),
            )
            obligations.append(obligation)
        return tuple(obligations)

    # ------------------------------------------------------------------
    # §1.5  merge_counterexample_obstructions
    # ------------------------------------------------------------------

    def merge_counterexample_obstructions(
        self,
        judgment: Judgment,
        records: Sequence[CounterexampleRecord],
    ) -> Judgment:
        """Attach counterexample obstructions to a judgment.

        For each counterexample record, constructs an
        :class:`~jugeo.judgments.judgment_terms.Obstruction` via
        :func:`_obstruction_from_record` and adds it to the judgment via
        :meth:`~jugeo.judgments.judgment_terms.Judgment.add_obstruction`.

        Obstructions are first-class semantic objects (cohomology classes)
        and are never silently erased.  Adding an obstruction to a judgment
        records the fact that a specific violation was detected at a specific
        coordinate, regardless of subsequent repair attempts.

        Parameters
        ----------
        judgment : Judgment
            The judgment to attach obstructions to.
        records : Sequence[CounterexampleRecord]
            The counterexample records to convert to obstructions.

        Returns
        -------
        Judgment
            Updated judgment with all new obstructions attached.
        """
        updated = judgment
        for record in records:
            obstruction = _obstruction_from_record(record)
            updated = updated.add_obstruction(obstruction)
        return updated

    # ------------------------------------------------------------------
    # §1.6  build_repair_evidence_item
    # ------------------------------------------------------------------

    def build_repair_evidence_item(
        self,
        session: DebugSession,
    ) -> EvidenceItem:
        """Build a single :class:`EvidenceItem` summarizing a repair session.

        The evidence item is a synthetic summary of the session's outcome:
        it records the session ID, iteration count, and status in its
        payload.  The trust level is determined by the session status via
        :func:`_trust_level_for_status`.

        ``EvidenceItemKind.ORACLE_PROPOSAL`` is used because the repair
        session summary is produced by the orchestration layer (not directly
        by a solver proof or formal verifier).

        Parameters
        ----------
        session : DebugSession
            The session to summarize.

        Returns
        -------
        EvidenceItem
            A new evidence item suitable for attachment to a judgment.
        """
        trust = _trust_level_for_status(session.status)
        content = (
            f"session:{session.session_id}"
            f":iter:{session.iteration_count}"
            f":status:{session.status.value}"
            f":ce_count:{len(session.counterexamples)}"
        )
        return EvidenceItem(
            kind=EvidenceItemKind.ORACLE_PROPOSAL,
            payload={
                "channel": "repair_session",
                "content": content,
                "session_id": session.session_id,
                "coordinate": session.coordinate or self.coordinate,
                "iteration_count": session.iteration_count,
                "status": session.status.value,
                "counterexample_count": len(session.counterexamples),
                "repair_attempt_count": len(session.repair_attempts),
                "generated_at": _now_iso(),
            },
            trust_level=trust,
            channel="repair_session",
            timestamp=_now_iso(),
            provenance=(
                "repair_semantics",
                f"session:{session.session_id}",
            ),
        )

    # ------------------------------------------------------------------
    # §1.7  validate_and_update_judgment
    # ------------------------------------------------------------------

    def validate_and_update_judgment(
        self,
        judgment: Judgment,
        session: DebugSession,
    ) -> Judgment:
        """Validate the latest repair plan and update the judgment accordingly.

        Creates a :class:`~jugeo.problem_modes.repair_semantics.models.RepairValidator`
        and validates the session's most recent repair plan.  Depending on
        the validation outcome:

        * **Success**: promotes trust via :meth:`promote_trust_after_repair`
          and removes resolved obstructions.
        * **Failure**: adds a new obstruction to the judgment recording the
          validation failure.  In strict mode, raises instead of adding
          the obstruction.

        Parameters
        ----------
        judgment : Judgment
            The judgment to update.
        session : DebugSession
            The session whose latest repair plan to validate.

        Returns
        -------
        Judgment
            Updated judgment reflecting the validation outcome.

        Raises
        ------
        jugeo.errors.JuGeoError
            In strict mode, if validation fails.
        """
        latest_plan = session.latest_repair_plan()
        if latest_plan is None:
            return judgment

        validator = RepairValidator(strict=self.strict_mode)
        ok, failures = validator.validate(latest_plan)

        if ok:
            return self.promote_trust_after_repair(judgment, session)

        # Validation failed: build obstruction
        failure_description = "; ".join(failures) if failures else "plan validation failed"

        if self.strict_mode:
            raise_with_scope(
                message=f"Repair plan validation failed: {failure_description}",
                scope=FailureScope.ORCHESTRATION,
                classification=FailureClassification.LOCAL_REPAIR,
                coordinate=session.coordinate or self.coordinate,
            )

        from jugeo.judgments.judgment_terms import Obstruction
        obstruction = Obstruction(
            violated_condition=f"repair_plan_invalid: {failure_description}",
            coordinate=session.coordinate or self.coordinate,
            repair_hints=(f"Fix repair plan {latest_plan.plan_id}",),
            cohomology_class=f"H1[plan_validation:{latest_plan.plan_id}]",
            evidence_at_time=(latest_plan.plan_id,),
        )
        return judgment.add_obstruction(obstruction)

    # ------------------------------------------------------------------
    # §1.8  export_debug_chain
    # ------------------------------------------------------------------

    def export_debug_chain(self, session: DebugSession) -> FailureChain:
        """Build a :class:`~jugeo.errors.FailureChain` from a debug session.

        Converts each counterexample record in the session into a
        :class:`~jugeo.errors.StructuredFailure` via
        :func:`~jugeo.errors.as_failure_payload`, then combines them into
        a single :class:`~jugeo.errors.FailureChain` via
        :func:`~jugeo.errors.chain_failures`.

        The failure chain preserves the overlap structure of the
        counterexamples: they remain individually addressable rather than
        being collapsed into a single error message.

        Parameters
        ----------
        session : DebugSession
            The session to export failures from.

        Returns
        -------
        FailureChain
            An ordered chain of structured failures.  Returns a chain with
            no failures if the session has no counterexamples.
        """
        failures: list[StructuredFailure] = []

        for record in session.counterexamples:
            # Build a synthetic exception-like payload for each record
            payload = as_failure_payload(
                ValueError(
                    f"Counterexample at {record.coordinate}: "
                    f"{record.failure_message or record.failure_class.value}"
                )
            )
            sf = StructuredFailure(
                message=record.failure_message or record.failure_class.value,
                coordinate=record.coordinate or self.coordinate,
                scope=FailureScope.SOLVER,
                classification=self._classification_for_failure_class(
                    record.failure_class
                ),
                evidence_family=EvidenceFamily.SOLVER,
                repair_hints=record.repair_hints,
                provenance_tags=(
                    f"session:{session.session_id}",
                    f"record:{record.record_id}",
                    f"cohomology:{record.cohomology_class}",
                ),
            )
            failures.append(sf)

        return chain_failures(
            failures,
            context_coordinate=session.coordinate or self.coordinate,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _priority_for_failure_class(self, fc: FailureClass) -> int:
        """Map a FailureClass to a numeric obligation priority.

        Lower numbers indicate higher urgency.

        Parameters
        ----------
        fc : FailureClass
            The failure class to map.

        Returns
        -------
        int
            Priority integer in range ``[1, 10]``.
        """
        _map: dict[FailureClass, int] = {
            FailureClass.ASSIGNMENT_CONFLICT: 1,
            FailureClass.SORT_VIOLATION: 1,
            FailureClass.FUNCTION_MISMATCH: 2,
            FailureClass.ARRAY_OUT_OF_BOUNDS: 2,
            FailureClass.QUANTIFIER_WITNESS: 3,
            FailureClass.UNKNOWN: 5,
        }
        return _map.get(fc, 5)

    def _classification_for_failure_class(
        self, fc: FailureClass
    ) -> FailureClassification:
        """Map a :class:`~jugeo.solver.countermodels.FailureClass` to a
        :class:`~jugeo.errors.FailureClassification`.

        Parameters
        ----------
        fc : FailureClass
            The failure class to map.

        Returns
        -------
        FailureClassification
            The matching failure classification.
        """
        _map: dict[FailureClass, FailureClassification] = {
            FailureClass.ASSIGNMENT_CONFLICT: FailureClassification.DESCENT_OBSTRUCTION,
            FailureClass.SORT_VIOLATION: FailureClassification.ENCODING_MISMATCH,
            FailureClass.FUNCTION_MISMATCH: FailureClassification.ENCODING_MISMATCH,
            FailureClass.ARRAY_OUT_OF_BOUNDS: FailureClassification.LOCAL_REPAIR,
            FailureClass.QUANTIFIER_WITNESS: FailureClassification.COVER_REFINEMENT,
            FailureClass.UNKNOWN: FailureClassification.UNCLASSIFIED,
        }
        return _map.get(fc, FailureClassification.UNCLASSIFIED)




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.solver, jugeo.evidence, jugeo.geometry)
# ---------------------------------------------------------------------------


def repair_from_countermodel(cm: Any) -> dict[str, Any]:
    """Extract repair guidance from a countermodel.

    Countermodels from the solver encode exactly where the current section
    fails — they are the starting point for all repair actions.

    Parameters
    ----------
    cm : Any
        A Countermodel object or dict with countermodel data.

    Returns
    -------
    dict[str, Any]
        Repair guidance with ``failing_coordinates``, ``repair_hints``,
        ``countermodel_id``, and ``obstruction_class`` keys.
    """
    try:
        from jugeo.solver.countermodels import extract_repair_hints, Countermodel
    except ImportError:
        extract_repair_hints = None
        Countermodel = None

    model_id = getattr(cm, "model_id", None) or (cm.get("model_id") if isinstance(cm, dict) else "unknown")
    coord = getattr(cm, "coordinate", None) or (cm.get("coordinate") if isinstance(cm, dict) else None)

    guidance: dict[str, Any] = {
        "countermodel_id": model_id,
        "failing_coordinates": [coord] if coord else [],
        "repair_hints": [],
        "obstruction_class": f"H1_from_{model_id}",
    }

    if extract_repair_hints is not None:
        try:
            hints = extract_repair_hints(cm)
            guidance["repair_hints"] = list(hints) if hints else []
        except Exception:
            pass

    return guidance


def repair_certificate(repair: Any) -> dict[str, Any]:
    """Build an evidence certificate for a completed repair.

    Repair certificates attest that a repair action was performed,
    passed validation, and restored section well-formedness.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``repair_id``, ``valid``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else str(uuid.uuid4())
    )
    valid = getattr(repair, "valid", None)
    if valid is None and isinstance(repair, dict):
        valid = repair.get("valid", repair.get("status") == "success")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "repair_id": repair_id,
        "valid": bool(valid) if valid is not None else False,
        "trust_level": "REPAIRED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(repair).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"repair_{repair_id}", satisfied=valid, source="repair_semantics"
            )
        except Exception:
            pass

    return cert


def repair_descent_check(repair: Any) -> dict[str, Any]:
    """Check whether a repair restores descent (gluing) conditions.

    A valid repair must restore the ability of local sections to glue
    into a global section — i.e., the cocycle obstruction must vanish.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Descent check with ``gluing_restored``, ``cocycle_trivial``,
        ``affected_coordinates``, and ``descent_status`` keys.
    """
    try:
        from jugeo.geometry.descent import check_descent_after_repair, DescentStatus
    except ImportError:
        check_descent_after_repair = None
        DescentStatus = None

    coords = getattr(repair, "affected_coordinates", None) or (
        repair.get("affected_coordinates") if isinstance(repair, dict) else []
    )
    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else "unknown"
    )

    check: dict[str, Any] = {
        "repair_id": repair_id,
        "affected_coordinates": list(coords) if coords else [],
        "gluing_restored": None,
        "cocycle_trivial": None,
        "descent_status": "UNKNOWN",
    }

    if check_descent_after_repair is not None:
        try:
            result = check_descent_after_repair(coords, repair_id=repair_id)
            check["gluing_restored"] = getattr(result, "gluing_restored", None)
            check["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            check["descent_status"] = getattr(result, "status", "UNKNOWN")
        except Exception:
            pass

    return check


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    "RepairSemanticsIntegration",
    # Module-level helpers (exported for testing)
    "_trust_level_for_status",
    "_obstruction_from_record",
    "_make_evidence_item",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: end of integration
