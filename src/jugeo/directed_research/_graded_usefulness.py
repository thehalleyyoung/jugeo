"""Relevance filtration and graded UNS scoring (§9.10)."""

from __future__ import annotations

from jugeo.directed_research._types import (
    BridgeProposition,
    UsefulNoveltyScore,
    RelevanceFiltrationLevel,
)


def graded_uns(proposition: BridgeProposition) -> float:
    """Compute the graded useful novelty score (Definition 9.6)."""
    return proposition.useful_novelty_score
