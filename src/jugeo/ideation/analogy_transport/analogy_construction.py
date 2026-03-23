"""analogy_construction – Analogy construction for jugeo.ideation.analogy_transport (Ch60).

Provides :class:`AnalogyConstructor` and :class:`AnalogyConfig` for building
and verifying :class:`~jugeo.ideation.analogy_transport.models.AnalogyMap`
instances from domain descriptions or :class:`~jugeo.ideation.federation.CrossRegimeBridge`
objects.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from jugeo.ideation.analogy_transport.models import (
    AnalogyMap, AnalogyVerification, AnalogyQuality,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# AnalogyConfig
# ---------------------------------------------------------------------------

@dataclass
class AnalogyConfig:
    """Configuration for the analogy construction pipeline.

    Parameters
    ----------
    min_faithfulness:
        Minimum faithfulness score required for a valid analogy (default 0.1).
    min_coverage:
        Minimum coverage score required (default 0.1).
    max_correspondences:
        Maximum number of correspondences to include (default 100).
    verify_on_construct:
        Whether to immediately verify constructed analogies (default False).
    """

    min_faithfulness: float = 0.1
    min_coverage: float = 0.1
    max_correspondences: int = 100
    verify_on_construct: bool = False


# ---------------------------------------------------------------------------
# AnalogyConstructor
# ---------------------------------------------------------------------------

class AnalogyConstructor:
    """Constructs and verifies :class:`AnalogyMap` instances.

    Parameters
    ----------
    config:
        Optional configuration; uses defaults when ``None``.
    """

    def __init__(self, config: AnalogyConfig | None = None) -> None:
        self._config = config or AnalogyConfig()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def construct(self, source: str, target: str) -> AnalogyMap:
        """Construct a minimal :class:`AnalogyMap` between *source* and *target*.

        The resulting map has no correspondences by default; callers are
        expected to enrich it with domain-specific knowledge.
        """
        return AnalogyMap(
            map_id=_uid(),
            source_domain=source,
            target_domain=target,
            correspondences=(),
            faithfulness_score=self._config.min_faithfulness,
            coverage_score=self._config.min_coverage,
            created_at=_now_iso(),
        )

    def construct_from_bridge(self, bridge: Any) -> AnalogyMap:
        """Derive an :class:`AnalogyMap` from a :class:`~jugeo.ideation.federation.CrossRegimeBridge`.

        Maps each entry in ``bridge.analogy_map`` to a correspondence pair and
        derives faithfulness from ``1 - bridge.trust_attenuation``.
        """
        raw: dict[str, str] = bridge.analogy_map if hasattr(bridge, "analogy_map") else {}
        correspondences: tuple[tuple[str, str], ...] = tuple(
            (k, v) for k, v in list(raw.items())[: self._config.max_correspondences]
        )
        trust_att = getattr(bridge, "trust_attenuation", 0.2)
        faithfulness = max(0.0, min(1.0, 1.0 - trust_att))
        coverage = len(correspondences) / max(len(raw), 1) if raw else self._config.min_coverage
        return AnalogyMap(
            map_id=_uid(),
            source_domain=getattr(bridge, "source", "unknown"),
            target_domain=getattr(bridge, "target", "unknown"),
            correspondences=correspondences,
            faithfulness_score=faithfulness,
            coverage_score=coverage,
            created_at=_now_iso(),
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, analogy: AnalogyMap) -> AnalogyVerification:
        """Perform basic verification of *analogy*.

        Checks faithfulness threshold, coverage threshold, and that at least
        one correspondence exists.
        """
        steps = [
            "check_faithfulness_threshold",
            "check_coverage_threshold",
            "check_correspondence_non_empty",
        ]
        passed: list[str] = []
        failed: list[str] = []

        # Faithfulness check
        if analogy.faithfulness_score >= self._config.min_faithfulness:
            passed.append("check_faithfulness_threshold")
        else:
            failed.append("check_faithfulness_threshold")

        # Coverage check
        if analogy.coverage_score >= self._config.min_coverage:
            passed.append("check_coverage_threshold")
        else:
            failed.append("check_coverage_threshold")

        # Non-empty correspondences
        if analogy.correspondences:
            passed.append("check_correspondence_non_empty")
        else:
            failed.append("check_correspondence_non_empty")

        is_valid = len(failed) == 0
        confidence = len(passed) / len(steps) if steps else 0.0
        return AnalogyVerification(
            verification_id=_uid(),
            map_id=analogy.map_id,
            verification_steps=tuple(steps),
            passed_checks=tuple(passed),
            failed_checks=tuple(failed),
            is_valid=is_valid,
            confidence=confidence,
            verified_at=_now_iso(),
        )


__all__ = ["AnalogyConfig", "AnalogyConstructor"]
