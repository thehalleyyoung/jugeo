"""H^1 computation, excess novelty fraction, and bridge ideas.

Implements the cohomological novelty criterion from §3 and §9.5.
"""

from __future__ import annotations

from jugeo.directed_research._types import (
    BridgeProposition,
    ExcessNoveltyFraction,
)


def compute_enf(
    overlap_size: int,
    restriction_image_size: int,
) -> float:
    """Compute excess novelty fraction.

    ENF = (dim(overlap) - dim(im(res_1) + im(res_2))) / dim(overlap)
    """
    if overlap_size == 0:
        return 0.0
    return max(0.0, (overlap_size - restriction_image_size) / overlap_size)


def is_novel(proposition: BridgeProposition) -> bool:
    """Check the H^1 condition: [sigma] != 0."""
    return proposition.novelty_score > 0.5
