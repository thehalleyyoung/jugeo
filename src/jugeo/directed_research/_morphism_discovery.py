"""Analogy morphism finding between domains (§5, §9.7)."""

from __future__ import annotations

from jugeo.directed_research._types import MethodologicalTranslation


def morphism_strength_to_kind(strength: float) -> str:
    """Map morphism strength to kind (§5.1)."""
    if strength >= 0.85:
        return "ISOMORPHISM"
    elif strength >= 0.6:
        return "EMBEDDING"
    else:
        return "ANALOGY"
