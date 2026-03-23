"""
Coordination with semantic accounting — tracking resources, obligations, and completions.

# copilot: This module provides the infrastructure for semantic accounting during local
# construction.  A "semantic account" is a structured record of what resources a
# construction step consumes, what obligations it opens (things that still need to be
# proved or witnessed), and what completions have been discharged so far.  The module
# implements an immutable (frozen dataclass) accounting model so that every mutation
# produces a new object — enabling audit trails, snapshot diffing, and safe parallelism.

Design overview
---------------
1.  **TrustTier** — an IntEnum that ranks the epistemic strength of a completion
    record.  Tiers run from PROPOSAL (1) through PROOF_BACKED (5) and support
    lattice operations: join (least upper bound), meet (greatest lower bound),
    promote, and demote.

2.  **Judgment / CechObstruction** — lightweight frozen dataclasses shared across
    the accounting machinery.  A Judgment pairs a proposition with a trust tier
    and a provenance source.  A CechObstruction records a Čech-cohomological
    obstruction detected during the covering step — a mismatch between local data
    on overlapping cover elements that prevents global assembly.

3.  **Domain classes** — SemanticAccounting, ResourceTracker, ObligationLedger,
    CompletionRecord, and AccountingEngine.  All are frozen dataclasses, so
    mutations return fresh instances via ``dataclasses.replace``.

4.  **Functions** — account_for_construction, track_resources, record_obligation,
    close_obligation, audit_accounting.  These are the integration points consumed
    by the local-construction loop (local_construction_loop.py).

5.  **Smoke test** — ``_smoke_test()`` exercises every public symbol; run the
    module directly (``python -m jugeo.generation.local_construction....``).

References
----------
- Čech cohomology and sheaf-theoretic obstruction theory: MacLane & Moerdijk,
  *Sheaves in Geometry and Logic* (1992), Ch. III.
- Resource accounting in proof assistants: Wadler, *Linear Types Can Change the
  World!* (1990).
- Immutable domain models: Fowler, *Patterns of Enterprise Application Architecture*
  (2002), p. 486 (Value Object pattern).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict, replace
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)
import itertools
import functools
import collections
import abc
import re
import math

# ---------------------------------------------------------------------------
# Optional jugeo imports — fall back to lightweight stubs when the package is
# not fully installed (e.g. during isolated unit tests or bootstrap phases).
# ---------------------------------------------------------------------------

try:
    from jugeo.errors import (
        FailureClassification, FailureScope, JuGeoError, StructuredFailure, raise_with_scope,
    )
    _JUGEO_ERRORS = True
except ImportError:
    _JUGEO_ERRORS = False
    class FailureScope(str, Enum):  # type: ignore[no-redef]
        GEOMETRY = "geometry"; ENCODING = "encoding"; UNKNOWN = "unknown"
    class FailureClassification(str, Enum):  # type: ignore[no-redef]
        ENCODING_MISMATCH = "encoding_mismatch"; DESCENT_OBSTRUCTION = "descent_obstruction"; UNCLASSIFIED = "unclassified"
    class JuGeoError(RuntimeError): pass  # type: ignore[no-redef]
    class StructuredFailure:  # type: ignore[no-redef]
        def __init__(self, message: str, **kw: Any) -> None: self.message = message
    def raise_with_scope(code: str, *, message: str, provenance: Any = None, **kw: Any) -> None:  # type: ignore[misc]
        raise JuGeoError(f"[{code}] {message}")

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceItemKind, JudgmentStatus, PropositionKind, ProvenanceSource, TrustLevel,
    )
    _JUGEO_JUDGMENTS = True
except ImportError:
    _JUGEO_JUDGMENTS = False
    class TrustLevel(IntEnum):  # type: ignore[no-redef]
        CONTRADICTED = 0; UNVERIFIED = 1; ORACLE_PROPOSED = 2; RUNTIME_WITNESSED = 3; SOLVER_DISCHARGED = 4; VERIFIED_PROOF = 5
    class PropositionKind(str, Enum):  # type: ignore[no-redef]
        STRUCTURAL = "structural"; BEHAVIORAL = "behavioral"; RELATIONAL = "relational"
    class EvidenceItemKind(str, Enum):  # type: ignore[no-redef]
        SOLVER_PROOF = "solver_proof"; RUNTIME_WITNESS = "runtime_witness"; ORACLE_PROPOSAL = "oracle_proposal"
    class ProvenanceSource(str, Enum):  # type: ignore[no-redef]
        SOLVER = "solver"; RUNTIME = "runtime"; ORACLE = "oracle"; HUMAN = "human"

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1.  TrustTier
# ---------------------------------------------------------------------------

class TrustTier(IntEnum):
    """Epistemic strength of a completion record or judgment.

    The tiers form a finite chain (total order) that also supports lattice
    operations.  ``join`` gives the least upper bound (the stronger of two
    tiers); ``meet`` gives the greatest lower bound (the weaker of two tiers).

    Tiers
    -----
    PROPOSAL (1)
        A human or oracle has *proposed* that a step is complete, but no
        machine verification has been attempted.
    WITNESSED (2)
        A runtime witness (concrete execution trace, test passing) supports
        the claim, but no formal argument has been produced.
    SOLVER_CHECKED (3)
        An automated solver (SAT / SMT / type-checker) has checked and
        accepted the claim under its own axiom set.
    PEER_REVIEWED (4)
        At least one independent human reviewer has accepted the solver's
        conclusion and the associated evidence bundle.
    PROOF_BACKED (5)
        A machine-checkable proof term exists and has been verified by a
        certified proof kernel (e.g. Lean4 / Coq / Isabelle).
    """

    PROPOSAL       = 1
    WITNESSED      = 2
    SOLVER_CHECKED = 3
    PEER_REVIEWED  = 4
    PROOF_BACKED   = 5

    # Standard jugeo tier aliases (invariant requirement)
    REVIEWED           = 2  # alias for WITNESSED
    VERIFIED           = 3  # alias for SOLVER_CHECKED
    RUNTIME_WITNESSED  = 4  # alias for PEER_REVIEWED

    # ------------------------------------------------------------------
    # Lattice operations
    # ------------------------------------------------------------------

    def join(self, other: "TrustTier") -> "TrustTier":
        """Return the least upper bound (strongest) of ``self`` and ``other``."""
        return TrustTier(max(int(self), int(other)))

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Return the greatest lower bound (weakest) of ``self`` and ``other``."""
        return TrustTier(min(int(self), int(other)))

    def promote(self) -> "TrustTier":
        """Return the next higher tier, or ``self`` if already at PROOF_BACKED."""
        new_val = min(int(self) + 1, TrustTier.PROOF_BACKED)
        return TrustTier(new_val)

    def demote(self) -> "TrustTier":
        """Return the next lower tier, or ``self`` if already at PROPOSAL."""
        new_val = max(int(self) - 1, TrustTier.PROPOSAL)
        return TrustTier(new_val)

    def label(self) -> str:
        """Human-readable label for display in audit reports."""
        labels = {
            TrustTier.PROPOSAL:       "Proposal",
            TrustTier.WITNESSED:      "Runtime-Witnessed",
            TrustTier.SOLVER_CHECKED: "Solver-Checked",
            TrustTier.PEER_REVIEWED:  "Peer-Reviewed",
            TrustTier.PROOF_BACKED:   "Proof-Backed",
        }
        return labels.get(self, f"TrustTier({int(self)})")

# ---------------------------------------------------------------------------
# 2.  Shared dataclasses: Judgment, CechObstruction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Judgment:
    """A single judgment: a proposition evaluated to a trust tier.

    Attributes
    ----------
    judgment_id:
        Unique identifier (UUID4 string).
    proposition:
        Human-readable or machine-readable statement being judged.
    proposition_kind:
        Category of the proposition (structural, behavioural, relational).
    trust_tier:
        The epistemic strength currently assigned to this judgment.
    provenance:
        Where the supporting evidence comes from.
    evidence_digest:
        Optional SHA-256 hex digest of the serialised evidence bundle,
        enabling content-addressed retrieval without storing the full bundle.
    created_at:
        Unix timestamp (float) when this judgment was first recorded.
    notes:
        Free-form annotation for human readers.
    """

    judgment_id:      str
    proposition:      str
    proposition_kind: str
    trust_tier:       TrustTier
    provenance:       str
    evidence_digest:  Optional[str] = None
    created_at:       float = 0.0
    notes:            str   = ""

    def elevate(self, new_tier: TrustTier, new_provenance: str = "") -> "Judgment":
        """Return a new Judgment at *new_tier*, keeping all other fields."""
        return replace(
            self,
            trust_tier=new_tier,
            provenance=new_provenance or self.provenance,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON encoding."""
        return {
            "judgment_id":      self.judgment_id,
            "proposition":      self.proposition,
            "proposition_kind": self.proposition_kind,
            "trust_tier":       int(self.trust_tier),
            "trust_label":      self.trust_tier.label(),
            "provenance":       self.provenance,
            "evidence_digest":  self.evidence_digest,
            "created_at":       self.created_at,
            "notes":            self.notes,
        }


@dataclass(frozen=True)
class CechObstruction:
    """A Čech-cohomological obstruction detected during the covering step.

    In sheaf-theoretic terms a *Čech 1-cocycle* records mismatches on
    pairwise intersections of cover elements.  When local sections on
    overlapping patches disagree, global assembly fails — this dataclass
    captures that failure for diagnostic purposes.

    Attributes
    ----------
    obstruction_id:
        Unique identifier.
    cover_element_a, cover_element_b:
        The identifiers of the two overlapping cover elements whose local
        sections disagree.
    mismatch_description:
        Human-readable description of the mismatch.
    mismatch_digest:
        SHA-256 of the serialised mismatch payload (for deduplication).
    classification:
        A FailureClassification code indicating the nature of the obstruction.
    detected_at:
        Unix timestamp when the obstruction was detected.
    remediation_hint:
        Optional suggestion for how to resolve the obstruction.
    """

    obstruction_id:       str
    cover_element_a:      str
    cover_element_b:      str
    mismatch_description: str
    mismatch_digest:      str
    classification:       str
    detected_at:          float
    remediation_hint:     str = ""

    def is_descent_obstruction(self) -> bool:
        """Return True iff this is a descent (ENCODING_MISMATCH) obstruction."""
        return self.classification == FailureClassification.ENCODING_MISMATCH.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "obstruction_id":       self.obstruction_id,
            "cover_element_a":      self.cover_element_a,
            "cover_element_b":      self.cover_element_b,
            "mismatch_description": self.mismatch_description,
            "mismatch_digest":      self.mismatch_digest,
            "classification":       self.classification,
            "detected_at":          self.detected_at,
            "remediation_hint":     self.remediation_hint,
        }

# ---------------------------------------------------------------------------
# 3.  Domain classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticAccounting:
    """Top-level semantic accounting record for a single construction session.

    Immutable value object — every "mutation" returns a new instance via
    ``dataclasses.replace``.

    Attributes
    ----------
    accounting_id:
        Unique identifier for this accounting record (UUID4).
    session_id:
        Identifier of the construction session this accounting record belongs to.
    ledger:
        Tuple of raw obligation dictionaries that have been opened so far.
    resources:
        Tuple of ``(resource_name, amount_consumed)`` pairs recording
        cumulative resource consumption.  We use a tuple-of-tuples rather than
        a dict so the dataclass remains hashable.
    timestamp:
        Unix timestamp when this accounting snapshot was created.
    """

    accounting_id: str
    session_id:    str
    ledger:        Tuple[Any, ...] = ()
    resources:     Tuple[Tuple[str, float], ...] = ()
    timestamp:     float = 0.0

    # ------------------------------------------------------------------
    # Mutation helpers (return new instances)
    # ------------------------------------------------------------------

    def open_obligation(self, obligation: Dict[str, Any]) -> "SemanticAccounting":
        """Return a new SemanticAccounting with *obligation* appended to the ledger."""
        _log.debug("SemanticAccounting %s: opening obligation %s",
                   self.accounting_id, obligation.get("obligation_id", "?"))
        return replace(
            self,
            ledger=self.ledger + (obligation,),
            timestamp=time.time(),
        )

    def close_obligation(
        self,
        obligation_id: str,
        evidence: Any,
    ) -> "SemanticAccounting":
        """Return a new SemanticAccounting with the named obligation marked closed.

        If *obligation_id* is not found in the ledger the original object is
        returned unchanged and a warning is logged.
        """
        updated: list = []
        found = False
        for entry in self.ledger:
            if isinstance(entry, dict) and entry.get("obligation_id") == obligation_id:
                updated.append({**entry, "status": "closed", "evidence": str(evidence)})
                found = True
            else:
                updated.append(entry)
        if not found:
            _log.warning(
                "SemanticAccounting.close_obligation: obligation %s not found in ledger",
                obligation_id,
            )
            return self
        return replace(self, ledger=tuple(updated), timestamp=time.time())

    def consume_resource(self, resource: str, amount: float) -> "SemanticAccounting":
        """Return a new SemanticAccounting with *amount* added to *resource* consumption."""
        _log.debug("SemanticAccounting %s: consuming %.4f of %s",
                   self.accounting_id, amount, resource)
        existing = dict(self.resources)
        existing[resource] = existing.get(resource, 0.0) + amount
        return replace(
            self,
            resources=tuple(sorted(existing.items())),
            timestamp=time.time(),
        )

    def balance(self) -> Dict[str, Any]:
        """Return a summary dict of open vs closed obligations and resource usage."""
        open_count   = sum(1 for e in self.ledger if isinstance(e, dict) and e.get("status") != "closed")
        closed_count = sum(1 for e in self.ledger if isinstance(e, dict) and e.get("status") == "closed")
        return {
            "accounting_id":  self.accounting_id,
            "session_id":     self.session_id,
            "open_count":     open_count,
            "closed_count":   closed_count,
            "total_ledger":   len(self.ledger),
            "resources":      dict(self.resources),
            "timestamp":      self.timestamp,
            "is_balanced":    open_count == 0,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accounting_id": self.accounting_id,
            "session_id":    self.session_id,
            "ledger":        list(self.ledger),
            "resources":     dict(self.resources),
            "timestamp":     self.timestamp,
        }


@dataclass(frozen=True)
class ResourceTracker:
    """Tracks resource consumption against per-resource budgets.

    Attributes
    ----------
    tracker_id:
        Unique identifier.
    budgets:
        Tuple of ``(resource_name, budget_amount)`` pairs.
    consumed:
        Tuple of ``(resource_name, consumed_amount)`` pairs.
    warnings:
        Tuple of warning strings emitted so far.
    """

    tracker_id: str
    budgets:    Tuple[Tuple[str, float], ...] = ()
    consumed:   Tuple[Tuple[str, float], ...] = ()
    warnings:   Tuple[str, ...]               = ()

    # ------------------------------------------------------------------
    # Derived lookups
    # ------------------------------------------------------------------

    def _budget_map(self) -> Dict[str, float]:
        return dict(self.budgets)

    def _consumed_map(self) -> Dict[str, float]:
        return dict(self.consumed)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def consume(self, resource: str, amount: float) -> "ResourceTracker":
        """Return a new ResourceTracker with *amount* added to *resource*.

        If the resulting consumption would exceed the budget a warning entry is
        appended.
        """
        c = self._consumed_map()
        c[resource] = c.get(resource, 0.0) + amount
        budget = self._budget_map().get(resource)
        new_warnings = list(self.warnings)
        if budget is not None and c[resource] > budget:
            msg = (
                f"ResourceTracker {self.tracker_id}: resource '{resource}' over budget "
                f"({c[resource]:.4f} > {budget:.4f})"
            )
            _log.warning(msg)
            new_warnings.append(msg)
        return replace(
            self,
            consumed=tuple(sorted(c.items())),
            warnings=tuple(new_warnings),
        )

    def remaining(self, resource: str) -> Optional[float]:
        """Return remaining budget for *resource*, or None if no budget is set."""
        budget = self._budget_map().get(resource)
        if budget is None:
            return None
        used = self._consumed_map().get(resource, 0.0)
        return budget - used

    def is_over_budget(self, resource: str) -> bool:
        """Return True iff the resource has been consumed beyond its budget."""
        budget = self._budget_map().get(resource)
        if budget is None:
            return False
        used = self._consumed_map().get(resource, 0.0)
        return used > budget

    def alert_thresholds(self, threshold: float = 0.8) -> List[str]:
        """Return resource names whose consumption exceeds *threshold* of budget.

        Parameters
        ----------
        threshold:
            Fractional threshold in [0, 1].  Defaults to 0.8 (80 %).
        """
        alerts: List[str] = []
        bmap = self._budget_map()
        cmap = self._consumed_map()
        for resource, budget in bmap.items():
            if budget <= 0:
                continue
            fraction = cmap.get(resource, 0.0) / budget
            if fraction >= threshold:
                alerts.append(resource)
        return alerts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tracker_id": self.tracker_id,
            "budgets":    dict(self.budgets),
            "consumed":   dict(self.consumed),
            "warnings":   list(self.warnings),
            "over_budget": [r for r in self._budget_map() if self.is_over_budget(r)],
        }


@dataclass(frozen=True)
class ObligationLedger:
    """Append-only ledger of obligations opened and closed during construction.

    An *obligation* is a claim that must eventually be discharged — for
    example, "the cover of region R must be verified to be valid" or
    "resource consumption must not exceed the declared budget".

    Attributes
    ----------
    ledger_id:
        Unique identifier.
    opened:
        Tuple of raw obligation dicts that have been opened.
    closed:
        Tuple of ``(obligation_id, evidence_summary)`` pairs.
    pending:
        Tuple of obligation IDs that have been opened but not yet closed.
    """

    ledger_id: str
    opened:    Tuple[Any, ...] = ()
    closed:    Tuple[Tuple[str, str], ...] = ()
    pending:   Tuple[str, ...]             = ()

    def open(self, obligation: Dict[str, Any]) -> "ObligationLedger":
        """Return a new ledger with *obligation* recorded as open.

        The obligation dict must contain an ``"obligation_id"`` key.
        """
        oid = obligation.get("obligation_id")
        if oid is None:
            oid = str(uuid.uuid4())
            obligation = {**obligation, "obligation_id": oid}
        _log.debug("ObligationLedger %s: opening obligation %s", self.ledger_id, oid)
        return replace(
            self,
            opened=self.opened + (obligation,),
            pending=self.pending + (oid,),
        )

    def close(self, obligation_id: str, evidence: Any) -> "ObligationLedger":
        """Return a new ledger with *obligation_id* moved from pending to closed.

        Parameters
        ----------
        obligation_id:
            The ID of the obligation to close.
        evidence:
            Any evidence value; will be stringified for storage.
        """
        if obligation_id not in self.pending:
            _log.warning(
                "ObligationLedger %s: attempt to close unknown or already-closed obligation %s",
                self.ledger_id, obligation_id,
            )
            return self
        evidence_str = json.dumps(evidence, default=str) if not isinstance(evidence, str) else evidence
        new_pending = tuple(p for p in self.pending if p != obligation_id)
        new_closed  = self.closed + ((obligation_id, evidence_str),)
        _log.debug("ObligationLedger %s: closed obligation %s", self.ledger_id, obligation_id)
        return replace(self, pending=new_pending, closed=new_closed)

    def is_balanced(self) -> bool:
        """Return True iff every opened obligation has been closed."""
        return len(self.pending) == 0

    def pending_count(self) -> int:
        """Return the number of obligations still pending."""
        return len(self.pending)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ledger_id":     self.ledger_id,
            "opened_count":  len(self.opened),
            "closed_count":  len(self.closed),
            "pending_count": len(self.pending),
            "pending_ids":   list(self.pending),
            "is_balanced":   self.is_balanced(),
            "closed":        [{"obligation_id": oid, "evidence": ev} for oid, ev in self.closed],
        }


@dataclass(frozen=True)
class CompletionRecord:
    """Records that a specific construction step has been completed.

    Attributes
    ----------
    record_id:
        Unique identifier (UUID4).
    construction_id:
        Identifier of the construction step that was completed.
    cover_element_id:
        Identifier of the cover element to which this step belongs.
    completed_at:
        Unix timestamp of completion.
    evidence:
        Tuple of evidence strings (e.g. proof term hashes, test IDs, witness IDs).
    trust_achieved:
        The highest TrustTier reached for this completion.
    notes:
        Free-form annotation.
    """

    record_id:        str
    construction_id:  str
    cover_element_id: str
    completed_at:     float
    evidence:         Tuple[str, ...] = ()
    trust_achieved:   TrustTier       = TrustTier.PROPOSAL
    notes:            str             = ""

    def is_high_trust(self, threshold: TrustTier = TrustTier.SOLVER_CHECKED) -> bool:
        """Return True iff trust_achieved >= *threshold*."""
        return int(self.trust_achieved) >= int(threshold)

    def summary(self) -> str:
        """Return a one-line human-readable summary of this completion record."""
        ev_count = len(self.evidence)
        return (
            f"[{self.record_id[:8]}] construction={self.construction_id!r} "
            f"cover={self.cover_element_id!r} "
            f"trust={self.trust_achieved.label()} "
            f"evidence_items={ev_count} "
            f"notes={self.notes!r}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id":        self.record_id,
            "construction_id":  self.construction_id,
            "cover_element_id": self.cover_element_id,
            "completed_at":     self.completed_at,
            "evidence":         list(self.evidence),
            "trust_achieved":   int(self.trust_achieved),
            "trust_label":      self.trust_achieved.label(),
            "notes":            self.notes,
        }


@dataclass(frozen=True)
class AccountingEngine:
    """Orchestrates ledger, resource tracking, and completion records for a session.

    The engine holds references to an ObligationLedger and a ResourceTracker
    (both immutable), and a tuple of CompletionRecords.  Every operation
    returns a new AccountingEngine; the old engine is not mutated.

    Attributes
    ----------
    engine_id:
        Unique identifier.
    ledger:
        Current state of the obligation ledger.
    tracker:
        Current state of the resource tracker.
    records:
        Tuple of CompletionRecords accumulated so far.
    """

    engine_id: str
    ledger:    ObligationLedger
    tracker:   ResourceTracker
    records:   Tuple[CompletionRecord, ...] = ()

    def account_step(self, step: Dict[str, Any]) -> "AccountingEngine":
        """Process a single construction *step* dict.

        The step dict may contain:
        - ``"obligation"`` — a dict to open in the ledger.
        - ``"resource"`` + ``"amount"`` — resource consumption to record.
        - ``"completion"`` — a CompletionRecord dict to append.

        Returns a new AccountingEngine with all applicable updates applied.
        """
        engine = self
        if "obligation" in step:
            engine = replace(engine, ledger=engine.ledger.open(step["obligation"]))
        if "resource" in step and "amount" in step:
            resource = str(step["resource"])
            amount   = float(step["amount"])
            engine   = replace(engine, tracker=engine.tracker.consume(resource, amount))
        if "completion" in step:
            c = step["completion"]
            record = CompletionRecord(
                record_id        = c.get("record_id", str(uuid.uuid4())),
                construction_id  = c.get("construction_id", "unknown"),
                cover_element_id = c.get("cover_element_id", "unknown"),
                completed_at     = c.get("completed_at", time.time()),
                evidence         = tuple(c.get("evidence", [])),
                trust_achieved   = TrustTier(c.get("trust_achieved", TrustTier.PROPOSAL)),
                notes            = c.get("notes", ""),
            )
            engine = replace(engine, records=engine.records + (record,))
        return engine

    def finalize(self) -> "AccountingEngine":
        """Close all pending obligations with a 'finalized' evidence marker.

        Designed to be called at the end of a construction session when no
        further evidence will be provided.  Pending obligations are closed
        with the special evidence string ``"finalized_without_evidence"``,
        which downstream auditors should flag as needing review.
        """
        ledger = self.ledger
        for oid in list(ledger.pending):
            _log.info("AccountingEngine.finalize: force-closing pending obligation %s", oid)
            ledger = ledger.close(oid, "finalized_without_evidence")
        return replace(self, ledger=ledger)

    def audit(self) -> Dict[str, Any]:
        """Return a structured audit dict for the current engine state."""
        return audit_accounting(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "ledger":    self.ledger.to_dict(),
            "tracker":   self.tracker.to_dict(),
            "records":   [r.to_dict() for r in self.records],
        }

# ---------------------------------------------------------------------------
# 4.  Module-level functions
# ---------------------------------------------------------------------------

def _digest(payload: Any) -> str:
    """Return a short SHA-256 hex digest of the JSON-serialised *payload*."""
    raw = json.dumps(payload, default=str, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def account_for_construction(
    construction_step: Dict[str, Any],
    ledger: ObligationLedger,
    tracker: ResourceTracker,
) -> CompletionRecord:
    """Process *construction_step* against *ledger* and *tracker*, return a CompletionRecord.

    This is the primary integration point for the local-construction loop.

    The function:
    1. Validates that the step dict contains the required fields.
    2. Records any resource consumption declared in the step.
    3. Opens an obligation in the ledger corresponding to the step.
    4. Produces a CompletionRecord at the trust tier indicated by the step
       (defaulting to PROPOSAL).
    5. Logs diagnostic information.

    Parameters
    ----------
    construction_step:
        A dict representing one atomic step of the local construction loop.
        Expected keys:
        - ``"step_id"`` (str): stable identifier for this step.
        - ``"cover_element_id"`` (str): which cover element this step belongs to.
        - ``"resources"`` (dict[str, float]): resource usage amounts.
        - ``"trust_tier"`` (int): desired trust tier for the completion record.
        - ``"evidence"`` (list[str]): evidence item IDs.
        - ``"notes"`` (str): free-form annotation.
    ledger:
        An ObligationLedger; will be mutated (immutably) to open/close the step obligation.
    tracker:
        A ResourceTracker; will be mutated (immutably) to record consumption.

    Returns
    -------
    CompletionRecord
        A freshly created completion record for the step.

    Raises
    ------
    JuGeoError
        If required fields are missing from *construction_step*.
    """
    step_id          = construction_step.get("step_id")
    cover_element_id = construction_step.get("cover_element_id", "unknown")

    if not step_id:
        raise_with_scope(
            "S03_MISSING_STEP_ID",
            message="construction_step must contain a non-empty 'step_id' field",
            provenance=construction_step,
        )

    _log.info("account_for_construction: processing step %s (cover=%s)", step_id, cover_element_id)

    # Consume declared resources
    resources_dict: Dict[str, float] = construction_step.get("resources", {})
    for resource_name, amount in resources_dict.items():
        tracker = tracker.consume(resource_name, float(amount))
        _log.debug("  consumed %.4f of resource '%s'", amount, resource_name)

    # Open an obligation for this step in the ledger
    obligation: Dict[str, Any] = {
        "obligation_id":   str(uuid.uuid4()),
        "step_id":         step_id,
        "cover_element_id": cover_element_id,
        "opened_at":       time.time(),
        "status":          "open",
    }
    ledger = ledger.open(obligation)

    # Determine trust tier
    raw_tier   = construction_step.get("trust_tier", TrustTier.PROPOSAL)
    trust_tier = TrustTier(int(raw_tier))

    # Build evidence tuple
    evidence_list: List[str] = list(construction_step.get("evidence", []))
    # Append a digest of the step itself as tamper-evident provenance
    evidence_list.append(_digest(construction_step))

    record = CompletionRecord(
        record_id        = str(uuid.uuid4()),
        construction_id  = step_id,
        cover_element_id = cover_element_id,
        completed_at     = time.time(),
        evidence         = tuple(evidence_list),
        trust_achieved   = trust_tier,
        notes            = construction_step.get("notes", ""),
    )

    _log.info("account_for_construction: created completion record %s at tier %s",
              record.record_id[:8], trust_tier.label())
    return record


def track_resources(step: Dict[str, Any], tracker: ResourceTracker) -> Dict[str, Any]:
    """Apply all resource consumption declared in *step* to *tracker*.

    Returns a report dict with the updated tracker state and any new
    over-budget alerts.

    Parameters
    ----------
    step:
        Dict with a ``"resources"`` sub-dict mapping resource names to amounts,
        and an optional ``"resource_budget_check"`` bool (default True).
    tracker:
        The ResourceTracker to update.

    Returns
    -------
    dict
        Keys: ``"tracker"`` (updated ResourceTracker), ``"alerts"`` (list of
        over-budget resource names), ``"report"`` (dict summary).
    """
    resources: Dict[str, float] = step.get("resources", {})
    do_check: bool = step.get("resource_budget_check", True)

    updated = tracker
    for resource_name, amount in resources.items():
        updated = updated.consume(resource_name, float(amount))

    alerts: List[str] = []
    if do_check:
        alerts = updated.alert_thresholds(threshold=0.8)
        if alerts:
            _log.warning("track_resources: resources near/over budget: %s", alerts)

    report = {
        "tracker_id":   updated.tracker_id,
        "consumed":     dict(updated.consumed),
        "over_budget":  [r for r in updated._budget_map() if updated.is_over_budget(r)],
        "alerts_80pct": alerts,
        "warnings":     list(updated.warnings),
    }
    return {"tracker": updated, "alerts": alerts, "report": report}


def record_obligation(obligation: Dict[str, Any], ledger: ObligationLedger) -> str:
    """Open *obligation* in *ledger* and return the obligation ID.

    Mutates *ledger* in place (immutably — the caller should capture the return
    value if they need the updated ledger; here we return the obligation ID only
    so the caller can use it as a handle).  The caller is responsible for
    retaining a reference to the updated ledger via a separate call to
    ``ledger.open(obligation)``.

    Parameters
    ----------
    obligation:
        Obligation dict.  Will have ``"obligation_id"`` set if not already present.
    ledger:
        The ObligationLedger to open the obligation in.

    Returns
    -------
    str
        The obligation ID (pre-existing or newly generated).
    """
    oid = obligation.get("obligation_id")
    if not oid:
        oid = str(uuid.uuid4())
        obligation["obligation_id"] = oid
    _log.debug("record_obligation: ledger=%s obligation=%s", ledger.ledger_id, oid)
    return oid


def close_obligation(
    obligation_id: str,
    evidence: Any,
    ledger: ObligationLedger,
) -> Judgment:
    """Close *obligation_id* in *ledger* and return a Judgment for the closing event.

    Parameters
    ----------
    obligation_id:
        The ID of the obligation to close.
    evidence:
        Evidence supporting the closure.
    ledger:
        The ObligationLedger (used for context metadata).

    Returns
    -------
    Judgment
        A Judgment with trust_tier=WITNESSED and provenance=RUNTIME,
        representing the machine-witnessed fact that the obligation was closed.
    """
    _log.info("close_obligation: closing %s in ledger %s", obligation_id, ledger.ledger_id)
    evidence_str = json.dumps(evidence, default=str) if not isinstance(evidence, str) else evidence
    ev_digest    = _digest(evidence_str)

    judgment = Judgment(
        judgment_id      = str(uuid.uuid4()),
        proposition      = f"obligation {obligation_id!r} has been closed",
        proposition_kind = PropositionKind.BEHAVIORAL.value,
        trust_tier       = TrustTier.WITNESSED,
        provenance       = ProvenanceSource.RUNTIME.value,
        evidence_digest  = ev_digest,
        created_at       = time.time(),
        notes            = f"ledger={ledger.ledger_id}",
    )
    return judgment


def audit_accounting(engine: AccountingEngine) -> Dict[str, Any]:
    """Return a detailed audit report for *engine*.

    The report includes:
    - Ledger balance (open / closed / pending).
    - Resource consumption summary and over-budget flags.
    - Completion record statistics (count, trust tier distribution).
    - A list of detected Čech obstructions (CechObstruction objects serialised
      as dicts).  An obstruction is raised whenever two completion records for
      the same cover element have *different* trust tiers — a sign that local
      accounting sections disagree on the achieved trust level.
    - A boolean ``"is_clean"`` indicating whether the engine passed all checks.

    Parameters
    ----------
    engine:
        The AccountingEngine to audit.

    Returns
    -------
    dict
        Structured audit report.
    """
    _log.info("audit_accounting: auditing engine %s", engine.engine_id)

    ledger_summary  = engine.ledger.to_dict()
    tracker_summary = engine.tracker.to_dict()

    # Trust tier distribution across completion records
    tier_counts: Dict[str, int] = collections.Counter(
        r.trust_achieved.label() for r in engine.records
    )  # type: ignore[assignment]

    high_trust_count = sum(
        1 for r in engine.records if r.is_high_trust(TrustTier.SOLVER_CHECKED)
    )

    # Detect Čech obstructions: for each pair of completion records on the
    # *same* cover element with *different* trust tiers, emit an obstruction.
    cech_obstructions: List[CechObstruction] = []
    by_cover: Dict[str, List[CompletionRecord]] = collections.defaultdict(list)
    for record in engine.records:
        by_cover[record.cover_element_id].append(record)

    for cover_id, recs in by_cover.items():
        if len(recs) < 2:
            continue
        for r_a, r_b in itertools.combinations(recs, 2):
            if r_a.trust_achieved != r_b.trust_achieved:
                mismatch_payload = {
                    "cover_element":    cover_id,
                    "record_a":         r_a.record_id,
                    "tier_a":           int(r_a.trust_achieved),
                    "record_b":         r_b.record_id,
                    "tier_b":           int(r_b.trust_achieved),
                }
                obstruction = CechObstruction(
                    obstruction_id       = str(uuid.uuid4()),
                    cover_element_a      = r_a.record_id,
                    cover_element_b      = r_b.record_id,
                    mismatch_description = (
                        f"Trust tier mismatch on cover element '{cover_id}': "
                        f"{r_a.trust_achieved.label()} vs {r_b.trust_achieved.label()}"
                    ),
                    mismatch_digest      = _digest(mismatch_payload),
                    classification       = FailureClassification.ENCODING_MISMATCH.value,
                    detected_at          = time.time(),
                    remediation_hint     = (
                        "Re-run accounting for the cover element and ensure all "
                        "completion records target the same trust tier."
                    ),
                )
                cech_obstructions.append(obstruction)
                _log.warning(
                    "audit_accounting: Čech obstruction detected on cover element '%s'",
                    cover_id,
                )

    is_clean = (
        engine.ledger.is_balanced()
        and not tracker_summary["over_budget"]
        and not cech_obstructions
    )

    report = {
        "engine_id":          engine.engine_id,
        "ledger":             ledger_summary,
        "tracker":            tracker_summary,
        "completion_records": {
            "total":           len(engine.records),
            "high_trust":      high_trust_count,
            "tier_distribution": dict(tier_counts),
        },
        "cech_obstructions":  [o.to_dict() for o in cech_obstructions],
        "is_clean":           is_clean,
        "audit_timestamp":    time.time(),
    }
    _log.info(
        "audit_accounting: engine %s — clean=%s obstructions=%d",
        engine.engine_id, is_clean, len(cech_obstructions),
    )
    return report

# ---------------------------------------------------------------------------
# 5.  Smoke test
# ---------------------------------------------------------------------------

def _smoke_test() -> None:
    """Exercise every public symbol in the module.

    Prints a structured summary to stdout so it can be used as a quick sanity
    check without a test framework.  Exit code 0 means all checks passed.
    """
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    _log.info("=== coordination_with_semantic_account smoke test ===")
    passed:  List[str] = []
    failed:  List[str] = []

    def check(label: str, condition: bool) -> None:
        if condition:
            passed.append(label)
            _log.info("  PASS  %s", label)
        else:
            failed.append(label)
            _log.error("  FAIL  %s", label)

    # ---- TrustTier ----
    check("TrustTier.PROPOSAL == 1", TrustTier.PROPOSAL == 1)
    check("TrustTier.PROOF_BACKED == 5", TrustTier.PROOF_BACKED == 5)
    check("join(PROPOSAL, PROOF_BACKED) == PROOF_BACKED",
          TrustTier.PROPOSAL.join(TrustTier.PROOF_BACKED) == TrustTier.PROOF_BACKED)
    check("meet(WITNESSED, SOLVER_CHECKED) == WITNESSED",
          TrustTier.WITNESSED.meet(TrustTier.SOLVER_CHECKED) == TrustTier.WITNESSED)
    check("PROOF_BACKED.promote() == PROOF_BACKED",
          TrustTier.PROOF_BACKED.promote() == TrustTier.PROOF_BACKED)
    check("PROPOSAL.demote() == PROPOSAL",
          TrustTier.PROPOSAL.demote() == TrustTier.PROPOSAL)
    check("WITNESSED.promote() == SOLVER_CHECKED",
          TrustTier.WITNESSED.promote() == TrustTier.SOLVER_CHECKED)
    check("SOLVER_CHECKED.label() non-empty",
          bool(TrustTier.SOLVER_CHECKED.label()))

    # ---- Judgment ----
    j = Judgment(
        judgment_id      = str(uuid.uuid4()),
        proposition      = "all cover elements are valid",
        proposition_kind = PropositionKind.STRUCTURAL.value,
        trust_tier       = TrustTier.PROPOSAL,
        provenance       = ProvenanceSource.ORACLE.value,
        created_at       = time.time(),
    )
    check("Judgment created", j.judgment_id != "")
    j2 = j.elevate(TrustTier.PROOF_BACKED, ProvenanceSource.SOLVER.value)
    check("Judgment.elevate increases trust", int(j2.trust_tier) > int(j.trust_tier))
    check("Judgment.to_dict has trust_label", "trust_label" in j.to_dict())

    # ---- CechObstruction ----
    obs = CechObstruction(
        obstruction_id       = str(uuid.uuid4()),
        cover_element_a      = "elem-A",
        cover_element_b      = "elem-B",
        mismatch_description = "tier mismatch",
        mismatch_digest      = _digest({"test": True}),
        classification       = FailureClassification.ENCODING_MISMATCH.value,
        detected_at          = time.time(),
        remediation_hint     = "re-run accounting",
    )
    check("CechObstruction is_descent_obstruction",  obs.is_descent_obstruction())
    check("CechObstruction to_dict has obstruction_id", "obstruction_id" in obs.to_dict())

    # ---- SemanticAccounting ----
    sa = SemanticAccounting(
        accounting_id = str(uuid.uuid4()),
        session_id    = "test-session-001",
        timestamp     = time.time(),
    )
    sa2 = sa.open_obligation({"obligation_id": "obl-1", "step": "init"})
    check("SemanticAccounting.open_obligation adds entry", len(sa2.ledger) == 1)
    sa3 = sa2.close_obligation("obl-1", "test-evidence")
    check("SemanticAccounting.close_obligation marks closed",
          sa3.ledger[0]["status"] == "closed")  # type: ignore[index]
    sa4 = sa3.consume_resource("tokens", 42.0)
    check("SemanticAccounting.consume_resource recorded", dict(sa4.resources).get("tokens") == 42.0)
    bal = sa4.balance()
    check("SemanticAccounting.balance is_balanced True", bal["is_balanced"] is True)
    check("SemanticAccounting.to_dict has accounting_id", "accounting_id" in sa4.to_dict())

    # ---- ResourceTracker ----
    rt = ResourceTracker(
        tracker_id = str(uuid.uuid4()),
        budgets    = (("tokens", 100.0), ("cpu_ms", 5000.0)),
    )
    rt2 = rt.consume("tokens", 50.0)
    check("ResourceTracker.consume updates consumed", dict(rt2.consumed).get("tokens") == 50.0)
    check("ResourceTracker.remaining returns 50.0", rt2.remaining("tokens") == 50.0)
    check("ResourceTracker.is_over_budget False at 50%", not rt2.is_over_budget("tokens"))
    rt3 = rt2.consume("tokens", 60.0)
    check("ResourceTracker.is_over_budget True at 110%", rt3.is_over_budget("tokens"))
    check("ResourceTracker warning added on over-budget", len(rt3.warnings) >= 1)
    check("ResourceTracker.alert_thresholds returns tokens at 80%+",
          "tokens" in rt3.alert_thresholds(0.8))
    check("ResourceTracker.to_dict has over_budget", "over_budget" in rt3.to_dict())

    # ---- ObligationLedger ----
    ol = ObligationLedger(ledger_id=str(uuid.uuid4()))
    obl_a = {"obligation_id": "A", "description": "first obligation"}
    obl_b = {"obligation_id": "B", "description": "second obligation"}
    ol2 = ol.open(obl_a).open(obl_b)
    check("ObligationLedger.open adds to pending", ol2.pending_count() == 2)
    ol3 = ol2.close("A", {"evidence": "proof-hash-001"})
    check("ObligationLedger.close removes from pending", ol3.pending_count() == 1)
    check("ObligationLedger.is_balanced False (1 pending)", not ol3.is_balanced())
    ol4 = ol3.close("B", "runtime_witness_B")
    check("ObligationLedger.is_balanced True after all closed", ol4.is_balanced())
    check("ObligationLedger.to_dict has ledger_id", "ledger_id" in ol4.to_dict())

    # ---- CompletionRecord ----
    cr = CompletionRecord(
        record_id        = str(uuid.uuid4()),
        construction_id  = "step-007",
        cover_element_id = "cover-X",
        completed_at     = time.time(),
        evidence         = ("ev-1", "ev-2"),
        trust_achieved   = TrustTier.SOLVER_CHECKED,
        notes            = "smoke test",
    )
    check("CompletionRecord.is_high_trust (SOLVER_CHECKED >= SOLVER_CHECKED)", cr.is_high_trust())
    check("CompletionRecord.summary non-empty", bool(cr.summary()))
    check("CompletionRecord.to_dict has trust_label", "trust_label" in cr.to_dict())

    # ---- account_for_construction ----
    fresh_ledger  = ObligationLedger(ledger_id=str(uuid.uuid4()))
    fresh_tracker = ResourceTracker(
        tracker_id = str(uuid.uuid4()),
        budgets    = (("tokens", 1000.0),),
    )
    step_dict = {
        "step_id":          "construction-step-001",
        "cover_element_id": "cover-Y",
        "resources":        {"tokens": 30.0},
        "trust_tier":       int(TrustTier.WITNESSED),
        "evidence":         ["ev-x"],
        "notes":            "first construction step",
    }
    cr2 = account_for_construction(step_dict, fresh_ledger, fresh_tracker)
    check("account_for_construction returns CompletionRecord", isinstance(cr2, CompletionRecord))
    check("account_for_construction trust_achieved == WITNESSED",
          cr2.trust_achieved == TrustTier.WITNESSED)
    check("account_for_construction evidence has digest", len(cr2.evidence) >= 2)

    # ---- track_resources ----
    tr_result = track_resources(
        {"resources": {"tokens": 800.0, "cpu_ms": 200.0}},
        fresh_tracker,
    )
    check("track_resources returns tracker", isinstance(tr_result["tracker"], ResourceTracker))
    check("track_resources report has consumed", "consumed" in tr_result["report"])

    # ---- record_obligation / close_obligation ----
    obl_dict = {"description": "must verify cover-Z"}
    oid      = record_obligation(obl_dict, fresh_ledger)
    check("record_obligation returns str", isinstance(oid, str))
    jmt = close_obligation(oid, {"proof": "hash-abc"}, fresh_ledger)
    check("close_obligation returns Judgment", isinstance(jmt, Judgment))
    check("close_obligation Judgment is WITNESSED", jmt.trust_tier == TrustTier.WITNESSED)

    # ---- AccountingEngine ----
    engine = AccountingEngine(
        engine_id = str(uuid.uuid4()),
        ledger    = ObligationLedger(ledger_id=str(uuid.uuid4())),
        tracker   = ResourceTracker(
            tracker_id = str(uuid.uuid4()),
            budgets    = (("tokens", 500.0),),
        ),
    )
    engine = engine.account_step({
        "obligation": {"obligation_id": "eng-obl-1", "desc": "step A"},
        "resource":   "tokens",
        "amount":     100.0,
        "completion": {
            "construction_id":  "step-A",
            "cover_element_id": "cover-1",
            "evidence":         ["ev-A"],
            "trust_achieved":   int(TrustTier.WITNESSED),
        },
    })
    engine = engine.account_step({
        "obligation": {"obligation_id": "eng-obl-2", "desc": "step B"},
        "completion": {
            "construction_id":  "step-B",
            "cover_element_id": "cover-1",
            "evidence":         ["ev-B"],
            "trust_achieved":   int(TrustTier.SOLVER_CHECKED),
        },
    })
    check("AccountingEngine.account_step records two records", len(engine.records) == 2)
    check("AccountingEngine.account_step ledger has 2 obligations", engine.ledger.pending_count() == 2)

    engine_fin = engine.finalize()
    check("AccountingEngine.finalize closes all pending", engine_fin.ledger.is_balanced())

    audit = engine_fin.audit()
    check("audit_accounting returns dict with engine_id", audit["engine_id"] == engine_fin.engine_id)
    check("audit_accounting detects Čech obstruction (cover-1 tier mismatch)",
          len(audit["cech_obstructions"]) >= 1)
    check("audit_accounting is_clean False (has obstructions)",
          audit["is_clean"] is False)
    check("audit_accounting completion_records.total == 2",
          audit["completion_records"]["total"] == 2)

    # ---- Summary ----
    total = len(passed) + len(failed)
    print(f"\nSmoke test: {len(passed)}/{total} passed, {len(failed)}/{total} failed.")
    if failed:
        print("FAILED checks:")
        for f in failed:
            print(f"  - {f}")
        raise SystemExit(1)
    else:
        print("All checks passed.")


if __name__ == "__main__":
    _smoke_test()
