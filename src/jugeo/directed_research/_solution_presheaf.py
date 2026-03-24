"""Solution presheaf, demand sheaf, and usefulness pairing.

Implements the solution presheaf S and demand presheaf P from §9.2-9.6.
These are used by the ideation engine to compute useful novelty scores.
"""

from __future__ import annotations

from jugeo.directed_research._types import (
    SolutionSection,
    DemandSection,
    SubDomain,
    UsefulNoveltyScore,
    RelevanceFiltrationLevel,
)


def usefulness_pairing(
    solution: SolutionSection,
    demand: DemandSection,
) -> float:
    """Compute <sigma, p> — the usefulness pairing (Definition 9.5).

    Returns a value in [0, 1] measuring how much of the problem's
    demand is met by the solution.
    """
    if solution.sub_domain != demand.sub_domain:
        return 0.0
    # Simple heuristic: check keyword overlap
    sol_words = set(solution.description.lower().split())
    dem_words = set(demand.description.lower().split())
    if not dem_words:
        return 0.0
    overlap = len(sol_words & dem_words)
    return min(1.0, overlap / max(len(dem_words), 1))
