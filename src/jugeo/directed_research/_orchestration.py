"""Phase orchestration for directed research.

Maps the Comet-H phase structure (Seed → Generate → Harden → Tail) onto
the descent-driven research loop. Each phase has a set of moves that it
can execute, and the transition between phases is governed by the
convergence criterion.
"""

from __future__ import annotations

from jugeo.research_orchestration import PhaseKind


# Phase ordering
PHASE_ORDER = [PhaseKind.SEED, PhaseKind.GENERATE, PhaseKind.HARDEN, PhaseKind.TAIL]


def next_phase(current: PhaseKind) -> PhaseKind:
    """Get the next phase in the ordering."""
    idx = PHASE_ORDER.index(current)
    if idx < len(PHASE_ORDER) - 1:
        return PHASE_ORDER[idx + 1]
    return current
