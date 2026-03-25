"""
Typed obligation presheaf management.

Replaces Comet-H's flat 5-dimensional obligation vector with a fully typed,
coordinate-aware presheaf structure.  Obligations are organised by kind and
coordinate, with support-aware staleness detection (no magic decay constants).
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any, Optional

from .models import (
    MoveResult,
    ObligationKind,
    ObligationPresheaf,
    SupportAwareDecay,
    TypedObligation,
)

__all__ = ["ObligationManager"]


class ObligationManager:
    """Create, track, discharge and analyse typed obligations."""

    def __init__(self) -> None:
        self._obligations: dict[str, TypedObligation] = {}
        self._failure_reasons: dict[str, str] = {}

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_obligation(
        self,
        kind: ObligationKind,
        coordinate_id: str,
        proposition: str,
        trust_target: str = "conjecture",
        priority: float = 1.0,
        deadline: Optional[float] = None,
    ) -> TypedObligation:
        """Create a new PENDING obligation and register it."""
        ob = TypedObligation(
            id=str(uuid.uuid4()),
            kind=kind,
            coordinate_id=coordinate_id,
            proposition=proposition,
            trust_target=trust_target,
            priority=priority,
            status="PENDING",
            created_at=time.time(),
            deadline=deadline,
        )
        self._obligations[ob.id] = ob
        return ob

    def discharge(self, obligation_id: str, evidence_id: str) -> bool:
        """Discharge an obligation with evidence. Returns True on success."""
        ob = self._obligations.get(obligation_id)
        if ob is None or ob.status != "PENDING":
            return False
        ob.status = "DISCHARGED"
        ob.discharge_evidence_id = evidence_id
        return True

    def fail(self, obligation_id: str, reason: str) -> bool:
        """Mark an obligation as FAILED. Returns True on success."""
        ob = self._obligations.get(obligation_id)
        if ob is None or ob.status != "PENDING":
            return False
        ob.status = "FAILED"
        self._failure_reasons[obligation_id] = reason
        return True

    def expire_overdue(self) -> list[str]:
        """Mark overdue obligations as EXPIRED. Returns list of expired ids."""
        now = time.time()
        expired: list[str] = []
        for ob in self._obligations.values():
            if (
                ob.status == "PENDING"
                and ob.deadline is not None
                and ob.deadline < now
            ):
                ob.status = "EXPIRED"
                expired.append(ob.id)
        return expired

    # ------------------------------------------------------------------
    # Pressure
    # ------------------------------------------------------------------

    def compute_pressure(self) -> float:
        """Total obligation pressure (sum of pending priorities)."""
        return sum(
            ob.priority
            for ob in self._obligations.values()
            if ob.status == "PENDING"
        )

    def pressure_by_kind(self) -> dict[ObligationKind, float]:
        """Pressure grouped by obligation kind."""
        result: dict[ObligationKind, float] = defaultdict(float)
        for ob in self._obligations.values():
            if ob.status == "PENDING":
                result[ob.kind] += ob.priority
        return dict(result)

    def pressure_by_coordinate(self, coordinate_id: str) -> float:
        """Pressure for a single coordinate."""
        return sum(
            ob.priority
            for ob in self._obligations.values()
            if ob.status == "PENDING" and ob.coordinate_id == coordinate_id
        )

    # ------------------------------------------------------------------
    # Staleness
    # ------------------------------------------------------------------

    def support_aware_staleness(
        self, code_change_times: dict[str, float]
    ) -> list[SupportAwareDecay]:
        """Detect stale evidence where code changed after evidence was gathered.

        Unlike magic-constant decay, staleness is determined entirely by
        comparing ``code_change_times[coordinate_id]`` with the obligation's
        ``created_at`` timestamp (used as evidence timestamp proxy).
        """
        results: list[SupportAwareDecay] = []
        seen: set[str] = set()
        for ob in self._obligations.values():
            cid = ob.coordinate_id
            if cid in seen:
                continue
            seen.add(cid)

            code_ts = code_change_times.get(cid)
            if code_ts is None:
                continue

            evidence_ts = ob.created_at
            is_stale = code_ts > evidence_ts
            staleness_days = max(0.0, (code_ts - evidence_ts) / 86400.0) if is_stale else 0.0

            results.append(
                SupportAwareDecay(
                    coordinate_id=cid,
                    last_code_change_at=code_ts,
                    evidence_timestamp=evidence_ts,
                    is_stale=is_stale,
                    staleness_days=staleness_days,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def pending_for_coordinate(self, coordinate_id: str) -> list[TypedObligation]:
        """All PENDING obligations for a coordinate."""
        return [
            ob
            for ob in self._obligations.values()
            if ob.status == "PENDING" and ob.coordinate_id == coordinate_id
        ]

    def pending_by_kind(self, kind: ObligationKind) -> list[TypedObligation]:
        """All PENDING obligations of a given kind."""
        return [
            ob
            for ob in self._obligations.values()
            if ob.status == "PENDING" and ob.kind == kind
        ]

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_from_move(self, move_result: MoveResult) -> list[TypedObligation]:
        """Generate obligations from a move result.

        For each obligation kind string in ``move_result.obligations_generated``
        and each modified section, create an obligation.
        """
        created: list[TypedObligation] = []
        for kind_str in move_result.obligations_generated:
            try:
                kind = ObligationKind(kind_str)
            except ValueError:
                kind = ObligationKind.CUSTOM
            for section in move_result.sections_modified:
                ob = self.create_obligation(
                    kind=kind,
                    coordinate_id=section,
                    proposition=f"Obligation from move {move_result.move_id}",
                    trust_target="conjecture",
                )
                created.append(ob)
        return created

    def generate_grounding_obligations(
        self, changed_coordinates: list[str]
    ) -> list[TypedObligation]:
        """Create GROUNDING obligations for each changed coordinate."""
        created: list[TypedObligation] = []
        for cid in changed_coordinates:
            ob = self.create_obligation(
                kind=ObligationKind.GROUNDING,
                coordinate_id=cid,
                proposition=f"Ground coordinate {cid} after code change",
                trust_target="conjecture",
            )
            created.append(ob)
        return created

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def to_presheaf(self) -> ObligationPresheaf:
        """Return a snapshot as an ObligationPresheaf."""
        by_kind: dict[str, list[str]] = defaultdict(list)
        by_coordinate: dict[str, list[str]] = defaultdict(list)
        obligations_dict: dict[str, dict[str, Any]] = {}

        for ob in self._obligations.values():
            obligations_dict[ob.id] = ob.to_dict()
            by_kind[ob.kind.value].append(ob.id)
            by_coordinate[ob.coordinate_id].append(ob.id)

        pressure_by_kind: dict[str, float] = {}
        for kind, pressure in self.pressure_by_kind().items():
            pressure_by_kind[kind.value] = pressure

        return ObligationPresheaf(
            obligations=obligations_dict,
            by_kind=dict(by_kind),
            by_coordinate=dict(by_coordinate),
            total_pressure=self.compute_pressure(),
            pressure_by_kind=pressure_by_kind,
        )

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def statistics(self) -> dict[str, Any]:
        """Return summary statistics about the obligation pool."""
        total = len(self._obligations)
        pending = sum(1 for o in self._obligations.values() if o.status == "PENDING")
        discharged = sum(1 for o in self._obligations.values() if o.status == "DISCHARGED")
        failed = sum(1 for o in self._obligations.values() if o.status == "FAILED")
        expired = sum(1 for o in self._obligations.values() if o.status == "EXPIRED")

        return {
            "total": total,
            "pending": pending,
            "discharged": discharged,
            "failed": failed,
            "expired": expired,
            "total_pressure": self.compute_pressure(),
            "pressure_by_kind": {
                k.value: v for k, v in self.pressure_by_kind().items()
            },
        }
