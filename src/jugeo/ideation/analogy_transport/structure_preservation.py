"""structure_preservation – Structure-preservation auditing (Ch60).

Provides :class:`StructurePreservationAuditor` for auditing how faithfully
an :class:`~jugeo.ideation.analogy_transport.models.AnalogyMap` preserves
the relational structure of its source domain.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from jugeo.ideation.analogy_transport.models import (
    AnalogyMap, StructurePreservation,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# StructurePreservationAuditor
# ---------------------------------------------------------------------------

class StructurePreservationAuditor:
    """Audits an :class:`AnalogyMap` for structural preservation.

    The auditor checks whether the correspondences in the map preserve the
    standard algebraic/logical relations (composition, identity, ordering, etc.)
    up to the faithfulness bound reported by the map itself.
    """

    # Relations considered when faithfulness is high enough
    _STANDARD_RELATIONS: tuple[str, ...] = (
        "composition",
        "identity",
        "ordering",
        "morphism",
        "inclusion",
        "inversion",
    )

    def audit(self, analogy: AnalogyMap) -> StructurePreservation:
        """Audit *analogy* and return a :class:`StructurePreservation` record.

        A relation is deemed *preserved* when the faithfulness score exceeds
        the relation's threshold, and *violated* otherwise.  The aggregate
        preservation score is the fraction of relations preserved, weighted by
        the map's faithfulness score.

        Parameters
        ----------
        analogy:
            The analogy map to audit.

        Returns
        -------
        StructurePreservation
            Audit result with preserved/violated relation lists and a score.
        """
        preserved: list[str] = []
        violated: list[str] = []

        # Simple threshold model: higher-order relations require higher faith
        thresholds: dict[str, float] = {
            "identity": 0.1,
            "inclusion": 0.2,
            "inversion": 0.3,
            "ordering": 0.4,
            "morphism": 0.5,
            "composition": 0.6,
        }

        for relation in self._STANDARD_RELATIONS:
            threshold = thresholds.get(relation, 0.5)
            if analogy.faithfulness_score >= threshold:
                preserved.append(relation)
            else:
                violated.append(relation)

        # Weight the score by faithfulness so perfect maps score 1.0
        total = len(self._STANDARD_RELATIONS)
        raw_score = len(preserved) / total if total else 0.0
        preservation_score = raw_score * analogy.faithfulness_score

        return StructurePreservation(
            preservation_id=_uid(),
            map_id=analogy.map_id,
            preserved_relations=tuple(preserved),
            violated_relations=tuple(violated),
            preservation_score=min(1.0, preservation_score),
            checked_at=_now_iso(),
        )


__all__ = ["StructurePreservationAuditor"]
