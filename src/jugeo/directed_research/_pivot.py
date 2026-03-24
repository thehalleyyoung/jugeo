"""Theory pivoting with adjacency constraints.

A pivot is an adjacency-constrained theory change: you change ONE aspect
of the mathematical framework while keeping the rest. In geometric terms,
a pivot moves the Theory section to an adjacent coordinate in the theory
space — one that shares most of its covering family but differs on the
aspect that caused the obstruction.
"""

from __future__ import annotations

import textwrap
from typing import Optional

from jugeo.research_orchestration import SurfaceKind

from jugeo.directed_research._types import TRUST_COPILOT, LLMSection
from jugeo.directed_research._agent_channel import agent_call


def pivot_theory(
    current_theory: str,
    prompt: str,
    obstructions: list[str],
    pivot_number: int,
) -> tuple[str, LLMSection]:
    """Execute an adjacency-constrained theory pivot.

    The pivot changes one aspect of the theory while keeping the rest.
    The obstruction descriptions guide what needs to change.
    """
    obs_text = "\n".join(f"  - {o}" for o in obstructions[:5])

    section = agent_call(
        textwrap.dedent(f"""\
            The current mathematical approach isn't working well enough.
            Propose a PIVOT — change ONE thing about the framework.

            CURRENT THEORY (first 500 chars): {current_theory[:500]}
            PROMPT: {prompt}
            PIVOT NUMBER: {pivot_number}
            OBSTRUCTIONS (why current approach fails):
            {obs_text}

            Change ONE thing about the mathematical framework that addresses
            the obstructions. Keep what works, fix what doesn't. The pivot
            must be ADJACENT — not a complete rewrite, but a targeted change.

            Write the revised theory (1000+ words). Return raw text.
        """),
        surface=SurfaceKind.THEORY,
        coordinate=f"theory.pivot_{pivot_number}",
    )

    return section.content, section
